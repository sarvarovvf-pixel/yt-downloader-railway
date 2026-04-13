import os
import uuid
import json
from flask import Flask, request, jsonify
from threading import Thread
import time
import requests as req

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")


def generate_russian_title(english_title):
    try:
        response = req.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 200,
                "messages": [{
                    "role": "user",
                    "content": f"""Придумай цепляющий русский заголовок для видео на основе английского заголовка. 
Заголовок должен быть живым, разговорным, подходящим для русскоязычной аудитории ВКонтакте.
Не переводи дословно, адаптируй под русский стиль.
Верни только заголовок, без кавычек и пояснений.

Английский заголовок: {english_title}"""
                }]
            },
            timeout=30
        )
        data = response.json()
        return data["content"][0]["text"].strip()
    except Exception as e:
        print(f"Claude API error: {e}")
        return english_title


app = Flask(__name__)

DOWNLOAD_DIR = "/tmp/downloads"
API_KEY = os.environ.get("API_KEY", "secret123")

os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def cleanup_file(path, delay=300):
    def _delete():
        time.sleep(delay)
        try:
            os.remove(path)
        except:
            pass
    Thread(target=_delete, daemon=True).start()


def download_via_rapidapi(youtube_url, output_path):
    # Шаг 1: инициируем скачивание
    response = req.get(
        "https://youtube-info-download-api.p.rapidapi.com/ajax/download.php",
        headers={
            "x-rapidapi-host": "youtube-info-download-api.p.rapidapi.com",
            "x-rapidapi-key": RAPIDAPI_KEY,
            "Content-Type": "application/json"
        },
        params={
            "format": "mp4",
            "add_info": "0",
            "url": youtube_url,
            "allow_extended_duration": "false",
            "no_merge": "false"
        },
        timeout=60
    )

    data = response.json()
    print(f"RapidAPI initial response: {data}")

    if not data.get("success"):
        raise Exception(f"RapidAPI error: {data}")

    # Шаг 2: polling по progress_url
    progress_url = data.get("progress_url")
    if not progress_url:
        raise Exception(f"No progress_url in response: {data}")

    download_url = None
    last_progress = None
    for attempt in range(30):
        time.sleep(10)
        last_progress = req.get(progress_url, timeout=30).json()
        print(f"Progress attempt {attempt + 1}: {last_progress}")

        if last_progress.get("download_url"):
            download_url = last_progress["download_url"]
            break
        elif last_progress.get("url"):
            download_url = last_progress["url"]
            break
        elif last_progress.get("status") == "error":
            raise Exception(f"RapidAPI processing error: {last_progress}")

    if not download_url:
        raise Exception(f"Download URL not ready after polling: {last_progress}")

    # Шаг 3: скачиваем файл
    with req.get(download_url, stream=True, timeout=900) as r:
        r.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

    return output_path


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "rapidapi": bool(RAPIDAPI_KEY),
        "mode": "rapidapi"
    })


@app.route("/upload_to_vk", methods=["POST"])
def upload_to_vk():
    auth = request.headers.get("X-API-Key")
    if auth != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    if not data or "url" not in data:
        return jsonify({"error": "url is required"}), 400

    url = data["url"]
    vk_token = data.get("vk_token") or os.environ.get("VK_TOKEN")
    group_id = data.get("group_id") or os.environ.get("VK_GROUP_ID")
    title = data.get("title", "")
    description = data.get("description", "")
    thumb_url = data.get("thumb_url")

    vk_title_ru = generate_russian_title(title)

    file_id = str(uuid.uuid4())[:8]
    output_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")

    try:
        download_via_rapidapi(url, output_path)
    except Exception as e:
        return jsonify({
            "error": "Download failed",
            "stderr": str(e)
        }), 500

    if not os.path.exists(output_path):
        return jsonify({"error": "Файл не найден после скачивания"}), 500

    try:
        vk_save = req.post("https://api.vk.com/method/video.save", data={
            "access_token": vk_token,
            "group_id": group_id,
            "name": vk_title_ru,
            "description": description,
            "v": "5.199"
        }).json()

        if "error" in vk_save:
            return jsonify({"error": "video.save failed", "vk_error": vk_save["error"]}), 500

        upload_url = vk_save["response"]["upload_url"]
        video_id = vk_save["response"]["video_id"]
        owner_id = vk_save["response"]["owner_id"]

        with open(output_path, "rb") as f:
            upload_resp = req.post(upload_url, files={"video_file": f}, timeout=600)

        cleanup_file(output_path)

        thumb_result = None
        if thumb_url:
            try:
                thumb_resp = req.get(thumb_url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
                thumb_data = thumb_resp.content

                get_thumb_url = req.post(
                    "https://api.vk.com/method/video.getThumbUploadUrl",
                    data={
                        "access_token": vk_token,
                        "owner_id": owner_id,
                        "video_id": video_id,
                        "v": "5.199"
                    }
                ).json()

                if "response" in get_thumb_url:
                    thumb_upload_url = get_thumb_url["response"]["upload_url"]
                    thumb_upload = req.post(
                        thumb_upload_url,
                        files={"file": ("thumb.jpg", thumb_data, "image/jpeg")}
                    ).json()

                    save_thumb = req.post(
                        "https://api.vk.com/method/video.saveUploadedThumb",
                        data={
                            "access_token": vk_token,
                            "owner_id": owner_id,
                            "video_id": video_id,
                            "thumb_json": json.dumps(thumb_upload),
                            "set_thumb": 1,
                            "v": "5.199"
                        }
                    ).json()
                    thumb_result = save_thumb
            except Exception as e:
                thumb_result = {"error": str(e)}

        return jsonify({
            "success": True,
            "video_id": video_id,
            "owner_id": owner_id,
            "vk_title_ru": vk_title_ru,
            "thumb_result": thumb_result
        })

    except Exception as e:
        cleanup_file(output_path)
        return jsonify({"error": str(e)}), 500


@app.route("/set_thumbnail", methods=["POST"])
def set_thumbnail():
    auth = request.headers.get("X-API-Key")
    if auth != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    vk_token = data.get("vk_token") or os.environ.get("VK_TOKEN")
    video_id = data.get("video_id")
    owner_id = data.get("owner_id")
    thumbnail_url = data.get("thumbnail_url")

    try:
        thumb_resp = req.get(thumbnail_url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        thumb_data = thumb_resp.content

        get_url_resp = req.post(
            "https://api.vk.com/method/video.getThumbUploadUrl",
            data={
                "access_token": vk_token,
                "owner_id": owner_id,
                "video_id": video_id,
                "v": "5.199"
            }
        ).json()

        if "error" in get_url_resp:
            return jsonify({"error": "getThumbUploadUrl failed", "vk_error": get_url_resp["error"]}), 500

        upload_url = get_url_resp["response"]["upload_url"]
        upload_resp = req.post(
            upload_url,
            files={"file": ("thumb.jpg", thumb_data, "image/jpeg")}
        ).json()

        save_resp = req.post(
            "https://api.vk.com/method/video.saveUploadedThumb",
            data={
                "access_token": vk_token,
                "owner_id": owner_id,
                "video_id": video_id,
                "thumb_json": json.dumps(upload_resp),
                "set_thumb": 1,
                "v": "5.199"
            }
        ).json()

        return jsonify({
            "upload_result": upload_resp,
            "save_result": save_resp
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
