# Heroku Container Registry build for Afrodit
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# yt-dlp needs FFmpeg for merging/transcoding downloaded media.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the dependencies for all three services.
COPY bot/requirements.txt /tmp/bot-requirements.txt
COPY dashboard/requirements.txt /tmp/dashboard-requirements.txt
COPY reclip/requirements.txt /tmp/reclip-requirements.txt
RUN python -m pip install --upgrade pip \
    && pip install -r /tmp/bot-requirements.txt \
    && pip install -r /tmp/dashboard-requirements.txt \
    && pip install -r /tmp/reclip-requirements.txt

# Keep the original project layout; start.py launches each service.
COPY . .

RUN mkdir -p /tmp/afrodit-downloads

EXPOSE 8000

CMD ["python", "-u", "start.py"]
