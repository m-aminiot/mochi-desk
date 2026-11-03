"""Cross-platform application launcher with no shell command evaluation."""

import platform
import configparser
import os
import shlex
import shutil
import subprocess
from pathlib import Path

DECLARATION = {
    "type": "function",
    "name": "open_app",
    "description": (
        "Launch an installed desktop application by its displayed name when the user asks to open it. "
        "Use this for desktop apps, not shell commands or scripts. The result reports whether launch succeeded."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "app_name": {
                "type": "string",
                "description": "Application name, for example Safari, Calculator, or Notes.",
            }
        },
        "required": ["app_name"],
    },
}

LIVE_DECLARATION = {
    key: value for key, value in DECLARATION.items() if key != "type"
}


def open_app(app_name: str) -> dict:
    """Launch an application by its name and report whether launch succeeded."""
    if not isinstance(app_name, str):
        return {"success": False, "error": "app_name must be text."}
    app_name = app_name.strip()
    if (
        not app_name
        or len(app_name) > 120
        or any(ord(char) < 32 for char in app_name)
        or "/" in app_name
        or "\\" in app_name
    ):
        return {"success": False, "error": "Enter a valid application name (1–120 characters)."}

    system = platform.system()
    try:
        if system == "Darwin":
            result = subprocess.run(
                ["open", "-a", app_name], capture_output=True, text=True, timeout=15, check=False
            )
            if result.returncode:
                detail = (result.stderr or result.stdout).strip()
                return {"success": False, "error": detail or f"Could not open {app_name}."}
        elif system == "Windows":
            start_menu_dirs = [
                Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
                Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
            ]
            shortcut = next(
                (
                    item
                    for folder in start_menu_dirs if folder.is_dir()
                    for item in folder.rglob("*.lnk")
                    if item.stem.casefold() == app_name.casefold()
                ),
                None,
            )
            if shortcut:
                os.startfile(str(shortcut))  # type: ignore[attr-defined]
            else:
                executable = shutil.which(app_name)
                if executable:
                    subprocess.Popen([executable], close_fds=True)
                else:
                    # Uses Windows ShellExecute app aliases and registered apps.
                    os.startfile(app_name)  # type: ignore[attr-defined]
        elif system == "Linux":
            executable = shutil.which(app_name)
            if executable:
                subprocess.Popen(
                    [executable], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            else:
                # Resolve the display name to an installed .desktop launcher.
                app_dirs = [
                    Path.home() / ".local/share/applications",
                    Path("/usr/local/share/applications"),
                    Path("/usr/share/applications"),
                ]
                launcher = None
                for folder in app_dirs:
                    if not folder.is_dir():
                        continue
                    for desktop_file in folder.rglob("*.desktop"):
                        parser = configparser.ConfigParser(interpolation=None)
                        try:
                            parser.read(desktop_file, encoding="utf-8")
                            entry = parser["Desktop Entry"]
                        except (OSError, KeyError, configparser.Error):
                            continue
                        if entry.get("Name", "").casefold() == app_name.casefold():
                            if entry.get("NoDisplay", "false").casefold() == "true":
                                continue
                            command = shlex.split(entry.get("Exec", ""))
                            command = [part for part in command if not (part.startswith("%") and len(part) == 2)]
                            if command and shutil.which(command[0]):
                                launcher = command
                                break
                    if launcher:
                        break
                if launcher:
                    subprocess.Popen(
                        launcher, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                else:
                    return {"success": False, "error": f"Application '{app_name}' was not found."}
        else:
            return {"success": False, "error": f"Opening applications is unsupported on {system}."}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"success": False, "error": str(exc)}

    return {"success": True, "app_name": app_name, "message": f"Opened {app_name}."}
