from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


class Message(BaseModel):
    id: str
    role: str = Field(pattern=r"^(user|assistant|system|tool)$")
    content: str
    created_at: float


class Session(BaseModel):
    id: str
    title: str
    created_at: float
    updated_at: float
    messages: List[Message] = []


class CreateSessionRequest(BaseModel):
    title: Optional[str] = None


class CreateSessionResponse(BaseModel):
    id: str
    title: str
    created_at: float


class ListSessionsResponse(BaseModel):
    items: List[Session]

