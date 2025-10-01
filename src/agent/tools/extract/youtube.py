from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse
from pathlib import Path
import os

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
    """Download best available YouTube video with audio (H.264/AAC in MP4) using yt-dlp.
    Returns (downloaded_path, meta). Raises ToolError if yt_dlp missing or download fails.

    Note: Despite the name, this function now downloads video-first to support
    Gemini transcription over video. It prefers H.264 video and AAC audio.
    """
    try:
        import yt_dlp  # type: ignore
    except Exception as e:  # pragma: no cover
        raise ToolError(
            "yt-dlp is required to download YouTube audio. Please install it (pip install yt-dlp).",
            tool_name="extract_audio",
        ) from e

    out_dir.mkdir(parents=True, exist_ok=True)
    # Write directly under out_dir, e.g., <downloads>/<video_id>.mp4
    outtmpl = str(out_dir / "%(id)s.%(ext)s")
    def _cookies_opts_from_env() -> dict:
        """Return yt-dlp cookie options from env without exposing secrets."""
        try:
            cookiefile = os.getenv("YT_COOKIES_FILE")
            if cookiefile:
                p = Path(cookiefile).expanduser()
                if p.exists():
                    return {"cookiefile": str(p)}
        except Exception:
            pass
        try:
            browser = (os.getenv("YT_COOKIES_FROM_BROWSER") or "").strip()
            profile = (os.getenv("YT_COOKIES_BROWSER_PROFILE") or "").strip()
            if browser:
                return {"cookiesfrombrowser": (browser,) if not profile else (browser, profile)}
        except Exception:
            pass
        return {}

    ydl_opts = {
        # Prefer H.264 (AVC) video in MP4 + AAC (mp4a) audio; fall back sensibly
        # avc1.* for H.264, mp4a.* for AAC in MP4 containers
        "format": (
            "bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a][acodec^=mp4a]/"
            "best[ext=mp4][vcodec^=avc1][acodec^=mp4a]/"
            "best[ext=mp4]/"
            "best"
        ),
        "merge_output_format": "mp4",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 20,
        "overwrites": False,
    }

    try:
        opts = {**ydl_opts, **_cookies_opts_from_env()}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath: Optional[str] = None
            req = info.get("requested_downloads") if isinstance(info, dict) else None
            if isinstance(req, list) and req:
                filepath = req[0].get("filepath")
            if not filepath:
                filepath = ydl.prepare_filename(info)
    except Exception as e:
        msg = str(e)
        if any(k in msg for k in ["Sign in to confirm", "cookies", "consent", "private", "account"]):
            raise ToolError(
                (
                    "YouTube blocked anonymous download (authentication required). "
                    "Provide cookies via YT_COOKIES_FILE or YT_COOKIES_FROM_BROWSER to proceed."
                ),
                tool_name="extract_audio",
            )
        raise ToolError(f"yt-dlp download failed: {msg}", tool_name="extract_audio")

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
