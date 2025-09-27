from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.core.state import AgentState, Chunk
from agent.errors import ToolError
from agent.contextengineering import allocate_tokens, to_generation_config

try:
    from google import genai
    from google.genai import types as genai_types  # type: ignore
except Exception:  # pragma: no cover
    genai = None
    genai_types = None  # type: ignore


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


def _poll_file_active(client, name: str, max_wait: float = 300.0, poll_interval: Optional[float] = None) -> bool:
    """Poll a Gemini file handle until it becomes ACTIVE or FAILED.

    Respects GEMINI_FILE_POLL_INTERVAL (seconds) if provided; defaults to 2s.
    """
    if poll_interval is None:
        try:
            poll_interval = float(os.getenv("GEMINI_FILE_POLL_INTERVAL", "2.0") or 2.0)
        except Exception:
            poll_interval = 2.0
    if poll_interval <= 0:
        poll_interval = 2.0

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
        time.sleep(poll_interval)
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
    # Debug: show resolved locations for manifest discovery
    try:
        print("CWD=", Path.cwd().resolve(), "runtime_dir=", Path(runtime_dir).resolve())
    except Exception:
        pass
    
    # ... inside transcribe_task, before the manifest search logic ...
    print("Pausing for 1 second to wait for manifest file...")
    time.sleep(1)

    # Now, continue with the manifest discovery logic
    manifest_p: Optional[Path] = Path(manifest_path).resolve() if manifest_path else None
    
    # Resolve manifest
    if not manifest_p and isinstance(state.artifacts.get("extract_audio"), dict):
        mp = state.artifacts.get("extract_audio", {}).get("manifest_path")
        if mp:
            manifest_p = Path(mp).resolve()
    if not manifest_p:
        manifest_p = _find_latest_extract_manifest(runtime_dir)
    if not manifest_p or not manifest_p.exists():
        raise ToolError("No extract manifest found. Run extract_audio first.", tool_name=tool)

    manifest = _load_manifest(manifest_p)
    res = manifest.get("result", {})
    chunks_meta = res.get("chunks", [])
    if not chunks_meta:
        # Single-file fallback
        wav_path = manifest.get("result", {}).get("wav_path")
        dur = float(manifest.get("result", {}).get("duration", 0.0) or 0.0)
        if not wav_path:
            raise ToolError("Manifest missing wav_path.", tool_name=tool)
        chunks_meta = [{"idx": 0, "start_sec": 0.0, "end_sec": dur, "path": str(wav_path)}]

    # Determine media preference: use audio-only (WAV) for very long videos
    try:
        total_duration_s = float(res.get("duration", 0.0) or 0.0)
    except Exception:
        # Fallback to max chunk end if duration missing
        try:
            total_duration_s = max(float(ch.get("end_sec", 0.0) or 0.0) for ch in chunks_meta) if chunks_meta else 0.0
        except Exception:
            total_duration_s = 0.0
    try:
        audio_only_minutes = float(os.getenv("ASR_AUDIO_ONLY_MINUTES", "60") or 60)
    except Exception:
        audio_only_minutes = 60.0
    prefer_wav = total_duration_s >= (audio_only_minutes * 60.0)

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
        media_path = (ch.get("path") if prefer_wav else (ch.get("video_path") or ch.get("path")))
        if not media_path or not Path(media_path).exists():
            raise ToolError(f"Chunk not found: {media_path}", tool_name=tool)

        # Upload
        try:
            myfile = client.files.upload(file=str(media_path))
        except Exception as e:
            raise ToolError(f"Gemini file upload failed for chunk {idx}: {e}", tool_name=tool)

        # Poll until ACTIVE (early check on immediate state)
        max_wait = float(os.getenv("GEMINI_FILE_WAIT_TIMEOUT", "300"))
        state0 = getattr(myfile, "state", None)
        state0_name = getattr(state0, "name", None) or str(state0 or "").upper()
        if state0_name == "ACTIVE":
            ok = True
        elif state0_name == "FAILED":
            ok = False
        else:
            ok = _poll_file_active(
                client,
                name=getattr(myfile, "name", None) or getattr(myfile, "id", None) or str(myfile),
                max_wait=max_wait,
            )
        if not ok:
            raise ToolError(f"Gemini file did not become ACTIVE within {int(max_wait)}s (chunk {idx}).", tool_name=tool)

        # Transcribe + Summarize (single call, structured output)
        # Inline prompt with exact delimiters as required by downstream parsing
        prompt = (
            "You are an expert transcriptionist and content analyst processing a media segment.\n"
            f"Segment bounds: start={_fmt_ts(start_s)}, end={_fmt_ts(end_s)}.\n\n"
            "TRANSCRIPTION REQUIREMENTS:\n\n"
            "SPEECH & AUDIO:\n"
            "• Transcribe ALL spoken words with maximum accuracy\n"
            "• Preserve natural speech patterns: \"um\", \"uh\", false starts, repetitions, interruptions\n"
            "• Use clear speaker labels: \"Speaker 1:\", \"Host:\", \"Interviewer:\", \"Guest:\", etc.\n"
            "• Mark unclear speech: [inaudible], [unclear], [mumbled]\n"
            "• Note overlapping speech: [Speaker 1 & 2 speaking simultaneously]\n"
            "• Include meaningful pauses: [pause], [long pause]\n"
            "• Capture relevant audio cues: [music], [applause], [phone ringing], [door closing]\n"
            "• Note audio quality issues: [low quality], [distorted], [echo], [static]\n\n"
            "VISUAL ELEMENTS (for video content):\n"
            "• Transcribe ALL readable on-screen text, captions, titles, signs, documents\n"
            "• Format as [SCREEN TEXT: \"content\"], [CAPTION: \"subtitle\"], [SIGN: \"exit\"]\n"
            "• Include UI elements: [BUTTON: \"Submit\"], [MENU: \"File > Save\"]\n"
            "• Note visual context: [showing graph], [pointing to screen], [slide change]\n"
            "• Capture timestamps, URLs, phone numbers if clearly visible\n"
            "• Include title cards, lower thirds, and visual overlays\n\n"
            "CONTENT ORGANIZATION:\n"
            "• Maintain chronological order within the segment\n"
            "• Use natural paragraph breaks for topic shifts\n"
            "• Preserve conversational flow and turn-taking\n"
            "• Include relevant context for references (\"as mentioned earlier\", \"this chart shows\")\n\n"
            "ACCURACY STANDARDS:\n"
            "• Prioritize precision over interpretation\n"
            "• Do not add commentary, analysis, or speculation\n"
            "• Include exact quotes, especially for important statements\n"
            "• Note when content seems incomplete or cut off\n"
            "• Mark technical terms, names, or specialized vocabulary carefully\n\n"
            "ERROR HANDLING:\n"
            "• Use [inaudible] for speech that cannot be understood\n"
            "• Use [unclear: possible word] for best-guess transcription\n"
            "• Note [audio cuts out] or [video freezes] for technical issues\n"
            "• Mark [multiple speakers - unclear] when speaker separation is impossible\n\n"
            "OUTPUT FORMAT - Use these EXACT delimiters:\n\n"
            "<TRANSCRIPT>\n"
            "Provide the complete, accurate transcription here.\n\n"
            "Include all speech, on-screen text, and relevant audio/visual cues.\n"
            "Use proper punctuation, capitalization, and speaker labels.\n"
            "Maintain natural flow while being comprehensive.\n"
            "No commentary or interpretation - only what is actually present.\n"
            "</TRANSCRIPT>\n\n"
            "<SUMMARY>\n"
            "Provide 4-8 concise bullet points covering:\n"
            "• Primary topics, themes, or subjects discussed\n"
            "• Key information, facts, decisions, or announcements\n"
            "• Important speakers, participants, or sources mentioned\n"
            "• Significant events, changes, or developments in this segment\n"
            "• Critical visual information (charts, documents, demonstrations)\n"
            "• Notable quotes or statements (if particularly important)\n"
            "• Context that connects to likely adjacent segments\n\n"
            "Base summary only on content actually present in this specific segment.\n"
            "Use clear, factual language without speculation or interpretation.\n"
            "Do not include timestamps in the SUMMARY.\n"
            "</SUMMARY>\n\n"
            "CRITICAL: Use the EXACT delimiter tags shown above. The parsing system depends on finding \"<TRANSCRIPT>\" and \"</TRANSCRIPT>\" as well as \"<SUMMARY>\" and \"</SUMMARY>\" exactly as written."
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
                        "used_media_kind": ("audio_wav" if str(media_path).lower().endswith(".wav") else "video"),
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
                "video_path": ch.get("video_path"),
                "used_media_path": media_path,
                "used_media_kind": ("audio_wav" if str(media_path).lower().endswith(".wav") else "video"),
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

    # Compute token allocation for the answer based on duration, query, and transcript length
    try:
        vid = getattr(state, "video", None)
        # Prefer manifest duration, then state.video, then chunk-derived duration
        duration_s = 0.0
        try:
            if manifest_path:
                _m = _load_manifest(Path(manifest_path))
                duration_s = float(((_m.get("result") or {}).get("duration") or 0.0))
        except Exception:
            duration_s = 0.0
        if not duration_s and vid and getattr(vid, "duration_s", None) is not None:
            duration_s = float(getattr(vid, "duration_s", 0) or 0)
        if not duration_s:
            duration_s = float(total_duration_s or 0.0)

        title = getattr(vid, "title", None) if vid else None
        _alloc = allocate_tokens(
            video_duration_s=duration_s,
            query_text=user_req,
            title=title,
            transcript_chars=total_chars,
        )
        _gen_cfg = to_generation_config(_alloc)
        _target_len_line = f"Target length: around {_alloc.tokens} tokens.\n"
    except Exception:
        _alloc = None
        _gen_cfg = {}
        _target_len_line = ""

    total_minutes = float(total_duration_s) / 60.0 if total_duration_s else 0.0

    def _direct_multimodal() -> str:
        system_instruction = _load_prompt_text("global_prompt.txt")
        contents: List[Any] = []
        meta_block = f"Video metadata:\n{meta_text}\n\n" if meta_text else ""
        req_with_intent = f"{user_req}\n\nIntent: {intent.strip()}" if intent and isinstance(intent, str) and intent.strip() else user_req
        user_prompt_text = (
            f"User request:\n{req_with_intent}\n\n"
            f"{meta_block}"
            "Based on the following media files (chunks), provide a comprehensive, grounded response.\n"
            "Important: Do not include any timestamps in the output.\n"
            f"{_target_len_line}"
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

        # Prefer top-level system_instruction and generation_config, fallback to config
        max_out = 0
        if isinstance(_gen_cfg, dict):
            try:
                max_out = int(_gen_cfg.get("max_output_tokens", 0) or 0)
            except Exception:
                max_out = 0
        if genai_types is not None:
            gen_cfg_dict = {"max_output_tokens": max_out} if max_out else None
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    system_instruction=(system_instruction if (system_instruction or "").strip() else None),
                    generation_config=gen_cfg_dict,
                )
            except TypeError:
                cfg_obj = genai_types.GenerateContentConfig(
                    system_instruction=(system_instruction if (system_instruction or "").strip() else None),
                )
                try:
                    response = client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=cfg_obj,
                        generation_config=gen_cfg_dict,
                    )
                except TypeError:
                    response = client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=cfg_obj,
                    )
        else:
            cfg_dict: Dict[str, Any] = {}
            if (system_instruction or "").strip():
                cfg_dict["system_instruction"] = system_instruction
            if max_out:
                cfg_dict["generation_config"] = {"max_output_tokens": max_out}
            if cfg_dict:
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=cfg_dict,
                )
            else:
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                )
        return getattr(response, "text", None) or ""

    def _map_reduce() -> str:
        system_instruction = _load_prompt_text("global_prompt.txt")
        header = [f"User request:\n{user_req}"]
        header.append("Important: Do not include any timestamps in the output.")
        if intent and isinstance(intent, str) and intent.strip():
            header.append(f"Intent: {intent.strip()}")
        if meta_text:
            header.append(f"\nVideo metadata:\n{meta_text}")
        if _target_len_line:
            header.append(_target_len_line.strip())
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
                    f"Chunk {ch['idx']}\n"
                    f"Summary of this chunk:\n{(ch.get('summary') or '').strip()}\n\n"
                    f"Transcript excerpt:\n{excerpt.strip()}\n"
                )
            )
        content_text = "\n".join(header + parts)
        # Prefer top-level system_instruction and generation_config, fallback to config
        max_out = 0
        if isinstance(_gen_cfg, dict):
            try:
                max_out = int(_gen_cfg.get("max_output_tokens", 0) or 0)
            except Exception:
                max_out = 0
        if genai_types is not None:
            gen_cfg_dict = {"max_output_tokens": max_out} if max_out else None
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=[content_text],
                    system_instruction=(system_instruction if (system_instruction or "").strip() else None),
                    generation_config=gen_cfg_dict,
                )
            except TypeError:
                cfg_obj = genai_types.GenerateContentConfig(
                    system_instruction=(system_instruction if (system_instruction or "").strip() else None),
                )
                try:
                    response = client.models.generate_content(
                        model=model,
                        contents=[content_text],
                        config=cfg_obj,
                        generation_config=gen_cfg_dict,
                    )
                except TypeError:
                    response = client.models.generate_content(
                        model=model,
                        contents=[content_text],
                        config=cfg_obj,
                    )
        else:
            cfg_dict: Dict[str, Any] = {}
            if (system_instruction or "").strip():
                cfg_dict["system_instruction"] = system_instruction
            if max_out:
                cfg_dict["generation_config"] = {"max_output_tokens": max_out}
            if cfg_dict:
                response = client.models.generate_content(
                    model=model,
                    contents=[content_text],
                    config=cfg_dict,
                )
            else:
                response = client.models.generate_content(
                    model=model,
                    contents=[content_text],
                )
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
                    "allocated_tokens": (_gen_cfg.get("max_output_tokens") if isinstance(_gen_cfg, dict) else None),
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
                "allocated_tokens": (_gen_cfg.get("max_output_tokens") if isinstance(_gen_cfg, dict) else None),
            }
        )

    # Local fallback if the model returned empty text
    if not (result_text or "").strip():
        def _local_fallback() -> str:
            summaries = [
                (ch.get("summary") or "").strip()
                for ch in chunks_with_paths
                if (ch.get("summary") or "").strip()
            ]
            if summaries:
                return "\n".join(summaries)
            # As a last resort, include short excerpts to avoid an empty file
            excerpts = [
                (ch.get("text") or "")[:400].strip()
                for ch in chunks_with_paths
                if (ch.get("text") or "").strip()
            ]
            return "\n\n".join(excerpts)

        result_text = _local_fallback()
        state.artifacts.setdefault(tool_name, {})
        state.artifacts[tool_name].update({
            "approach": state.artifacts[tool_name].get("approach", "chunk_by_chunk"),
            "fallback": "local_merge",
            "result_chars": len(result_text or ""),
        })

    # Persist global summary into runtime/summaries/<job-id>/
    try:
        runtime_dir = getattr(state.config, "runtime_dir", Path("runtime")) if getattr(state, "config", None) else Path("runtime")
        job_id = Path(manifest_path).parent.name if manifest_path else "session"
        summary_dir = Path(runtime_dir) / "summaries" / job_id
        summary_dir.mkdir(parents=True, exist_ok=True)
        gp = summary_dir / "global_summary.gemini.txt"
        gp.write_text((result_text or "").strip() + "\n", encoding="utf-8")
        state.artifacts.setdefault(tool_name, {})
        state.artifacts[tool_name]["global_summary_path"] = str(gp)
    except Exception:
        pass

    return result_text