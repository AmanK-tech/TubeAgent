from __future__ import annotations

import asyncio
from fastapi import APIRouter, HTTPException
import time

from ...state import store
from ...schemas.message import PostMessageRequest, PostMessageResponse, MessagesPage
from ...schemas.session import Message as MessageSchema
from ...services.agent import AgentService
from ...sockets.manager import ws_manager


router = APIRouter()
agent = AgentService()


@router.get("/messages", response_model=MessagesPage)
async def list_messages(session_id: str, cursor: int | None = None, limit: int = 50) -> MessagesPage:
    s = store.get_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    start = int(cursor or 0)
    end = min(start + max(1, limit), len(s.messages))
    items = [MessageSchema(**m.__dict__).model_dump() for m in s.messages[start:end]]
    next_cursor = end if end < len(s.messages) else None
    return MessagesPage(items=items, next_cursor=next_cursor)


@router.post("/messages", response_model=PostMessageResponse)
async def post_message(session_id: str, payload: PostMessageRequest) -> PostMessageResponse:
    s = store.get_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    # Add user message (replace-last ONLY after an error)
    user_msg = None
    try:
        # If the last message is a recent user message and the session recorded
        # a recent error that happened after that message, replace instead of appending.
        s_obj = store.get_session(session_id)
        if s_obj and s_obj.messages:
            last = s_obj.messages[-1]
            now = time.time()
            window_s = 120.0
            # Pull last error timestamp recorded in agent_ctx
            ctx = store.get_agent_context(session_id) or {}
            try:
                err_ts = float(ctx.get("last_error_ts", 0.0) or 0.0)
            except Exception:
                err_ts = 0.0
            last_created = float(getattr(last, "created_at", 0.0) or 0.0)
            is_recent_user = last.role == "user" and (now - last_created) <= window_s
            error_recent = err_ts and (now - err_ts) <= window_s and err_ts >= last_created
            if is_recent_user and error_recent:
                user_msg = store.replace_last_user_message(session_id, payload.content)
                # Clear the error flag so subsequent sends don't keep replacing
                try:
                    ctx2 = dict(ctx)
                    ctx2.pop("last_error_ts", None)
                    store.set_agent_context(session_id, ctx2)
                except Exception:
                    pass
        if user_msg is None:
            user_msg = store.add_message(session_id, role=payload.role, content=payload.content)
    except Exception:
        # Fallback to normal append if anything goes wrong during heuristic
        user_msg = store.add_message(session_id, role=payload.role, content=payload.content)

    # Produce assistant response and stream via WS if connected
    async def _run():
        try:
            # If user_req provided, use it to drive the agent (planner will map to transcribe_asr with user_req)
            drive_text = payload.user_req or payload.content
            async for chunk in agent.respond_stream(session_id, drive_text):
                await ws_manager.emit_token(session_id, chunk)
            # Once complete, aggregate and store final assistant message from chunks
            # The manager buffers per session to assemble final text
            final = await ws_manager.flush_buffer(session_id)
            if final:
                store.add_message(session_id, role="assistant", content=final)
                await ws_manager.emit_complete(session_id, final)
        except Exception as e:  # pragma: no cover - best effort
            await ws_manager.emit_error(session_id, str(e))
            # Record the error timestamp in agent context for replace-on-retry logic
            try:
                ctx = store.get_agent_context(session_id) or {}
                ctx2 = dict(ctx)
                ctx2["last_error_ts"] = time.time()
                # Preserve other ctx keys
                store.set_agent_context(session_id, ctx2)
            except Exception:
                pass

    # Fire and forget
    try:
        store.clear_progress(session_id)
    except Exception:
        pass
    asyncio.create_task(_run())

    return PostMessageResponse(message_id=user_msg.id)
