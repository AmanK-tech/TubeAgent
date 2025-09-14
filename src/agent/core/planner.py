from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, List
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
    """Return a compact view of state to help planning (and for observability)."""
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
            chunks_meta = ta.get("chunks") or []
            snap["transcribed_chunks"] = len(chunks_meta)
            # include a small window of chunk bounds to aid time-based queries
            preview = []
            for ent in chunks_meta[: min(5, len(chunks_meta))]:
                try:
                    preview.append(
                        {
                            "idx": int(ent.get("idx", 0)),
                            "start": int(float(ent.get("start_sec", 0) or 0)),
                            "end": int(float(ent.get("end_sec", 0) or 0)),
                        }
                    )
                except Exception:
                    continue
            if preview:
                snap["chunk_bounds_preview"] = preview
    except Exception:
        pass
    try:
        hist = ((getattr(state, "artifacts", {}) or {}).get("planner", {}) or {}).get("history", [])
        if hist:
            snap["previous_queries"] = hist[-5:]
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


# ---------------------- Query Intent Classification -------------------------

INTENTS = (
    "summary",  # general notes/summary/outline
    "question",  # specific question
    "analysis",  # deeper analysis/themes/opinions
    "search",  # find/locate within transcript
    "comparison",  # compare X vs Y
    "time_based",  # mentions timestamps or specific time
    "fact_extraction",  # numbers/dates/stats
    "follow_up",  # follow-on from previous exchange
)


