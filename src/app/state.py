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
    # Persist minimal agent context so follow-ups work without re-supplying the URL
    # Structure: { "video": {...}, "artifacts": {...}, "transcript_path": str | None }
    agent_ctx: dict = field(default_factory=dict)


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

    def replace_last_user_message(self, sid: str, new_content: str) -> Optional[Message]:
        """Replace the last message if it is a user message; return the updated message or None.

        Useful for retry flows where the previous user input caused an immediate validation error
        and the next input is a corrected version that should not appear as a second bubble.
        """
        with self._lock:
            sess = self.sessions.get(sid)
            if not sess or not sess.messages:
                return None
            last = sess.messages[-1]
            if last.role != "user":
                return None
            last.content = new_content
            sess.updated_at = time.time()
            return last

    # --- Agent context helpers -------------------------------------------------
    def set_agent_context(self, sid: str, ctx: dict) -> None:
        with self._lock:
            if sid in self.sessions:
                self.sessions[sid].agent_ctx = ctx or {}

    def get_agent_context(self, sid: str) -> dict:
        with self._lock:
            s = self.sessions.get(sid)
            return dict(getattr(s, "agent_ctx", {}) or {}) if s else {}


store = MemoryStore()
