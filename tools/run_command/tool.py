"""Run and cancel an explicitly approved command from the project directory."""

import os
import signal
import subprocess
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMMAND_TIMEOUT_SECONDS = 45
OUTPUT_LIMIT = 8000

_JOBS = {}
_JOBS_LOCK = threading.Lock()

DECLARATION = {
    "type": "function",
    "name": "run_terminal_command",
    "description": (
        "Run a shell command needed for the user's requested task, including relevant non-interactive SSH operations. "
        "The app displays the exact command and requires user approval before execution. "
        "Runs from the project directory, captures output, and times out after 45 seconds; it is not interactive. "
        "Prefer minimal purposeful commands and never include secrets. Use open_app to launch desktop applications."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "A concise, relevant shell command for the requested task; do not include secrets.",
            }
        },
        "required": ["command"],
    },
}

LIVE_DECLARATION = {key: value for key, value in DECLARATION.items() if key != "type"}


def cancel_command(command_id: str) -> bool:
    """Request cancellation; the worker terminates the process group promptly."""
    with _JOBS_LOCK:
        job = _JOBS.get(command_id)
        if job is None:
            return False
        job["cancel"].set()
        return True


def _terminate_process(process):
    """Terminate the command and its children without blocking the UI thread."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=5,
                check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.terminate()
        except OSError:
            pass


def _kill_process_tree(process):
    """Force-kill the process group if graceful termination did not finish it."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=5,
                check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass


def run_command(command: str, confirm=None, command_id=None, on_state=None) -> dict:
    """Ask for approval, then run a command with cooperative cancellation."""
    if not isinstance(command, str):
        return {"success": False, "error": "command must be text."}
    command = command.strip()
    if not command or len(command) > 2000 or "\0" in command:
        return {"success": False, "error": "Enter a command between 1 and 2,000 characters."}

    cancel_event = threading.Event()
    job = {"cancel": cancel_event, "process": None}
    if command_id:
        with _JOBS_LOCK:
            _JOBS[command_id] = job

    try:
        if cancel_event.is_set() or confirm is None or not confirm(command, command_id):
            return {
                "success": False,
                "approved": False,
                "cancelled": cancel_event.is_set(),
                "message": "The user did not approve this command.",
            }
        if cancel_event.is_set():
            return {"success": False, "approved": True, "cancelled": True, "message": "Command cancelled."}

        if on_state:
            on_state("running")
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            env=os.environ.copy(),
            start_new_session=(os.name != "nt"),
            creationflags=creationflags,
        )
        with _JOBS_LOCK:
            if command_id in _JOBS:
                _JOBS[command_id]["process"] = process

        elapsed = 0.0
        while True:
            if cancel_event.is_set():
                _terminate_process(process)
                try:
                    stdout, stderr = process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    _kill_process_tree(process)
                    stdout, stderr = process.communicate()
                return {
                    "success": False,
                    "approved": True,
                    "cancelled": True,
                    "message": "Command cancelled by the user.",
                    "return_code": process.returncode,
                    "stdout": stdout[-OUTPUT_LIMIT:],
                    "stderr": stderr[-OUTPUT_LIMIT:],
                    "working_directory": str(PROJECT_ROOT),
                }
            if elapsed >= COMMAND_TIMEOUT_SECONDS:
                _terminate_process(process)
                try:
                    stdout, stderr = process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    _kill_process_tree(process)
                    stdout, stderr = process.communicate()
                return {
                    "success": False,
                    "approved": True,
                    "error": f"Command timed out after {COMMAND_TIMEOUT_SECONDS} seconds.",
                    "stdout": stdout[-OUTPUT_LIMIT:],
                    "stderr": stderr[-OUTPUT_LIMIT:],
                    "working_directory": str(PROJECT_ROOT),
                }
            try:
                stdout, stderr = process.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                elapsed += 0.1

        return {
            "success": process.returncode == 0,
            "approved": True,
            "return_code": process.returncode,
            "stdout": stdout[-OUTPUT_LIMIT:],
            "stderr": stderr[-OUTPUT_LIMIT:],
            "working_directory": str(PROJECT_ROOT),
        }
    except OSError as exc:
        return {"success": False, "approved": True, "error": str(exc)}
    finally:
        if command_id:
            with _JOBS_LOCK:
                _JOBS.pop(command_id, None)
