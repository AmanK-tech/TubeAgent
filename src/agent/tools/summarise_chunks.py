from pathlib import Path
import os
try:
    # Prefer package-safe resource loading when available
    from importlib.resources import files as _res_files  # Python 3.9+
except Exception:  # pragma: no cover - fallback if not available
    _res_files = None

from agent.llm.client import LLMClient


def summarise_chunk(state, chunk, user_req, *, llm: LLMClient | None = None):
    """
    Generate a concise summary for a single transcript chunk using the configured LLM.

    Example call:

        summarise_chunk(state, chunk, "Summarize this chunk clearly and concisely.")

    Args:
        state (AgentState): Agent state providing provider/model/max_tokens via `state.config`.
        chunk (Chunk): Transcript chunk with `text` and optional `start_s`/`end_s`.
        user_req (str): Instruction or request to guide the summary content and style.
        llm (LLMClient, optional): Reused client instance; built from state if not provided.

    Returns:
        str: The generated summary text.

    Raises:
        ToolError: If LLM provider configuration is invalid or the request fails.
    """
    provider = getattr(state.config, "provider", "deepseek")
    model = state.config.model
    key = getattr(state.config, "api_key", None) or os.getenv("DEEPSEEK_API_KEY")
    text = chunk.text
    # Reuse a provided LLM client or build one from state
    llm = llm or LLMClient(provider=provider, model=model, api_key=key)

    # Resolve prompt from package resources with a robust fallback to filesystem
    def _load_prompt_text(filename: str) -> str:
        # Try importlib.resources via the 'agent' package
        if _res_files is not None:
            try:
                return (_res_files("agent") / "prompts" / filename).read_text(encoding="utf-8")
            except Exception:
                pass
        # Fallback to path relative to this file (src layout or editable installs)
        try:
            agent_dir = Path(__file__).resolve().parents[1]  # .../agent
            return (agent_dir / "prompts" / filename).read_text(encoding="utf-8")
        except Exception:
            return ""

    system_instruction = _load_prompt_text("chunk_prompt.txt")

    # Build a single user message with both the request and grounded transcript
    start_s = getattr(chunk, "start_s", None)
    end_s = getattr(chunk, "end_s", None)
    header = ""
    if isinstance(start_s, (int, float)) and isinstance(end_s, (int, float)):
        header = f"Transcript chunk ({int(start_s)}s–{int(end_s)}s)\n"

    content_text = (
        f"User request:\n{user_req}\n\n" +
        header +
        "Transcript:\n" + (text or "")
    )

    res = llm.generate(system_instruction=system_instruction, user_text=content_text, max_output_tokens=state.config.max_tokens)
    return res or ""
