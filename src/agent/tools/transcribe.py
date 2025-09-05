from __future__ import annotations

import json
import os
import threading
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


def transcribe_task(
    state: AgentState,
    tool_name: str = "transcribe_asr",
    *,
    language: str = "en-US",
    manifest_path: Optional[str] = None,
    azure_key: Optional[str] = None,
    azure_region: Optional[str] = None,
    azure_endpoint: Optional[str] = None,
) -> List[Chunk]:
    """
    Transcribe previously extracted audio chunks using Azure Speech-to-Text.

    - Expects chunk info from the extract manifest referenced in state.artifacts["extract_audio"],
      or the latest manifest under runtime/cache/extract, or an explicit manifest_path.
    - Writes per-chunk transcripts and a combined transcript alongside the manifest.
    - Updates state.chunks, state.transcript, and state.artifacts[tool_name].
    - Raises ToolError on failures.
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

    for ch in chunk_meta:
        wav_path = ch.get("path")
        if not wav_path or not Path(wav_path).exists():
            raise ToolError(f"Chunk not found: {wav_path}", tool_name=tool)
        idx = int(ch.get("idx", 0))
        start_s = float(ch.get("start_sec", 0.0))
        end_s = float(ch.get("end_sec", max(start_s, 0.0)))
        duration_min = max(0.0, (end_s - start_s) / 60.0)

        try:
            text, raw = _transcribe_chunk_azure(
                wav_path,
                subscription_key=key,
                region=region,
                endpoint=endpoint,
                language=language,
            )
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"Azure transcription failed for chunk {idx}: {e}", tool_name=tool)

        # Persist per-chunk transcript and raw events
        txt_path = out_dir / f"chunk_{idx:04d}.azure.txt"
        json_path = out_dir / f"chunk_{idx:04d}.azure.json"
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write((text or "").strip() + "\n")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump({"results": raw}, f)
        except Exception:
            # Do not fail the whole run on IO errors; continue
            pass

        combined_text_parts.append(text.strip() if text else "")
        chunks_out.append(Chunk(start_s=int(start_s), end_s=int(end_s), text=text or ""))
        artifacts["chunks"].append(
            {
                "idx": idx,
                "start_sec": start_s,
                "end_sec": end_s,
                "text_path": str(txt_path),
                "json_path": str(json_path),
                "chars": len(text or ""),
            }
        )

        # Update usage ledger per successful chunk
        _add_usage(duration_min)

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
