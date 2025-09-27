from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


class PostMessageRequest(BaseModel):
    role: str = Field(pattern=r"^(user|system)$")
    content: str
    # Optional: when provided, the backend will treat this as the
    # explicit request to drive transcription+summary (transcribe_asr user_req)
    user_req: str | None = None


class PostMessageResponse(BaseModel):
    message_id: str


class MessagesPage(BaseModel):
    items: List[dict]
    next_cursor: Optional[int] = None
