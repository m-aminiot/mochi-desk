"""Gemini text chat with independent, locally saved conversations."""

import json
import platform
import sys
import threading
import time
import uuid
from pathlib import Path

from google import genai

from config import get_app_data_dir, read_api_key
from tools import DECLARATIONS, execute_tool

MODEL = "gemini-3.5-flash-lite"
TEXT_MODELS = {
    "Gemini 3.5 Flash Lite": "gemini-3.5-flash-lite",
    "Gemini 3 Flash": "gemini-3-flash-preview",
    "Gemini 2.5 Flash Lite": "gemini-2.5-flash-lite",
}
DATA_FILE = get_app_data_dir() / "chats.json"
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
SOUL_FILE = RESOURCE_DIR / "soul"
MAX_TURNS = 200


class RequestCancelled(Exception):
    """Raised when the user stops an in-flight text request."""


def read_soul() -> str:
    """Return soul instructions plus detected host OS for both modalities."""
    personality = SOUL_FILE.read_text(encoding="utf-8").strip() if SOUL_FILE.exists() else ""
    system_name = platform.system() or "Unknown"
    pretty_name = {"Darwin": "macOS", "Windows": "Windows", "Linux": "Linux"}.get(
        system_name, system_name
    )
    environment = (
        f"Host environment (detected when this app starts): {pretty_name} "
        f"({system_name}), version {platform.release() or 'unknown'}, "
        f"architecture {platform.machine() or 'unknown'}. "
        "Use commands and application instructions appropriate for this operating system. "
        "Do not assume a different operating system."
    )
    return f"{personality}\n\n{environment}".strip()


