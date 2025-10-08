from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import asyncio
import json
import logging

from .manager import ws_manager


router = APIRouter()


@router.websocket("/ws/chat/{session_id}")
async def chat_ws(ws: WebSocket, session_id: str):
    logger = logging.getLogger("app.ws")
    await ws_manager.connect(session_id, ws)
    try:
        while True:
            # Receive a text frame; tolerate non-JSON frames and transient errors.
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect as e:
                # Normal disconnect path (client closed or network drop)
                try:
                    logger.info("WebSocket disconnect (session=%s, code=%s)", session_id, getattr(e, "code", None))
                except Exception:
                    pass
                break
            except RuntimeError as e:
                # Starlette raises a RuntimeError with a generic message when the socket
                # is not in CONNECTED state (either not yet accepted or already closed).
                try:
                    logger.info("WebSocket not connected/closed (session=%s): %s", session_id, str(e))
                except Exception:
                    pass
                break
            except Exception:
                # Log and continue rather than tearing down the socket immediately.
                logger.exception("WebSocket receive error (session=%s)", session_id)
                await asyncio.sleep(0.05)
                continue

            try:
                data = json.loads(raw) if raw else {}
            except Exception:
                data = {}

            typ = (data or {}).get("type")
            if typ == "ping":
                try:
                    await ws.send_json({"type": "pong"})
                except Exception:
                    # Best-effort pong; keep the loop alive
                    logger.debug("Failed to send pong (session=%s)", session_id)
            # user_message on WS is optional; REST POST triggers responses
    except Exception:
        # Catch-all to avoid silent teardown on unexpected errors
        logger.exception("WebSocket handler error (session=%s)", session_id)
    finally:
        await ws_manager.disconnect(session_id, ws)
