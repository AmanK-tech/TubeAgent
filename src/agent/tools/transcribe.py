from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.core.state import AgentState, Chunk
from agent.errors import ToolError


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


def _ticks_to_seconds(value: Any) -> float:
    # Azure returns 100-nanosecond ticks (int) for offset/duration.
    try:
        if hasattr(value, "total_seconds"):
            return float(value.total_seconds())  # datetime.timedelta
        v = float(value)
        # 10,000,000 ticks per second
        return v / 10_000_000.0
    except Exception:
        return 0.0


def _month_key(dt: Optional[datetime] = None) -> str:
    d = dt or datetime.now(timezone.utc)
    return f"{d.year:04d}-{d.month:02d}"


def _usage_file(runtime_dir: Path) -> Path:
    f = runtime_dir / "cache" / "usage" / "azure_speech.usage.json"
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


def _transcribe_chunk_azure(
    wav_path: str,
    *,
    subscription_key: str,
    region: Optional[str] = None,
    endpoint: Optional[str] = None,
    language: str = "en-US",
    timeout_sec: Optional[float] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    try:
        import azure.cognitiveservices.speech as speechsdk  # type: ignore
    except Exception as e:  # pragma: no cover - import-time error path
        raise ToolError(
            "Azure Speech SDK not installed. Install 'azure-cognitiveservices-speech'.",
            tool_name="transcribe_asr",
        ) from e

    if endpoint:
        speech_config = speechsdk.SpeechConfig(subscription=subscription_key, endpoint=endpoint)
    else:
        if not region:
            raise ToolError("Azure region must be provided if endpoint is not set.", tool_name="transcribe_asr")
        speech_config = speechsdk.SpeechConfig(subscription=subscription_key, region=region)

    speech_config.speech_recognition_language = language

    audio_config = speechsdk.audio.AudioConfig(filename=wav_path)
    recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)

    results: List[Dict[str, Any]] = []
    done = threading.Event()
    cancel_info: Dict[str, Any] = {"error_code": None, "error_details": "", "reason": ""}

    def recognized_cb(evt):
        try:
            r = evt.result
            if r.reason == speechsdk.ResultReason.RecognizedSpeech:
                results.append(
                    {
                        "text": r.text or "",
                        "offset_sec": _ticks_to_seconds(getattr(r, "offset", 0)),
                        "duration_sec": _ticks_to_seconds(getattr(r, "duration", 0)),
                    }
                )
        except Exception:
            # Swallow individual record errors; continue transcription
            pass

    def canceled_cb(evt):
        try:
            cancel_info["reason"] = str(getattr(evt, "reason", ""))
            # Try to resolve detailed error and code
            try:
                det = speechsdk.CancellationDetails.from_result(evt.result)
                cancel_info["error_code"] = getattr(det, "error_code", None)
                cancel_info["error_details"] = getattr(det, "error_details", "")
            except Exception:
                cancel_info["error_details"] = str(getattr(evt, "error_details", ""))
        finally:
            try:
                recognizer.stop_continuous_recognition()
            finally:
                done.set()

    def stop_cb(evt):  # noqa: ARG001 - callback signature
        try:
            recognizer.stop_continuous_recognition()
        finally:
            done.set()

    recognizer.recognized.connect(recognized_cb)  # type: ignore[attr-defined]
    recognizer.canceled.connect(canceled_cb)  # type: ignore[attr-defined]
    recognizer.session_stopped.connect(stop_cb)  # type: ignore[attr-defined]

    recognizer.start_continuous_recognition()
    if timeout_sec and timeout_sec > 0:
        completed = done.wait(timeout=timeout_sec)
        if not completed:
            try:
                recognizer.stop_continuous_recognition()
            except Exception:
                pass
            raise ToolError(
                f"Azure transcription timed out after {timeout_sec:.1f}s",
                tool_name="transcribe_asr",
            )
    else:
        done.wait()

    # Handle quota/limit errors explicitly
    details = (cancel_info.get("error_details") or "").lower()
    code = str(cancel_info.get("error_code") or "")
    if not results and (
        "quota" in details
        or "exceed" in details
        or "too many requests" in details
        or "insufficient" in details and "quota" in details
        or code.upper() in {"TOO_MANY_REQUESTS", "SERVICE_ERROR"}
    ):
        raise ToolError(
            f"Azure Speech returned a quota/limit error: {cancel_info.get('error_details') or cancel_info.get('reason')}",
            tool_name="transcribe_asr",
        )

    transcript_text = " ".join(r.get("text", "").strip() for r in results if r.get("text"))
    return transcript_text.strip(), results


def _is_transient_quota_error(msg: str) -> bool:
    m = msg.lower()
    return (
        "too many requests" in m
        or "429" in m
        or ("quota" in m and ("exceed" in m or "exceeded" in m or "insufficient" in m))
        or "rate limit" in m
        or "throttle" in m
    )


