from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["meta"]) 


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/meta")
async def meta() -> dict:
    # Keep this minimal; expand with build info if needed
    return {
        "app": "TubeAgent API",
        "version": "0.1.0",
        "models": ["deepseek-chat"],
    }

