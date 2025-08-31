# core/state.py

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------- Config ----------
@dataclass
class Config:
    profile: str 
    provider: str
    model: str 
    max_tokens: int
    cost_limit_usd: float 
    step_limit: int 
    runtime_dir: Path = Path("runtime")


# ---------- Video Metadata ----------
@dataclass
class VideoMeta:
    video_id: str
    title: str
    duration_s: int
    source_url: str


# ---------- Transcript Chunk ----------
@dataclass
class Chunk:
    start_s: int
    end_s: int
    text: str
    summary: str | None = None


# ---------- Agent State ----------
@dataclass
class AgentState:
    config: Config
    video: VideoMeta | None = None
    transcript: str | None = None
    chunks: list[Chunk] = field(default_factory=list)
    notes: dict[str, str] = field(default_factory=dict)      
    cost: dict[str, float] = field(default_factory=dict)     
    artifacts: dict[str, Any] = field(default_factory=dict)  
