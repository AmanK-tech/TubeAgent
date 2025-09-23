from __future__ import annotations

import json
import time
from collections import deque
from typing import Optional
from pathlib import Path
import os

from agent.errors import ToolError
from agent.llm.client import LLMClient
from agent.core.toolkit import get_tools, dispatch_tool_call
from agent.core.planner import Planner

# ---- small, local safeguards ----
MAX_MSG_WINDOW = 24           # cap chat history to avoid context bloat
MAX_TOOL_CONTENT = 8000       # cap tool payload echoed back to LLM


def _as_tool_content(state, payload, *, limit: int = MAX_TOOL_CONTENT) -> str:
    """Serialize tool payload; truncate if huge. Optionally store full in cache."""
    s = json.dumps(payload, ensure_ascii=False)
    if len(s) <= limit:
        return s
    # If you have a blob store on state.cache, use it; otherwise just mark truncated.
    blob_store = getattr(getattr(state, "cache", None), "store_blob", None)
    key = blob_store(s) if callable(blob_store) else None
    return json.dumps({"truncated": True, "blob_key": key, "preview": s[:limit]}, ensure_ascii=False)


def _persist_enabled() -> bool:
    v = (os.getenv("PERSIST_CHAT_HISTORY", "") or "").strip().lower()
    return v in {"1", "true", "yes", "on"}


def _runtime_dir(state) -> Path:
    try:
        rd = getattr(getattr(state, "config", None), "runtime_dir", None)
        return Path(rd) if rd else Path("runtime")
    except Exception:
        return Path("runtime")


def _current_job_id(state) -> str:
    try:
        arts = getattr(state, "artifacts", {}) or {}
        ta = arts.get("transcribe_asr", {}) or {}
        mp = ta.get("manifest_path")
        if mp:
            return Path(mp).parent.name
    except Exception:
        pass
    return "session"


def _load_chat_history(state) -> list[dict]:
    """Return stored chat history for the current job id; optionally load from disk.

    Only keeps minimal entries of the form {role, content} with role in {user, assistant}.
    """
    try:
        arts = getattr(state, "artifacts", None)
        if arts is None:
            return []
        arts.setdefault("chat_history", {})
        jid = _current_job_id(state)
        if jid in arts["chat_history"] and isinstance(arts["chat_history"][jid], list):
            return arts["chat_history"][jid]
        # Optionally load persisted history from disk
        if _persist_enabled():
            fp = _runtime_dir(state) / "sessions" / jid / "chat_history.json"
            if fp.exists():
                try:
                    data = json.load(fp.open("r", encoding="utf-8"))
                    if isinstance(data, list):
                        norm = []
                        for m in data:
                            if not isinstance(m, dict):
                                continue
                            role = (m.get("role") or "").strip()
                            content = m.get("content")
                            if role in {"user", "assistant"} and isinstance(content, str):
                                norm.append({"role": role, "content": content})
                        arts["chat_history"][jid] = norm
                        return norm
                except Exception:
                    pass
        arts["chat_history"][jid] = []
        return arts["chat_history"][jid]
    except Exception:
        return []


