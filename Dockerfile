# syntax=docker/dockerfile:1

# --- Base image --------------------------------------------------------------
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System deps: ffmpeg/ffprobe for media processing
RUN apt-get update \ 
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \ 
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 10001 appuser

WORKDIR /app

# --- Python deps -------------------------------------------------------------
# Install only what the backend needs. Do NOT bake secrets into the image.
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn[standard] \
    pydantic \
    PyYAML \
    yt-dlp \
    google-genai \
    google-generativeai \
    audioop-lts

# --- App code ----------------------------------------------------------------
# Copy only the backend source; exclude tests/web/etc via .dockerignore
COPY src ./src

# Runtime directory (optional persistent mount)
RUN mkdir -p /data/runtime && chown -R appuser:appuser /data

ENV PYTHONPATH=/app/src \
    PORT=8000 \
    RUNTIME_DIR=/data/runtime

EXPOSE 8000
USER appuser

# NOTE: Secrets like DEEPSEEK_API_KEY, GOOGLE_API_KEY should be provided at runtime.
# If YT_COOKIES_B64 is provided, the entrypoint will write it to /run/secrets/yt_cookies.txt
# and export YT_COOKIES_FILE to that path automatically.

COPY bin/start.sh /app/bin/start.sh
RUN chmod +x /app/bin/start.sh

ENTRYPOINT ["/app/bin/start.sh"]
