"""Local web interface for Mochi Desk."""

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from config import (TOKEN_FILE, friendly_gemini_error, get_app_data_dir, get_api_base_url,
                    read_api_key, use_custom_api_base_url)
from text import TEXT_MODELS, TextChat
from voice import VoiceSession

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
PREFERENCES_FILE = get_app_data_dir() / "settings.json"
THEMES = {
    "Violet": {"accent": "#a78bfa", "hover": "#8b6fe0"},
    "Ocean": {"accent": "#38bdf8", "hover": "#0ea5e9"},
    "Rose": {"accent": "#f472b6", "hover": "#db579b"},
}


def load_preferences():
    defaults = {"theme": "Violet", "model": "gemini-3.5-flash-lite",
                "api_base_url": get_api_base_url(),
                "use_custom_api_base_url": use_custom_api_base_url()}
    try:
        saved = json.loads(PREFERENCES_FILE.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            defaults.update(saved)
    except (OSError, ValueError, TypeError):
        pass
    if defaults["theme"] not in THEMES:
        defaults["theme"] = "Violet"
    if defaults["model"] not in TEXT_MODELS.values():
        defaults["model"] = "gemini-3.5-flash-lite"
    return defaults


def save_preferences(preferences):
    clean = {
        "theme": preferences["theme"] if preferences.get("theme") in THEMES else "Violet",
        "model": preferences["model"] if preferences.get("model") in TEXT_MODELS.values()
        else "gemini-3.5-flash-lite",
        "api_base_url": str(preferences.get("api_base_url", get_api_base_url()) or "").strip(),
        "use_custom_api_base_url": bool(preferences.get("use_custom_api_base_url", False)),
    }
    if clean["use_custom_api_base_url"] and clean["api_base_url"]:
        parsed = urlparse(clean["api_base_url"])
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("API base URL must be a valid http:// or https:// URL.")
    temporary = PREFERENCES_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(clean, indent=2), encoding="utf-8")
    temporary.replace(PREFERENCES_FILE)


def save_api_key(api_key):
    api_key = str(api_key).strip()
    if not api_key or "\n" in api_key or "\r" in api_key:
        raise ValueError("Enter a valid API key on one line.")
    temporary = TOKEN_FILE.with_suffix(".txt.tmp")
    temporary.write_text(f"api_key={api_key}\n", encoding="utf-8")
    temporary.replace(TOKEN_FILE)


def _step_text(step):
    for item in step.get("content", []) or []:
        if isinstance(item, dict) and item.get("text"):
            return item["text"]
    return ""


