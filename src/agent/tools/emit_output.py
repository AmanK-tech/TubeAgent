from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from agent.core.state import AgentState
from agent.errors import ToolError


def _slugify(s: str, *, max_len: int = 80) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9\-_. ]+", "", s)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s[:max_len] or "notes"


def _normalize_text(s: str) -> str:
    # Normalize line endings and strip trailing whitespace blocks
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in s.split("\n")]
    # Ensure a single trailing newline
    out = "\n".join(lines).strip() + "\n"
    return out


def _derive_base_dir(state: AgentState, out_dir: Optional[str]) -> Path:
    if out_dir:
        return Path(out_dir).resolve()
    art = getattr(state, "artifacts", {}) or {}
    ta = art.get("transcribe_asr", {}) if isinstance(art.get("transcribe_asr"), dict) else {}
    combined = ta.get("combined_transcript_path") if isinstance(ta, dict) else None
    if combined:
        return Path(str(combined)).resolve().parent
    runtime_dir = getattr(state.config, "runtime_dir", Path("runtime")) if getattr(state, "config", None) else Path("runtime")
    return (Path(runtime_dir) / "outputs").resolve()


def _derive_base_name(state: AgentState, filename: Optional[str]) -> tuple[str, str]:
    """Return (base_name_without_ext, preferred_ext)."""
    if filename:
        name = Path(filename).name
        stem = Path(name).stem
        ext = Path(name).suffix.lower().lstrip(".")
        if ext in {"md", "markdown", "txt", "json"}:
            return stem, ("md" if ext == "markdown" else ext)
        return stem, "md"
    vid = getattr(state, "video", None)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    if vid and getattr(vid, "video_id", None):
        title_part = _slugify(getattr(vid, "title", "") or "")
        if title_part and title_part != "notes":
            return f"{ts}_{title_part}_{vid.video_id}.notes", "md"
        return f"{ts}_{vid.video_id}.notes", "md"
    return "global_notes", "md"


def _build_metadata(state: AgentState) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "profile": getattr(state.config, "profile", None),
        "provider": getattr(state.config, "provider", None),
        "model": getattr(state.config, "model", None),
        "max_tokens": getattr(state.config, "max_tokens", None),
    }
    vid = getattr(state, "video", None)
    if vid is not None:
        meta.update({
            "video_id": getattr(vid, "video_id", None),
            "title": getattr(vid, "title", None),
            "duration_s": getattr(vid, "duration_s", None),
            "source_url": getattr(vid, "source_url", None),
        })
    # Summarisation stats if present
    try:
        sg = (getattr(state, "artifacts", {}) or {}).get("summarise_global", {})
        if isinstance(sg, dict):
            meta.update({
                "chunks_used": sg.get("chunks_used"),
                "generated_chunk_summaries": sg.get("generated_chunk_summaries"),
                "loaded_cached_summaries": sg.get("loaded_cached_summaries"),
            })
    except Exception:
        pass
    return meta


