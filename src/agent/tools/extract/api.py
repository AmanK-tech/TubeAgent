from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.core.config import ExtractAudioConfig
from agent.core.state import AgentState
from agent.errors import ToolError

from .ffmpeg_utils import (
    _bin_exists,
    _ffmpeg_path,
    _run,
    _probe_source,
    _build_filters,
    _maybe_short_circuit,
    _seconds_to_hms,
)
from .cache import cache_key
from .chunking import compute_chunk_boundaries_duration, chunk_vad_energy
from .manifest import write_manifest
from .youtube import is_youtube_url, download_youtube_audio


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


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_audio_task(
    state: AgentState,
    tool_name: str,
    input_path: Optional[str] = None,
    input_url: Optional[str] = None,
    out_dir: Optional[str] = None,
    config: Optional[ExtractAudioConfig] = None,
) -> ExtractResult:
    """
    Extract audio to WAV and optionally chunk it, writing a manifest and cached outputs.

    Example call:

        extract_audio_task(
            state,
            "extract_audio",
            input_url="https://www.youtube.com/watch?v=...",
            config=ExtractAudioConfig(chunk_strategy="duration", chunk_duration_sec=150),
        )

    Args:
        state (AgentState): Mutable agent state holding config and artifacts.
        tool_name (str): Tool name label, commonly "extract_audio".
        input_path (str, optional): Local media file path when not using a URL.
        input_url (str, optional): Source URL; YouTube is supported and will be downloaded.
        out_dir (str, optional): Override the output/cache directory (defaults under runtime/cache/extract).
        config (ExtractAudioConfig, optional): Audio processing and chunking configuration.

    Returns:
        ExtractResult: Contains `wav_path`, `manifest_path`, and optional `chunks` metadata.

    Raises:
        ToolError: On invalid inputs, ffprobe/ffmpeg failures, or chunk export errors.
    """
    tool = tool_name or "extract_audio"

    if not input_path and not input_url:
        raise ToolError("extract_audio requires an input_path or input_url", tool_name=tool)
    source = input_path or input_url  # type: ignore

    cfg = config or ExtractAudioConfig()

    runtime_dir = getattr(state.config, "runtime_dir", Path("runtime")) if getattr(state, "config", None) else Path("runtime")
    cache_dir = Path(out_dir) if out_dir else (Path(cfg.io_cache_dir) if cfg.io_cache_dir else runtime_dir / "cache" / "extract")
    tmp_dir = Path(cfg.io_tmp_dir) if cfg.io_tmp_dir else runtime_dir / "tmp"
    downloads_dir = runtime_dir / "downloads"
    for d in (cache_dir, tmp_dir, downloads_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Optional: download YouTube URL to local file first (video-first)
    download_meta: Dict[str, Any] = {}
    original_url: Optional[str] = None
    if input_url and is_youtube_url(input_url):
        original_url = input_url
        dl_path, meta = download_youtube_audio(input_url, downloads_dir)
        source = str(dl_path)
        download_meta = meta

    # Probe source
    probe = _probe_source(source)
    if not probe.get("ok", False):
        raise ToolError(f"ffprobe failed for source: {probe.get('error')}", tool_name=tool)

    duration = probe.get("duration") or 0.0
    if not duration or duration <= 0:
        raise ToolError("Input duration is zero or unknown.", tool_name=tool)

    effective_end = cfg.end_offset_sec if cfg.end_offset_sec is not None else duration
    if cfg.max_duration_sec is not None:
        effective_end = min(effective_end, cfg.start_offset_sec + cfg.max_duration_sec)
    if effective_end <= cfg.start_offset_sec:
        raise ToolError("Configured offsets produce empty output.", tool_name=tool)
    effective_duration = effective_end - cfg.start_offset_sec

    # Cache key and paths
    key = cache_key(f"{source}|{cfg.start_offset_sec}|{cfg.end_offset_sec}", cfg)
    base = key[:16]
    base_dir = cache_dir / base
    base_dir.mkdir(parents=True, exist_ok=True)

    wav_out = base_dir / f"audio_{base}.wav"
    manifest_path = base_dir / "extract_audio.manifest.json"

    # Cache reuse
    if not cfg.force and manifest_path.exists() and wav_out.exists():
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest_old = json.load(f)
            expected_sha = manifest_old.get("result", {}).get("wav_sha256")
            if expected_sha and _sha256_file(wav_out) == expected_sha:
                if cfg.chunk_strategy != "none":
                    chunks_ok = True
                    for ch in manifest_old.get("result", {}).get("chunks", []):
                        if not Path(ch.get("path", "")).exists():
                            chunks_ok = False
                            break
                    if chunks_ok:
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
                    return ExtractResult(type="single", wav_path=str(wav_out), chunks=[], manifest_path=str(manifest_path))
        except Exception:
            pass

    short_circuit = _maybe_short_circuit(probe, cfg)

    ffmpeg_bin = _ffmpeg_path()
    if not _bin_exists(ffmpeg_bin):
        raise ToolError("ffmpeg binary not found in PATH.", tool_name=tool)

    filters, filter_notes = _build_filters(cfg, force_mono=True)
    input_opts: List[str] = []
    if cfg.start_offset_sec and cfg.start_offset_sec > 0:
        input_opts += ["-ss", _seconds_to_hms(cfg.start_offset_sec)]
    duration_opts: List[str] = []
    if effective_duration and effective_duration > 0:
        duration_opts += ["-t", f"{effective_duration:.3f}"]

    log_path = base_dir / f"ffmpeg_{base}.log"

    def run_ffmpeg(apply_loudnorm: bool) -> Tuple[int, str]:
        flt = filters
        if not apply_loudnorm and "loudnorm" in flt:
            parts = [p for p in flt.split(",") if not p.startswith("loudnorm")]
            flt2 = ",".join(parts)
        else:
            flt2 = flt

        if short_circuit:
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

    t0 = time.time()
    code, err1 = run_ffmpeg(apply_loudnorm=True)
    if code != 0 and cfg.normalize and cfg.loudnorm_ebu:
        code, err2 = run_ffmpeg(apply_loudnorm=False)
        if code != 0:
            raise ToolError(f"ffmpeg failed (with and without loudnorm). Last error: {err2[-400:]}", tool_name=tool)
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
        raise ToolError(f"Output verification failed (sr={asr_sr}, ch={out_ch}, codec={out_codec}).", tool_name=tool)
    if not out_dur or out_dur <= 0:
        raise ToolError("Output duration seems invalid.", tool_name=tool)

    wav_sha = _sha256_file(wav_out)

    # Chunking
    chunks: List[ChunkInfo] = []
    chunk_meta: List[Dict[str, Any]] = []
    if cfg.chunk_strategy in ("duration", "vad"):
        if cfg.chunk_strategy == "duration":
            boundaries = compute_chunk_boundaries_duration(out_dur, cfg)
        else:
            boundaries = chunk_vad_energy(wav_out, cfg)

        ffmpeg_bin_local = ffmpeg_bin
        for idx, (rel_s, rel_e) in enumerate(boundaries):
            abs_s = cfg.start_offset_sec + rel_s
            abs_e = cfg.start_offset_sec + rel_e
            # Export audio chunk (wav)
            out_path = base_dir / f"chunk_{idx:04d}_{base}.wav"
            cmd = [
                ffmpeg_bin_local,
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
            ch = ChunkInfo(idx=idx, start_sec=abs_s, end_sec=abs_e, duration=max(0.0, abs_e - abs_s), path=str(out_path), sha256=sha)
            chunks.append(ch)
            ch_dict = asdict(ch)

            # Export corresponding video chunk (mp4) via stream copy (H.264/AAC source expected)
            video_chunk = base_dir / f"chunk_{idx:04d}_{base}.mp4"
            dur = max(0.01, rel_e - rel_s)
            cmd_vid_copy = [
                ffmpeg_bin_local,
                "-hide_banner",
                "-ss", f"{abs_s:.3f}",  # use absolute start from original source
                "-i", str(source),
                "-t", f"{dur:.3f}",
                "-map", "0:v:0",
                "-map", "0:a?",
                "-c", "copy",
                "-movflags", "+faststart",
                "-avoid_negative_ts", "make_zero",
                "-y", str(video_chunk),
            ]
            code_v2, out_v2, err_v2 = _run(cmd_vid_copy, timeout=int(max(120, dur * 6)))
            if video_chunk.exists():
                ch_dict["video_path"] = str(video_chunk)

            chunk_meta.append(ch_dict)

    # Build warnings
    warnings: List[str] = []
    if probe.get("channels") and int(probe["channels"]) != 1:
        warnings.append("Channel downmix applied")
    if probe.get("sample_rate") and int(probe["sample_rate"]) != cfg.sample_rate:
        warnings.append("Resampling applied")

    # Write manifest
    write_manifest(
        manifest_path,
        source=source,
        original_url=original_url,
        downloaded_path=download_meta.get("filepath") if download_meta else None,
        probe=probe,
        filter_notes=filter_notes,
        cfg=cfg,
        wav_path=wav_out,
        wav_sha256=wav_sha,
        out_dur=out_dur,
        chunk_meta=chunk_meta,
        wall_time_sec=wall_time,
        log_path=log_path,
        warnings=warnings,
        video_path=Path(source),
    )

    state.artifacts.setdefault(tool, {})
    state.artifacts[tool].update({
        "manifest_path": str(manifest_path),
        "wav_path": str(wav_out),
        "video_path": str(source),
        "chunks": chunk_meta,
    })

    return ExtractResult(type="chunks" if chunks else "single", wav_path=str(wav_out), chunks=chunks, manifest_path=str(manifest_path))
