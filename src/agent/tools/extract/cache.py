from __future__ import annotations

import hashlib
import json
from agent.core.config import ExtractAudioConfig


def cache_key(source: str, cfg: ExtractAudioConfig) -> str:
    payload = {
        "source": source,
        "sample_rate": cfg.sample_rate,
        "mono": cfg.mono,
        "normalize": cfg.normalize,
        "loudnorm_ebu": cfg.loudnorm_ebu,
        "target_lufs": cfg.target_lufs,
        "max_peak_dbfs": cfg.max_peak_dbfs,
        "silence_trim": cfg.silence_trim,
        "silence_threshold_db": cfg.silence_threshold_db,
        "silence_min_ms": cfg.silence_min_ms,
        "max_duration_sec": cfg.max_duration_sec,
        "start_offset_sec": cfg.start_offset_sec,
        "end_offset_sec": cfg.end_offset_sec,
        "chunk_strategy": cfg.chunk_strategy,
        "chunk_duration_sec": cfg.chunk_duration_sec,
        "chunk_overlap_sec": cfg.chunk_overlap_sec,
        "chunk_max_sec": cfg.chunk_max_sec,
    }
    s = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(s).hexdigest()

