import os
import uuid
import subprocess
import json
from flask import Flask, request, jsonify, send_file
from threading import Thread
import time
import requests as req

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")


def generate_russian_title(english_title):
    """Генерируем русский заголовок через Claude API"""
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
COOKIES_PATH = "/app/cookies.txt"
PROXY = os.environ.get("PROXY_URL")  # http://user:pass@host:port

os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def cleanup_file(path, delay=300):
    def _delete():
        time.sleep(delay)
        try:
            os.remove(path)
        except:
            pass
    Thread(target=_delete, daemon=True).start()


def find_node():
    for path in ["/usr/bin/node", "/usr/local/bin/node", "/usr/bin/nodejs"]:
        if os.path.exists(path):
            return path
    return "node"


def build_ytdlp_cmd(url, output_path):
    node_path = find_node()
    cmd = [
        "yt-dlp",
        "-f", "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4][height<=720]/best[height<=720]",
        "--merge-output-format", "mp4",
        "-o", output_path,
        "--no-playlist",
        "--js-runtimes", f"node:{node_path}",
    ]

    # Куки если файл существует и не пустой
    if os.path.exists(COOKIES_PATH) and os.path.getsize(COOKIES_PATH) > 0:
        cmd += ["--cookies", COOKIES_PATH]

    # Прокси если задан
    if PROXY:
        cmd += ["--proxy", PROXY]

    cmd.append(url)
    return cmd


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "proxy": bool(PROXY),
        "cookies": os.path.exists(COOKIES_PATH) and os.path.getsize(COOKIES_PATH) > 0
    })


@app.route("/update-cookies", methods=["POST"])
def update_cookies():
    """Обновить cookies.txt без передеплоя"""
    auth = request.headers.get("X-API-Key")
    if auth != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    if "file" not in request.files:
        return jsonify({"error": "Нет файла, передай file в multipart/form-data"}), 400

    file = request.files["file"]
    file.save(COOKIES_PATH)

    return jsonify({
        "success": True,
        "size": os.path.getsize(COOKIES_PATH)
    })


# ==========================================
# НОВЫЙ ЭНДПОИНТ: только скачать видео
# ==========================================
@app.route("/download_only", methods=["POST"])
def download_only():
    """Скачать видео с YouTube и вернуть ссылку на файл + русский заголовок"""
    auth = request.headers.get("X-API-Key")
    if auth != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    if not data or "url" not in data:
        return jsonify({"error": "url is required"}), 400

    url = data["url"]
    title = data.get("title", "")
    generate_title = data.get("generate_title", True)

    file_id = str(uuid.uuid4())[:8]
    output_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")

    # --- Шаг 1: скачиваем видео ---
    cmd = build_ytdlp_cmd(url, output_path)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)

    if result.returncode != 0:
        return jsonify({
            "error": "Download failed",
            "stderr": result.stderr[-3000:],
            "stdout": result.stdout[-1000:]
        }), 500

    # Ищем файл если имя изменилось
    if not os.path.exists(output_path):
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(file_id):
                output_path = os.path.join(DOWNLOAD_DIR, f)
                break
        else:
            return jsonify({"error": "Файл не найден после скачивания"}), 500

    file_size = os.path.getsize(output_path)
    filename = os.path.basename(output_path)

    # --- Шаг 2: генерим русский заголовок ---
    vk_title_ru = title
    if generate_title and title:
        vk_title_ru = generate_russian_title(title)

    # Файл живет 30 минут
    cleanup_file(output_path, delay=1800)

    # Формируем прямую ссылку на файл
base_url = request.host_url.rstrip("/").replace("http://", "https://")
file_url = f"{base_url}/files/{filename}"

    return jsonify({
        "success": True,
        "file_url": file_url,
        "filename": filename,
        "file_size": file_size,
        "title_original": title,
        "title_ru": vk_title_ru
    })


@app.route("/files/<filename>", methods=["GET"])
def serve_file(filename):
    """Отдаем скачанный файл по прямой ссылке"""
    # Защита от path traversal
    safe_name = os.path.basename(filename)
    file_path = os.path.join(DOWNLOAD_DIR, safe_name)

    if not os.path.exists(file_path):
        return jsonify({"error": "File not found"}), 404

    return send_file(file_path, mimetype="video/mp4", as_attachment=True, download_name=safe_name)


# ==========================================
# СТАРЫЙ ЭНДПОИНТ: оставляем для совместимости
# ==========================================
@app.route("/upload_to_vk", methods=["POST"])
def upload_to_vk():
    """Скачать с YouTube и загрузить в VK с превью"""
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

    # --- Генерируем русский заголовок ---
    vk_title_ru = generate_russian_title(title)

    file_id = str(uuid.uuid4())[:8]
    output_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")

    # --- Шаг 1: скачиваем видео ---
    cmd = build_ytdlp_cmd(url, output_path)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)

    if result.returncode != 0:
        return jsonify({
            "error": "Download failed",
            "stderr": result.stderr[-3000:],
            "stdout": result.stdout[-1000:]
        }), 500

    # Ищем файл если имя изменилось
    if not os.path.exists(output_path):
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(file_id):
                output_path = os.path.join(DOWNLOAD_DIR, f)
                break
        else:
            return jsonify({"error": "Файл не найден после скачивания"}), 500

    try:
        # --- Шаг 2: получаем upload URL от VK ---
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

        # --- Шаг 3: загружаем видео в VK ---
        with open(output_path, "rb") as f:
            upload_resp = req.post(upload_url, files={"video_file": f}, timeout=600)

        cleanup_file(output_path)

        # --- Шаг 4: превью если есть ---
        thumb_result = None
        if thumb_url:
            try:
                thumb_resp = req.get(thumb_url, timeout=30, headers={
                    "User-Agent": "Mozilla/5.0"
                })
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

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timeout"}), 504
    except Exception as e:
        cleanup_file(output_path)
        return jsonify({"error": str(e)}), 500


@app.route("/set_thumbnail", methods=["POST"])
def set_thumbnail():
    """Отдельный эндпоинт для установки превью"""
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
