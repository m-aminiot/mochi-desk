"""Run approved shell commands from the project directory and stop them on request."""

import os
import signal
import subprocess
import threading
from pathlib import Path

# Resolve the working directory from this source file, independent of launch location.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Bound command duration and captured output to keep execution responsive and memory use predictable.
COMMAND_TIMEOUT_SECONDS = 45
OUTPUT_LIMIT = 8000

# Active jobs are indexed by tool-call ID so another thread can request cancellation.
_JOBS = {}
_JOBS_LOCK = threading.Lock()

# Model-facing description includes approval, length, timeout, and non-interactive constraints.
DECLARATION = {
    "type": "function",
    "name": "run_terminal_command",
    "description": (
        "Run a shell command needed for the user's requested task, including relevant non-interactive SSH operations. "
        "The app displays the exact command and requires user approval before execution. "
        "Each command must be under 2,000 characters. Split longer work into short, logically ordered commands, "
        "run them one at a time, and inspect each result before continuing. If a command is rejected for length, "
        "split and retry only that unfinished step; do not repeat completed side effects. "
        "Runs from the project directory, captures output, and times out after 45 seconds; it is not interactive. "
        "Prefer minimal purposeful commands and never include secrets. Use open_app to launch desktop applications."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "One concise shell command under 2,000 characters. Split longer tasks into sequential steps; do not include secrets.",
            }
        },
        "required": ["command"],
    },
}

LIVE_DECLARATION = {key: value for key, value in DECLARATION.items() if key != "type"}


def cancel_command(command_id: str) -> bool:
    """Signal a running command to terminate; return whether its job was found."""
    with _JOBS_LOCK:
        job = _JOBS.get(command_id)
        if job is None:
            return False
        job["cancel"].set()
        return True


def _terminate_process(process):
    """Try to stop the process tree gracefully, falling back to the direct process."""
    try:
        if os.name == "nt":
            # Windows does not expose POSIX process groups; taskkill targets the full tree.
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=5,
                check=False,
            )
        else:
            # Commands run in a new session, so the process group includes their child processes.
            os.killpg(process.pid, signal.SIGTERM)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.terminate()
        except OSError:
            pass


def _kill_process_tree(process):
    """Force-stop the process tree after graceful termination exceeds its short wait."""
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
    """Obtain user approval, execute one bounded command, and return captured output.

    The command is run from the project root with a timeout. Cancellation and
    timeout both terminate child processes and return whatever output was captured.
    """
    # Validate before registering a job or displaying an approval prompt.
    if not isinstance(command, str):
        return {"success": False, "error": "command must be text."}
    command = command.strip()
    if not command:
        return {"success": False, "error": "Command is empty. Provide one concise command."}
    if len(command) > 2000:
        return {
            "success": False,
            "error": (
                f"Command is {len(command)} characters; the limit is 2,000. "
                "Split this unfinished step into shorter, logically ordered commands, "
                "then run and inspect them one at a time. Do not repeat completed steps."
            ),
        }
    if "\0" in command:
        return {"success": False, "error": "Command contains an invalid null character."}

    cancel_event = threading.Event()
    job = {"cancel": cancel_event, "process": None}
    if command_id:
        # Publish the cancellation event before waiting for approval so Stop can cancel that wait.
        with _JOBS_LOCK:
            _JOBS[command_id] = job

    try:
        # A missing callback is treated as no approval; tools never self-approve shell commands.
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
        # Start a separate process group/session so timeout and cancellation can stop descendants.
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
                # Try a graceful stop first, then force-kill if children do not exit promptly.
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
                # Apply the same process-tree cleanup when the fixed command limit expires.
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
                # Short communicate intervals keep cancellation responsive while collecting both streams.
                stdout, stderr = process.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                elapsed += 0.1

        # Keep only the tail of each stream so errors at the end remain visible within the output cap.
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
            # Remove completed jobs so later cancellation calls cannot target stale processes.
            with _JOBS_LOCK:
                _JOBS.pop(command_id, None)
