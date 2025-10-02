from __future__ import annotations

import os
from fastapi import FastAPI
import asyncio
import time
from pathlib import Path
import sys as _sys

# Ensure local src/ is importable so `agent.*` can be resolved when running from repo
_src = Path(__file__).resolve().parents[1] / "src"
if str(_src) not in _sys.path:
    _sys.path.insert(0, str(_src))
from fastapi.middleware.cors import CORSMiddleware

from .api.routes.health import router as health_router
from .api.routes.sessions import router as sessions_router
from .api.routes.messages import router as messages_router
from .sockets.ws import router as ws_router
from .sockets.manager import ws_manager
from .state import store
from .services.cleanup import cleanup_session_artifacts, safe_purge_runtime


def create_app() -> FastAPI:
    app = FastAPI(title="TubeAgent API", version=os.getenv("APP_VERSION", "0.1.0"))

    # CORS for local dev and typical frontend origins
    web_origin = os.getenv("WEB_ORIGIN", "http://localhost:5173")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[web_origin, "http://localhost:3000", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(sessions_router, prefix="/sessions", tags=["sessions"])
    app.include_router(messages_router, prefix="/sessions/{session_id}", tags=["messages"])
    app.include_router(ws_router)

    # --- Background janitor: auto-clean idle sessions ---
    def _env_bool(name: str, default: bool = False) -> bool:
        v = os.getenv(name)
        if v is None:
            return default
        return (v or "").strip().lower() in {"1", "true", "yes", "on"}

    async def _janitor_loop() -> None:
        # Idle TTL and sweep interval
        try:
            ttl_min = float(os.getenv("SESSION_IDLE_TTL_MINUTES", "60") or 60.0)
        except Exception:
            ttl_min = 60.0
        try:
            sweep_s = float(os.getenv("CLEANUP_SWEEP_INTERVAL_SECONDS", "300") or 300.0)
        except Exception:
            sweep_s = 300.0
        purge_runtime = _env_bool("PURGE_RUNTIME_ON_SESSION_DELETE", False)

        while True:
            now = time.time()
            try:
                sessions = list(store.list_sessions())
                for s in sessions:
                    idle_s = now - float(getattr(s, "updated_at", now))
                    if idle_s < (ttl_min * 60.0):
                        continue
                    try:
                        if await ws_manager.has_connections(s.id):
                            continue
                    except Exception:
                        pass
                    # Delete session and cleanup artifacts (best-effort)
                    try:
                        ctx = dict(getattr(s, "agent_ctx", {}) or {})
                        store.delete_session(s.id)
                        cleanup_session_artifacts(ctx)
                        if purge_runtime:
                            safe_purge_runtime()
                    except Exception:
                        # keep going
                        pass
            except Exception:
                pass
            await asyncio.sleep(max(5.0, sweep_s))

    @app.on_event("startup")
    async def _start_background() -> None:
        app.state._janitor_task = asyncio.create_task(_janitor_loop())

    @app.on_event("shutdown")
    async def _stop_background() -> None:
        # Stop janitor loop
        t = getattr(app.state, "_janitor_task", None)
        if t:
            t.cancel()

        # On server shutdown (e.g., Ctrl+C), optionally clean all sessions
        def _env_bool(name: str, default: bool = True) -> bool:
            v = os.getenv(name)
            if v is None:
                return default
            return (v or "").strip().lower() in {"1", "true", "yes", "on"}

        cleanup_all = _env_bool("CLEANUP_ON_SHUTDOWN", True)
        purge_runtime = _env_bool("PURGE_RUNTIME_ON_SHUTDOWN", True)

        if cleanup_all:
            try:
                sessions = list(store.list_sessions())
            except Exception:
                sessions = []
            # First delete Gemini uploads and per-session artifacts
            for s in sessions:
                try:
                    ctx = dict(getattr(s, "agent_ctx", {}) or {})
                    cleanup_session_artifacts(ctx)
                except Exception:
                    pass
            # Clear in-memory sessions
            try:
                for s in sessions:
                    try:
                        store.delete_session(s.id)
                    except Exception:
                        continue
            except Exception:
                pass

        # Optionally purge entire runtime directory at shutdown
        if purge_runtime:
            try:
                safe_purge_runtime()
            except Exception:
                pass
        # Per-session cleanup already ran; optional full purge may run above.

    return app


app = create_app()
