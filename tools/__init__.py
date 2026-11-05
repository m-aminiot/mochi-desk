"""Register local tools and adapt them for Gemini text and Live API requests."""

from tools.open_app import DECLARATION as OPEN_APP_DECLARATION
from tools.open_app import LIVE_DECLARATION as OPEN_APP_LIVE_DECLARATION
from tools.open_app import open_app
from tools.run_command import DECLARATION as RUN_COMMAND_DECLARATION
from tools.run_command import LIVE_DECLARATION as RUN_COMMAND_LIVE_DECLARATION
from tools.run_command import cancel_command, run_command
from tools.task_scratchpad import DECLARATION as SCRATCHPAD_DECLARATION
from tools.task_scratchpad import LIVE_DECLARATION as SCRATCHPAD_LIVE_DECLARATION
from tools.task_scratchpad import task_scratchpad
import uuid

# Text Interactions declarations include the function type; Live declarations use
# the function_declarations list format expected by the Live connection config.
DECLARATIONS = [OPEN_APP_DECLARATION, RUN_COMMAND_DECLARATION, SCRATCHPAD_DECLARATION]
LIVE_TOOLS = [{
    "function_declarations": [
        OPEN_APP_LIVE_DECLARATION,
        RUN_COMMAND_LIVE_DECLARATION,
        SCRATCHPAD_LIVE_DECLARATION,
    ]
}]
# Handlers map model-visible function names to their local implementations.
_HANDLERS = {
    "open_app": open_app,
    "run_terminal_command": run_command,
    "task_scratchpad": task_scratchpad,
}
# Human-readable titles are sent to the UI while a tool is running.
_TITLES = {
    "open_app": "Open app",
    "run_terminal_command": "Terminal command",
    "task_scratchpad": "Task notes",
}


def execute_tool(name: str, arguments: dict, on_event=None, confirm=None) -> dict:
    """Dispatch one model tool call and return a JSON-serializable result.

    Optional events let the interface show progress. Terminal commands receive
    the approval callback and cancellation ID; other tools execute directly.
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"success": False, "error": f"Unknown tool: {name}"}
    arguments = arguments or {}
    # The ID ties approval, progress events, and cancellation to this exact call.
    tool_call_id = uuid.uuid4().hex

    def emit(state, result=None):
        """Send a best-effort UI event without allowing UI code to break the tool."""
        if on_event:
            try:
                on_event({
                    "state": state,
                    "name": name,
                    "title": _TITLES.get(name, name),
                    "tool_call_id": tool_call_id,
                    "detail": arguments.get(
                        "app_name" if name == "open_app" else "command" if name == "run_terminal_command" else "action",
                        "",
                    ),
                    "result": result,
                })
            except Exception:
                pass

    # Terminal commands pause for explicit approval; local read/write tools start immediately.
    emit("awaiting_approval" if name == "run_terminal_command" else "running")
    try:
        if name == "run_terminal_command":
            # Inject runtime callbacks that are deliberately absent from model arguments.
            result = handler(
                **arguments,
                confirm=confirm,
                command_id=tool_call_id,
                on_state=lambda state: emit(state),
            )
        else:
            result = handler(**arguments)
    except TypeError as exc:
        # Convert malformed model arguments into a normal tool result for the next model turn.
        result = {"success": False, "error": f"Invalid arguments for {name}: {exc}"}
    except Exception as exc:
        # Keep a local tool failure from crashing the whole chat request.
        result = {"success": False, "error": str(exc)}
    # Normalize the event state from the handler's structured result.
    state = (
        "cancelled"
        if result.get("approved") is False or result.get("cancelled")
        else "completed" if result.get("success") else "failed"
    )
    emit(state, result)
    return result


def cancel_tool(tool_call_id: str) -> bool:
    """Request cancellation of a registered terminal command by its call ID."""
    return cancel_command(tool_call_id)
