from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from agent.errors import ToolError


def is_youtube_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    return (
        host == "youtu.be"
        or host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtube-nocookie.com"
        or host.endswith(".youtube-nocookie.com")
    )


def download_youtube_audio(url: str, out_dir: Path) -> Tuple[Path, Dict[str, Any]]:
    """Download best available YouTube video with audio (mp4 preferred) using yt-dlp.
    Returns (downloaded_path, meta). Raises ToolError if yt_dlp missing or download fails.

    Note: Despite the name, this function now downloads video-first to support
    Gemini transcription over video. It prefers MP4 output via yt-dlp merging.
    """
    try:
        import yt_dlp  # type: ignore
    except Exception as e:  # pragma: no cover
        raise ToolError(
            "yt-dlp is required to download YouTube audio. Please install it (pip install yt-dlp).",
            tool_name="extract_audio",
        ) from e

    out_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(out_dir / "%(id)s.%(ext)s")
    ydl_opts = {
        # Prefer mp4 video+audio; fall back gracefully
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 20,
        "overwrites": False,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath: Optional[str] = None
            req = info.get("requested_downloads") if isinstance(info, dict) else None
            if isinstance(req, list) and req:
                filepath = req[0].get("filepath")
            if not filepath:
                filepath = ydl.prepare_filename(info)
    except Exception as e:
        raise ToolError(f"yt-dlp download failed: {e}", tool_name="extract_audio")

    dl_path = Path(filepath).resolve()
    if not dl_path.exists():
        raise ToolError("yt-dlp reported success, but file missing.", tool_name="extract_audio")

    meta = {
        "id": info.get("id"),
        "title": info.get("title"),
        "ext": info.get("ext"),
        "uploader": info.get("uploader"),
        "duration": info.get("duration"),
        "webpage_url": info.get("webpage_url") or url,
        "filepath": str(dl_path),
    }
    return dl_path, meta
