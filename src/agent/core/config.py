# config.py

import os
import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from agent.core.state import Config


# Audio extraction configuration (used by tools.extract)
@dataclass
class ExtractAudioConfig:
    # Target
    sample_rate: int = 16000
    mono: bool = True
    format: str = "wav"  # always wav for ASR

    # Normalization / processing
    normalize: bool = True
    loudnorm_ebu: bool = True
    target_lufs: float = -23.0  # EBU R128 target range -23 to -16
    max_peak_dbfs: float = -1.0  # limiter ceiling
    silence_trim: bool = False
    silence_threshold_db: float = -40.0  # head/tail trim threshold
    silence_min_ms: int = 800

    # Limits and offsets
    max_duration_sec: Optional[int] = None  # None => no cap
    start_offset_sec: float = 0.0
    end_offset_sec: Optional[float] = None

    # Chunking (tuned for faster turnaround)
    chunk_strategy: str = "duration"  # none | duration | vad
    chunk_duration_sec: int = 150     # ~2.5 minutes per chunk by default
    chunk_overlap_sec: float = 1.0    # small overlap to avoid cuts
    chunk_max_sec: int = 180          # VAD upper bound to prevent long runs

    # IO & cache
    io_cache_dir: Optional[Path] = None
    io_tmp_dir: Optional[Path] = None
    force: bool = False


def load_config(profile: str = "default") -> Config:
    """Load configuration with optional YAML + env overrides."""

    
    defaults = {
        "profile": profile,
        "model": "gemini-2.5-flash",
        "max_tokens": 4000,
        "cost_limit_usd": 5.0,
        "base_steps": 10,
        "per_chunk_steps": 2,
        "runtime_dir": Path("runtime"),
        "chunk_duration_s": 300,
        "api_key": None,
    }

    
    yaml_path = Path("configs") / f"{profile}.yaml"
    if yaml_path.exists():
        with open(yaml_path, "r") as f:
            yaml_config = yaml.safe_load(f) or {}
            defaults.update(yaml_config)

    
    env_overrides = {
        "model": os.getenv("AGENT_MODEL"),
        "max_tokens": os.getenv("AGENT_MAX_TOKENS"),
        "cost_limit_usd": os.getenv("AGENT_COST_LIMIT"),
        "chunk_duration_s": os.getenv("AGENT_CHUNK_SEC"),
        "api_key": os.getenv("GEMINI_API_KEY"),   # <-- pick up key
    }

    for k, v in env_overrides.items():
        if v is not None:
            if k in ["max_tokens", "chunk_duration_s"]:
                v = int(v)
            elif k in ["cost_limit_usd"]:
                v = float(v)
            defaults[k] = v

    
    return Config(**defaults)
