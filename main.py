import os
import uuid
import subprocess
import json
from flask import Flask, request, jsonify, send_file
from threading import Thread
import time
import requests as req

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
POSTMYPOST_API = "https://api.postmypost.io/v4.1"


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
                "messages": [{"role": "user", "content": f"Придумай цепляющий русский заголовок для видео на основе английского заголовка. Заголовок должен быть живым, разговорным, подходящим для русскоязычной аудитории ВКонтакте. Не переводи дословно, адаптируй под русский стиль. Верни только заголовок, без кавычек и пояснений.\n\nАнглийский заголовок: {english_title}"}]
            },
            timeout=30
        )
        data = response.json()
        return data["content"][0]["text"].strip()
    except Exception as e:
        print(f"Claude API error: {e}")
        return english_title


def upload_file_to_postmypost_s3(file_path, file_name, mime_type, project_id, pmp_token):
    file_size = os.path.getsize(file_path) if isinstance(file_path, str) else len(file_path)
    is_bytes = not isinstance(file_path, str)

    init_resp = req.post(
        f"{POSTMYPOST_API}/upload/init",
        headers={"Authorization": f"Bearer {pmp_token}", "Content-Type": "application/json"},
        json={"project_id": project_id, "name": file_name, "size": file_size},
        timeout=30
    ).json()

    upload_id = init_resp.get("id")
    s3_action = init_resp.get("action")
    s3_fields = init_resp.get("fields")

    if not upload_id or not s3_action or not s3_fields:
        return {"error": "init_upload failed", "response": init_resp}

    fields = {}
    for field in s3_fields:
        fields[field["key"]] = field["value"]

    if is_bytes:
        files_data = {"file": (file_name, file_path, mime_type)}
    else:
        f = open(file_path, "rb")
        files_data = {"file": (file_name, f, mime_type)}

    s3_resp = req.post(s3_action, data=fields, files=files_data, timeout=600)

    if not is_bytes:
        f.close()

    if s3_resp.status_code not in [200, 201, 204]:
        return {"error": "S3 upload failed", "s3_status": s3_resp.status_code}

    req.post(
        f"{POSTMYPOST_API}/upload/complete",
        headers={"Authorization": f"Bearer {pmp_token}"},
        params={"id": upload_id},
        timeout=30
    )

    for attempt in range(20):
        time.sleep(3)
        status_resp = req.get(
            f"{POSTMYPOST_API}/upload/status",
            headers={"Authorization": f"Bearer {pmp_token}"},
            params={"id": upload_id},
            timeout=30
        ).json()
        file_id = status_resp.get("file_id")
        if file_id:
            return {"success": True, "file_id": file_id, "upload_id": upload_id}

    return {"error": "Timeout waiting for file_id", "last_status": status_resp}


app = Flask(__name__)

DOWNLOAD_DIR = "/tmp/downloads"
API_KEY = os.environ.get("API_KEY", "secret123")
COOKIES_PATH = "/app/cookies.txt"
PROXY = os.environ.get("PROXY_URL")

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
    if os.path.exists(COOKIES_PATH) and os.path.getsize(COOKIES_PATH) > 0:
        cmd += ["--cookies", COOKIES_PATH]
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
    auth = request.headers.get("X-API-Key")
    if auth != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    file = request.files["file"]
    file.save(COOKIES_PATH)
    return jsonify({"success": True, "size": os.path.getsize(COOKIES_PATH)})


