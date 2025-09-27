from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException

from ...state import store
from ...schemas.session import (
    CreateSessionRequest,
    CreateSessionResponse,
    ListSessionsResponse,
    Session as SessionSchema,
)
from ...services.cleanup import safe_purge_runtime, delete_gemini_uploads


router = APIRouter()


@router.post("/", response_model=CreateSessionResponse)
async def create_session(payload: CreateSessionRequest | None = None) -> CreateSessionResponse:
    s = store.create_session(title=(payload.title if payload else None))
    return CreateSessionResponse(id=s.id, title=s.title, created_at=s.created_at)


@router.get("/", response_model=ListSessionsResponse)
async def list_sessions() -> ListSessionsResponse:
    items = [SessionSchema(**s.__dict__) for s in store.list_sessions()]
    return ListSessionsResponse(items=items)


@router.get("/{session_id}", response_model=SessionSchema)
async def get_session(session_id: str) -> SessionSchema:
    s = store.get_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionSchema(**s.__dict__)


@router.delete("/{session_id}")
async def delete_session(session_id: str, bg: BackgroundTasks) -> dict:
    s = store.delete_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    # Launch cleanup tasks (best-effort)
    bg.add_task(safe_purge_runtime)
    # If you track Gemini files per session, pass them here; we don't in memory.
    bg.add_task(delete_gemini_uploads, [], None)
    return {"ok": True}

