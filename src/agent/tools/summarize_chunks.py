from google import genai
from google.genai import types
from pathlib import Path
import os

def summarise_chunk(state, chunk, user_req):
    provider = state.config.provider
    model = state.config.model
    key = getattr(state.config, "api_key", None) or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    text = chunk.text
    client = genai.Client(api_key=key) if key else genai.Client()

    system_instruction = Path("src/agent/prompts/chunk_prompt.txt").read_text(encoding="utf-8")

    generation_config = types.GenerationConfig(max_output_tokens=state.config.max_tokens)

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

    response = client.models.generate_content(
        model=model,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            generation_config=generation_config,
        ),
        contents=[
            types.Content(
                role="user",
                parts=[types.Part.from_text(content_text)],
            )
        ],
    )

    res = response.text
    return res

