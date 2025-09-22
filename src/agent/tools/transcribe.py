from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.core.state import AgentState, Chunk
from agent.errors import ToolError

try:
    from google import genai  
except Exception:  
    genai = None  


def _load_manifest(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _find_latest_extract_manifest(runtime_dir: Path) -> Optional[Path]:
    base = runtime_dir / "cache" / "extract"
    if not base.exists():
        return None
    newest: tuple[float, Optional[Path]] = (0.0, None)
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


def _poll_file_active(client, name: str, max_wait: float = 300.0) -> bool:
    t0 = time.time()
    while (time.time() - t0) < max_wait:
        try:
            f = client.files.get(name=name)
            state = getattr(f, "state", None)
            state_name = getattr(state, "name", None) or str(state or "").upper()
            if state_name == "ACTIVE":
                return True
            if state_name == "FAILED":
                return False
        except Exception:
            # transient error, keep polling
            pass
        time.sleep(2)
    return False


def _load_prompt_text(filename: str) -> str:
    """Load a prompt text from package resources with robust fallback."""
    try:
        from importlib.resources import files as _res_files  # Python 3.9+
        return (_res_files("agent") / "prompts" / filename).read_text(encoding="utf-8")
    except Exception:
        try:
            agent_dir = Path(__file__).resolve().parents[1]
            return (agent_dir / "prompts" / filename).read_text(encoding="utf-8")
        except Exception:
            return ""


def _fmt_ts(seconds: float | int | None) -> str:
    try:
        total = int(float(seconds or 0))
    except Exception:
        total = 0
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"


def _init_gemini_client(tool_name: str):
    if genai is None:  # type: ignore
        raise ToolError("Missing dependency: google-genai", tool_name=tool_name)
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ToolError("Missing Google API key. Set GOOGLE_API_KEY.", tool_name=tool_name)
    return genai.Client(api_key=api_key)


def _split_transcript_summary(text: str) -> tuple[str, str]:
    """Best-effort splitter for combined transcript+summary response.

    Expects the model to return the exact delimiters we requested. Falls back to
    heuristic splits if not found.
    """
    if not text:
        return "", ""
    lo = text.lower()
    start_t = lo.find("<transcript>")
    end_t = lo.find("</transcript>")
    start_s = lo.find("<summary>")
    end_s = lo.find("</summary>")
    if start_t != -1 and end_t != -1:
        t_body = text[start_t + len("<TRANSCRIPT>"): end_t]
    else:
        # Heuristic: take before a 'summary' marker if present
        if start_s != -1:
            t_body = text[: start_s]
        else:
            t_body = text
    if start_s != -1 and end_s != -1:
        s_body = text[start_s + len("<SUMMARY>"): end_s]
    else:
        # Heuristic: try to find a 'summary' section heading
        for marker in ("summary:", "tl;dr", "key points:"):
            pos = lo.rfind(marker)
            if pos != -1:
                s_body = text[pos:]
                break
        else:
            s_body = ""
    return t_body.strip(), s_body.strip()


def transcribe_task(
    state: AgentState,
    tool_name: str = "transcribe_asr",
    *,
    manifest_path: Optional[str] = None,
    model: Optional[str] = None,
    concurrency: Optional[int] = None,
) -> List[Chunk]:
    """
    Minimal Gemini transcription: upload each chunk, wait for ACTIVE, then
    call generate_content. Keeps the repo's tool API and artifacts.
    """
    tool = tool_name or "transcribe_asr"
    runtime_dir = getattr(state.config, "runtime_dir", Path("runtime")) if getattr(state, "config", None) else Path("runtime")

    # Resolve manifest
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
    chunks_meta = manifest.get("result", {}).get("chunks", [])
    if not chunks_meta:
        # Single-file fallback
        wav_path = manifest.get("result", {}).get("wav_path")
        dur = float(manifest.get("result", {}).get("duration", 0.0) or 0.0)
        if not wav_path:
            raise ToolError("Manifest missing wav_path.", tool_name=tool)
        chunks_meta = [{"idx": 0, "start_sec": 0.0, "end_sec": dur, "path": str(wav_path)}]

    # Client and model
    client = _init_gemini_client(tool)
    gemini_model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    out_dir = manifest_p.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    combined_parts: List[str] = []
    chunk_results: List[Chunk] = []
    artifacts: Dict[str, Any] = {"manifest_path": str(manifest_p), "gemini_model": gemini_model, "chunks": []}

    # Process chunks sequentially (keep simple; concurrency possible later)
    for ch in chunks_meta:
        idx = int(ch.get("idx", 0))
        start_s = float(ch.get("start_sec", 0.0))
        end_s = float(ch.get("end_sec", max(start_s, 0.0)))
        media_path = ch.get("video_path") or ch.get("path")
        if not media_path or not Path(media_path).exists():
            raise ToolError(f"Chunk not found: {media_path}", tool_name=tool)

        # Upload
        try:
            myfile = client.files.upload(file=str(media_path))
        except Exception as e:
            raise ToolError(f"Gemini file upload failed for chunk {idx}: {e}", tool_name=tool)

        # Poll until ACTIVE
        max_wait = float(os.getenv("GEMINI_FILE_WAIT_TIMEOUT", "300"))
        ok = _poll_file_active(
            client,
            name=getattr(myfile, "name", None) or getattr(myfile, "id", None) or str(myfile),
            max_wait=max_wait,
        )
        if not ok:
            raise ToolError(f"Gemini file did not become ACTIVE within {int(max_wait)}s (chunk {idx}).", tool_name=tool)

        # Transcribe + Summarize (single call, structured output)
        # Strong delimiters for robust parsing
        prompt = (
            "You are transcribing one media chunk and then summarizing it.\n"
            f"Chunk bounds: start={_fmt_ts(start_s)}, end={_fmt_ts(end_s)}.\n"
            "Return output using EXACT delimiters below.\n\n"
            "<TRANSCRIPT>\n"
            "Transcribe all spoken words and clearly readable on-screen text.\n"
            "Do not include commentary.\n"
            "</TRANSCRIPT>\n\n"
            "<SUMMARY>\n"
            "Provide a concise, faithful summary (5-10 bullets).\n"
            "Use only information present in this chunk.\n"
            "</SUMMARY>\n"
        )
        try:
            response = client.models.generate_content(
                model=gemini_model,
                contents=[myfile, prompt],
            )
        except Exception as e:
            raise ToolError(f"Gemini generate_content failed for chunk {idx}: {e}", tool_name=tool)

        full_text = (getattr(response, "text", None) or "").strip()
        transcript_text, summary_text = _split_transcript_summary(full_text)
        text = transcript_text

        # Write artifacts per chunk
        txt_path = out_dir / f"chunk_{idx:04d}.gemini.txt"
        sum_path = out_dir / f"chunk_{idx:04d}.summary.txt"
        json_path = out_dir / f"chunk_{idx:04d}.gemini.json"
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            with open(sum_path, "w", encoding="utf-8") as f:
                f.write((summary_text or "").strip() + "\n")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "model": gemini_model,
                        "file": str(media_path),
                        "gemini_file_name": getattr(myfile, "name", None) or getattr(myfile, "id", None),
                        "text": text,
                        "summary": summary_text,
                    },
                    f,
                )
        except Exception:
            pass

        combined_parts.append(text)
        chunk_results.append(Chunk(start_s=int(start_s), end_s=int(end_s), text=text, summary=(summary_text or None)))
        artifacts["chunks"].append(
            {
                "idx": idx,
                "start_sec": start_s,
                "end_sec": end_s,
                "video_path": media_path,
                "text_path": str(txt_path),
                "json_path": str(json_path),
                "summary_path": str(sum_path),
                "gemini_file_name": getattr(myfile, "name", None) or getattr(myfile, "id", None),
                "chars": len(text),
                "summary_chars": len(summary_text or ""),
            }
        )

    # Combined transcript
    combined_text = "\n\n".join([p for p in combined_parts if p])
    all_txt_path = out_dir / "transcript.gemini.txt"
    try:
        with open(all_txt_path, "w", encoding="utf-8") as f:
            f.write(combined_text.strip() + "\n")
        artifacts["combined_transcript_path"] = str(all_txt_path)
    except Exception:
        pass

    # Update state
    state.chunks = chunk_results
    state.transcript = combined_text
    state.artifacts[tool] = artifacts

    return chunk_results


