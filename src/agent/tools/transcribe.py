from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import math

from agent.core.state import AgentState, Chunk
from agent.errors import ToolError
from agent.tools.extract.ffmpeg_utils import _ffmpeg_path, _run


def _load_manifest(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _find_latest_extract_manifest(runtime_dir: Path) -> Optional[Path]:
    base = runtime_dir / "cache" / "extract"
    if not base.exists():
        return None
    newest: Tuple[float, Optional[Path]] = (0.0, None)
    for child in base.iterdir():
        if not child.is_dir():
            continue
        mp = child / "extract_audio.manifest.json"
        if mp.exists():
            try:
                mt = mp.stat().st_mtime
            except Exception:
                mt = 0.0
            if mt >= newest[0]:
                newest = (mt, mp)
    return newest[1]


def _month_key(dt: Optional[datetime] = None) -> str:
    d = dt or datetime.now(timezone.utc)
    return f"{d.year:04d}-{d.month:02d}"


def _usage_file(runtime_dir: Path) -> Path:
    f = runtime_dir / "cache" / "usage" / "gemini.usage.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    return f


def _load_usage(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_usage(path: Path, data: Dict[str, Any]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        # Non-fatal
        pass


def _sum_planned_minutes(chunks: List[Dict[str, Any]]) -> float:
    total_sec = 0.0
    for ch in chunks:
        dur = ch.get("duration")
        if dur is None:
            try:
                start = float(ch.get("start_sec", 0.0) or 0.0)
                end = float(ch.get("end_sec", start))
                dur = max(0.0, float(end) - float(start))
            except Exception:
                dur = 0.0
        try:
            total_sec += float(dur or 0.0)
        except Exception:
            pass
    return max(0.0, total_sec / 60.0)


# Legacy STT implementation removed — Gemini is the only transcription backend.


def _transcribe_chunk_gemini(
    media_path: str,
    *,
    model: str,
    language: str = "en-US",
    timeout_sec: Optional[float] = None,  # kept for interface parity; not used directly
) -> Tuple[str, List[Dict[str, Any]]]:
    try:
        from google import genai  # type: ignore
    except Exception as e:  # pragma: no cover - import-time error path
        raise ToolError(
            "Gemini client not installed. Install 'google-genai'.",
            tool_name="transcribe_asr",
        ) from e

    try:
        client = genai.Client()
    except Exception as e:
        raise ToolError(f"Failed to initialize Gemini client: {e}", tool_name="transcribe_asr")

    try:
        myfile = client.files.upload(file=media_path)
    except Exception as e:
        raise ToolError(f"Gemini file upload failed: {e}", tool_name="transcribe_asr")

    prompt = (
        f"Transcribe the spoken audio and any visible on-screen text. "
        f"Language: {language}. Do not summarize. Return only the transcript."
    )

    try:
        response = client.models.generate_content(
            model=model,
            contents=[myfile, prompt],
        )
    except Exception as e:
        # Surface raw error; retry logic is handled by caller
        raise ToolError(f"Gemini generate_content failed: {e}", tool_name="transcribe_asr")

    text = (getattr(response, "text", None) or "").strip()
    raw = [{"text": text, "model": model}]
    return text, raw


def _is_transient_quota_error(msg: str) -> bool:
    m = msg.lower()
    return (
        "too many requests" in m
        or "429" in m
        or ("quota" in m and ("exceed" in m or "exceeded" in m or "insufficient" in m))
        or "rate limit" in m
        or "throttle" in m
        or "503" in m
        or "temporar" in m
        or "unavailable" in m
    )


def _is_size_or_timeout_error(msg: str) -> bool:
    m = (msg or "").lower()
    return (
        "too large" in m
        or "payload" in m
        or "request entity too large" in m
        or "413" in m
        or "timeout" in m
        or "timed out" in m
        or "exceeds limit" in m
    )


def _split_media_into_subchunks(
    media_path: str,
    out_dir: Path,
    idx: int,
    total_dur_s: float,
    *,
    sub_dur_s: float = 1200.0,
    overlap_s: float = 1.0,
) -> List[Path]:
    ffmpeg_bin = _ffmpeg_path()
    parts: List[Path] = []
    if total_dur_s <= 0:
        return parts
    t = 0.0
    part = 0
    while t < total_dur_s:
        s_rel = max(0.0, t - (overlap_s if part > 0 else 0.0))
        e_rel = min(total_dur_s, t + sub_dur_s)
        dur = max(0.01, e_rel - s_rel)
        out = out_dir / f"chunk_{idx:04d}_part_{part:04d}.mp4"
        # Try stream copy first
        cmd_copy = [
            ffmpeg_bin,
            "-hide_banner",
            "-ss",
            f"{s_rel:.3f}",
            "-i",
            str(media_path),
            "-t",
            f"{dur:.3f}",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-avoid_negative_ts",
            "make_zero",
            "-y",
            str(out),
        ]
        code, out1, err1 = _run(cmd_copy, timeout=int(max(60, dur * 6)))
        if code != 0 or not out.exists():
            # Fallback to re-encode for this subchunk only
            cmd_enc = [
                ffmpeg_bin,
                "-hide_banner",
                "-ss",
                f"{s_rel:.3f}",
                "-i",
                str(media_path),
                "-t",
                f"{dur:.3f}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                "-y",
                str(out),
            ]
            _run(cmd_enc, timeout=int(max(120, dur * 10)))
        if out.exists():
            parts.append(out)
        part += 1
        t = e_rel
    return parts


def transcribe_task(
    state: AgentState,
    tool_name: str = "transcribe_asr",
    *,
    language: str = "en-US",
    manifest_path: Optional[str] = None,
    model: Optional[str] = None,
    concurrency: Optional[int] = None,
) -> List[Chunk]:
    """
    Transcribe previously extracted chunks using Gemini (video preferred; falls back to audio).

    Example call:

        transcribe_task(
            state,
            tool_name="transcribe_asr",
            language="en-US",
            model="gemini-2.5-flash-lite",
            concurrency=2,
        )

    Args:
        state (AgentState): Agent state; uses extract manifest from artifacts or `manifest_path`.
        tool_name (str): Tool label; default "transcribe_asr".
        language (str): Recognition language code (e.g., "en-US").
        manifest_path (str, optional): Explicit path to an extract manifest JSON.
        Requires GOOGLE_API_KEY in env. Concurrency via GEMINI_CONCURRENCY or `concurrency` arg.

    Returns:
        list[Chunk]: Transcript chunks with text and time bounds. Also updates `state.transcript` and artifacts.

    Raises:
        ToolError: If no manifest is found, credentials are missing, free tier is exceeded, or chunks fail.
    """
    tool = tool_name or "transcribe_asr"

    runtime_dir = getattr(state.config, "runtime_dir", Path("runtime")) if getattr(state, "config", None) else Path("runtime")

    # Resolve manifest path and chunk list
    manifest_p: Optional[Path] = Path(manifest_path).resolve() if manifest_path else None
    if not manifest_p and isinstance(state.artifacts.get("extract_audio"), dict):
        mp = state.artifacts.get("extract_audio", {}).get("manifest_path")
        if mp:
            manifest_p = Path(mp).resolve()
    if not manifest_p:
        manifest_p = _find_latest_extract_manifest(runtime_dir)
    if not manifest_p or not manifest_p.exists():
        raise ToolError("No extract manifest found. Run extract_audio first.", tool_name=tool)

    manifest = _load_manifest(manifest_p)
    chunk_meta = manifest.get("result", {}).get("chunks", [])
    if not chunk_meta:
        # Single-file case: transcribe the whole wav
        wav_path = manifest.get("result", {}).get("wav_path")
        if not wav_path:
            raise ToolError("Manifest missing wav_path.", tool_name=tool)
        chunk_meta = [
            {
                "idx": 0,
                "start_sec": 0.0,
                "end_sec": float(manifest.get("result", {}).get("duration", 0.0) or 0.0),
                "duration": float(manifest.get("result", {}).get("duration", 0.0) or 0.0),
                "path": str(wav_path),
                "sha256": manifest.get("result", {}).get("wav_sha256", ""),
            }
        ]

    out_dir = manifest_p.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # Gemini credentials/model
    if not os.getenv("GOOGLE_API_KEY"):
        raise ToolError("Missing Google API key. Set GOOGLE_API_KEY.", tool_name=tool)
    gemini_model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

    # Usage ledger (non-gating)
    usage_path = _usage_file(runtime_dir)
    usage = _load_usage(usage_path)
    month = _month_key()
    used_min = float(usage.get(month, {}).get("minutes", 0.0)) if isinstance(usage.get(month), dict) else float(usage.get(month, 0.0) or 0.0)
    planned_min = _sum_planned_minutes(chunk_meta)

    # Concurrency + retry settings
    concurrency = int(concurrency or os.getenv("GEMINI_CONCURRENCY", "2") or 2)
    retries = int(os.getenv("GEMINI_RETRIES", "2") or 2)
    backoff_base = float(os.getenv("GEMINI_BACKOFF", "2.0") or 2.0)
    timeout_factor = float(os.getenv("GEMINI_TIMEOUT_FACTOR", "2.0") or 2.0)
    min_timeout = float(os.getenv("GEMINI_TIMEOUT_MIN", "45") or 45)

    chunks_out: List[Chunk] = []
    artifacts: Dict[str, Any] = {
        "manifest_path": str(manifest_p),
        "language": language,
        "gemini_model": gemini_model,
        "chunks": [],
    }

    combined_text_parts: List[str] = []

    def _add_usage(delta_min: float) -> None:
        if delta_min <= 0:
            return
        data = _load_usage(usage_path)
        entry = data.get(month)
        if isinstance(entry, dict):
            entry["minutes"] = float(entry.get("minutes", 0.0)) + float(delta_min)
            data[month] = entry
        elif entry is None:
            data[month] = {"minutes": float(delta_min)}
        else:
            # legacy plain number form
            try:
                data[month] = {"minutes": float(entry) + float(delta_min)}
            except Exception:
                data[month] = {"minutes": float(delta_min)}
        _save_usage(usage_path, data)


    # Prepare work items
    work_items: List[Dict[str, Any]] = []
    for ch in chunk_meta:
        wav_path = ch.get("path")
        video_path = ch.get("video_path")
        media_path = video_path or wav_path
        if not media_path or not Path(media_path).exists():
            raise ToolError(f"Chunk not found: {media_path}", tool_name=tool)
        idx = int(ch.get("idx", 0))
        start_s = float(ch.get("start_sec", 0.0))
        end_s = float(ch.get("end_sec", max(start_s, 0.0)))
        dur_s = max(0.0, end_s - start_s)
        work_items.append({
            "idx": idx,
            "media_path": media_path,
            "start_s": start_s,
            "end_s": end_s,
            "dur_s": dur_s,
        })

    # Concurrency executor
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def do_one(item: Dict[str, Any]) -> Tuple[int, str, List[Dict[str, Any]], Path, Path]:
        idx = item["idx"]
        media_path = item["media_path"]
        dur_s = float(item["dur_s"]) or 0.0
        timeout = max(min_timeout, (dur_s * timeout_factor) + 20.0)

        last_err: Optional[Exception] = None
        fallback_attempted = False
        for attempt in range(retries + 1):
            try:
                # Gemini transcription over video (preferred)
                text, raw = _transcribe_chunk_gemini(
                    media_path,
                    model=gemini_model,
                    language=language,
                    timeout_sec=timeout,
                )
                # Write files per chunk
                txt_path = out_dir / f"chunk_{idx:04d}.gemini.txt"
                json_path = out_dir / f"chunk_{idx:04d}.gemini.json"
                try:
                    with open(txt_path, "w", encoding="utf-8") as f:
                        f.write((text or "").strip() + "\n")
                    with open(json_path, "w", encoding="utf-8") as f:
                        json.dump({"results": raw}, f)
                except Exception:
                    pass
                return idx, text or "", raw, txt_path, json_path
            except ToolError as e:
                msg = str(e)
                last_err = e
                # If size/timeout-related, downshift to 20-min subchunks with small overlap and try once
                if not fallback_attempted and _is_size_or_timeout_error(msg):
                    try:
                        subpaths = _split_media_into_subchunks(media_path, out_dir, idx, dur_s, sub_dur_s=1200.0, overlap_s=1.0)
                        if subpaths:
                            sub_texts: List[str] = []
                            sub_raw: List[Dict[str, Any]] = []
                            for sp in subpaths:
                                t_sub, r_sub = _transcribe_chunk_gemini(str(sp), model=gemini_model, language=language, timeout_sec=max(min_timeout, ((dur_s / max(1, len(subpaths))) * timeout_factor) + 20.0))
                                sub_texts.append(t_sub or "")
                                sub_raw.append({"path": str(sp), "text": t_sub or ""})
                            text = "\n\n".join([t.strip() for t in sub_texts if t])
                            raw = [{"model": gemini_model, "subchunks": sub_raw}]
                            txt_path = out_dir / f"chunk_{idx:04d}.gemini.txt"
                            json_path = out_dir / f"chunk_{idx:04d}.gemini.json"
                            try:
                                with open(txt_path, "w", encoding="utf-8") as f:
                                    f.write((text or "").strip() + "\n")
                                with open(json_path, "w", encoding="utf-8") as f:
                                    json.dump({"results": raw}, f)
                            except Exception:
                                pass
                            return idx, text or "", raw, txt_path, json_path
                    except Exception:
                        # continue to retry/backoff logic
                        pass
                    finally:
                        fallback_attempted = True
                if _is_transient_quota_error(msg) and attempt < retries:
                    backoff = backoff_base * (2 ** attempt)
                    time.sleep(backoff)
                    continue
                raise
            except Exception as e:
                last_err = e
                if attempt < retries:
                    backoff = backoff_base * (2 ** attempt)
                    time.sleep(backoff)
                    continue
                raise ToolError(f"Gemini transcription failed for chunk {idx}: {e}", tool_name=tool)

        # Should not reach here
        raise ToolError(f"Gemini transcription failed for chunk {idx}: {last_err}", tool_name=tool)

    total_success_min = 0.0
    results: List[Tuple[int, str, List[Dict[str, Any]], Path, Path]] = []
    errors: List[Tuple[int, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        future_map = {ex.submit(do_one, it): it for it in work_items}
        for fut in as_completed(future_map):
            it = future_map[fut]
            idx = it["idx"]
            try:
                idx2, text, raw, txt_path, json_path = fut.result()
                # Record artifacts
                artifacts["chunks"].append(
                    {
                        "idx": idx2,
                        "start_sec": it["start_s"],
                        "end_sec": it["end_s"],
                        "text_path": str(txt_path),
                        "json_path": str(json_path),
                        "chars": len(text or ""),
                    }
                )
                total_success_min += float(it["dur_s"]) / 60.0
                results.append((idx2, text, raw, txt_path, json_path))
            except Exception as e:
                errors.append((idx, str(e)))

    if errors:
        errs = "; ".join([f"chunk {i}: {m}" for i, m in errors])
        raise ToolError(f"One or more chunks failed: {errs}", tool_name=tool)

    # Build combined transcript and state in index order
    results.sort(key=lambda r: r[0])
    idx_to_bounds = {it["idx"]: (it["start_s"], it["end_s"]) for it in work_items}
    for idx, text, raw, _, _ in results:
        s, e = idx_to_bounds.get(idx, (0, 0))
        combined_text_parts.append(text.strip() if text else "")
        chunks_out.append(Chunk(start_s=int(s), end_s=int(e), text=text or ""))

    # Update usage ledger once per run
    _add_usage(total_success_min)

    combined_text = "\n\n".join([p for p in combined_text_parts if p])

    # Persist combined transcript
    all_txt_path = out_dir / "transcript.gemini.txt"
    try:
        with open(all_txt_path, "w", encoding="utf-8") as f:
            f.write(combined_text.strip() + "\n")
        artifacts["combined_transcript_path"] = str(all_txt_path)
    except Exception:
        pass

    # Update state
    state.chunks = chunks_out
    state.transcript = combined_text
    state.artifacts[tool] = artifacts

    return chunks_out
