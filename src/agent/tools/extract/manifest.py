from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.core.config import ExtractAudioConfig


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_manifest(
    manifest_path: Path,
    *,
    source: str,
    original_url: Optional[str],
    downloaded_path: Optional[str],
    probe: Dict[str, Any],
    filter_notes: Dict[str, Any],
    cfg: ExtractAudioConfig,
    wav_path: Path,
    wav_sha256: str,
    out_dur: float,
    chunk_meta: List[Dict[str, Any]],
    wall_time_sec: float,
    log_path: Path,
    warnings: List[str],
) -> None:
    manifest: Dict[str, Any] = {
        "version": 1,
        "created_at": _now_iso(),
        "source": {
            "path_or_url": source,
            "original_url": original_url,
            "downloaded_path": downloaded_path,
            "probe": {
                "container": probe.get("container"),
                "audio_codec": probe.get("audio_codec"),
                "channels": probe.get("channels"),
                "sample_rate": probe.get("sample_rate"),
                "bit_rate": probe.get("bit_rate"),
                "duration": probe.get("duration"),
            },
        },
        "processing": {
            "target": {
                "format": cfg.format,
                "codec": "pcm_s16le",
                "sample_rate": cfg.sample_rate,
                "channels": 1 if cfg.mono else 2,
            },
            "filters": filter_notes,
            "normalize": cfg.normalize,
            "loudnorm_ebu": cfg.loudnorm_ebu,
            "target_lufs": cfg.target_lufs,
            "max_peak_dbfs": cfg.max_peak_dbfs,
            "silence_trim": cfg.silence_trim,
            "start_offset_sec": cfg.start_offset_sec,
            "end_offset_sec": cfg.end_offset_sec,
        },
        "result": {
            "type": "chunks" if chunk_meta else "single",
            "wav_path": str(wav_path),
            "wav_sha256": wav_sha256,
            "duration": out_dur,
            "chunks": chunk_meta,
        },
        "warnings": warnings,
        "stats": {
            "wall_time_sec": round(wall_time_sec, 3),
            "output_total_size": (wav_path.stat().st_size + sum(Path(c["path"]).stat().st_size for c in chunk_meta)) if chunk_meta else wav_path.stat().st_size,
            "log_path": str(log_path),
        },
    }

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

