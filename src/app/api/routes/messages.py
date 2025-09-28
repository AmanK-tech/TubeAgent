from __future__ import annotations

import asyncio
from fastapi import APIRouter, HTTPException

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

    # Add user message
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

    # Fire and forget
    asyncio.create_task(_run())

    return PostMessageResponse(message_id=user_msg.id)
