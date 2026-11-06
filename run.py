"""Local web interface for Mochi Desk."""

import json
import base64
import hashlib
import logging
from logging.handlers import RotatingFileHandler
import re
import struct
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from config import (TOKEN_FILE, DEFAULT_SETTINGS, _read_settings, friendly_gemini_error, get_app_data_dir,
                    get_api_base_url, read_api_key, save_settings, use_custom_api_base_url)
from text import TEXT_MODELS, UPLOAD_DIR, TextChat
from voice import VoiceSession

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
UPLOAD_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".heic": "image/heic",
    ".heif": "image/heif", ".bmp": "image/bmp", ".tif": "image/tiff",
    ".tiff": "image/tiff", ".pdf": "application/pdf",
    ".txt": "text/plain", ".md": "text/plain", ".csv": "text/plain",
    ".json": "text/plain", ".py": "text/plain", ".js": "text/plain",
    ".ts": "text/plain", ".jsx": "text/plain", ".tsx": "text/plain",
    ".html": "text/plain", ".css": "text/plain", ".xml": "text/plain",
    ".yaml": "text/plain", ".yml": "text/plain", ".toml": "text/plain",
    ".sql": "text/plain", ".sh": "text/plain", ".c": "text/plain",
    ".h": "text/plain", ".cpp": "text/plain", ".java": "text/plain",
    ".go": "text/plain", ".rs": "text/plain", ".php": "text/plain",
    ".rb": "text/plain", ".log": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".odp": "application/vnd.oasis.opendocument.presentation",
}
LOG_DIR = get_app_data_dir() / "logs"
LOGGER = logging.getLogger("mochi")
LOGGER.setLevel(logging.INFO)
if not LOGGER.handlers:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_DIR / "mochi.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    LOGGER.addHandler(handler)
THEMES = {
    "Violet": {"accent": "#a78bfa", "hover": "#8b6fe0"},
    "Ocean": {"accent": "#38bdf8", "hover": "#0ea5e9"},
    "Rose": {"accent": "#f472b6", "hover": "#db579b"},
    "Emerald": {"accent": "#34d399", "hover": "#10b981"},
    "Amber": {"accent": "#fbbf24", "hover": "#f59e0b"},
    "Midnight": {"accent": "#818cf8", "hover": "#6366f1"},
    "Light": {"accent": "#7c5ce7", "hover": "#6845d2"},
}


