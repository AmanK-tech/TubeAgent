from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException
import asyncio

from ...state import store
from ...schemas.session import (
    CreateSessionRequest,
    CreateSessionResponse,
    ListSessionsResponse,
    Session as SessionSchema,
)
import os
from ...services.cleanup import safe_purge_runtime, delete_gemini_uploads, cleanup_session_artifacts
from ...sockets.manager import ws_manager


router = APIRouter()


@router.post("/", response_model=CreateSessionResponse)
@router.post("", response_model=CreateSessionResponse)
async def create_session(payload: CreateSessionRequest | None = None) -> CreateSessionResponse:
    s = store.create_session(title=(payload.title if payload else None))
    return CreateSessionResponse(id=s.id, title=s.title, created_at=s.created_at)


@router.get("/", response_model=ListSessionsResponse)
@router.get("", response_model=ListSessionsResponse)
async def list_sessions() -> ListSessionsResponse:
    items = []
    for s in store.list_sessions():
        session_data = {
            "id": s.id,
            "title": s.title,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
            "messages": [
                {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "created_at": m.created_at
                }
                for m in (s.messages or [])
            ]
        }
        items.append(SessionSchema(**session_data))
    return ListSessionsResponse(items=items)


@router.get("/{session_id}", response_model=SessionSchema)
async def get_session(session_id: str) -> SessionSchema:
    s = store.get_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    session_data = {
        "id": s.id,
        "title": s.title,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at
            }
            for m in (s.messages or [])
        ]
    }
    return SessionSchema(**session_data)


@router.get("/{session_id}/progress")
async def get_session_progress(session_id: str) -> dict:
    s = store.get_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    return store.get_progress(session_id)


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
    try:
        if (os.getenv("PURGE_RUNTIME_ON_SESSION_DELETE") or "").strip().lower() in {"1", "true", "yes", "on"}:
            bg.add_task(safe_purge_runtime)
    except Exception:
        pass
    # 3) Legacy hook (no-op here but kept for compatibility)
    bg.add_task(delete_gemini_uploads, [], None)
    return {"ok": True}


@router.post("/{session_id}/close")
async def close_session(session_id: str, bg: BackgroundTasks) -> dict:
    """Schedule a delayed best-effort cleanup when a client tab/app is closed.

    Rationale: During a full page refresh the WebSocket briefly disconnects
    and reconnects. Immediate deletion would kill the session mid-refresh.
    We therefore apply a small grace period, then check for active WS
    connections before deleting the session and cleaning artifacts.
    """
    s = store.get_session(session_id)
    if not s:
        return {"ok": True, "skipped": "not_found"}

    # Copy minimal context needed for cleanup later
    ctx = dict(getattr(s, "agent_ctx", {}) or {})

    try:
        grace = float(os.getenv("SESSION_CLOSE_GRACE_SECONDS", "8") or 8.0)
    except Exception:
        grace = 8.0

    async def _delayed_close(sid: str, ctx_copy: dict, wait_s: float) -> None:
        try:
            await asyncio.sleep(max(0.0, float(wait_s)))
            # If a client reconnected during the grace window, keep the session
            try:
                if await ws_manager.has_connections(sid):
                    return
            except Exception:
                pass
            # Delete session + artifacts
            sess = store.delete_session(sid)
            try:
                cleanup_session_artifacts(ctx_copy)
            except Exception:
                pass
        except Exception:
            # Best-effort; swallow any background errors
            pass

    # Schedule delayed close; response returns immediately
    try:
        bg.add_task(_delayed_close, session_id, ctx, grace)
    except Exception:
        # If scheduling fails, fall back to immediate best-effort close
        try:
            sess = store.delete_session(session_id)
        except Exception:
            sess = None
        try:
            cleanup_session_artifacts(ctx)
        except Exception:
            pass
        return {"ok": True, "grace_s": 0, "scheduled": False}

    return {"ok": True, "scheduled": True, "grace_s": grace}
