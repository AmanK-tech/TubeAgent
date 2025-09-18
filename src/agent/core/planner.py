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


def _is_identity_query(user_text: str) -> bool:
    t = (user_text or "").strip().lower()
    if not t:
        return False
    phrases = [
        "who is the youtuber",
        "who is the channel",
        "who is the creator",
        "channel name",
        "youtuber name",
        "who uploaded",
        "what's the channel",
        "whats the channel",
        "what is the channel",
        "what is the youtuber",
    ]
    return any(p in t for p in phrases)


def _has_metadata(state) -> bool:
    try:
        vid = getattr(state, "video", None)
        art = (getattr(state, "artifacts", {}) or {}).get("fetch_task", {}) or (getattr(state, "artifacts", {}) or {}).get("fetch", {})
        if vid and (getattr(vid, "title", None) or getattr(vid, "source_url", None)):
            return True
        if isinstance(art, dict) and (art.get("channel") or art.get("uploader") or art.get("normalized_url")):
            return True
    except Exception:
        pass
    return False


def _wants_metadata(user_text: str, intent: str) -> bool:
    """Heuristic: include metadata in global summary when it's likely relevant.

    - Summary queries that reference channel/title/host/speaker/etc.
    - Identity-like questions (who is the youtuber/channel/creator).
    """
    t = (user_text or "").lower()
    if _is_identity_query(t):
        return True
    if intent == "summary":
        keys = ["channel", "youtuber", "creator", "title", "host", "speaker", "presenter"]
        return any(k in t for k in keys)
    return False


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


def _state_signature(state) -> Dict[str, Any]:
    """Small signature of session progress to control mode churn.

    When this signature changes (e.g., transcript becomes available), we may lift
    the mode lock and re-evaluate routing.
    """
    sig: Dict[str, Any] = {
        "has_video": bool(getattr(state, "video", None)),
        "has_transcript": bool(getattr(state, "transcript", None)),
        "chunk_count": int(len(getattr(state, "chunks", []) or [])),
    }
    try:
        ta = (getattr(state, "artifacts", {}) or {}).get("transcribe_asr", {})
        if isinstance(ta, dict) and ta.get("chunks"):
            sig["transcribed_chunks"] = int(len(ta.get("chunks") or []))
    except Exception:
        pass
    return sig


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

        # Identity fast-path: answer "who is the youtuber/channel" directly from metadata if available
        if intent == "question" and _is_identity_query(user_text):
            art = getattr(state, "artifacts", {}) or {}
            fetch_art = art.get("fetch_task") or art.get("fetch") or {}
            has_meta = bool(fetch_art) or bool(getattr(state, "video", None))
            if has_meta:
                return {"action": "tool_call", "tool": "answer_from_metadata", "arguments": {"question": user_text}}

        # Early fast-paths: if transcript exists and it is a direct follow-up/search/fact request, go straight to global synthesis
        has_transcript = bool(getattr(state, "transcript", None) or getattr(state, "chunks", None))
        if has_transcript and intent in {"question", "search", "time_based", "fact_extraction", "comparison", "analysis"}:
            args = {"user_req": user_text, "intent": intent}
            if _has_metadata(state) and _wants_metadata(user_text, intent):
                args["include_metadata"] = True
            obj = {"action": "tool_call", "tool": "summarise_global", "arguments": args}
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
            # attach include_metadata when warranted and metadata exists
            if obj["tool"] == "summarise_global" and "include_metadata" not in args:
                if _has_metadata(state) and _wants_metadata(user_text, intent):
                    args["include_metadata"] = True
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

    # ---------------------- Hybrid Routing Helpers ---------------------------

    def _choose_mode(self, state, user_text: str, *, history: Optional[list[dict]] = None) -> Dict[str, Any]:
        """Decide whether to use planner or delegate to tool-calling.

        Respects a per-session mode lock in artifacts["planner"]["mode_lock"].
        Lifts the lock when the state signature changes materially (e.g., transcript appears).
        """
        arts = getattr(state, "artifacts", {}) or {}
        arts.setdefault("planner", {})
        lock = arts["planner"].get("mode_lock") if isinstance(arts["planner"], dict) else None
        sig = _state_signature(state)

        # If locked and signature unchanged, reuse the mode to avoid churn
        if isinstance(lock, dict) and lock.get("state_signature") == sig:
            return {"mode": lock.get("mode"), "reason": lock.get("reason"), "locked": True}

        # No lock or signature changed: compute fresh routing
        intent = _classify_intent_heuristic(user_text, history=history)
        has_transcript = bool(getattr(state, "transcript", None) or getattr(state, "chunks", None))
        has_video = bool(getattr(state, "video", None))

        # Default routing: prefer planner unless explicitly complex analysis with transcript
        if not has_video:
            mode = "planner"; reason = "need_initial_pipeline"
        elif has_transcript and intent in {"question", "search", "fact_extraction"}:
            mode = "planner"; reason = "fast_path_available"
        elif intent == "summary" and not has_transcript:
            mode = "planner"; reason = "domain_workflow"
        elif has_transcript and intent in {"comparison", "analysis"}:
            mode = "tools"; reason = "complex_analysis"
        else:
            mode = "planner"; reason = "domain_default"

        # Set/refresh lock with current signature
        try:
            arts["planner"]["mode_lock"] = {"mode": mode, "reason": reason, "state_signature": sig}
        except Exception:
            pass

        return {"mode": mode, "reason": reason, "locked": False}

    def route_and_plan(self, state, user_text: str, *, history: Optional[list[dict]] = None) -> Dict[str, Any]:
        """Hybrid entry point: choose orchestration mode, then return a plan or delegation.

        Returns one of:
          - {"action":"tool_call", ...}  (planner decided a concrete next step)
          - {"action":"final", "content":"..."}
          - {"action":"delegate_tools", "reason":"..."}   (call function-calling controller)

        This does not execute tools; callers should either run dispatch_tool_call() for
        tool_call actions, or switch to the function-calling controller for delegation.
        """
        routing = self._choose_mode(state, user_text, history=history)
        mode = routing.get("mode")
        reason = routing.get("reason")
        _log(state, "routing", {"mode": mode, "reason": reason, "locked": routing.get("locked")})

        if mode == "planner":
            # Use existing planning logic (unchanged pipeline behavior)
            try:
                return self.plan_next(state, user_text, history=history)
            except Exception as e:
                # Normalize error and delegate to tools as fallback
                _log(state, "routing_fallback", {"from": "planner", "error": str(e)})
                return {"action": "delegate_tools", "reason": "planner_error"}
        else:
            # Delegate to function-calling controller
            return {"action": "delegate_tools", "reason": reason}
