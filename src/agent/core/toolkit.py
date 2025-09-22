from __future__ import annotations
import json
import dataclasses
from pathlib import Path
from typing import Any, Callable
from agent.errors import ToolError
from agent.core.config import ExtractAudioConfig
from agent.tools.fetch import fetch_task
from agent.tools.extract import extract_audio_task
from agent.tools.transcribe import transcribe_task
from agent.tools.emit_output import emit_output
from agent.tools.answer_from_metadata import answer_from_metadata
from agent.llm.client import LLMClient
from agent.tools.transcribe import summarise_gemini


def to_jsonable(obj:Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    if isinstance(obj,Path):
        return str(obj)
    if isinstance(obj,(list,tuple)):
        return [to_jsonable(o) for o in obj]
    if isinstance(obj,dict):
        return {k:to_jsonable(v) for k,v in obj.items()}
    return obj

def run_tool_json(state,tool_name,fn,*args,**kwargs) -> dict:
    try:
        result = fn(*args, **kwargs)
        return {
        "tool": tool_name,
        "ok": True,
        "result": to_jsonable(result),
        "artifacts": to_jsonable(getattr(state, "artifacts", {}).get(tool_name)),
        }
    except ToolError as e:
        return {"tool": tool_name, "ok": False, "error": {"type": "ToolError", "tool": e.tool_name, "message": str(e)}}
    except Exception as e:
        return {"tool": tool_name, "ok": False, "error": {"type": e.__class__.__name__, "message": str(e)}}


def get_tools() -> list[dict[str, Any]]:
    """Return tool/function specs for use with function-calling LLMs.

    Tools covered:
      - fetch_task
      - extract_audio
      - transcribe_asr (can optionally summarise)
      - emit_output
    """
    tools = [
        {
            "type": "function",
            "function": {
                "name": "fetch_task",
                "description": "Fetch YouTube video metadata from free-form user text containing a URL; populates AgentState.video.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_text": {
                            "type": "string",
                            "description": "Free-form text that includes a YouTube URL (youtube.com or youtu.be).",
                        },
                    },
                    "required": ["user_text"],
                    "additionalProperties": False
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "extract_audio",
                "description": "Download video (YouTube supported), extract normalized WAV, and create aligned MP4+WAV chunks; writes a manifest and caches outputs.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "input_path": {
                            "type": "string",
                            "description": "Local media file path to extract from (use if not providing input_url).",
                        },
                        "input_url": {
                            "type": "string",
                            "description": "Remote source URL (YouTube supported). Downloads full video (mp4 preferred) for video-first processing.",
                        },
                        "out_dir": {
                            "type": "string",
                            "description": "Override output/cache directory (defaults under runtime/cache/extract).",
                        },
                        "config": {
                            "type": "object",
                            "description": "Processing and chunking configuration (video-first; defaults to 30-minute duration chunks with small overlap).",
                            "properties": {
                                "sample_rate": {"type": "integer", "description": "Target sample rate (Hz). Default 16000."},
                                "mono": {"type": "boolean", "description": "Downmix to mono. Default true."},
                                "format": {"type": "string", "description": "Output format, typically 'wav'."},
                                "normalize": {"type": "boolean", "description": "Apply normalization pipeline. Default true."},
                                "loudnorm_ebu": {"type": "boolean", "description": "Use EBU R128 loudness normalization. Default true."},
                                "target_lufs": {"type": "number", "description": "Target LUFS if loudnorm enabled. Default -23.0."},
                                "max_peak_dbfs": {"type": "number", "description": "Limiter ceiling in dBFS. Default -1.0."},
                                "silence_trim": {"type": "boolean", "description": "Trim head/tail silence. Default false."},
                                "silence_threshold_db": {"type": "number", "description": "Silence threshold in dB. Default -40.0."},
                                "silence_min_ms": {"type": "integer", "description": "Minimum silence length (ms). Default 800."},
                                "max_duration_sec": {"type": "integer", "description": "Cap processed duration in seconds. Default none."},
                                "start_offset_sec": {"type": "number", "description": "Start offset (seconds). Default 0."},
                                "end_offset_sec": {"type": "number", "description": "Optional end offset (seconds)."},
                                "chunk_strategy": {"type": "string", "enum": ["none", "duration", "vad"], "description": "Chunking strategy. Default 'duration'."},
                                "chunk_duration_sec": {"type": "integer", "description": "Target chunk length (s) for duration strategy. Default 1800 (30 min)."},
                                "chunk_overlap_sec": {"type": "number", "description": "Overlap between chunks (s). Default 1.0."},
                                "chunk_max_sec": {"type": "integer", "description": "Upper bound when using VAD (s). Default 180."},
                                "io_cache_dir": {"type": "string", "description": "Custom cache directory."},
                                "io_tmp_dir": {"type": "string", "description": "Custom temp directory."},
                                "force": {"type": "boolean", "description": "Ignore cache and force regeneration. Default false."},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "additionalProperties": False,
                    "description": "Provide either input_path or input_url.",
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "transcribe_asr",
                "description": "Transcribe chunks with Gemini; optionally also produce a global summary when 'user_req' is provided.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "manifest_path": {"type": "string", "description": "Explicit path to extract manifest JSON; auto-discovered if not provided."},
                        "model": {"type": "string", "description": "Gemini model to use (default via GEMINI_MODEL env)."},
                        "concurrency": {"type": "integer", "description": "Parallel chunk uploads (default via GEMINI_CONCURRENCY, typically 2)."},
                        "user_req": {"type": "string", "description": "If set, run global summarization immediately after transcription and return the final text."},
                        "intent": {"type": "string", "description": "Optional intent hint for summarization (e.g., summary, question, search)."},
                        "include_metadata": {"type": "boolean", "description": "If true, include video title/channel/URL as grounding for summarization."},
                    },
                    "additionalProperties": False,
                },
            },
        },
        
        {
            "type": "function",
            "function": {
                "name": "answer_from_metadata",
                "description": "Answer simple identity/metadata questions directly from already-fetched video metadata (no LLM).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "Original user question (optional)."}
                    },
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "emit_output",
                "description": "Persist the final text (and optional structured data) to files/console/API with sensible naming and metadata.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Final text to persist. Required."},
                        "side_data": {"type": "object", "description": "Optional structured bundle to include in JSON output."},
                        "formats": {"type": "array", "items": {"type": "string", "enum": ["md", "txt", "json"]}, "description": "Which formats to create. Defaults to ['md']."},
                        "targets": {"type": "array", "items": {"type": "string", "enum": ["file", "console", "api"]}, "description": "Where to send outputs. Defaults to ['file','console']."},
                        "filename": {"type": "string", "description": "Base filename (with or without extension). Auto‑derived if omitted."},
                        "out_dir": {"type": "string", "description": "Output directory; defaults to transcription folder or runtime/outputs."},
                        "preview_chars": {"type": "integer", "description": "Characters to print in console preview (default 1200)."},
                        "webhook_url": {"type": "string", "description": "If set and 'api' target chosen, POST a JSON payload to this URL."},
                    },
                    "required": ["text"],
                    "additionalProperties": False,
                },
            },
        },
    ]

    return tools


