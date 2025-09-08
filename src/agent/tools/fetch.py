from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse, parse_qs

import yt_dlp

from agent.core.state import VideoMeta
from agent.errors import ToolError


# --- Helpers -----------------------------------------------------------------

_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,}$")


def _is_youtube_host(host: str) -> bool:
    """Return True for recognized YouTube hosts (incl. subdomains and nocookie)."""
    if not host:
        return False
    host = host.lower()
    return (
        host == "youtu.be"
        or host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtube-nocookie.com"
        or host.endswith(".youtube-nocookie.com")
    )


def _parse_timestamp_to_seconds(value: Optional[str]) -> Optional[int]:
    """Parse YouTube t/start values into seconds. Supports 123, 90s, 1m30s, 1h2m3s."""
    if not value:
        return None
    s = value.strip().lower()
    # If pure integer
    if s.isdigit():
        try:
            return int(s)
        except ValueError:
            return None
    # Match 1h2m3s, 2m10s, 45s, 1h
    pattern = re.compile(r"^(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+)s)?$")
    m = pattern.match(s)
    if not m:
        return None
    hours = int(m.group("h") or 0)
    minutes = int(m.group("m") or 0)
    seconds = int(m.group("s") or 0)
    total = hours * 3600 + minutes * 60 + seconds
    return total if total > 0 else None


def _extract_and_normalize_youtube_url(text: str) -> Optional[str]:
    """
    Find the first YouTube URL in text and normalize it to:
        https://www.youtube.com/watch?v={VIDEO_ID}[&t=SECONDS]
    Returns None if no valid YouTube URL found.
    """
    url_pattern = r"(https?://[^\s]+|www\.[^\s]+)"
    for raw in re.findall(url_pattern, text):
        url = raw if raw.startswith("http") else f"https://{raw}"
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not _is_youtube_host(host):
            continue

        video_id: Optional[str] = None
        ts_seconds: Optional[int] = None

        # /watch?v=ID
        if host.endswith("youtube.com") and parsed.path == "/watch":
            qs = parse_qs(parsed.query or "")
            v = (qs.get("v", [None])[0])
            if v:
                video_id = v
            ts_seconds = _parse_timestamp_to_seconds(qs.get("t", [None])[0] or qs.get("start", [None])[0])

        # youtu.be/ID
        elif host == "youtu.be":
            video_id = (parsed.path or "/").lstrip("/")
            qs = parse_qs(parsed.query or "")
            ts_seconds = _parse_timestamp_to_seconds(qs.get("t", [None])[0] or qs.get("start", [None])[0])

        # /embed/ID or /v/ID
        elif host.endswith("youtube.com") and (parsed.path.startswith("/embed/") or parsed.path.startswith("/v/")):
            video_id = (parsed.path.rsplit("/", 1)[-1] or "").strip()
            qs = parse_qs(parsed.query or "")
            ts_seconds = _parse_timestamp_to_seconds(qs.get("t", [None])[0] or qs.get("start", [None])[0])

        # /shorts/ID
        elif host.endswith("youtube.com") and parsed.path.startswith("/shorts/"):
            video_id = (parsed.path.rsplit("/", 1)[-1] or "").strip()
            qs = parse_qs(parsed.query or "")
            ts_seconds = _parse_timestamp_to_seconds(qs.get("t", [None])[0] or qs.get("start", [None])[0])

        # /live/ID (rare but exists)
        elif host.endswith("youtube.com") and parsed.path.startswith("/live/"):
            video_id = (parsed.path.rsplit("/", 1)[-1] or "").strip()
            qs = parse_qs(parsed.query or "")
            ts_seconds = _parse_timestamp_to_seconds(qs.get("t", [None])[0] or qs.get("start", [None])[0])

        if video_id and _YT_ID_RE.match(video_id):
            base = f"https://www.youtube.com/watch?v={video_id}"
            return f"{base}&t={ts_seconds}" if ts_seconds else base

    return None


# --- Tool --------------------------------------------------------------------

def fetch_task(state, fetch_name: str, user_text: str):
    """
    Fetch video metadata for the first valid YouTube URL found in user_text.

    Example call:

        fetch_task(state, "fetch_video", "Check https://youtu.be/abc123?t=90")

    Args:
        state (AgentState): Mutable agent state; will populate `state.video` and artifacts.
        fetch_name (str): Tool name label; typically "fetch_video".
        user_text (str): Text possibly containing a YouTube URL in any common format.

    Returns:
        VideoMeta: Populated metadata (video_id, title, duration_s, source_url).

    Raises:
        ToolError: If no valid YouTube URL is found, a playlist is detected, or yt-dlp fails.
    """
    tool_name = "fetch_video"

    # 1) Extract + normalize URL
    normalized_url = _extract_and_normalize_youtube_url(user_text)
    if not normalized_url:
        raise ToolError(
            "No valid YouTube URL found (expected youtube.com or youtu.be).",
            tool_name=tool_name,
        )

    # 2) Single yt-dlp metadata fetch (no download)
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 10,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(normalized_url, download=False)
    except Exception as e:
        raise ToolError(f"Unable to retrieve video metadata: {e}", tool_name=tool_name)

    # 3) Validate basics and build VideoMeta
    if info.get("entries"):
        raise ToolError("Playlist URLs are not supported. Please provide a single video URL.", tool_name=tool_name)

    video_id = info.get("id") or ""
    title = info.get("title") or "Untitled"
    duration = info.get("duration")  # None for live/premiere
    live_status = info.get("live_status")
    source_url = info.get("webpage_url") or normalized_url

    if not _YT_ID_RE.match(video_id):
        raise ToolError("Could not resolve a valid YouTube video ID.", tool_name=tool_name)

    duration_s = int(duration) if isinstance(duration, (int, float)) else 0

    state.video = VideoMeta(
        video_id=video_id,
        title=title,
        duration_s=duration_s,
        source_url=source_url,
    )

    # 4) Record helpful notes for observability (store under artifacts)
    fetch_notes = {
        "normalized_url": normalized_url,
        "uploader": info.get("uploader"),
        "uploader_id": info.get("uploader_id"),
        "channel_id": info.get("channel_id"),
        "channel": info.get("channel"),
        "upload_date": info.get("upload_date"),
        "live_status": live_status,
        "duration_reported": duration,
    }
    state.artifacts["fetch"] = fetch_notes

    return state.video
