@app.route("/upload_to_vk", methods=["POST"])
def upload_to_vk():
    auth = request.headers.get("X-API-Key")
    if auth != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    if not data or "url" not in data:
        return jsonify({"error": "url is required"}), 400

    url = data["url"]
    vk_token = data.get("vk_token")
    group_id = data.get("group_id")
    title = data.get("title", "")
    description = data.get("description", "")

    file_id = str(uuid.uuid4())[:8]
    output_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")
    node_path = find_node()

    try:
        # Скачиваем видео
        cmd = [
            "yt-dlp",
            "-f", "bestvideo[ext=mp4][filesize<900M]+bestaudio[ext=m4a]/best[ext=mp4][filesize<900M]/best[filesize<900M]",
            "--merge-output-format", "mp4",
            "-o", output_path,
            "--no-playlist",
            "--cookies", COOKIES_PATH,
            "--js-runtimes", f"node:{node_path}",
            url
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            return jsonify({"error": "Download failed", "stderr": result.stderr[-2000:]}), 500

        import requests as req

        # Получаем upload_url от ВК
        vk_save = req.post("https://api.vk.com/method/video.save", data={
            "access_token": vk_token,
            "group_id": group_id,
            "name": title,
            "description": description,
            "v": "5.131"
        }).json()

        upload_url = vk_save["response"]["upload_url"]
        video_id = vk_save["response"]["video_id"]
        owner_id = vk_save["response"]["owner_id"]

        # Загружаем видео напрямую в ВК
        with open(output_path, "rb") as f:
            req.post(upload_url, files={"video_file": f})

        cleanup_file(output_path)

        return jsonify({
            "video_id": video_id,
            "owner_id": owner_id
        })

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timeout"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500
