from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import time
import hashlib
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from agent.core.state import AgentState
from agent.core.config import ExtractAudioConfig
from agent.errors import ToolError


# ------------------------------ Schema for tool -------------------------------

@dataclass
class ChunkInfo:
    idx: int
    start_sec: float
    end_sec: float
    duration: float
    path: str
    sha256: str


@dataclass
class ExtractResult:
    type: str  # "single" | "chunks"
    wav_path: Optional[str]
    chunks: List[ChunkInfo]
    manifest_path: str


# ------------------------------ Utilities -----------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _bin_exists(bin_name: str) -> bool:
    return shutil.which(bin_name) is not None


def _ffmpeg_path() -> str:
    # Use system ffmpeg if available, otherwise fallback to name
    return shutil.which("ffmpeg") or "ffmpeg"


def _ffprobe_path() -> str:
    # Use system ffprobe if available, otherwise fallback to name
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


def _ensure_dirs(*paths: Path) -> None:
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)


def _cache_key(source: str, cfg: ExtractAudioConfig) -> str:
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


def _probe_source(input_path_or_url: str) -> Dict[str, Any]:
    probe_bin = _ffprobe_path()
    if not _bin_exists(probe_bin):
        # Minimal fallback when ffprobe is not available
        return {
            "ok": False,
            "error": "ffprobe_not_found",
            "format": {},
            "streams": [],
        }

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
    # Extract audio stream info
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


def _is_youtube_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    return (
        host == "youtu.be"
        or host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtube-nocookie.com"
        or host.endswith(".youtube-nocookie.com")
    )


