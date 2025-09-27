from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .manager import ws_manager


router = APIRouter()


@router.websocket("/ws/chat/{session_id}")
async def chat_ws(ws: WebSocket, session_id: str):
    await ws_manager.connect(session_id, ws)
    try:
        while True:
            # Client may send ping/typing or user messages; we mainly echo pings
            data = await ws.receive_json()
            typ = data.get("type")
            if typ == "ping":
                await ws.send_json({"type": "pong"})
            # user_message on WS is optional; REST POST triggers responses
    except WebSocketDisconnect:
        await ws_manager.disconnect(session_id, ws)