def load_preferences():
    saved = _read_settings()
    defaults = {**DEFAULT_SETTINGS, **{key: value for key, value in saved.items() if key in DEFAULT_SETTINGS}}
    if not isinstance(saved.get("use_custom_api_base_url"), bool):
        defaults["use_custom_api_base_url"] = bool(defaults["api_base_url"])
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
    save_settings(clean)


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
        self.websocket_clients = set()
        self.event_id = 0
        self.pending = {}
        self.busy = False
        self.cancel_event = None
        self.active_chat = None
        self.voice_chat_id = None
        self.voice_status = "Voice is ready when you are."
        self.voice_reply = ""
        self.voice_levels = {"input": 0.0, "output": 0.0}
        self.last_voice_event = 0.0
        self.voice_muted = False
        self.preferences = load_preferences()
        try:
            self.chat = TextChat()
            self.startup_error = ""
        except Exception as exc:
            self.chat = None
            self.startup_error = str(exc)
            LOGGER.exception("Could not initialize chat storage or client")
        self.voice = VoiceSession(
            on_status=self._voice_status,
            on_text=self._voice_text,
            on_tool_event=lambda event: self.emit("tool", {"mode": "voice", **event}),
            confirm_tool=self._confirm_tool,
        )
        self.voice.on_level = self._voice_level

    def emit(self, kind, data):
        with self.lock:
            self.event_id += 1
            self.events.append({"sequence": self.event_id, "type": kind, **data})
            self.events = self.events[-500:]
            for client in tuple(getattr(self, "websocket_clients", ())):
                try:
                    client.send_event(self.events[-1])
                except OSError:
                    self.websocket_clients.discard(client)

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
        if message.startswith("Voice error:"):
            LOGGER.error("Voice session failed: %s", message)
        with self.lock:
            self.voice_status = message
        self.emit("voice_status", {"message": message})

    def _voice_text(self, message):
        with self.lock:
            self.voice_reply = message
        self.emit("voice_text", {"message": message})

    def _voice_level(self, channel, value):
        self.voice_levels[channel] = value
        now = time.monotonic()
        if now - self.last_voice_event >= 0.05:
            self.last_voice_event = now
            self.emit("voice_level", {"levels": self.voice.levels()})

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
                    attachments = [{"id": item["id"], "name": item["name"],
                                        "mime_type": item["mime_type"], "size": item["size"]}
                                       for item in step.get("content", [])
                                       if isinstance(item, dict) and item.get("type") == "attachment"]
                    if content or attachments:
                        messages.append({"role": "user" if step.get("type") == "user_input" else "assistant",
                                          "content": content, "attachments": attachments})
            pending = [{"id": key, "command": value["command"], "directory": str(ROOT)}
                       for key, value in self.pending.items() if not value["decision"].is_set()]
            configuration = self.configuration_status()
            return {
                "events": events,
                "next_event_id": self.event_id,
                "chats": [{"id": c["id"], "title": c["title"], "created_at": c.get("created_at"), "updated_at": c.get("updated_at"), "preview": next(
                    (_step_text(s) for t in reversed(c.get("turns", [])) for s in t if s.get("type") == "user_input"), "New conversation"
                )} for c in chats],
                "active_chat_id": active.get("id") if active else None,
                "active_title": active.get("title", "New chat") if active else "Mochi Desk",
                "messages": messages,
                "busy": self.busy,
                "pending": pending,
                "voice_status": self.voice_status,
                "voice_reply": self.voice_reply,
                "voice_levels": self.voice.levels(),
                "voice_muted": self.voice_muted,
                "startup_error": self.startup_error,
                **configuration,
                "setup_required": not all(configuration.values()),
                "preferences": self.preferences,
                "models": list(TEXT_MODELS.items()),
                "themes": list(THEMES),
            }

    def send(self, message, attachments=None):
        message = str(message).strip()
        attachments = attachments or []
        if not message and not attachments:
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
        self.emit("user_message", {"chat_id": chat_id, "message": message, "attachments": attachments})
        self.emit("busy", {"value": True})

        def work():
            try:
                answer = chat.send_message(
                    message, chat_id=chat_id, model=model, cancel_event=cancel,
                    attachments=attachments,
                    on_delta=lambda delta: self.emit("assistant_delta", {"chat_id": chat_id, "delta": delta}),
                    on_tool_event=lambda event: self.emit("tool", {"mode": "text", **event}),
                    confirm_tool=self._confirm_tool,
                )
                if not cancel.is_set():
                    self.emit("assistant_message", {"chat_id": chat_id, "message": answer or "(No text was returned.)"})
            except Exception as exc:
                if not cancel.is_set():
                    LOGGER.exception("Text request failed")
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


class WebSocketEventClient:
    """Serialize event writes for one upgraded HTTP handler connection."""

    def __init__(self, handler):
        self.handler = handler
        self.lock = threading.Lock()

    def send_event(self, event):
        """Write one JSON event frame without interleaving concurrent callbacks."""
        with self.lock:
            self.handler._websocket_frame(event)

    def send_frame(self, payload, opcode):
        """Send a protocol control frame through the same synchronized writer."""
        with self.lock:
            self.handler._websocket_frame(payload, opcode)


