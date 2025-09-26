from __future__ import annotations

import os
from fastapi import FastAPI
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

    return app


app = create_app()
