from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.core.config import ExtractAudioConfig


def _bin_exists(bin_name: str) -> bool:
    return shutil.which(bin_name) is not None


def _ffmpeg_path() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _ffprobe_path() -> str:
    return shutil.which("ffprobe") or "ffprobe"


def _run(cmd: List[str], cwd: Optional[Path] = None, timeout: Optional[int] = None) -> Tuple[int, str, str]:
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        return 124, out, err
    return proc.returncode, out, err


def _probe_source(input_path_or_url: str) -> Dict[str, Any]:
    probe_bin = _ffprobe_path()
    if not _bin_exists(probe_bin):
        return {"ok": False, "error": "ffprobe_not_found", "format": {}, "streams": []}

    cmd = [
        probe_bin,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        input_path_or_url,
    ]
    code, out, err = _run(cmd, timeout=30)
    if code != 0:
        return {"ok": False, "error": err.strip(), "format": {}, "streams": []}
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        data = {"format": {}, "streams": []}
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    astreams = [s for s in streams if s.get("codec_type") == "audio"]
    a = astreams[0] if astreams else {}
    duration = None
    try:
        duration = float(a.get("duration") or fmt.get("duration"))
    except Exception:
        duration = None
    return {
        "ok": True,
        "format": fmt,
        "stream": a,
        "container": fmt.get("format_name"),
        "audio_codec": a.get("codec_name"),
        "sample_rate": int(a.get("sample_rate")) if a.get("sample_rate") else None,
        "channels": int(a.get("channels")) if a.get("channels") else None,
        "bit_rate": int(a.get("bit_rate")) if a.get("bit_rate") else None,
        "duration": duration,
        "raw": data,
    }


def _build_filters(cfg: ExtractAudioConfig, force_mono: bool) -> tuple[str, Dict[str, Any]]:
    filters: list[str] = []
    notes: Dict[str, Any] = {}

    if cfg.mono and force_mono:
        filters.append("pan=mono|c0=0.5*FL+0.5*FR")
        notes["downmix"] = "pan=mono|c0=0.5*FL+0.5*FR"

    filters.append(f"aresample={cfg.sample_rate}:resampler=soxr")
    notes["resample"] = {"sample_rate": cfg.sample_rate, "resampler": "soxr"}

    if cfg.normalize and cfg.loudnorm_ebu:
        filters.append(f"loudnorm=I={cfg.target_lufs}:LRA=11:TP={cfg.max_peak_dbfs}")
        notes["loudnorm"] = {"I": cfg.target_lufs, "LRA": 11, "TP": cfg.max_peak_dbfs, "mode": "one_pass"}
    elif cfg.normalize:
        filters.append(f"alimiter=limit={cfg.max_peak_dbfs}dB")
        notes["limiter"] = {"limit_dB": cfg.max_peak_dbfs}

    if cfg.silence_trim:
        thr = cfg.silence_threshold_db
        min_d = max(50, cfg.silence_min_ms)
        filters.append(
            f"silenceremove=start_periods=1:start_threshold={thr}dB:start_silence={min_d}ms:"
            f"detection=peak,aformat=sample_fmts=s16:sample_rates={cfg.sample_rate},"
            f"areverse,silenceremove=start_periods=1:start_threshold={thr}dB:start_silence={min_d}ms,areverse"
        )
        notes["silenceremove"] = {"threshold_db": thr, "min_ms": min_d, "mode": "head_tail"}

    return ",".join(filters), notes


def _maybe_short_circuit(probe: Dict[str, Any], cfg: ExtractAudioConfig) -> bool:
    codec = (probe.get("audio_codec") or "").lower()
    sr = probe.get("sample_rate")
    ch = probe.get("channels")
    container = (probe.get("container") or "").lower()
    if (
        container.startswith("wav")
        and codec in ("pcm_s16le", "pcm_s16be", "pcm_s24le", "pcm_s24be", "pcm_f32le")
        and sr == cfg.sample_rate
        and (not cfg.mono or ch == 1)
        and not cfg.normalize
        and not cfg.silence_trim
    ):
        return True
    return False


def _seconds_to_hms(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"

