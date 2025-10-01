from __future__ import annotations

import asyncio
from typing import Dict, Set
from fastapi import WebSocket


class WSManager:
    def __init__(self) -> None:
        self._conns: Dict[str, Set[WebSocket]] = {}
        self._buffers: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def connect(self, session_id: str, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._conns.setdefault(session_id, set()).add(ws)
            await ws.send_json({"type": "connected", "session": session_id})

    async def disconnect(self, session_id: str, ws: WebSocket) -> None:
        async with self._lock:
            if session_id in self._conns:
                self._conns[session_id].discard(ws)
                if not self._conns[session_id]:
                    self._conns.pop(session_id, None)

    async def _broadcast(self, session_id: str, payload: dict) -> None:
        async with self._lock:
            conns = list(self._conns.get(session_id, set()))
        for c in conns:
            try:
                await c.send_json(payload)
            except Exception:
                # Drop broken connections lazily
                await self.disconnect(session_id, c)

    async def emit_token(self, session_id: str, token: str) -> None:
        self._buffers[session_id] = self._buffers.get(session_id, "") + token
        await self._broadcast(session_id, {"type": "token", "text": token})

    async def flush_buffer(self, session_id: str) -> str:
        text = self._buffers.get(session_id, "")
        self._buffers[session_id] = ""
        return text

    async def emit_complete(self, session_id: str, text: str) -> None:
        await self._broadcast(session_id, {"type": "message_complete", "text": text})

    async def emit_error(self, session_id: str, message: str) -> None:
        await self._broadcast(session_id, {"type": "error", "message": message})

    async def has_connections(self, session_id: str) -> bool:
        async with self._lock:
            return bool(self._conns.get(session_id))

    async def active_session_ids(self) -> set[str]:
        async with self._lock:
            return set(self._conns.keys())


ws_manager = WSManager()
