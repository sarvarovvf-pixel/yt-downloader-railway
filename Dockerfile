FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

RUN curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp \
    && chmod a+rx /usr/local/bin/yt-dlp

# Устанавливаем скрипт для решения YouTube challenge
RUN npm install -g @ybd-project/yt-dlp-ytdlp-ejs 2>/dev/null || true

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cookies.txt /app/cookies.txt
COPY . .

EXPOSE 8080

CMD ["python", "main.py"]
