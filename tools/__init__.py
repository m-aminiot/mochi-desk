"""Tool registry shared by Gemini text and voice interactions."""

from tools.open_app import DECLARATION as OPEN_APP_DECLARATION
from tools.open_app import LIVE_DECLARATION as OPEN_APP_LIVE_DECLARATION
from tools.open_app import open_app
from tools.run_command import DECLARATION as RUN_COMMAND_DECLARATION
from tools.run_command import LIVE_DECLARATION as RUN_COMMAND_LIVE_DECLARATION
from tools.run_command import cancel_command, run_command
import uuid

DECLARATIONS = [OPEN_APP_DECLARATION, RUN_COMMAND_DECLARATION]
LIVE_TOOLS = [{
    "function_declarations": [OPEN_APP_LIVE_DECLARATION, RUN_COMMAND_LIVE_DECLARATION]
}]
_HANDLERS = {"open_app": open_app, "run_terminal_command": run_command}
_TITLES = {"open_app": "Open app", "run_terminal_command": "Terminal command"}


def execute_tool(name: str, arguments: dict, on_event=None, confirm=None) -> dict:
    """Run a registered tool and return a JSON-serializable result."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"success": False, "error": f"Unknown tool: {name}"}
    arguments = arguments or {}
    tool_call_id = uuid.uuid4().hex

    def emit(state, result=None):
        if on_event:
            try:
                on_event({
                    "state": state,
                    "name": name,
                    "title": _TITLES.get(name, name),
                    "tool_call_id": tool_call_id,
                    "detail": arguments.get("app_name" if name == "open_app" else "command", ""),
                    "result": result,
                })
            except Exception:
                pass

    emit("awaiting_approval" if name == "run_terminal_command" else "running")
    try:
        if name == "run_terminal_command":
            result = handler(
                **arguments,
                confirm=confirm,
                command_id=tool_call_id,
                on_state=lambda state: emit(state),
            )
        else:
            result = handler(**arguments)
    except TypeError as exc:
        result = {"success": False, "error": f"Invalid arguments for {name}: {exc}"}
    except Exception as exc:
        result = {"success": False, "error": str(exc)}
    state = (
        "cancelled"
        if result.get("approved") is False or result.get("cancelled")
        else "completed" if result.get("success") else "failed"
    )
    emit(state, result)
    return result


def cancel_tool(tool_call_id: str) -> bool:
    """Request cancellation of a running terminal tool call."""
    return cancel_command(tool_call_id)
