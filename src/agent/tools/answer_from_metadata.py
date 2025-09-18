from __future__ import annotations

from typing import Optional

from agent.core.state import AgentState


def _pick(values: list[Optional[str]]) -> Optional[str]:
    for v in values:
        if v and str(v).strip():
            return str(v).strip()
    return None


def answer_from_metadata(state: AgentState, question: Optional[str] = None, *, tool_name: str = "answer_from_metadata") -> str:
    """Return a small human-friendly answer using fetched video metadata.

    Prefers channel/uploader when available from fetch_task artifacts; falls back to title/source_url.
    Safe, deterministic, no LLM usage.
    """
    art = getattr(state, "artifacts", {}) or {}
    fetch_art = art.get("fetch_task") or art.get("fetch") or {}
    vid = getattr(state, "video", None)

    channel = _pick([fetch_art.get("channel"), fetch_art.get("uploader"), fetch_art.get("uploader_id"), fetch_art.get("channel_id")])
    title = _pick([getattr(vid, "title", None)])
    url = _pick([getattr(vid, "source_url", None), fetch_art.get("normalized_url")])

    # If the question explicitly asks who the youtuber/channel is, return identity directly
    q = (question or "").lower()
    identity_like = any(k in q for k in ["who is the youtuber", "who is the channel", "who is the creator", "channel name", "youtuber name", "who uploaded"]) if q else False

    if channel:
        if identity_like:
            return channel
        # Provide concise metadata line otherwise
        if title and url:
            return f"Channel: {channel}\nTitle: {title}\nURL: {url}"
        if title:
            return f"Channel: {channel}\nTitle: {title}"
        if url:
            return f"Channel: {channel}\nURL: {url}"
        return f"Channel: {channel}"

    # Fallbacks if channel is missing
    if title and url:
        return f"Title: {title}\nURL: {url}"
    if title:
        return f"Title: {title}"
    if url:
        return f"URL: {url}"

    return "No video metadata is available yet. Please provide a single YouTube video URL."

