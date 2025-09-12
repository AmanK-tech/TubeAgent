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
    """Load agent Config with YAML and env overrides (DeepSeek only).

    Only the fields defined in agent.core.state.Config are accepted:
      profile, provider, model, max_tokens, cost_limit_usd, step_limit, runtime_dir
    """

    # Base defaults aligned with state.Config
    cfg_map: dict[str, object] = {
        "profile": profile,
        "provider": "deepseek",
        "model": "deepseek-chat",
        "max_tokens": 4000,
        "cost_limit_usd": 5.0,
        "step_limit": 0,
        "runtime_dir": Path("runtime"),
    }

    # Optional YAML overrides; only accept known keys
    yaml_path = Path("configs") / f"{profile}.yaml"
    if yaml_path.exists():
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                yaml_config = yaml.safe_load(f) or {}
            if isinstance(yaml_config, dict):
                for k in list(cfg_map.keys()):
                    if k in yaml_config and yaml_config[k] is not None:
                        cfg_map[k] = yaml_config[k]
        except Exception:
            # Ignore YAML issues; stick to defaults
            pass

    # Environment overrides
    env_overrides = {
        "provider": os.getenv("AGENT_PROVIDER"),
        "model": os.getenv("AGENT_MODEL"),
        "max_tokens": os.getenv("AGENT_MAX_TOKENS"),
        "cost_limit_usd": os.getenv("AGENT_COST_LIMIT"),
        "step_limit": os.getenv("AGENT_STEP_LIMIT"),
    }
    for k, v in env_overrides.items():
        if v is None:
            continue
        if k in {"max_tokens", "step_limit"}:
            try:
                cfg_map[k] = int(v)
            except Exception:
                continue
        elif k in {"cost_limit_usd"}:
            try:
                cfg_map[k] = float(v)
            except Exception:
                continue
        else:
            cfg_map[k] = v

    # Coerce runtime_dir to Path if a string slipped in
    rd = cfg_map.get("runtime_dir")
    if isinstance(rd, str):
        cfg_map["runtime_dir"] = Path(rd)

    return Config(**cfg_map)  # type: ignore[arg-type]
