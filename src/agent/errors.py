# errors.py

class AgentError(Exception):
    """Base class for all agent errors."""
    pass


class ToolError(AgentError):
    """Raised when a tool fails (e.g., fetch_video, transcribe_asr)."""
    def __init__(self, message: str, tool_name: str | None = None):
        super().__init__(message)
        self.tool_name = tool_name or "unknown_tool"


class PlanningError(AgentError):
    """Raised when the planner cannot decide the next action."""
    pass


class BudgetExceeded(AgentError):
    """Raised when token or cost budgets are exceeded."""
    def __init__(self, message: str, current_cost: float, limit: float):
        super().__init__(message)
        self.current_cost = current_cost
        self.limit = limit
