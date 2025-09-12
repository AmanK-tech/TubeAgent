from pathlib import Path
import os

from agent.errors import ToolError
from agent.tools.summarise_chunks import summarise_chunk
from agent.llm.client import LLMClient


def _fmt_ts(seconds: float | int | None) -> str:
    try:
        total = int(float(seconds or 0))
    except Exception:
        total = 0
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def summarise_global(state, user_req):
    """
    Produce a final deliverable by synthesizing across transcript chunks (and cached summaries).

    Example call:

        summarise_global(state, "Write a coherent global summary across all chunks.")

    Args:
        state (AgentState): Agent state with `chunks` and/or combined `transcript`.
        user_req (str): Instruction/request describing the desired final output.

    Returns:
        str: The final global text. Also records minimal stats in `state.artifacts["summarise_global"]`.

    Raises:
        ToolError: If neither chunks nor a combined transcript are available.
    """
    model = state.config.model
    key = getattr(state.config, "api_key", None) or os.getenv("DEEPSEEK_API_KEY")
    llm = LLMClient(provider=getattr(state.config, "provider", "deepseek"), model=model, api_key=key)

    system_instruction = Path("src/agent/prompts/global_prompt.txt").read_text(encoding="utf-8")

    # Ensure we have something to work with
    chunks = list(getattr(state, "chunks", []) or [])
    if not chunks:
        # Fall back to a single pseudo-chunk from the combined transcript
        transcript = getattr(state, "transcript", None)
        if not transcript:
            raise ToolError("No chunks or transcript found for global summarisation.", tool_name="summarise_global")
        # Construct a minimal pseudo-chunk-like object
        class _PseudoChunk:
            start_s = 0
            end_s = 0
            def __init__(self, text: str):
                self.text = text
                self.summary = None
        chunks = [_PseudoChunk(transcript)]

    # Attempt to map chunks back to transcribe artifacts to discover indices/paths
    art = getattr(state, "artifacts", {}) or {}
    ta = art.get("transcribe_asr", {}) if isinstance(art.get("transcribe_asr"), dict) else {}
    ta_chunks = ta.get("chunks", []) if isinstance(ta.get("chunks"), list) else []
    bounds_to_info = {}
    out_dir = None
    try:
        for ent in ta_chunks:
            s = int(float(ent.get("start_sec", 0) or 0))
            e = int(float(ent.get("end_sec", 0) or 0))
            idx = int(ent.get("idx", 0))
            tp = ent.get("text_path")
            if tp:
                out_dir = out_dir or Path(tp).parent
            bounds_to_info[(s, e)] = {"idx": idx, "out_dir": Path(tp).parent if tp else None}
    except Exception:
        bounds_to_info = {}

    # Build or use per-chunk local outputs
    local_outputs = []
    used_generated = 0
    used_loaded = 0
    # Optional: skip calling per-chunk summariser and rely on raw excerpts only
    skip_chunk_calls = str(os.getenv("SUMMARISE_GLOBAL_SKIP_CHUNK_CALLS", "0")).strip() in {"1", "true", "yes"}
    # Excerpt length (chars) for grounding; smaller -> less prompt cost
    try:
        excerpt_len = int(os.getenv("GLOBAL_EXCERPT_CHARS", "400") or 400)
    except Exception:
        excerpt_len = 400
    for i, ch in enumerate(chunks, start=1):
        # Use existing summary if present, else generate now (unless skipping)
        summary_text = getattr(ch, "summary", None)
        # Try loading a cached on-disk summary if we can map this chunk
        if not summary_text:
            info = bounds_to_info.get((int(getattr(ch, "start_s", 0) or 0), int(getattr(ch, "end_s", 0) or 0)))
            if info and info.get("out_dir") is not None:
                idx0 = int(info.get("idx", i - 1))
                cand = info["out_dir"] / f"chunk_{idx0:04d}.summary.txt"
                try:
                    if cand.exists():
                        summary_text = cand.read_text(encoding="utf-8").strip()
                        used_loaded += 1
                except Exception:
                    pass
        # Generate if still missing
        if not summary_text and not skip_chunk_calls:
            summary_text = summarise_chunk(state, ch, user_req, llm=llm)
            # Try to persist back to state for downstream visibility
            try:
                ch.summary = summary_text
                used_generated += 1
            except Exception:
                pass
            # Also persist to disk if we know the output dir and chunk idx
            try:
                info = bounds_to_info.get((int(getattr(ch, "start_s", 0) or 0), int(getattr(ch, "end_s", 0) or 0)))
                if info and info.get("out_dir") is not None:
                    idx0 = int(info.get("idx", i - 1))
                    sp = info["out_dir"] / f"chunk_{idx0:04d}.summary.txt"
                    sp.write_text((summary_text or "").strip() + "\n", encoding="utf-8")
            except Exception:
                pass

        # Prepare a short raw excerpt to aid global synthesis without huge context
        raw_text = (getattr(ch, "text", None) or "").strip()
        excerpt = raw_text[: max(0, excerpt_len)]  # keep inputs bounded

        local_outputs.append(
            {
                "idx": i,
                "start_s": getattr(ch, "start_s", None),
                "end_s": getattr(ch, "end_s", None),
                "summary": summary_text or "",
                "excerpt": excerpt,
            }
        )

    # Compose the user content for the global model
    header = [
        "User request:",
        str(user_req or ""),
        "",
        "Below are per-chunk outputs and brief raw excerpts.",
        "Use only information from these chunks; do not invent facts.",
        "",
        "CHUNKS:",
    ]

    parts = []
    for item in local_outputs:
        idx = item["idx"]
        ss = _fmt_ts(item.get("start_s"))
        es = _fmt_ts(item.get("end_s"))
        parts.append(
            (
                f"---\n"
                f"Chunk {idx}  [start={ss}, end={es}]\n"
                f"Local output:\n{item['summary'].strip()}\n\n"
                f"Transcript excerpt:\n{item['excerpt'].strip()}\n"
            )
        )

    content_text = "\n".join(header + parts)

    result_text = llm.generate(system_instruction=system_instruction, user_text=content_text, max_output_tokens=state.config.max_tokens)

    # Record minimal artifacts for observability
    try:
        state.artifacts.setdefault("summarise_global", {})
        state.artifacts["summarise_global"].update(
            {
                "chunks_used": len(local_outputs),
                "generated_chunk_summaries": used_generated,
                "loaded_cached_summaries": used_loaded,
                "result_chars": len(result_text or ""),
            }
        )
    except Exception:
        pass

    return result_text
