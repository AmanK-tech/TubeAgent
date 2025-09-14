from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional
from pathlib import Path

try:
    from importlib.resources import files as _res_files  # Python 3.9+
except Exception:  # pragma: no cover
    _res_files = None

from agent.errors import PlanningError
from agent.llm.client import LLMClient


def _load_planner_system() -> str:
    """Load the system prompt for the planner from package resources with a robust fallback."""
    if _res_files is not None:
        try:
            return (_res_files("agent") / "prompts" / "planner_prompt.txt").read_text(encoding="utf-8")
        except Exception:
            pass
    try:
        return (Path(__file__).resolve().parents[1] / "prompts" / "planner_prompt.txt").read_text(encoding="utf-8")
    except Exception:
        return ""


PLANNER_SCHEMA_INSTRUCTION = (
    "\nYou must respond with a SINGLE JSON object only, matching exactly one of:\n"
    "{\n  \"action\": \"tool_call\",\n  \"tool\": \"<one of: fetch_task | extract_audio | transcribe_asr | summarise_global | emit_output>\",\n  \"arguments\": { /* JSON args for the tool */ }\n}\n"
    "or\n"
    "{\n  \"action\": \"final\",\n  \"content\": \"<final assistant text>\"\n}\n"
    "Do not include any other text, code fences, or commentary."
)


def _extract_json_object(s: str) -> Dict[str, Any]:
    """Extract a JSON object from the model response, tolerating code fences."""
    txt = s.strip()
    # Remove markdown code fences if present
    if txt.startswith("```"):
        lines = [ln for ln in txt.splitlines() if not ln.strip().startswith("```")]
        txt = "\n".join(lines).strip()
    # Attempt direct load
    try:
        return json.loads(txt)
    except Exception:
        pass
    # Fallback: find first outermost {...}
    start = txt.find("{")
    end = txt.rfind("}")
    if start != -1 and end != -1 and end > start:
        frag = txt[start : end + 1]
        return json.loads(frag)
    raise PlanningError("Planner returned non-JSON response")


def _normalize_tool_name(name: str) -> str:
    n = (name or "").strip()
    mapping = {
        "fetch": "fetch_task",
        "fetch_video": "fetch_task",
        "fetch_task": "fetch_task",
        "extract": "extract_audio",
        "extract_audio": "extract_audio",
        "transcribe": "transcribe_asr",
        "transcribe_asr": "transcribe_asr",
        "summarize_global": "summarise_global",
        "summarise_global": "summarise_global",
        "emit": "emit_output",
        "emit_output": "emit_output",
    }
    return mapping.get(n, n)


def _state_snapshot(state) -> Dict[str, Any]:
    """Return a compact view of state to help planning in non-tool-calling mode."""
    snap: Dict[str, Any] = {
        "has_video": bool(getattr(state, "video", None)),
        "has_chunks": bool(getattr(state, "chunks", None)),
        "has_transcript": bool(getattr(state, "transcript", None)),
        "artifacts": list((getattr(state, "artifacts", {}) or {}).keys()),
    }
    try:
        vid = getattr(state, "video", None)
        if vid and getattr(vid, "duration_s", None) is not None:
            snap["duration_minutes"] = int(getattr(vid, "duration_s", 0) or 0) / 60.0
    except Exception:
        pass
    try:
        ta = (getattr(state, "artifacts", {}) or {}).get("transcribe_asr", {})
        if isinstance(ta, dict) and ta.get("chunks"):
            snap["transcribed_chunks"] = len(ta.get("chunks") or [])
    except Exception:
        pass
    return snap


def _rule_based_next(state, user_text: str) -> Dict[str, Any]:
    """Deterministic fallback for next action if the model response is unusable."""
    art = getattr(state, "artifacts", {}) or {}
    if not getattr(state, "video", None):
        return {"action": "tool_call", "tool": "fetch_task", "arguments": {"user_text": user_text}}
    if not art.get("extract_audio"):
        src = getattr(getattr(state, "video", None), "source_url", None)
        return {"action": "tool_call", "tool": "extract_audio", "arguments": {"input_url": src}}
    if not art.get("transcribe_asr") or not getattr(state, "transcript", None):
        return {"action": "tool_call", "tool": "transcribe_asr", "arguments": {"language": "en-US"}}
    # Prefer global summary next
    return {"action": "tool_call", "tool": "summarise_global", "arguments": {"user_req": user_text}}


@dataclass
class Planner:
    """LLM-driven planner that decides the next action/tool or a final response.

    This is optional when using native function-calling, but useful for explicit planning
    or non-tool-calling providers.
    """

    model: str
    api_key: Optional[str] = None

    def _client(self) -> LLMClient:
        return LLMClient(provider="deepseek", model=self.model, api_key=self.api_key)

    def plan_next(self, state, user_text: str, *, history: Optional[list[dict]] = None) -> Dict[str, Any]:
        system = _load_planner_system() + "\n\n" + PLANNER_SCHEMA_INSTRUCTION
        messages = []
        if (system or "").strip():
            messages.append({"role": "system", "content": system})
        # Provide a compact state snapshot so the planner can skip completed steps
        snapshot = json.dumps(_state_snapshot(state))
        prompt = (
            "User request:\n" + user_text + "\n\n" +
            "State snapshot:\n" + snapshot + "\n\n" +
            "Decide the next step."
        )
        messages.append({"role": "user", "content": prompt})

        try:
            raw = self._client().chat_raw(messages=messages, tools=None, tool_choice=None, max_output_tokens=256)
            choice = (raw.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content", "")
            obj = _extract_json_object(content)
        except Exception:
            obj = _rule_based_next(state, user_text)

        # Normalize tool naming and fill common defaults
        if obj.get("action") == "tool_call":
            obj["tool"] = _normalize_tool_name(obj.get("tool", ""))
            args = obj.get("arguments") or {}
            if obj["tool"] == "fetch_task" and "user_text" not in args:
                args["user_text"] = user_text
            if obj["tool"] == "summarise_global" and "user_req" not in args:
                args["user_req"] = user_text
            obj["arguments"] = args
        elif obj.get("action") == "final":
            # Ensure content string exists
            obj["content"] = str(obj.get("content") or "")
        else:
            # Fallback if malformed
            obj = _rule_based_next(state, user_text)

        return obj