def emit_output(
    state: AgentState,
    text: str | None,
    *,
    side_data: Optional[Dict[str, Any]] = None,
    formats: Optional[list[str]] = None,  # subset of {"md","txt","json"}
    targets: Optional[list[str]] = None,  # subset of {"file","console","api"}
    filename: Optional[str] = None,
    out_dir: Optional[str] = None,
    preview_chars: int = 1200,
    webhook_url: Optional[str] = None,  # used if "api" target chosen
    tool_name: str = "emit_output",
) -> Dict[str, Any]:
    """
    Turn the model’s final text (and optional structured side data) into clean, consumable
    deliverables and persist/announce them as part of the workflow.

    Example call:

        emit_output(
            state,
            result_text,
            side_data=state.artifacts.get("summarise_global"),
            formats=["md", "json"],
            targets=["file", "console"],
        )

    Args:
        state (AgentState): Mutable agent state; used to choose default output directory and to store artifacts.
        text (str | None): Final text to persist. Must be non-empty.
        side_data (dict[str, Any] | None): Optional structured bundle to include in the JSON output.
        formats (list[str] | None): One or more of {"md","txt","json"}. Defaults to ["md"].
        targets (list[str] | None): Any of {"file","console","api"}. Defaults to ["file","console"].
        filename (str | None): Base filename (stem or name with extension). Auto-derived from video metadata if omitted.
        out_dir (str | None): Output directory. Defaults to the transcription output folder or runtime/outputs.
        preview_chars (int): Characters to print in the console preview. Default 1200.
        webhook_url (str | None): If provided and "api" target is included, POSTs a JSON payload to this URL.
        tool_name (str): Artifact namespace key. Default "emit_output".

    Returns:
        dict[str, Any]: Summary including `primary_path`, `outputs` (by format), `dir`, `meta`, and optional `api` result.

    Raises:
        ToolError: If `text` is empty.
    """
    if not text or not str(text).strip():
        raise ToolError("emit_output requires non-empty text", tool_name=tool_name)

    formats = formats or ["md"]
    formats = [f.lower() for f in formats if f.lower() in {"md", "txt", "json"}]
    if not formats:
        formats = ["md"]
    targets = targets or ["file", "console"]
    targets = [t.lower() for t in targets]

    base_dir = _derive_base_dir(state, out_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    base_name, preferred_ext = _derive_base_name(state, filename)

    meta = _build_metadata(state)
    clean_text = _normalize_text(str(text))

    outputs: Dict[str, str] = {}

    # Build Markdown
    if "md" in formats:
        # Render a minimal YAML front matter from scalar meta entries
        def _yaml_scalar(v: Any) -> str:
            if v is None:
                return ""  # skip
            if isinstance(v, (int, float)):
                return str(v)
            s = str(v)
            # quote if has special chars
            if any(ch in s for ch in [":", "#", "\n", "\""]):
                s = s.replace("\\", "\\\\").replace("\"", "\\\"")
                return f'"{s}"'
            return s

        yaml_lines = []
        for k, v in meta.items():
            yv = _yaml_scalar(v)
            if yv == "":
                continue
            yaml_lines.append(f"{k}: {yv}")
        front_matter = "---\n" + "\n".join(yaml_lines) + "\n---\n\n"
        md_body = clean_text
        md_path = (base_dir / f"{base_name}.md").resolve()
        md_path.write_text(front_matter + md_body, encoding="utf-8")
        outputs["md"] = str(md_path)

    # Build TXT
    if "txt" in formats:
        txt_path = (base_dir / f"{base_name}.txt").resolve()
        txt_path.write_text(clean_text, encoding="utf-8")
        outputs["txt"] = str(txt_path)

    # Build JSON bundle
    if "json" in formats:
        bundle = {
            "meta": meta,
            "text": clean_text.strip(),
        }
        if side_data is not None:
            bundle["data"] = side_data
        json_path = (base_dir / f"{base_name}.json").resolve()
        json_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        outputs["json"] = str(json_path)

    # Console announcement (preview)
    if "console" in targets:
        try:
            preview = clean_text[: max(0, int(preview_chars))]
            # Avoid excessive console noise in library context; print concise lines
            print(f"Emit: wrote deliverables in {base_dir}")
            if outputs:
                primary = outputs.get(preferred_ext) or next(iter(outputs.values()))
                print(f"Primary: {primary}")
            print("--- Preview ---\n" + (preview + ("…" if len(clean_text) > len(preview) else "")))
        except Exception:
            pass

    # Optional API announce/post (best-effort)
    api_result: Dict[str, Any] = {}
    if "api" in targets and webhook_url:
        try:  # Lazy import to avoid hard dep when unused
            import urllib.request as _rq  # type: ignore
            import urllib.error as _er  # type: ignore
            data = json.dumps({
                "meta": meta,
                "text": clean_text.strip(),
                "outputs": outputs,
                "side_data": side_data or {},
            }).encode("utf-8")
            req = _rq.Request(webhook_url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            with _rq.urlopen(req, timeout=20) as resp:
                api_result = {"status": resp.status}
        except Exception as e:
            # Do not fail the tool for API issues; record instead
            api_result = {"error": str(e)}

    result = {
        "dir": str(base_dir),
        "base_name": base_name,
        "outputs": outputs,
        "meta": meta,
        "primary_path": outputs.get(preferred_ext) or (next(iter(outputs.values())) if outputs else None),
        "api": api_result,
    }

    try:
        state.artifacts.setdefault(tool_name, {})
        state.artifacts[tool_name].update(result)
        # Back-compat single path fields
        if result.get("primary_path"):
            state.artifacts[tool_name]["output_path"] = result["primary_path"]
            state.artifacts[tool_name]["filename"] = Path(result["primary_path"]).name
            state.artifacts[tool_name]["chars"] = len(clean_text)
    except Exception:
        pass

    return result
