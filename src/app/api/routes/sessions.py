from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException

from ...state import store
from ...schemas.session import (
    CreateSessionRequest,
    CreateSessionResponse,
    ListSessionsResponse,
    Session as SessionSchema,
)
from ...services.cleanup import safe_purge_runtime, delete_gemini_uploads, cleanup_session_artifacts, clean_extract_jobs_and_downloads
from ...sockets.manager import ws_manager


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
    # 1) Per-session artifacts: extract folder, summaries/<job-id>, Gemini uploads
    try:
        bg.add_task(cleanup_session_artifacts, dict(getattr(s, "agent_ctx", {}) or {}))
    except Exception:
        pass
    # 2) Optional full runtime purge (mirrors cleanup_runtime.py -y behavior)
    bg.add_task(safe_purge_runtime)
    # 3) Legacy hook (no-op here but kept for compatibility)
    bg.add_task(delete_gemini_uploads, [], None)
    # 4) Always clear extract job folders and webm downloads (keep roots)
    bg.add_task(clean_extract_jobs_and_downloads)
    return {"ok": True}


@router.post("/{session_id}/close")
async def close_session(session_id: str, bg: BackgroundTasks) -> dict:
    """Best-effort cleanup when a client tab/app is closed.

    - If there are still active WS connections for this session, skip cleanup.
    - Otherwise, perform per-session cleanup and delete it from memory.
    - Does NOT run full runtime purge by default (to avoid affecting other sessions).
    """
    s = store.get_session(session_id)
    if not s:
        return {"ok": True, "skipped": "not_found"}

    try:
        if await ws_manager.has_connections(session_id):
            return {"ok": False, "skipped": "active_connections"}
    except Exception:
        pass

    # Remove session + artifacts
    store.delete_session(session_id)
    try:
        bg.add_task(cleanup_session_artifacts, dict(getattr(s, "agent_ctx", {}) or {}))
    except Exception:
        pass
    try:
        bg.add_task(clean_extract_jobs_and_downloads)
    except Exception:
        pass
    return {"ok": True}
