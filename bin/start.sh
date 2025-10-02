#!/usr/bin/env sh
set -eu

mkdir -p /run/secrets

# If a base64-encoded cookies.txt is provided, materialize it to a readable file
if [ "${YT_COOKIES_B64:-}" != "" ]; then
  # Decode into secrets file (ignore any trailing newline)
  echo "$YT_COOKIES_B64" | base64 -d > /run/secrets/yt_cookies.txt 2>/dev/null || true
  chmod 0400 /run/secrets/yt_cookies.txt 2>/dev/null || true
  export YT_COOKIES_FILE=/run/secrets/yt_cookies.txt
fi

# If a secret file exists but is mounted unreadable (e.g., /etc/secrets root-owned),
# copy it into /run/secrets so the non-root app user can read it.
if [ -n "${YT_COOKIES_FILE:-}" ]; then
  if [ ! -r "$YT_COOKIES_FILE" ] && [ -f "$YT_COOKIES_FILE" ]; then
    cp "$YT_COOKIES_FILE" /run/secrets/yt_cookies.txt 2>/dev/null || true
    if [ -r /run/secrets/yt_cookies.txt ]; then
      chmod 0400 /run/secrets/yt_cookies.txt 2>/dev/null || true
      export YT_COOKIES_FILE=/run/secrets/yt_cookies.txt
    fi
  fi
elif [ -r /etc/secrets/yt_cookies.txt ]; then
  cp /etc/secrets/yt_cookies.txt /run/secrets/yt_cookies.txt 2>/dev/null || true
  if [ -r /run/secrets/yt_cookies.txt ]; then
    chmod 0400 /run/secrets/yt_cookies.txt 2>/dev/null || true
    export YT_COOKIES_FILE=/run/secrets/yt_cookies.txt
  fi
fi

# Print a concise note (path only) for troubleshooting if cookies are present
if [ -n "${YT_COOKIES_FILE:-}" ]; then
  echo "Cookies file configured at: ${YT_COOKIES_FILE}"
fi

HOST="0.0.0.0"
PORT="${PORT:-8000}"

exec uvicorn app.main:app --host "$HOST" --port "$PORT"