def _download_youtube_audio(url: str, out_dir: Path) -> Tuple[Path, Dict[str, Any]]:
    """Download bestaudio for a YouTube URL using yt-dlp into out_dir.
    Returns (downloaded_path, meta). Raises ToolError if yt_dlp missing or download fails.
    """
    try:
        import yt_dlp  # type: ignore
    except Exception as e:  # pragma: no cover
        raise ToolError(
            "yt-dlp is required to download YouTube audio. Please install it (pip install yt-dlp).",
            tool_name="extract_audio",
        ) from e

    _ensure_dirs(out_dir)
    # Output template ensures stable file path by video id
    outtmpl = str(out_dir / "%(id)s.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 20,
        "overwrites": False,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # Determine the actual file path
            filepath: Optional[str] = None
            req = info.get("requested_downloads") if isinstance(info, dict) else None
            if isinstance(req, list) and req:
                filepath = req[0].get("filepath")
            if not filepath:
                # Fallback to prepare_filename
                filepath = ydl.prepare_filename(info)
    except Exception as e:
        raise ToolError(f"yt-dlp download failed: {e}", tool_name="extract_audio")

    dl_path = Path(filepath).resolve()
    if not dl_path.exists():
        raise ToolError("yt-dlp reported success, but file missing.", tool_name="extract_audio")

    meta = {
        "id": info.get("id"),
        "title": info.get("title"),
        "ext": info.get("ext"),
        "uploader": info.get("uploader"),
        "duration": info.get("duration"),
        "webpage_url": info.get("webpage_url") or url,
        "filepath": str(dl_path),
    }
    return dl_path, meta


def _build_filters(cfg: ExtractAudioConfig, force_mono: bool) -> Tuple[str, Dict[str, Any]]:
    filters: List[str] = []
    notes: Dict[str, Any] = {}

    # Channel downmix to mono
    if cfg.mono and force_mono:
        filters.append("pan=mono|c0=0.5*FL+0.5*FR")
        notes["downmix"] = "pan=mono|c0=0.5*FL+0.5*FR"

    # Resample with soxr for quality
    filters.append(f"aresample={cfg.sample_rate}:resampler=soxr")
    notes["resample"] = {"sample_rate": cfg.sample_rate, "resampler": "soxr"}

    # Optional loudness normalization
    if cfg.normalize and cfg.loudnorm_ebu:
        # Use one-pass loudnorm for simplicity; two-pass can be added later
        filters.append(f"loudnorm=I={cfg.target_lufs}:LRA=11:TP={cfg.max_peak_dbfs}")
        notes["loudnorm"] = {"I": cfg.target_lufs, "LRA": 11, "TP": cfg.max_peak_dbfs, "mode": "one_pass"}
    elif cfg.normalize:
        # Simple peak limiter only
        filters.append(f"alimiter=limit={cfg.max_peak_dbfs}dB")
        notes["limiter"] = {"limit_dB": cfg.max_peak_dbfs}

    # Optional head/tail silence trim (disabled by default for stable timecodes)
    if cfg.silence_trim:
        thr = cfg.silence_threshold_db
        min_d = max(50, cfg.silence_min_ms)
        filters.append(
            f"silenceremove=start_periods=1:start_threshold={thr}dB:start_silence={min_d}ms:"
            f"detection=peak,aformat=sample_fmts=s16:sample_rates={cfg.sample_rate},"
            f"areverse,silenceremove=start_periods=1:start_threshold={thr}dB:start_silence={min_d}ms,areverse"
        )
        notes["silenceremove"] = {
            "threshold_db": thr,
            "min_ms": min_d,
            "mode": "head_tail",
        }

    return ",".join(filters), notes


def _maybe_short_circuit(probe: Dict[str, Any], cfg: ExtractAudioConfig) -> bool:
    # If input already mono/16k PCM WAV and no normalization/trim requested, we can copy
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


def _compute_chunk_boundaries_duration(total_dur: float, cfg: ExtractAudioConfig) -> List[Tuple[float, float]]:
    step = max(1, int(cfg.chunk_duration_sec))
    ov = max(0.0, float(cfg.chunk_overlap_sec))
    cuts: List[Tuple[float, float]] = []
    t = 0.0
    while t < total_dur:
        end = min(total_dur, t + step)
        cuts.append((t, end))
        if end >= total_dur:
            break
        t = end - ov
    # Merge tiny last segment (<30s) if needed
    if len(cuts) >= 2 and (cuts[-1][1] - cuts[-1][0]) < 30.0:
        prev_s, prev_e = cuts[-2]
        cuts[-2] = (prev_s, cuts[-1][1])
        cuts.pop()
    return cuts


def _chunk_vad_energy(pcm_wav_path: Path, cfg: ExtractAudioConfig) -> List[Tuple[float, float]]:
    """Energy-based VAD over mono/16k PCM WAV.
    Prefer stdlib audioop; fall back to audioop-lts; finally pure-Python RMS.
    """
    import wave

    # Prefer audioop_lts; fall back to pure-Python RMS if unavailable
    try:  # pip install audioop-lts
        import audioop_lts as audioop  # type: ignore
        _rms = lambda data, sw: float(audioop.rms(data, sw))  # noqa: E731
    except Exception:  # pragma: no cover - environment dependent
        audioop = None  # type: ignore
        _rms = None  # type: ignore

    def rms_fallback_s16(data: bytes, sample_width: int) -> float:
        # Pure-Python fallback for 16-bit signed PCM
        if sample_width != 2 or not data:
            return 0.0
        from array import array
        arr = array("h")
        try:
            arr.frombytes(data)
        except Exception:
            return 0.0
        if not arr:
            return 0.0
        ssum = 0
        for v in arr:
            ssum += v * v
        mean = ssum / float(len(arr))
        return math.sqrt(mean)

    segments: List[Tuple[float, float]] = []
    with wave.open(str(pcm_wav_path), "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        if sr != cfg.sample_rate or ch != 1:
            total = wf.getnframes() / float(sr) if sr else 0.0
            return [(0.0, total)] if total > 0 else []

        frame_ms = 30  # 30ms frames
        frames_per_step = max(1, int(sr * (frame_ms / 1000.0)))
        threshold = 300.0  # empirical RMS threshold for s16
        speech_regions: List[Tuple[int, int]] = []
        in_speech = False
        start_idx = 0
        idx = 0
        data = wf.readframes(frames_per_step)
        while data:
            if _rms is not None:
                val = _rms(data, sw)
            else:
                val = rms_fallback_s16(data, sw)
            if val >= threshold:
                if not in_speech:
                    in_speech = True
                    start_idx = idx
            else:
                if in_speech:
                    in_speech = False
                    speech_regions.append((start_idx, idx))
            idx += 1
            data = wf.readframes(frames_per_step)
        if in_speech:
            speech_regions.append((start_idx, idx))

        # Merge very short gaps (<300ms)
        merged: List[Tuple[int, int]] = []
        for seg in speech_regions:
            if not merged:
                merged.append(seg)
                continue
            prev_s, prev_e = merged[-1]
            if (seg[0] - prev_e) * frame_ms < 300:
                merged[-1] = (prev_s, seg[1])
            else:
                merged.append(seg)

        # Constrain to max duration; split on nearest frame boundary
        max_frames = max(1, int(cfg.chunk_max_sec * 1000 / frame_ms))
        for s, e in merged:
            while (e - s) > max_frames:
                segments.append((s * frame_ms / 1000.0, (s + max_frames) * frame_ms / 1000.0))
                s += max_frames
            segments.append((s * frame_ms / 1000.0, e * frame_ms / 1000.0))

        # Add overlap between adjacent chunks
        if cfg.chunk_overlap_sec > 0 and segments:
            ov = cfg.chunk_overlap_sec
            out: List[Tuple[float, float]] = []
            for i, (s, e) in enumerate(segments):
                s2 = max(0.0, s - (ov if i > 0 else 0.0))
                e2 = e + (ov if i < len(segments) - 1 else 0.0)
                out.append((s2, e2))
            segments = out

    return segments


# ------------------------------ Core Task -----------------------------------

def extract_audio_task(
    state: AgentState,
    tool_name: str,
    input_path: Optional[str] = None,
    input_url: Optional[str] = None,
    out_dir: Optional[str] = None,
    config: Optional[ExtractAudioConfig] = None,
) -> ExtractResult:
    """
    Extract mono, 16k PCM WAV from a media source, optionally chunked, with manifest.
    - Either `input_path` (local file) or `input_url` (direct demux) must be provided.
    - Returns ExtractResult and writes manifest JSON alongside outputs.
    - Uses caching to avoid recomputation.
    """
    tool = tool_name or "extract_audio"

    if not input_path and not input_url:
        raise ToolError("extract_audio requires an input_path or input_url", tool_name=tool)
    source = input_path or input_url  # type: ignore

    cfg = config or ExtractAudioConfig()

    # IO layout
    runtime_dir = getattr(state.config, "runtime_dir", Path("runtime")) if getattr(state, "config", None) else Path("runtime")
    cache_dir = Path(out_dir) if out_dir else (Path(cfg.io_cache_dir) if cfg.io_cache_dir else runtime_dir / "cache" / "extract")
    tmp_dir = Path(cfg.io_tmp_dir) if cfg.io_tmp_dir else runtime_dir / "tmp"
    downloads_dir = runtime_dir / "downloads"
    _ensure_dirs(cache_dir, tmp_dir, downloads_dir)

    # If input is a YouTube URL, download audio first
    download_meta: Dict[str, Any] = {}
    original_url: Optional[str] = None
    if input_url and _is_youtube_url(input_url):
        original_url = input_url
        dl_path, meta = _download_youtube_audio(input_url, downloads_dir)
        source = str(dl_path)
        download_meta = meta

    # Probe source (local file or direct URL)
    probe = _probe_source(source)
    if not probe.get("ok", False):
        raise ToolError(f"ffprobe failed for source: {probe.get('error')}", tool_name=tool)

    duration = probe.get("duration") or 0.0
    if not duration or duration <= 0:
        raise ToolError("Input duration is zero or unknown.", tool_name=tool)

    # Enforce max duration: if specified, crop to end or error. Here we crop.
    effective_end = cfg.end_offset_sec if cfg.end_offset_sec is not None else duration
    if cfg.max_duration_sec is not None:
        effective_end = min(effective_end, cfg.start_offset_sec + cfg.max_duration_sec)
    if effective_end <= cfg.start_offset_sec:
        raise ToolError("Configured offsets produce empty output.", tool_name=tool)
    effective_duration = effective_end - cfg.start_offset_sec

    # Cache key & base name
    key = _cache_key(
        f"{source}|{cfg.start_offset_sec}|{cfg.end_offset_sec}",
        cfg,
    )
    base = key[:16]
    base_dir = cache_dir / base
    _ensure_dirs(base_dir)

    wav_out = base_dir / f"audio_{base}.wav"
    manifest_path = base_dir / "extract_audio.manifest.json"

    # Cache reuse if present and not forced
    if not cfg.force and manifest_path.exists() and wav_out.exists():
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest_old = json.load(f)
            # Basic validation of checksum
            expected_sha = manifest_old.get("result", {}).get("wav_sha256")
            if expected_sha and _sha256_file(wav_out) == expected_sha:
                # If chunks were requested, ensure chunk files exist
                if cfg.chunk_strategy != "none":
                    chunks_ok = True
                    for ch in manifest_old.get("result", {}).get("chunks", []):
                        if not Path(ch.get("path", "")).exists():
                            chunks_ok = False
                            break
                    if chunks_ok:
                        # Return cached result
                        chunks = [
                            ChunkInfo(
                                idx=int(ch["idx"]),
                                start_sec=float(ch["start_sec"]),
                                end_sec=float(ch["end_sec"]),
                                duration=float(ch["duration"]),
                                path=str(ch["path"]),
                                sha256=str(ch.get("sha256", "")),
                            )
                            for ch in manifest_old.get("result", {}).get("chunks", [])
                        ]
                        return ExtractResult(
                            type="chunks" if chunks else "single",
                            wav_path=str(wav_out),
                            chunks=chunks,
                            manifest_path=str(manifest_path),
                        )
                else:
                    return ExtractResult(
                        type="single",
                        wav_path=str(wav_out),
                        chunks=[],
                        manifest_path=str(manifest_path),
                    )
        except Exception:
            # Fall through to re-extract
            pass

    # Short-circuit possible copy
    short_circuit = _maybe_short_circuit(probe, cfg)

    ffmpeg_bin = _ffmpeg_path()
    if not _bin_exists(ffmpeg_bin):
        raise ToolError("ffmpeg binary not found in PATH.", tool_name=tool)

    # Build command
    filters, filter_notes = _build_filters(cfg, force_mono=True)
    input_opts: List[str] = []
    if cfg.start_offset_sec and cfg.start_offset_sec > 0:
        input_opts += ["-ss", _seconds_to_hms(cfg.start_offset_sec)]

    duration_opts: List[str] = []
    if effective_duration and effective_duration > 0:
        duration_opts += ["-t", f"{effective_duration:.3f}"]

    # Create log file for diagnostics
    log_path = base_dir / f"ffmpeg_{base}.log"

    def run_ffmpeg(apply_loudnorm: bool) -> Tuple[int, str]:
        flt = filters
        if not apply_loudnorm and "loudnorm" in flt:
            # Remove loudnorm by rebuilding without it
            parts = [p for p in flt.split(",") if not p.startswith("loudnorm")]
            flt2 = ",".join(parts)
        else:
            flt2 = flt

        if short_circuit:
            # Copy with appropriate container if already PCM 16k mono WAV and no processing
            cmd = [
                ffmpeg_bin,
                *input_opts,
                "-i",
                source,
                *duration_opts,
                "-vn",
                "-acodec",
                "copy",
                "-y",
                str(wav_out),
            ]
        else:
            cmd = [
                ffmpeg_bin,
                "-hide_banner",
                *input_opts,
                "-i",
                source,
                *duration_opts,
                "-vn",
                "-ac",
                "1" if cfg.mono else "2",
                "-af",
                flt2,
                "-ar",
                str(cfg.sample_rate),
                "-acodec",
                "pcm_s16le",
                "-f",
                "wav",
                "-y",
                str(wav_out),
            ]

        code, out, err = _run(cmd, timeout=max(120, int(effective_duration or 60) * 2))
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write("\n=== CMD ===\n" + " ".join(cmd) + "\n")
            lf.write("=== STDOUT ===\n" + (out or "") + "\n")
            lf.write("=== STDERR ===\n" + (err or "") + "\n")
        return code, err

    # Execute extraction with one retry (skip loudnorm on retry)
    t0 = time.time()
    code, err1 = run_ffmpeg(apply_loudnorm=True)
    if code != 0 and cfg.normalize and cfg.loudnorm_ebu:
        code, err2 = run_ffmpeg(apply_loudnorm=False)
        if code != 0:
            raise ToolError(
                f"ffmpeg failed (with and without loudnorm). Last error: {err2[-400:]}",
                tool_name=tool,
            )
    elif code != 0:
        raise ToolError(f"ffmpeg failed: {err1[-400:]}", tool_name=tool)
    wall_time = time.time() - t0

    # Verify output
    out_probe = _probe_source(str(wav_out))
    asr_sr = out_probe.get("stream", {}).get("sample_rate") or out_probe.get("sample_rate")
    out_codec = (out_probe.get("audio_codec") or "").lower()
    out_ch = out_probe.get("stream", {}).get("channels") or out_probe.get("channels")
    out_dur = out_probe.get("duration") or 0.0
    if int(asr_sr or 0) != cfg.sample_rate or int(out_ch or 0) != 1 or out_codec != "pcm_s16le":
        raise ToolError(
            f"Output verification failed (sr={asr_sr}, ch={out_ch}, codec={out_codec}).",
            tool_name=tool,
        )
    if not out_dur or out_dur <= 0:
        raise ToolError("Output duration seems invalid.", tool_name=tool)

    wav_sha = _sha256_file(wav_out)

    # Chunking
    chunks: List[ChunkInfo] = []
    chunk_meta: List[Dict[str, Any]] = []
    if cfg.chunk_strategy in ("duration", "vad"):
        if cfg.chunk_strategy == "duration":
            boundaries = _compute_chunk_boundaries_duration(out_dur, cfg)
        else:
            boundaries = _chunk_vad_energy(wav_out, cfg)

        for idx, (rel_s, rel_e) in enumerate(boundaries):
            abs_s = cfg.start_offset_sec + rel_s
            abs_e = cfg.start_offset_sec + rel_e
            out_path = base_dir / f"chunk_{idx:04d}_{base}.wav"
            # Export chunk using ffmpeg from the processed WAV
            cmd = [
                ffmpeg_bin,
                "-hide_banner",
                "-ss",
                f"{rel_s:.3f}",
                "-i",
                str(wav_out),
                "-t",
                f"{max(0.01, rel_e - rel_s):.3f}",
                "-acodec",
                "pcm_s16le",
                "-ar",
                str(cfg.sample_rate),
                "-ac",
                "1",
                "-f",
                "wav",
                "-y",
                str(out_path),
            ]
            code, out, err = _run(cmd, timeout=int(max(30, (rel_e - rel_s) * 5)))
            if code != 0:
                raise ToolError(f"Chunk export failed: {err[-400:]}", tool_name=tool)
            sha = _sha256_file(out_path)
            ch = ChunkInfo(
                idx=idx,
                start_sec=abs_s,
                end_sec=abs_e,
                duration=max(0.0, abs_e - abs_s),
                path=str(out_path),
                sha256=sha,
            )
            chunks.append(ch)
            chunk_meta.append(asdict(ch))

    # Build manifest
    manifest: Dict[str, Any] = {
        "version": 1,
        "created_at": _now_iso(),
        "source": {
            "path_or_url": source,
            "original_url": original_url,
            # Only include the downloaded file path; exclude extra video metadata
            "downloaded_path": download_meta.get("filepath") if download_meta else None,
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
            "type": "chunks" if chunks else "single",
            "wav_path": str(wav_out),
            "wav_sha256": wav_sha,
            "duration": out_dur,
            "chunks": chunk_meta,
        },
        "warnings": [],
        "stats": {
            "wall_time_sec": round(wall_time, 3),
            "output_total_size": (wav_out.stat().st_size + sum(Path(c.path).stat().st_size for c in chunks)) if chunks else wav_out.stat().st_size,
            "log_path": str(log_path),
        },
    }

    # Warnings
    if probe.get("channels") and int(probe["channels"]) != 1:
        manifest["warnings"].append("Channel downmix applied")
    if probe.get("sample_rate") and int(probe["sample_rate"]) != cfg.sample_rate:
        manifest["warnings"].append("Resampling applied")

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # Update state artifacts
    state.artifacts.setdefault(tool, {})
    state.artifacts[tool].update({
        "manifest_path": str(manifest_path),
        "wav_path": str(wav_out),
        "chunks": chunk_meta,
    })

    return ExtractResult(
        type="chunks" if chunks else "single",
        wav_path=str(wav_out),
        chunks=chunks,
        manifest_path=str(manifest_path),
    )


__all__ = [
    "ExtractResult",
    "ChunkInfo",
    "extract_audio_task",
]