class MochiApp:
    def __init__(self):
        self.lock = threading.RLock()
        self.events = []
        self.event_id = 0
        self.pending = {}
        self.busy = False
        self.cancel_event = None
        self.active_chat = None
        self.voice_chat_id = None
        self.voice_status = "Voice is ready when you are."
        self.voice_reply = ""
        self.voice_muted = False
        self.preferences = load_preferences()
        try:
            self.chat = TextChat()
            self.startup_error = ""
        except Exception as exc:
            self.chat = None
            self.startup_error = str(exc)
        self.voice = VoiceSession(
            on_status=self._voice_status,
            on_text=self._voice_text,
            on_tool_event=lambda event: self.emit("tool", {"mode": "voice", **event}),
            confirm_tool=self._confirm_tool,
        )

    def emit(self, kind, data):
        with self.lock:
            self.event_id += 1
            self.events.append({"sequence": self.event_id, "type": kind, **data})
            self.events = self.events[-500:]

    def configuration_status(self):
        try:
            read_api_key()
            api_key_configured = True
        except (OSError, ValueError):
            api_key_configured = False
        return {
            "api_key_configured": api_key_configured,
            "api_base_url_configured": (not use_custom_api_base_url() or bool(get_api_base_url())),
        }

    def _voice_status(self, message):
        with self.lock:
            self.voice_status = message
        self.emit("voice_status", {"message": message})

    def _voice_text(self, message):
        with self.lock:
            self.voice_reply = message
        self.emit("voice_text", {"message": message})

    def _confirm_tool(self, command, tool_call_id):
        decision = threading.Event()
        with self.lock:
            self.pending[tool_call_id] = {"command": command, "decision": decision, "approved": False}
        self.emit("approval", {"id": tool_call_id, "command": command, "directory": str(ROOT)})
        decision.wait(timeout=300)
        with self.lock:
            item = self.pending.pop(tool_call_id, None)
        return bool(item and item["approved"])

    def decide(self, tool_id, approved):
        with self.lock:
            item = self.pending.get(tool_id)
            if not item:
                return False
            item["approved"] = bool(approved)
            item["decision"].set()
            return True

    def stop(self):
        with self.lock:
            cancel = self.cancel_event
            pending = list(self.pending.values())
            chat = self.active_chat or self.chat
            for item in pending:
                item["approved"] = False
                item["decision"].set()
        if cancel:
            cancel.set()
        if chat is not None:
            chat.cancel_active_request()

    def state(self, since=0):
        with self.lock:
            events = [event for event in self.events if event["sequence"] > since]
            active = self.chat.get_chat() if self.chat else None
            chats = self.chat.list_chats() if self.chat else []
            turns = active.get("turns", []) if active else []
            messages = []
            for turn in turns:
                for step in turn:
                    if step.get("type") in {"function_call", "function_result"}:
                        continue
                    content = _step_text(step)
                    if content:
                        messages.append({"role": "user" if step.get("type") == "user_input" else "assistant", "content": content})
            pending = [{"id": key, "command": value["command"], "directory": str(ROOT)}
                       for key, value in self.pending.items() if not value["decision"].is_set()]
            configuration = self.configuration_status()
            return {
                "events": events,
                "next_event_id": self.event_id,
                "chats": [{"id": c["id"], "title": c["title"], "preview": next(
                    (_step_text(s) for t in reversed(c.get("turns", [])) for s in t if s.get("type") == "user_input"), "New conversation"
                )} for c in chats],
                "active_chat_id": active.get("id") if active else None,
                "active_title": active.get("title", "New chat") if active else "Mochi Desk",
                "messages": messages,
                "busy": self.busy,
                "pending": pending,
                "voice_status": self.voice_status,
                "voice_reply": self.voice_reply,
                "voice_muted": self.voice_muted,
                "startup_error": self.startup_error,
                **configuration,
                "setup_required": not all(configuration.values()),
                "preferences": self.preferences,
                "models": list(TEXT_MODELS.items()),
                "themes": list(THEMES),
            }

    def send(self, message):
        message = str(message).strip()
        if not message:
            return
        if self.chat is None:
            raise ValueError(self.startup_error or "Configure the Gemini API key and endpoint in Settings.")
        with self.lock:
            if self.busy:
                raise ValueError("A reply is already in progress.")
            self.busy = True
            self.cancel_event = threading.Event()
            cancel = self.cancel_event
            chat = self.chat
            self.active_chat = chat
            chat_id = chat.get_active_chat_id()
            model = self.preferences["model"]
        self.emit("user_message", {"chat_id": chat_id, "message": message})
        self.emit("busy", {"value": True})

        def work():
            try:
                answer = chat.send_message(
                    message, chat_id=chat_id, model=model, cancel_event=cancel,
                    on_tool_event=lambda event: self.emit("tool", {"mode": "text", **event}),
                    confirm_tool=self._confirm_tool,
                )
                if not cancel.is_set():
                    self.emit("assistant_message", {"chat_id": chat_id, "message": answer or "(No text was returned.)"})
            except Exception as exc:
                if not cancel.is_set():
                    self.emit("error", {"message": friendly_gemini_error(exc)})
            finally:
                try:
                    chat.finish_active_request()
                finally:
                    with self.lock:
                        self.busy = False
                        self.cancel_event = None
                        self.active_chat = None
                    self.emit("busy", {"value": False})

        threading.Thread(target=work, daemon=True).start()

    def close(self):
        self.stop()
        self.voice.stop()


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "MochiDesk/1"

        def log_message(self, _format, *_args):
            pass

        def _json(self, data, status=200):
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _local_request(self):
            host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").lower()
            if host not in {"127.0.0.1", "localhost", "::1"}:
                return False
            origin = self.headers.get("Origin")
            return not origin or origin in {f"http://127.0.0.1:{self.server.server_port}",
                                           f"http://localhost:{self.server.server_port}"}

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/api/state":
                if not self._local_request():
                    return self._json({"error": "Local requests only."}, 403)
                from urllib.parse import parse_qs
                try:
                    since = int(parse_qs(parsed.query).get("since", [0])[0])
                except ValueError:
                    since = 0
                return self._json(app.state(since))
            files = {"/": "index.html", "/style.css": "style.css", "/app.js": "app.js"}
            filename = files.get(parsed.path)
            if not filename:
                return self._json({"error": "Not found."}, 404)
            raw = (WEB_ROOT / filename).read_bytes()
            content_type = "text/html; charset=utf-8" if filename.endswith("html") else "text/" + ("css" if filename.endswith("css") else "javascript") + "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if not self._local_request():
                return self._json({"error": "Local requests only."}, 403)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 65536:
                    return self._json({"error": "Request is too large."}, 413)
                data = json.loads(self.rfile.read(length) or b"{}")
                action = urlparse(self.path).path
                if action == "/api/send":
                    app.send(data.get("message", ""))
                elif action == "/api/stop":
                    app.stop()
                elif action == "/api/new-chat":
                    if not app.chat:
                        raise ValueError(app.startup_error)
                    app.chat.create_chat()
                    app.emit("refresh", {})
                elif action == "/api/select-chat":
                    app.chat.select_chat(data.get("id"))
                    app.emit("refresh", {})
                elif action == "/api/delete-chat":
                    app.chat.delete_chat(data.get("id"))
                    app.emit("refresh", {})
                elif action in {"/api/approve", "/api/reject"}:
                    if not app.decide(data.get("id", ""), action == "/api/approve"):
                        raise ValueError("This approval request has expired.")
                elif action == "/api/voice":
                    if data.get("active"):
                        configuration = app.configuration_status()
                        if not all(configuration.values()) or app.chat is None:
                            raise ValueError(app.startup_error or "Configure the Gemini API key and endpoint in Settings first.")
                        app.voice_chat_id = app.chat.get_active_chat_id() if app.chat else None
                        app.voice_muted = False
                        app.voice.set_microphone_muted(False)
                        app.voice_status = "Connecting to Gemini Live…"
                        app.voice.start()
                    else:
                        app.voice.stop()
                        app.voice_muted = False
                        app.voice.set_microphone_muted(False)
                elif action == "/api/mute":
                    app.voice_muted = bool(data.get("muted"))
                    app.voice.set_microphone_muted(app.voice_muted)
                elif action == "/api/settings":
                    updated = {**app.preferences,
                               "theme": data.get("theme", app.preferences["theme"]),
                               "model": data.get("model", app.preferences["model"]),
                               "api_base_url": data.get("api_base_url", app.preferences["api_base_url"]),
                               "use_custom_api_base_url": data.get(
                                   "use_custom_api_base_url", app.preferences["use_custom_api_base_url"])}
                    save_preferences(updated)
                    if data.get("api_key"):
                        save_api_key(data["api_key"])
                    app.preferences = load_preferences()
                    configuration = app.configuration_status()
                    if all(configuration.values()):
                        try:
                            if app.chat is None:
                                app.chat = TextChat()
                            else:
                                app.chat.reload_client()
                            app.startup_error = ""
                        except Exception as exc:
                            app.startup_error = str(exc)
                    else:
                        app.chat = None
                        missing = []
                        if not configuration["api_key_configured"]:
                            missing.append("Gemini API key")
                        if (app.preferences["use_custom_api_base_url"]
                                and not configuration["api_base_url_configured"]):
                            missing.append("API base URL")
                        app.startup_error = "Configure " + " and ".join(missing) + " in Settings."
                    app.emit("refresh", {})
                    return self._json({"ok": True, "restart_required": False})
                else:
                    return self._json({"error": "Not found."}, 404)
                return self._json({"ok": True})
            except (ValueError, KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
                return self._json({"error": str(exc)}, 400)

    return Handler


def main():
    app = MochiApp()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    server.daemon_threads = True
    address = f"http://127.0.0.1:{server.server_port}/"
    print(f"Mochi Desk is running at {address}")
    threading.Timer(0.4, lambda: webbrowser.open(address)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        app.close()


if __name__ == "__main__":
    main()
