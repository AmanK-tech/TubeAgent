#!/usr/bin/env sh
set -eu

mkdir -p /run/secrets

# Pick a writable secrets directory (avoid /run on locked-down hosts)
pick_dir() {
  for d in \
    "/run/secrets" \
    "/tmp/secrets" \
    "/var/tmp/secrets" \
    "/app/.secrets" \
    "$HOME/.secrets"; do
    mkdir -p "$d" 2>/dev/null && [ -w "$d" ] && echo "$d" && return 0
  done
  return 1
}

SECRETS_DIR="$(pick_dir || true)"

# If base64 cookies are provided, materialize them to a readable file under SECRETS_DIR
if [ -n "${YT_COOKIES_B64:-}" ] && [ -n "$SECRETS_DIR" ]; then
  echo "$YT_COOKIES_B64" | base64 -d > "$SECRETS_DIR/yt_cookies.txt" 2>/dev/null || true
  chmod 0400 "$SECRETS_DIR/yt_cookies.txt" 2>/dev/null || true
  if [ -r "$SECRETS_DIR/yt_cookies.txt" ]; then
    export YT_COOKIES_FILE="$SECRETS_DIR/yt_cookies.txt"
  fi
fi

# If a secret file path is set and readable, keep it. If unreadable but we have a writable
# directory and the file is world-readable, copy it into our space.
if [ -n "${YT_COOKIES_FILE:-}" ]; then
  if [ ! -r "$YT_COOKIES_FILE" ] && [ -f "$YT_COOKIES_FILE" ] && [ -n "$SECRETS_DIR" ]; then
    cp "$YT_COOKIES_FILE" "$SECRETS_DIR/yt_cookies.txt" 2>/dev/null || true
    if [ -r "$SECRETS_DIR/yt_cookies.txt" ]; then
      chmod 0400 "$SECRETS_DIR/yt_cookies.txt" 2>/dev/null || true
      export YT_COOKIES_FILE="$SECRETS_DIR/yt_cookies.txt"
    fi
  fi
elif [ -r /etc/secrets/yt_cookies.txt ]; then
  # If env var not set but a default secret file exists and is readable, adopt it
  export YT_COOKIES_FILE=/etc/secrets/yt_cookies.txt
fi

# Log the path (not contents) if a readable cookies file is configured
if [ -n "${YT_COOKIES_FILE:-}" ] && [ -r "${YT_COOKIES_FILE}" ]; then
  echo "Cookies file configured at: ${YT_COOKIES_FILE}"
fi

HOST="0.0.0.0"
PORT="${PORT:-8000}"

exec uvicorn app.main:app --host "$HOST" --port "$PORT"
