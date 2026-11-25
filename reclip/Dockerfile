FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg ca-certificates curl unzip && \
    rm -rf /var/lib/apt/lists/* && \
    curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s v2.3.0 && \
    deno --version

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8899
ENV HOST=0.0.0.0
ENV DOWNLOADS_PATH=/downloads
CMD ["python", "app.py"]