class TextChat:
    def __init__(self):
        self.client = genai.Client(api_key=read_api_key())
        self._lock = threading.Lock()
        self.data = self._load_data()
        self._repair_data()

    @staticmethod
    def _blank_data():
        chat_id = uuid.uuid4().hex
        return {
            "active_chat_id": chat_id,
            "chats": [{"id": chat_id, "title": "New chat", "turns": []}],
        }

    def _load_data(self):
        if not DATA_FILE.exists():
            # Import the old single-chat history as the first conversation.
            old_memory = get_app_data_dir() / "memory.json"
            if not old_memory.exists():
                old_memory = RESOURCE_DIR / "memory.json"
            if old_memory.exists():
                try:
                    old = json.loads(old_memory.read_text(encoding="utf-8"))
                    if "turns_hex" in old:
                        turns = [json.loads(bytes.fromhex(item).decode("utf-8")) for item in old["turns_hex"]]
                    else:
                        turns = []
                        for item in old.get("steps_hex", []):
                            step = json.loads(bytes.fromhex(item).decode("utf-8"))
                            if step.get("type") == "user_input":
                                turns.append([step])
                            elif turns:
                                turns[-1].append(step)
                    chat_id = uuid.uuid4().hex
                    return {
                        "active_chat_id": chat_id,
                        "chats": [{"id": chat_id, "title": "Imported chat", "turns": turns[-MAX_TURNS:]}],
                    }
                except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                    pass
            return self._blank_data()
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            if not isinstance(data.get("chats"), list):
                raise ValueError("expected a chats list")
            return data
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Chat file {DATA_FILE.name} is invalid: {exc}") from exc

    def _repair_data(self):
        chats = self.data["chats"]
        for chat in chats:
            chat.setdefault("id", uuid.uuid4().hex)
            chat.setdefault("title", "New chat")
            chat.setdefault("turns", [])
            chat["turns"] = chat["turns"][-MAX_TURNS:]
        if not chats:
            replacement = self._blank_data()
            self.data.update(replacement)
        elif self.get_chat(self.data.get("active_chat_id")) is None:
            self.data["active_chat_id"] = chats[0]["id"]
        self._save_data()

    def _save_data(self):
        temporary = DATA_FILE.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(DATA_FILE)

    def list_chats(self):
        return self.data["chats"]

    def get_chat(self, chat_id=None):
        chat_id = chat_id or self.data.get("active_chat_id")
        return next((chat for chat in self.data["chats"] if chat["id"] == chat_id), None)

    def get_active_chat_id(self):
        return self.data["active_chat_id"]

    def get_turns(self, chat_id=None):
        chat = self.get_chat(chat_id)
        return chat["turns"] if chat else []

    def create_chat(self):
        with self._lock:
            chat = {"id": uuid.uuid4().hex, "title": "New chat", "turns": []}
            self.data["chats"].insert(0, chat)
            self.data["active_chat_id"] = chat["id"]
            self._save_data()
            return chat

    def select_chat(self, chat_id):
        if self.get_chat(chat_id) is None:
            raise KeyError("Chat does not exist")
        self.data["active_chat_id"] = chat_id
        self._save_data()

    def delete_chat(self, chat_id):
        with self._lock:
            self.data["chats"] = [chat for chat in self.data["chats"] if chat["id"] != chat_id]
            if not self.data["chats"]:
                replacement = self._blank_data()
                self.data.update(replacement)
            elif self.data.get("active_chat_id") == chat_id:
                self.data["active_chat_id"] = self.data["chats"][0]["id"]
            self._save_data()
            return self.data["active_chat_id"]

    def send_message(
        self, message: str, chat_id=None, on_tool_event=None, confirm_tool=None, model=None,
        cancel_event=None,
    ) -> str:
        with self._lock:
            chat = self.get_chat(chat_id)
            if chat is None:
                raise KeyError("Chat does not exist")
            chat_id = chat["id"]
            old_turns = list(chat["turns"])
            previous_steps = [step for turn in old_turns for step in turn]

        user_step = {
            "type": "user_input",
            "content": [{"type": "text", "text": message}],
        }
        soul = read_soul()
        full_input = [*previous_steps, user_step]
        turn_steps = []
        request_input = full_input
        response = ""
        tool_call_count = 0

        def ensure_active():
            if cancel_event is not None and cancel_event.is_set():
                raise RequestCancelled("Request stopped by the user.")

        # Keep network requests and tool execution outside the history lock so
        # stopping a slow request never blocks new chats or later messages.
        for tool_round in range(9):
            for attempt in range(4):
                ensure_active()
                try:
                    interaction = self.client.interactions.create(
                        model=model if model in TEXT_MODELS.values() else MODEL,
                        input=request_input,
                        store=False,
                        system_instruction=soul or None,
                        tools=DECLARATIONS if tool_round < 8 else [],
                    )
                    break
                except Exception as exc:
                    ensure_active()
                    code = getattr(exc, "code", None)
                    if code not in (429, 500, 502, 503, 504) or attempt == 3:
                        raise
                    if cancel_event is not None:
                        if cancel_event.wait(1.5 * (2 ** attempt)):
                            ensure_active()
                    else:
                        time.sleep(1.5 * (2 ** attempt))

            ensure_active()
            response_steps = list(interaction.steps or [])
            turn_steps.extend(step.model_dump() for step in response_steps)
            function_calls = [step for step in response_steps if step.type == "function_call"]
            response = interaction.output_text or ""
            if not function_calls:
                break

            function_results = []
            for call in function_calls:
                ensure_active()
                tool_call_count += 1
                if tool_call_count > 8:
                    result = {"success": False, "error": "Tool-call limit reached for this message."}
                else:
                    result = execute_tool(
                        call.name, call.arguments, on_event=on_tool_event, confirm=confirm_tool
                    )
                ensure_active()
                function_results.append({
                    "type": "function_result",
                    "call_id": call.id,
                    "name": call.name,
                    "result": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                })
            turn_steps.extend(function_results)
            request_input = [*previous_steps, user_step, *turn_steps]

        ensure_active()
        if not response:
            response = "I handled the requested tool action, but no follow-up text was returned."
        new_turn = [user_step, *turn_steps]
        with self._lock:
            ensure_active()
            current_chat = self.get_chat(chat_id)
            if current_chat is None:
                raise KeyError("Chat does not exist")
            previous_turns = list(current_chat["turns"])
            previous_title = current_chat["title"]
            current_chat["turns"] = [*previous_turns, new_turn][-MAX_TURNS:]
            if current_chat["title"] == "New chat":
                current_chat["title"] = " ".join(message.split())[:36] or "New chat"
            try:
                self._save_data()
            except Exception:
                current_chat["turns"] = previous_turns
                current_chat["title"] = previous_title
                raise
        return response
