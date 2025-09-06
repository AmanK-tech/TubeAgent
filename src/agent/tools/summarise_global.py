from google import genai
from google.genai import types
from pathlib import Path
import os

from agent.errors import ToolError
from agent.tools.summarize_chunks import summarise_chunk


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
    Produce a final, global deliverable by synthesizing across all transcript chunks.

    - Prefers existing per-chunk summaries on state.chunks[].summary.
    - If a summary is missing, calls summarise_chunk() to generate it.
    - Sends chunk-level outputs (and small raw excerpts) to the global prompt.
    - Returns the final generated text and records minimal artifacts.
    """
    model = state.config.model
    key = getattr(state.config, "api_key", None) or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=key) if key else genai.Client()

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

    # Build or use per-chunk local outputs
    local_outputs = []
    used_generated = 0
    for i, ch in enumerate(chunks, start=1):
        # Use existing summary if present, else generate now
        summary_text = getattr(ch, "summary", None)
        if not summary_text:
            summary_text = summarise_chunk(state, ch, user_req)
            # Try to persist back to state for downstream visibility
            try:
                ch.summary = summary_text
                used_generated += 1
            except Exception:
                pass

        # Prepare a short raw excerpt to aid global synthesis without huge context
        raw_text = (getattr(ch, "text", None) or "").strip()
        excerpt = raw_text[:800]  # keep inputs bounded

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

    response = client.models.generate_content(
        model=model,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            generation_config=types.GenerationConfig(max_output_tokens=state.config.max_tokens),
        ),
        contents=[
            types.Content(
                role="user",
                parts=[types.Part.from_text(content_text)],
            )
        ],
    )

    result_text = response.text

    # Record minimal artifacts for observability
    try:
        state.artifacts.setdefault("summarise_global", {})
        state.artifacts["summarise_global"].update(
            {
                "chunks_used": len(local_outputs),
                "generated_chunk_summaries": used_generated,
                "result_chars": len(result_text or ""),
            }
        )
    except Exception:
        pass

    return result_text