def _classify_intent_heuristic(user_text: str, *, history: Optional[List[dict]] = None) -> str:
    t = (user_text or "").strip().lower()
    if not t:
        return "summary"
    # time notation
    import re
    if re.search(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", t) or any(w in t for w in ["timestamp", "at ", "minute", "second"]):
        return "time_based"
    # comparison cues
    if any(w in t for w in ["compare", "versus", "vs", "difference between", "diff between"]):
        return "comparison"
    # fact extraction cues
    if any(w in t for w in ["statistics", "stats", "figures", "numbers", "dates", "list all", "extract"]):
        return "fact_extraction"
    # search cues
    if any(t.startswith(w) for w in ["find", "where", "locate"]) or "search" in t:
        return "search"
    # question cues
    if t.endswith("?") or any(t.startswith(w) for w in ["what", "how", "why", "when", "who", "which"]):
        return "question"
    # analysis cues
    if any(w in t for w in ["analyze", "analyse", "analysis", "themes", "sentiment", "stance", "pros and cons", "tradeoffs"]):
        return "analysis"
    # follow-up cues (based on history presence and small add-on phrasing)
    if history and any(w in t for w in ["also", "more on", "and about", "follow up", "next"]):
        return "follow_up"
    # default summary/notes intent
    if any(w in t for w in ["summary", "summarize", "summarise", "notes", "outline", "brief"]):
        return "summary"
    return "summary"


def _log(state, kind: str, data: Dict[str, Any]) -> None:
    try:
        arts = getattr(state, "artifacts", None)
        if arts is None:
            return
        arts.setdefault("planner", {})
        arts["planner"].setdefault("log", [])
        arts["planner"]["log"].append({"kind": kind, "data": data})
    except Exception:
        pass


def _record_query(state, user_text: str, intent: str) -> None:
    try:
        arts = getattr(state, "artifacts", None)
        if arts is None:
            return
        arts.setdefault("planner", {})
        arts["planner"].setdefault("history", [])
        arts["planner"]["history"].append({"query": user_text, "intent": intent})
    except Exception:
        pass


def _validate_action(state, plan: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """Validate the proposed plan against current state.

    Returns (ok, corrected_plan_or_none, reason)
    """
    try:
        if plan.get("action") != "tool_call":
            return True, None, "ok"
        tool = plan.get("tool")
        art = getattr(state, "artifacts", {}) or {}
        if tool == "extract_audio" and not getattr(state, "video", None):
            return False, _rule_based_next(state, plan.get("arguments", {}).get("user_text", "")), "missing_video"
        if tool == "transcribe_asr" and not art.get("extract_audio"):
            return False, _rule_based_next(state, ""), "missing_extract"
        if tool == "summarise_global" and not (getattr(state, "transcript", None) or getattr(state, "chunks", None)):
            return False, _rule_based_next(state, plan.get("arguments", {}).get("user_req", "")), "missing_transcript"
        return True, None, "ok"
    except Exception as e:
        return True, None, f"validate_error:{e}"


@dataclass
class Planner:
    """LLM-driven planner that decides the next action/tool or a final response.

    This is optional when using native function-calling, but useful for explicit planning
    or non-tool-calling providers.
    """

    model: str
    api_key: Optional[str] = None
    use_llm: bool = True
    classify_with_llm: bool = False
    max_output_tokens: int = 256

    def _client(self) -> LLMClient:
        return LLMClient(provider="deepseek", model=self.model, api_key=self.api_key)

    def plan_next(self, state, user_text: str, *, history: Optional[list[dict]] = None) -> Dict[str, Any]:
        # 1) Classify intent (heuristic; optional LLM classification could be added later)
        intent = _classify_intent_heuristic(user_text, history=history)
        _record_query(state, user_text, intent)
        _log(state, "intent", {"intent": intent, "user_text": user_text})

        # Early fast-paths: if transcript exists and it is a direct follow-up/search/fact request, go straight to global synthesis
        has_transcript = bool(getattr(state, "transcript", None) or getattr(state, "chunks", None))
        if has_transcript and intent in {"question", "search", "time_based", "fact_extraction", "comparison", "analysis"}:
            obj = {"action": "tool_call", "tool": "summarise_global", "arguments": {"user_req": user_text, "intent": intent}}
        else:
            if not self.use_llm:
                obj = _rule_based_next(state, user_text)
            else:
                system = _load_planner_system() + "\n\n" + PLANNER_SCHEMA_INSTRUCTION
                messages = []
                if (system or "").strip():
                    messages.append({"role": "system", "content": system})
                # Provide a compact state snapshot so the planner can skip completed steps
                snapshot = json.dumps(_state_snapshot(state))
                prompt = (
                    "User request:\n" + user_text + "\n\n" +
                    "Recognized intent:\n" + intent + "\n\n" +
                    "State snapshot:\n" + snapshot + "\n\n" +
                    "Decide the next step."
                )
                messages.append({"role": "user", "content": prompt})

                try:
                    raw = self._client().chat_raw(messages=messages, tools=None, tool_choice=None, max_output_tokens=self.max_output_tokens)
                    choice = (raw.get("choices") or [{}])[0]
                    content = (choice.get("message") or {}).get("content", "")
                    obj = _extract_json_object(content)
                except Exception as e:
                    _log(state, "llm_plan_error", {"error": str(e)})
                    obj = _rule_based_next(state, user_text)

        # Normalize tool naming and fill common defaults
        if obj.get("action") == "tool_call":
            obj["tool"] = _normalize_tool_name(obj.get("tool", ""))
            args = obj.get("arguments") or {}
            if obj["tool"] == "fetch_task" and "user_text" not in args:
                args["user_text"] = user_text
            if obj["tool"] == "summarise_global" and "user_req" not in args:
                args["user_req"] = user_text
            # carry intent hint forward for downstream tools if helpful
            if intent and obj["tool"] == "summarise_global" and "intent" not in args:
                args["intent"] = intent
            obj["arguments"] = args
            ok, corrected, reason = _validate_action(state, obj)
            if not ok and corrected:
                _log(state, "plan_corrected", {"from": obj, "reason": reason, "to": corrected})
                obj = corrected
            else:
                _log(state, "plan", {"decision": obj, "reason": reason})
        elif obj.get("action") == "final":
            # Ensure content string exists
            obj["content"] = str(obj.get("content") or "")
            _log(state, "final", {"content_len": len(obj["content"])})
        else:
            # Fallback if malformed
            obj2 = _rule_based_next(state, user_text)
            _log(state, "malformed_plan", {"original": obj, "fallback": obj2})
            obj = obj2

        return obj
