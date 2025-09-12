from __future__ import annotations
import json
import dataclasses
from pathlib import Path
from typing import Any, Callable
from agent.errors import ToolError

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