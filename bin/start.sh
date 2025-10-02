#!/usr/bin/env sh
set -eu

# If a base64-encoded cookies.txt is provided, materialize it to a file
if [ "${YT_COOKIES_B64:-}" != "" ]; then
  mkdir -p /run/secrets
  # Decode into secrets file (ignore any trailing newline)
  echo "$YT_COOKIES_B64" | base64 -d > /run/secrets/yt_cookies.txt || true
  chmod 0400 /run/secrets/yt_cookies.txt || true
  export YT_COOKIES_FILE=/run/secrets/yt_cookies.txt
fi

HOST="0.0.0.0"
PORT="${PORT:-8000}"

exec uvicorn app.main:app --host "$HOST" --port "$PORT"

