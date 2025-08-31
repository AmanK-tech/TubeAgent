# config.py

import os
import yaml
from pathlib import Path
from agent.core.state import Config


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
