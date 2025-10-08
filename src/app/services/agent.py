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
        # Initialize progress for this turn
        try:
            store.clear_progress(session_id)
        except Exception:
            pass
        # Progress hook invoked from controller (runs in worker thread)
        def _progress(event: str, data: dict | None = None) -> None:
            name = (data or {}).get("tool") if isinstance(data, dict) else None
            note = (data or {}).get("note") if isinstance(data, dict) else None
            try:
                if event == "start" and name:
                    store.begin_step(session_id, str(name), str(note) if note else None)
                elif event == "end" and name:
                    store.end_step(session_id, str(name), ok=True, note=str(note) if note else None)
                elif event == "error" and name:
                    store.end_step(session_id, str(name), ok=False, note=str(note) if note else None)
            except Exception:
                pass
        # Run synchronously in thread to avoid blocking loop
        loop = asyncio.get_running_loop()
        final_text: str = await loop.run_in_executor(
            None,
            lambda: run_hybrid_session(
                state,
                user_text,
                system_instruction=system_instruction,
                progress_cb=_progress,
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
        # Do not modify content to synthesize a title; rely on model instructions.
        # final_text remains as provided by the model.

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