def dispatch_tool_call(state, name: str, params: dict) -> dict:
    """Route a tool name + params to the concrete implementation and wrap output.

    Ensures artifacts are reported under the same tool name used by callers.
    """
    tool = (name or "").strip()

    if tool == "fetch_task":
        # Keep artifact namespace consistent with tool name
        return run_tool_json(state, tool, lambda: fetch_task(state, tool, params["user_text"]))

    if tool == "extract_audio":
        cfg_in = params.get("config")
        cfg = ExtractAudioConfig(**cfg_in) if isinstance(cfg_in, dict) else None
        return run_tool_json(
            state,
            tool,
            lambda: extract_audio_task(
                state,
                tool,
                input_path=params.get("input_path"),
                input_url=params.get("input_url"),
                out_dir=params.get("out_dir"),
                config=cfg,
            ),
        )

    if tool == "transcribe_asr":
        def _do_transcribe_and_maybe_summarise():
            # Always transcribe first
            transcribe_task(
                state,
                tool,
                manifest_path=params.get("manifest_path"),
                model=params.get("model"),
                concurrency=params.get("concurrency"),
            )
            # If user_req provided, run global summary now and return text
            user_req = params.get("user_req")
            if isinstance(user_req, str) and user_req.strip():
                return summarise_gemini(
                    state,
                    user_req,
                    intent=params.get("intent"),
                    include_metadata=bool(params.get("include_metadata", False)),
                )
            # Otherwise return a compact report of chunk metadata
            ta = (getattr(state, "artifacts", {}) or {}).get("transcribe_asr", {})
            return {
                "chunks": ta.get("chunks"),
                "combined_transcript_path": ta.get("combined_transcript_path"),
                "gemini_model": ta.get("gemini_model"),
            }

        return run_tool_json(state, tool, _do_transcribe_and_maybe_summarise)

    if tool == "emit_output":
        return run_tool_json(
            state,
            tool,
            lambda: emit_output(
                state,
                params["text"],
                side_data=params.get("side_data"),
                formats=params.get("formats"),
                targets=params.get("targets"),
                filename=params.get("filename"),
                out_dir=params.get("out_dir"),
                preview_chars=params.get("preview_chars", 1200),
                webhook_url=params.get("webhook_url"),
                tool_name=tool,
            ),
        )

    if tool == "answer_from_metadata":
        return run_tool_json(state, tool, lambda: answer_from_metadata(state, question=params.get("question")))

    raise ToolError(f"Unknown tool: {name}", tool_name=name)