def _save_chat_history(state, history: list[dict]) -> None:
    try:
        arts = getattr(state, "artifacts", None)
        if arts is None:
            return
        jid = _current_job_id(state)
        arts.setdefault("chat_history", {})
        arts["chat_history"][jid] = history
        if _persist_enabled():
            base = _runtime_dir(state) / "sessions" / jid
            base.mkdir(parents=True, exist_ok=True)
            fp = base / "chat_history.json"
            with fp.open("w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _append_and_save_history(state, user_text: str, assistant_text: str) -> None:
    hist = _load_chat_history(state)
    if (user_text or "").strip():
        hist.append({"role": "user", "content": user_text})
    if (assistant_text or "").strip():
        hist.append({"role": "assistant", "content": assistant_text})
    # Trim to max window
    if len(hist) > MAX_MSG_WINDOW:
        del hist[: len(hist) - MAX_MSG_WINDOW]
    _save_chat_history(state, hist)


def _safe_dispatch(state, name: str, args: dict, *, max_attempts: int = 3, backoff: float = 0.5):
    """Retry flaky tools briefly; on final failure, return a structured error object."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return dispatch_tool_call(state, name, args)
        except Exception as e:
            if attempt >= max_attempts:
                return {"ok": False, "error": str(e), "tool": name, "args": args}
            time.sleep(backoff * (2 ** (attempt - 1)))


def run_session(
    state,
    user_text: str,
    *,
    system_instruction: Optional[str] = None,
    tool_choice: Optional[object] = None,
    max_output_tokens: Optional[int] = None,
) -> str:
    """Run a function-calling session with the LLM and our tool dispatcher.

    Returns the final assistant content when the model stops calling tools.
    Designed to be called by a backend handler or CLI.
    """
    provider = getattr(state.config, "provider", "deepseek")
    model = getattr(state.config, "model", "deepseek-chat")
    llm = LLMClient(provider=provider, model=model)

    tools = get_tools()
    messages: list[dict] = []
    if (system_instruction or "").strip():
        messages.append({"role": "system", "content": system_instruction})
    # Seed prior chat history for continuity
    prior = _load_chat_history(state)
    if prior:
        # Only include the most recent window to keep context lean
        window = prior[-(MAX_MSG_WINDOW - 1):] if len(prior) >= (MAX_MSG_WINDOW - 1) else prior
        messages.extend(window)
    # Current user turn
    messages.append({"role": "user", "content": user_text})

    step_limit = getattr(state.config, "step_limit", 8) or 8
    out_tokens = int(max_output_tokens or getattr(state.config, "max_tokens", 1024) or 1024)

    # track simple budget/loop hygiene
    recent_tools: deque[tuple[str, str]] = deque(maxlen=4)
    total_tokens = 0
    total_cost = 0.0
    forced_choice = tool_choice  # only force once if provided

    for _ in range(max(1, step_limit)):
        resp = llm.chat_raw(messages=messages, tools=tools, tool_choice=forced_choice, max_output_tokens=out_tokens)
        forced_choice = None  # allow free tool choice after first round

        # usage → budget hooks (if available)
        usage = (resp or {}).get("usage") or {}
        step_tokens = usage.get("total_tokens") or (
            (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)
        )
        total_tokens += int(step_tokens or 0)
        if hasattr(state, "cost") and hasattr(state.cost, "add_llm_tokens"):
            state.cost.add_llm_tokens(int(step_tokens or 0))
            total_cost = getattr(state.cost, "total_cost", 0.0)
        if getattr(getattr(state, "budget", None), "exceeded", None):
            if state.budget.exceeded(total_tokens, total_cost):
                raise ToolError("Budget exceeded", tool_name="controller")
        choices = resp.get("choices") or []
        if not choices:
            raise ToolError("DeepSeek returned no choices", tool_name="controller")
        msg = choices[0].get("message") or {}

        # Record assistant message (with tool_calls if any) for traceability
        messages.append({k: v for k, v in msg.items() if k in {"role", "content", "tool_calls"}} or {"role": "assistant", "content": ""})

        tool_calls = msg.get("tool_calls") or []
        if tool_calls:
            for tc in tool_calls:
                fn = (tc or {}).get("function", {})
                name = fn.get("name") or ""
                args_json = fn.get("arguments") or "{}"
                try:
                    args = json.loads(args_json)
                except Exception:
                    args = {}
                # Repeat-tool loop guard (same tool + same args N times)
                sig = (name, json.dumps(args, sort_keys=True))
                recent_tools.append(sig)
                if len(recent_tools) == recent_tools.maxlen and len(set(recent_tools)) == 1:
                    raise ToolError(f"Repeat tool loop detected: {name}", tool_name="controller")

                # Execute tool (with retries) and cap payload size back to LLM
                tool_result = _safe_dispatch(state, name, args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "name": name,
                        "content": _as_tool_content(state, tool_result),
                    }
                )
            # Trim message window to protect context budget
            if len(messages) > MAX_MSG_WINDOW:
                messages = [messages[0]] + messages[-(MAX_MSG_WINDOW - 1):]
            # Ask again with tool results appended
            continue

        # No tool calls => final answer
        final_text = msg.get("content") or ""
        _append_and_save_history(state, user_text, final_text)
        return final_text

    raise ToolError("Step limit reached without final answer", tool_name="controller")


def run_hybrid_session(
    state,
    user_text: str,
    *,
    system_instruction: Optional[str] = None,
    tool_choice: Optional[object] = None,
    max_output_tokens: Optional[int] = None,
    max_steps: Optional[int] = None,
) -> str:
    """Hybrid orchestration: planner routing with fallback to function-calling.

    - Uses Planner.route_and_plan to decide between planner-driven step(s) and
      delegation to the function-calling controller (run_session).
    - Avoids mode churn by honoring planner's internal mode lock.
    - Keeps artifacts consistent by executing actual tools via dispatch_tool_call.
    """
    planner = Planner(model=getattr(state.config, "model", "deepseek-chat"))

    # Small loop-guard for planner-driven steps
    from collections import deque
    recent_tools: deque[tuple[str, str]] = deque(maxlen=4)

    steps = int(max_steps or getattr(state.config, "step_limit", 8) or 8)
    for _ in range(max(1, steps)):
        route = planner.route_and_plan(state, user_text)
        act = route.get("action")
        if act == "delegate_tools":
            # Delegate to function-calling controller for complex/creative analyses
            return run_session(
                state,
                user_text,
                system_instruction=system_instruction,
                tool_choice=tool_choice,
                max_output_tokens=max_output_tokens,
            )
        if act == "final":
            final_text = route.get("content") or ""
            _append_and_save_history(state, user_text, final_text)
            return final_text
        if act == "tool_call":
            name = route.get("tool")
            params = route.get("arguments") or {}
            # Repeat-tool loop guard
            import json as _json
            sig = (name or "", _json.dumps(params, sort_keys=True))
            recent_tools.append(sig)
            if len(recent_tools) == recent_tools.maxlen and len(set(recent_tools)) == 1:
                raise ToolError(f"Repeat tool loop detected: {name}", tool_name="controller")

            # Execute the tool via dispatcher
            try:
                res = dispatch_tool_call(state, name, params)
            except Exception as e:
                # Normalize by delegating to tools path on planner execution error
                return run_session(
                    state,
                    user_text,
                    system_instruction=system_instruction,
                    tool_choice=tool_choice,
                    max_output_tokens=max_output_tokens,
                )

            # If transcribe_asr was called with user_req and returned text, we can finish here
            if name == "transcribe_asr" and isinstance(res, dict) and res.get("ok") and isinstance(res.get("result"), str):
                return res.get("result") or ""

            # Otherwise continue the loop to plan next step
            continue

        # Unexpected action → delegate to tools as a safe default
        return run_session(
            state,
            user_text,
            system_instruction=system_instruction,
            tool_choice=tool_choice,
            max_output_tokens=max_output_tokens,
        )

    raise ToolError("Step limit reached without final answer", tool_name="controller")


def run_agent_with_tools(
    state,
    user_text: str,
    *,
    system_instruction: Optional[str] = None,
    tool_choice: Optional[object] = None,
    max_output_tokens: Optional[int] = None,
) -> str:
    """Compatibility alias for existing callers; delegates to run_session."""
    return run_session(
        state,
        user_text,
        system_instruction=system_instruction,
        tool_choice=tool_choice,
        max_output_tokens=max_output_tokens,
    )