def summarise_gemini(
    state: AgentState, user_req: str, intent: Optional[str] = None, include_metadata: Optional[bool] = False
) -> str:
    """
    Produce a final deliverable by synthesizing across transcript chunks using Gemini.

    Strategy:
      - If total duration ≤ GLOBAL_DIRECT_MINUTES_LIMIT (default 20), do a direct
        multimodal call over Gemini file handles (avoid re-upload).
      - Else, compose a map-reduce style prompt from per-chunk summaries and short
        excerpts, and make a single global call.
    """
    tool_name = "summarise_global"
    client = _init_gemini_client(tool_name)
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    # Discover transcribe artifacts
    ta = state.artifacts.get("transcribe_asr", {}) if isinstance(state.artifacts.get("transcribe_asr"), dict) else {}
    manifest_path = ta.get("manifest_path")
    out_dir = Path(manifest_path).parent if manifest_path else (getattr(state.config, "runtime_dir", Path("runtime")) / "cache" / "extract")
    ta_chunks = ta.get("chunks", []) if isinstance(ta.get("chunks"), list) else []
    if not ta_chunks:
        raise ToolError("No transcription artifacts found. Run transcribe_asr first.", tool_name=tool_name)

    chunks_with_paths: List[Dict[str, Any]] = []
    total_duration_s = 0.0
    total_chars = 0
    for ent in ta_chunks:
        try:
            start_s = float(ent.get("start_sec", 0.0) or 0.0)
            end_s = float(ent.get("end_sec", 0.0) or 0.0)
        except Exception:
            start_s, end_s = 0.0, 0.0
        text_path = ent.get("text_path")
        summary_path = ent.get("summary_path")
        video_path = ent.get("video_path") or ent.get("path")
        gem_file = ent.get("gemini_file_name")
        text = ""
        try:
            if text_path:
                text = Path(text_path).read_text(encoding="utf-8")
        except Exception:
            text = ""
        summary_text = ""
        try:
            if summary_path and Path(summary_path).exists():
                summary_text = Path(summary_path).read_text(encoding="utf-8").strip()
        except Exception:
            summary_text = ""
        chunks_with_paths.append(
            {
                "idx": int(ent.get("idx", len(chunks_with_paths))),
                "start_s": start_s,
                "end_s": end_s,
                "path": video_path,
                "gemini_file_name": gem_file,
                "text": text,
                "summary": summary_text,
            }
        )
        total_duration_s = max(total_duration_s, end_s)
        total_chars += len(text)

    minutes_limit = 20.0
    try:
        minutes_limit = float(os.getenv("GLOBAL_DIRECT_MINUTES_LIMIT", "20.0") or 20.0)
    except Exception:
        pass

    # Prepare optional metadata string
    meta_lines: List[str] = []
    if include_metadata:
        try:
            vid = getattr(state, "video", None)
            art = (getattr(state, "artifacts", {}) or {}).get("fetch_task", {})
            chan = art.get("channel") or art.get("uploader")
            if getattr(vid, "title", None):
                meta_lines.append(f"Title: {vid.title}")
            if chan:
                meta_lines.append(f"Channel: {chan}")
            if getattr(vid, "source_url", None):
                meta_lines.append(f"URL: {vid.source_url}")
        except Exception:
            pass
    meta_text = "\n".join(meta_lines) if meta_lines else ""

    total_minutes = float(total_duration_s) / 60.0 if total_duration_s else 0.0

    def _direct_multimodal() -> str:
        system_instruction = _load_prompt_text("global_prompt.txt")
        contents: List[Any] = [system_instruction]
        meta_block = f"Video metadata:\n{meta_text}\n\n" if meta_text else ""
        req_with_intent = f"{user_req}\n\nIntent: {intent.strip()}" if intent and isinstance(intent, str) and intent.strip() else user_req
        user_prompt_text = (
            f"User request:\n{req_with_intent}\n\n"
            f"{meta_block}"
            "Based on the following media files (chunks), provide a comprehensive, grounded response."
        )
        contents.append(user_prompt_text)

        # Attach files via existing Gemini file names if possible; otherwise re-upload as fallback
        for ch in chunks_with_paths:
            mf = None
            if ch.get("gemini_file_name"):
                try:
                    mf = client.files.get(name=ch["gemini_file_name"])  # type: ignore
                except Exception:
                    mf = None
            if mf is None:
                p = ch.get("path")
                if p and Path(p).exists():
                    try:
                        mf = client.files.upload(file=str(p))
                        _ = _poll_file_active(client, name=getattr(mf, "name", None) or getattr(mf, "id", None) or str(mf), max_wait=float(os.getenv("GEMINI_FILE_WAIT_TIMEOUT", "300")))
                    except Exception:
                        mf = None
            if mf is not None:
                contents.append(mf)

        response = client.models.generate_content(model=model, contents=contents, request_options={"timeout": 600})
        return getattr(response, "text", None) or ""

    def _map_reduce() -> str:
        system_instruction = _load_prompt_text("global_prompt.txt")
        header = [f"User request:\n{user_req}"]
        if intent and isinstance(intent, str) and intent.strip():
            header.append(f"Intent: {intent.strip()}")
        if meta_text:
            header.append(f"\nVideo metadata:\n{meta_text}")
        header.append("\nBelow are per-chunk summaries and short raw excerpts. Synthesize them into a single, coherent response.")
        header.append("CHUNKS:")

        parts: List[str] = []
        excerpt_len = 400
        try:
            excerpt_len = int(os.getenv("GLOBAL_EXCERPT_CHARS", "400") or 400)
        except Exception:
            pass
        for ch in chunks_with_paths:
            excerpt = (ch.get("text") or "")[: max(0, excerpt_len)]
            parts.append(
                (
                    f"---\n"
                    f"Chunk {ch['idx']} [{_fmt_ts(ch['start_s'])} - {_fmt_ts(ch['end_s'])}]\n"
                    f"Summary of this chunk:\n{(ch.get('summary') or '').strip()}\n\n"
                    f"Transcript excerpt:\n{excerpt.strip()}\n"
                )
            )
        content_text = "\n".join(header + parts)
        response = client.models.generate_content(model=model, contents=[system_instruction, content_text])
        return getattr(response, "text", None) or ""

    # Try direct multimodal for short videos
    result_text = ""
    if total_minutes <= minutes_limit:
        try:
            result_text = _direct_multimodal()
            state.artifacts.setdefault(tool_name, {})
            state.artifacts[tool_name].update(
                {
                    "approach": "direct_multimodal",
                    "chunks_used": len(chunks_with_paths),
                    "result_chars": len(result_text or ""),
                    "duration_minutes": total_minutes,
                }
            )
        except Exception as e:
            msg = str(e).lower()
            if not any(k in msg for k in ["context", "token", "length", "too large", "deadline", "quota", "rate", "503", "429"]):
                raise
            # Fall back

    # Fallback or long videos: map-reduce
    if not result_text:
        result_text = _map_reduce()
        state.artifacts.setdefault(tool_name, {})
        state.artifacts[tool_name].update(
            {
                "approach": "chunk_by_chunk",
                "chunks_used": len(chunks_with_paths),
                "result_chars": len(result_text or ""),
                "duration_minutes": total_minutes,
                "intent": intent,
            }
        )

    # Persist global summary next to chunk artifacts when possible
    try:
        if out_dir:
            gp = Path(out_dir) / "global_summary.gemini.txt"
            gp.write_text((result_text or "").strip() + "\n", encoding="utf-8")
            state.artifacts.setdefault(tool_name, {})
            state.artifacts[tool_name]["global_summary_path"] = str(gp)
    except Exception:
        pass

    return result_text
