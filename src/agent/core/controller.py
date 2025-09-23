from __future__ import annotations

import json
import time
from collections import deque
from typing import Optional

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
        return msg.get("content") or ""

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
            return route.get("content") or ""
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