@app.route("/download_and_publish", methods=["POST"])
def download_and_publish():
    auth = request.headers.get("X-API-Key")
    if auth != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    if not data or "url" not in data:
        return jsonify({"error": "url is required"}), 400

    url = data["url"]
    title = data.get("title", "")
    thumb_url = data.get("thumb_url")
    generate_title = data.get("generate_title", True)
    pmp_token = data.get("pmp_token")
    project_id = data.get("project_id")

    if not pmp_token or not project_id:
        return jsonify({"error": "pmp_token and project_id are required"}), 400

    file_id = str(uuid.uuid4())[:8]
    output_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")

    cmd = build_ytdlp_cmd(url, output_path)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)

    if result.returncode != 0:
        return jsonify({
            "error": "Download failed",
            "stderr": result.stderr[-3000:],
            "stdout": result.stdout[-1000:]
        }), 500

    if not os.path.exists(output_path):
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(file_id):
                output_path = os.path.join(DOWNLOAD_DIR, f)
                break
        else:
            return jsonify({"error": "File not found after download"}), 500

    title_ru = title
    if generate_title and title:
        title_ru = generate_russian_title(title)

    video_name = f"{title_ru}.mp4" if title_ru else f"{file_id}.mp4"
    video_result = upload_file_to_postmypost_s3(
        output_path, video_name, "video/mp4", project_id, pmp_token
    )

    cleanup_file(output_path, delay=300)

    if "error" in video_result:
        return jsonify({"error": "Video upload failed", "details": video_result}), 500

    video_file_id = video_result["file_id"]

    thumb_file_id = None
    if thumb_url:
        try:
            thumb_resp = req.get(thumb_url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            if thumb_resp.status_code == 200:
                thumb_data = thumb_resp.content
                thumb_result = upload_file_to_postmypost_s3(
                    thumb_data, "thumbnail.jpg", "image/jpeg", project_id, pmp_token
                )
                if "file_id" in thumb_result:
                    thumb_file_id = thumb_result["file_id"]
        except Exception as e:
            print(f"Thumbnail upload error: {e}")

    return jsonify({
        "success": True,
        "title_original": title,
        "title_ru": title_ru,
        "video_file_id": video_file_id,
        "thumb_file_id": thumb_file_id
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

    cmd = build_ytdlp_cmd(url, output_path)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)

    if result.returncode != 0:
        return jsonify({
            "error": "Download failed",
            "stderr": result.stderr[-3000:],
            "stdout": result.stdout[-1000:]
        }), 500

    if not os.path.exists(output_path):
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(file_id):
                output_path = os.path.join(DOWNLOAD_DIR, f)
                break
        else:
            return jsonify({"error": "File not found"}), 500

    try:
        vk_save = req.post("https://api.vk.com/method/video.save", data={
            "access_token": vk_token, "group_id": group_id,
            "name": vk_title_ru, "description": description, "v": "5.199"
        }).json()

        if "error" in vk_save:
            return jsonify({"error": "video.save failed", "vk_error": vk_save["error"]}), 500

        upload_url = vk_save["response"]["upload_url"]
        video_id = vk_save["response"]["video_id"]
        owner_id = vk_save["response"]["owner_id"]

        with open(output_path, "rb") as f:
            req.post(upload_url, files={"video_file": f}, timeout=600)

        cleanup_file(output_path)

        thumb_result = None
        if thumb_url:
            try:
                thumb_resp = req.get(thumb_url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
                thumb_data = thumb_resp.content
                get_thumb_url = req.post("https://api.vk.com/method/video.getThumbUploadUrl", data={
                    "access_token": vk_token, "owner_id": owner_id, "video_id": video_id, "v": "5.199"
                }).json()
                if "response" in get_thumb_url:
                    thumb_upload_url = get_thumb_url["response"]["upload_url"]
                    thumb_upload = req.post(thumb_upload_url, files={"file": ("thumb.jpg", thumb_data, "image/jpeg")}).json()
                    save_thumb = req.post("https://api.vk.com/method/video.saveUploadedThumb", data={
                        "access_token": vk_token, "owner_id": owner_id, "video_id": video_id,
                        "thumb_json": json.dumps(thumb_upload), "set_thumb": 1, "v": "5.199"
                    }).json()
                    thumb_result = save_thumb
            except Exception as e:
                thumb_result = {"error": str(e)}

        return jsonify({
            "success": True, "video_id": video_id, "owner_id": owner_id,
            "vk_title_ru": vk_title_ru, "thumb_result": thumb_result
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timeout"}), 504
    except Exception as e:
        cleanup_file(output_path)
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
