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