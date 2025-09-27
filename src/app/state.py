from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Message:
    id: str
    role: str  # user | assistant | system | tool
    content: str
    created_at: float


@dataclass
class Session:
    id: str
    title: str
    created_at: float
    updated_at: float
    messages: List[Message] = field(default_factory=list)


class MemoryStore:
    """A thread-safe in-memory store for sessions and messages."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.sessions: Dict[str, Session] = {}

    def create_session(self, title: Optional[str] = None) -> Session:
        with self._lock:
            sid = str(uuid.uuid4())
            now = time.time()
            s = Session(id=sid, title=title or "New Chat", created_at=now, updated_at=now)
            self.sessions[sid] = s
            return s

    def list_sessions(self) -> List[Session]:
        with self._lock:
            return list(self.sessions.values())

    def get_session(self, sid: str) -> Optional[Session]:
        with self._lock:
            return self.sessions.get(sid)

    def delete_session(self, sid: str) -> Optional[Session]:
        with self._lock:
            return self.sessions.pop(sid, None)

    def add_message(self, sid: str, role: str, content: str) -> Message:
        with self._lock:
            sess = self.sessions[sid]
            m = Message(id=str(uuid.uuid4()), role=role, content=content, created_at=time.time())
            sess.messages.append(m)
            sess.updated_at = m.created_at
            return m


store = MemoryStore()