class MochiHTTPServer(ThreadingHTTPServer):
    """Log uncaught request-handler failures with their traceback."""

    def handle_error(self, request, client_address):
        LOGGER.exception("Unhandled HTTP handler error from %s", client_address[0])


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "MochiDesk/1"

        def log_message(self, _format, *_args):
            pass

        def _websocket_frame(self, payload, opcode=1):
            """Send one unmasked server-to-client WebSocket text frame."""
            payload = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            size = len(payload)
            header = bytes([0x80 | opcode]) + (bytes([size]) if size < 126 else bytes([126]) + struct.pack("!H", size) if size < 65536 else bytes([127]) + struct.pack("!Q", size))
            self.wfile.write(header + payload)
            self.wfile.flush()

        def _websocket_events(self):
            """Upgrade a trusted local connection and push app events until it closes."""
            if not self._local_request() or self.headers.get("Upgrade", "").lower() != "websocket":
                return self._json({"error": "WebSocket upgrade required from localhost."}, 403)
            key = self.headers.get("Sec-WebSocket-Key", "")
            if not key:
                return self._json({"error": "Missing WebSocket key."}, 400)
            accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            client = WebSocketEventClient(self)
            try:
                with app.lock:
                    app.websocket_clients.add(client)
                    snapshot = app.state()
                    client.send_event({"type": "snapshot", "state": snapshot})
                while True:
                    first = self.rfile.read(2)
                    if len(first) != 2:
                        break
                    opcode, length = first[0] & 0x0F, first[1] & 0x7F
                    if length == 126:
                        length = struct.unpack("!H", self.rfile.read(2))[0]
                    elif length == 127:
                        length = struct.unpack("!Q", self.rfile.read(8))[0]
                    if not first[1] & 0x80:
                        break
                    mask = self.rfile.read(4) if first[1] & 0x80 else b""
                    if length > 65536:
                        break
                    payload = self.rfile.read(length)
                    if mask and len(payload) == length:
                        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
                    if opcode == 8:
                        break
                    if opcode == 9:
                        client.send_frame(payload, 10)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with app.lock:
                    app.websocket_clients.discard(client)

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
            if parsed.path == "/api/events":
                return self._websocket_events()
            if parsed.path == "/api/state":
                if not self._local_request():
                    return self._json({"error": "Local requests only."}, 403)
                from urllib.parse import parse_qs
                try:
                    since = int(parse_qs(parsed.query).get("since", [0])[0])
                except ValueError:
                    since = 0
                return self._json(app.state(since))
            upload_match = re.fullmatch(r"/uploads/([a-f0-9]{32}\.[a-z0-9]{1,8})", parsed.path)
            if upload_match:
                file_path = UPLOAD_DIR / upload_match.group(1)
                if not file_path.is_file():
                    return self._json({"error": "File not found."}, 404)
                raw = file_path.read_bytes()
                stored_type = UPLOAD_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
                is_image = stored_type.startswith("image/")
                content_type = stored_type if is_image else "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
                self.send_header("Content-Disposition", "inline" if is_image else "attachment; filename=attached-file")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
                return
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
                action = urlparse(self.path).path
                limit = MAX_UPLOAD_BYTES * 4 // 3 + 16384 if action == "/api/upload" else 65536
                if length > limit:
                    return self._json({"error": "Request is too large."}, 413)
                data = json.loads(self.rfile.read(length) or b"{}")
                if action == "/api/send":
                    attachments = []
                    requested_attachments = data.get("attachments", [])
                    if len(requested_attachments) > 4:
                        raise ValueError("Attach up to four files per message.")
                    for item in requested_attachments:
                        file_id = str(item.get("id", ""))
                        if not re.fullmatch(r"[a-f0-9]{32}\.[a-z0-9]{1,8}", file_id):
                            raise ValueError("An attachment reference is invalid.")
                        file_path = UPLOAD_DIR / file_id
                        mime_type = UPLOAD_TYPES.get(file_path.suffix.lower())
                        if not mime_type or not file_path.is_file() or file_path.stat().st_size > MAX_UPLOAD_BYTES:
                            raise ValueError("An attachment is missing or has an unsupported type.")
                        attachments.append({"id": file_id, "name": Path(str(item.get("name", file_id))).name[:255],
                                            "mime_type": mime_type, "size": file_path.stat().st_size})
                    app.send(data.get("message", ""), attachments)
                elif action == "/api/upload":
                    name = Path(str(data.get("name", "upload"))).name[:255]
                    extension = Path(name).suffix.lower()
                    mime_type = UPLOAD_TYPES.get(extension)
                    if not mime_type:
                        raise ValueError("Supported: PNG/JPEG/WEBP/GIF/HEIC/HEIF/BMP/TIFF, PDF, text/code, and Office/OpenDocument files.")
                    try:
                        payload = base64.b64decode(data.get("data", ""), validate=True)
                    except (ValueError, TypeError) as exc:
                        raise ValueError("The uploaded file data is invalid.") from exc
                    if not payload or len(payload) > MAX_UPLOAD_BYTES:
                        raise ValueError("Each file must be between 1 byte and 8 MB.")
                    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
                    file_id = f"{uuid.uuid4().hex}{extension}"
                    (UPLOAD_DIR / file_id).write_bytes(payload)
                    return self._json({"id": file_id, "name": name, "mime_type": mime_type,
                                       "size": len(payload), "url": f"/uploads/{file_id}"})
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
                LOGGER.warning("Rejected API request: %s", exc)
                return self._json({"error": str(exc)}, 400)
            except Exception as exc:
                LOGGER.exception("Unhandled API error")
                return self._json({"error": str(exc) or "Internal server error."}, 500)

    return Handler


def main():
    app = MochiApp()
    server = MochiHTTPServer(("127.0.0.1", 0), make_handler(app))
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
