from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.core.state import AgentState, Chunk
from agent.errors import ToolError


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
    try:
        from google import genai  # type: ignore
    except Exception as e:  # pragma: no cover
        raise ToolError("Missing dependency: google-genai", tool_name=tool_name) from e

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
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ToolError("Missing Google API key. Set GOOGLE_API_KEY.", tool_name=tool)
    client = genai.Client(api_key=api_key)
    gemini_model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

    out_dir = manifest_p.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    combined_parts: List[str] = []
    chunk_results: List[Chunk] = []
    artifacts: Dict[str, Any] = {"manifest_path": str(manifest_p), "gemini_model": gemini_model, "chunks": []}

    # Process chunks sequentially
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

        # Transcribe
        prompt = (
            "Transcribe the audio from this video, giving timestamps for salient events in the video. "
            "Also provide visual descriptions."
        )
        try:
            response = client.models.generate_content(
                model=gemini_model,
                contents=[myfile, prompt],
            )
        except Exception as e:
            raise ToolError(f"Gemini generate_content failed for chunk {idx}: {e}", tool_name=tool)

        text = (getattr(response, "text", None) or "").strip()

        # Write artifacts per chunk
        txt_path = out_dir / f"chunk_{idx:04d}.gemini.txt"
        json_path = out_dir / f"chunk_{idx:04d}.gemini.json"
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump({"model": gemini_model, "file": str(media_path), "text": text}, f)
        except Exception:
            pass

        combined_parts.append(text)
        chunk_results.append(Chunk(start_s=int(start_s), end_s=int(end_s), text=text))
        artifacts["chunks"].append(
            {
                "idx": idx,
                "start_sec": start_s,
                "end_sec": end_s,
                "text_path": str(txt_path),
                "json_path": str(json_path),
                "chars": len(text),
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

