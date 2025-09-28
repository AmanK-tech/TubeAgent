from __future__ import annotations

from typing import AsyncGenerator, Optional
import asyncio

from agent.core.config import load_config
from agent.core.state import AgentState, VideoMeta
from agent.core.controller import run_hybrid_session
from ..state import store
from pathlib import Path


class AgentService:
    """Thin adapter over src/agent to produce responses.

    Since the underlying LLM client does not expose streaming, we
    pseudo-stream by chunking the final text into parts for WS delivery.
    """

    def __init__(self, profile: str = "default") -> None:
        self.profile = profile

    def _new_state(self, session_id: str | None = None) -> AgentState:
        cfg = load_config(self.profile)
        state = AgentState(config=cfg)
        # Hydrate from prior session context so follow-ups work without pasting URL again
        if session_id:
            ctx = store.get_agent_context(session_id)
            try:
                vid = ctx.get("video") if isinstance(ctx, dict) else None
                if isinstance(vid, dict) and vid.get("video_id"):
                    state.video = VideoMeta(
                        video_id=str(vid.get("video_id")),
                        title=str(vid.get("title", "")),
                        duration_s=int(vid.get("duration_s") or 0),
                        source_url=str(vid.get("source_url", "")),
                    )
            except Exception:
                pass
            try:
                arts = ctx.get("artifacts") if isinstance(ctx, dict) else None
                if isinstance(arts, dict):
                    state.artifacts = arts
                # Load combined transcript if present for planner fast-paths
                ta = (arts or {}).get("transcribe_asr", {}) if isinstance(arts, dict) else {}
                ctp = ta.get("combined_transcript_path") if isinstance(ta, dict) else None
                if ctp and Path(ctp).exists():
                    try:
                        state.transcript = Path(ctp).read_text(encoding="utf-8").strip()
                    except Exception:
                        state.transcript = None
            except Exception:
                pass
        return state

    async def respond_stream(
        self,
        session_id: str,
        user_text: str,
        *,
        system_instruction: Optional[str] = None,
        chunk_size: int = 20,
        delay_s: float = 0.0,
    ) -> AsyncGenerator[str, None]:
        state = self._new_state(session_id)
        # Run synchronously in thread to avoid blocking loop
        loop = asyncio.get_running_loop()
        final_text: str = await loop.run_in_executor(
            None,
            lambda: run_hybrid_session(
                state,
                user_text,
                system_instruction=system_instruction,
            ),
        )
        # Persist minimal context for follow-ups
        try:
            ctx_out = {}
            if getattr(state, "video", None):
                v = state.video
                ctx_out["video"] = {
                    "video_id": v.video_id,
                    "title": v.title,
                    "duration_s": v.duration_s,
                    "source_url": v.source_url,
                }
            if getattr(state, "artifacts", None):
                ctx_out["artifacts"] = state.artifacts
            store.set_agent_context(session_id, ctx_out)
        except Exception:
            pass
        # Ensure there is a prominent heading at the top of the output so the UI
        # can render it in bold/highlighted style. If a heading is already present
        # (Markdown #/## or **bold** on the first line), leave as-is.
        def _ensure_heading(text: str) -> str:
            t = (text or "").lstrip()
            if not t:
                return t
            import re
            first_line = t.splitlines()[0].strip()
            has_md_header = bool(re.match(r"^#{1,6}\s+\S", first_line))
            has_bold_header = bool(re.match(r"^\*\*.+\*\*$", first_line))
            if has_md_header or has_bold_header:
                return t

            # Prefer video title when available; otherwise derive from user/request or content.
            candidate = None
            try:
                vid = getattr(state, "video", None)
                if vid and getattr(vid, "title", None):
                    candidate = str(getattr(vid, "title"))
            except Exception:
                candidate = None

            if not candidate:
                # Take first sentence or up to 80 chars from content as a title-ish line
                parts = re.split(r"(?<=[.!?])\s+|\n", t)
                if parts and parts[0]:
                    candidate = parts[0].strip().rstrip(".:;—-")
            if not candidate and (user_text or "").strip():
                candidate = f"Answer: {user_text.strip()}"
            if not candidate:
                candidate = "Response"

            # Trim overly long headings
            candidate = candidate.strip()
            if len(candidate) > 100:
                candidate = candidate[:97].rstrip() + "…"

            return f"**{candidate}**\n\n" + t

        final_text = _ensure_heading(final_text)

        # Pseudo-stream by chunks
        if not final_text:
            return
        # Send the heading as a single unit so the frontend can style it immediately
        i = 0
        try:
            nl2 = final_text.find("\n\n")
            nl1 = final_text.find("\n")
            hdr_end = nl2 if nl2 != -1 else nl1
            if hdr_end != -1:
                first_block = final_text[: hdr_end + (2 if nl2 != -1 else 1)]
                if first_block:
                    yield first_block
                    i = len(first_block)
        except Exception:
            i = 0
        while i < len(final_text):
            yield final_text[i : i + chunk_size]
            i += chunk_size
            if delay_s > 0:
                await asyncio.sleep(delay_s)