def transcribe_task(
    state: AgentState,
    tool_name: str = "transcribe_asr",
    *,
    language: str = "en-US",
    manifest_path: Optional[str] = None,
    azure_key: Optional[str] = None,
    azure_region: Optional[str] = None,
    azure_endpoint: Optional[str] = None,
    azure_concurrency: Optional[int] = None,
) -> List[Chunk]:
    """
    Transcribe previously extracted audio chunks using Azure Speech‑to‑Text.

    Example call:

        transcribe_task(
            state,
            tool_name="transcribe_asr",
            language="en-US",
            azure_key="<KEY>",
            azure_region="eastus",
        )

    Args:
        state (AgentState): Agent state; uses extract manifest from artifacts or `manifest_path`.
        tool_name (str): Tool label; default "transcribe_asr".
        language (str): Recognition language code (e.g., "en-US").
        manifest_path (str, optional): Explicit path to an extract manifest JSON.
        azure_key (str, optional): Azure Speech key; falls back to AZURE_SPEECH_KEY env.
        azure_region (str, optional): Azure region; required if no endpoint. Falls back to AZURE_SPEECH_REGION.
        azure_endpoint (str, optional): Full endpoint URL; overrides region.
        azure_concurrency (int, optional): Parallel recognizers; defaults from AZURE_SPEECH_CONCURRENCY or 2.

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

    # Azure credentials from args or environment
    key = azure_key or os.getenv("AZURE_SPEECH_KEY") or os.getenv("AZURE_COGNITIVE_SERVICE_KEY")
    if not key:
        raise ToolError("Missing Azure key. Set AZURE_SPEECH_KEY.", tool_name=tool)
    region = azure_region or os.getenv("AZURE_SPEECH_REGION") or os.getenv("AZURE_REGION")
    endpoint = azure_endpoint or os.getenv("AZURE_SPEECH_ENDPOINT")

    # --- Free tier limit guard -------------------------------------------------
    # Default free tier allowance is typically ~300 minutes/month. Allow override via env.
    limit_min_env = os.getenv("AZURE_SPEECH_FREE_LIMIT_MINUTES")
    limit_hr_env = os.getenv("AZURE_SPEECH_FREE_LIMIT_HOURS")
    try:
        free_limit_min = float(limit_min_env) if limit_min_env is not None else (float(limit_hr_env) * 60.0 if limit_hr_env is not None else 300.0)
    except Exception:
        free_limit_min = 300.0

    usage_path = _usage_file(runtime_dir)
    usage = _load_usage(usage_path)
    month = _month_key()
    used_min = float(usage.get(month, {}).get("minutes", 0.0)) if isinstance(usage.get(month), dict) else float(usage.get(month, 0.0) or 0.0)

    planned_min = _sum_planned_minutes(chunk_meta)
    if used_min >= free_limit_min:
        raise ToolError(
            f"Azure Speech free tier limit reached: used {used_min:.1f} min of {free_limit_min:.0f} min this month.",
            tool_name=tool,
        )
    if used_min + planned_min > free_limit_min:
        remaining = max(0.0, free_limit_min - used_min)
        raise ToolError(
            f"Transcription would exceed free tier: planned {planned_min:.1f} min, remaining {remaining:.1f} min, limit {free_limit_min:.0f} min.",
            tool_name=tool,
        )

    # Concurrency + retry settings
    concurrency = int(azure_concurrency or int(os.getenv("AZURE_SPEECH_CONCURRENCY", "2") or 2))
    retries = int(os.getenv("AZURE_SPEECH_RETRIES", "2") or 2)
    backoff_base = float(os.getenv("AZURE_SPEECH_BACKOFF", "2.0") or 2.0)
    timeout_factor = float(os.getenv("AZURE_SPEECH_TIMEOUT_FACTOR", "2.0") or 2.0)
    min_timeout = float(os.getenv("AZURE_SPEECH_TIMEOUT_MIN", "45") or 45)

    chunks_out: List[Chunk] = []
    artifacts: Dict[str, Any] = {
        "manifest_path": str(manifest_p),
        "language": language,
        "azure_region": region,
        "azure_endpoint": endpoint,
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
        if not wav_path or not Path(wav_path).exists():
            raise ToolError(f"Chunk not found: {wav_path}", tool_name=tool)
        idx = int(ch.get("idx", 0))
        start_s = float(ch.get("start_sec", 0.0))
        end_s = float(ch.get("end_sec", max(start_s, 0.0)))
        dur_s = max(0.0, end_s - start_s)
        work_items.append({
            "idx": idx,
            "wav_path": wav_path,
            "start_s": start_s,
            "end_s": end_s,
            "dur_s": dur_s,
        })

    # Concurrency executor
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def do_one(item: Dict[str, Any]) -> Tuple[int, str, List[Dict[str, Any]], Path, Path]:
        idx = item["idx"]
        wav_path = item["wav_path"]
        dur_s = float(item["dur_s"]) or 0.0
        timeout = max(min_timeout, (dur_s * timeout_factor) + 20.0)

        last_err: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                text, raw = _transcribe_chunk_azure(
                    wav_path,
                    subscription_key=key,
                    region=region,
                    endpoint=endpoint,
                    language=language,
                    timeout_sec=timeout,
                )
                # Write files per chunk
                txt_path = out_dir / f"chunk_{idx:04d}.azure.txt"
                json_path = out_dir / f"chunk_{idx:04d}.azure.json"
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
                raise ToolError(f"Azure transcription failed for chunk {idx}: {e}", tool_name=tool)

        # Should not reach here
        raise ToolError(f"Azure transcription failed for chunk {idx}: {last_err}", tool_name=tool)

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
    all_txt_path = out_dir / "transcript.azure.txt"
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
