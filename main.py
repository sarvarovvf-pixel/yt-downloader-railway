import os
import uuid
import subprocess
from flask import Flask, request, jsonify, send_file
from threading import Thread
import time

app = Flask(__name__)

DOWNLOAD_DIR = "/tmp/downloads"
API_KEY = os.environ.get("API_KEY", "secret123")
COOKIES_PATH = "/app/cookies.txt"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def cleanup_file(path, delay=300):
    def _delete():
        time.sleep(delay)
        try:
            os.remove(path)
        except:
            pass
    Thread(target=_delete, daemon=True).start()


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/download", methods=["POST"])
def download():
    auth = request.headers.get("X-API-Key")
    if auth != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    if not data or "url" not in data:
        return jsonify({"error": "url is required"}), 400

    url = data["url"]
    file_id = str(uuid.uuid4())[:8]
    output_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")

    try:
        cmd = [
            "yt-dlp",
            "-f", "bestvideo[ext=mp4][filesize<900M]+bestaudio[ext=m4a]/best[ext=mp4][filesize<900M]/best[filesize<900M]",
            "--merge-output-format", "mp4",
            "-o", output_path,
            "--no-playlist",
            "--cookies", COOKIES_PATH,
            "--extractor-args", "youtube:player_client=web,default",
            url
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
        print(f"Return code: {result.returncode}")

        if result.returncode != 0:
            return jsonify({
                "error": "Download failed",
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-2000:],
                "returncode": result.returncode
            }), 500

        if not os.path.exists(output_path):
            for f in os.listdir(DOWNLOAD_DIR):
                if f.startswith(file_id):
                    output_path = os.path.join(DOWNLOAD_DIR, f)
                    break
            else:
                return jsonify({"error": "File not found after download", "stderr": result.stderr[-1000:]}), 500

        cleanup_file(output_path)

        return send_file(
            output_path,
            mimetype="video/mp4",
            as_attachment=True,
            download_name=f"{file_id}.mp4"
        )

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Download timeout (10 min exceeded)"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/info", methods=["POST"])
def info():
    auth = request.headers.get("X-API-Key")
    if auth != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    if not data or "url" not in data:
        return jsonify({"error": "url is required"}), 400

    url = data["url"]

    try:
        cmd = [
            "yt-dlp",
            "--dump-json",
            "--no-playlist",
            "--cookies", COOKIES_PATH,
            url
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

        if result.returncode != 0:
            return jsonify({"error": "Failed to get info", "stderr": result.stderr[-1000:]}), 500

        import json
        info_data = json.loads(result.stdout)

        return jsonify({
            "title": info_data.get("title"),
            "description": info_data.get("description"),
            "thumbnail": info_data.get("thumbnail"),
            "duration": info_data.get("duration"),
            "filesize_approx": info_data.get("filesize_approx"),
            "upload_date": info_data.get("upload_date"),
            "video_id": info_data.get("id"),
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
