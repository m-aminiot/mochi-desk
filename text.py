"""Text conversations, model requests, tool loops, and local chat persistence.

Each conversation is stored in its own JSON file under ``chats/``. A small index
file records the conversation order and selected chat. Gemini models use the
Interactions API, while Gemma uses the GenerateContent API.
"""

import asyncio
import base64
import concurrent.futures
import json
import platform
import re
import sys
import threading
from datetime import datetime, timezone
import time
import uuid
from pathlib import Path

from google import genai
from google.genai import types

from config import get_api_base_url, get_app_data_dir, read_api_key, use_custom_api_base_url
from tools import DECLARATIONS, execute_tool

# Fallback model used when a caller does not provide a valid selection.
MODEL = "gemini-3.5-flash-lite"
# Display names and API model IDs shared with the settings and web interface.
TEXT_MODELS = {
    "Gemini 3.5 Flash Lite": "gemini-3.5-flash-lite",
    "Gemini 3 Flash": "gemini-3-flash-preview",
    "Gemini 2.5 Flash Lite": "gemini-2.5-flash-lite",
    "Gemma 4 31B": "gemma-4-31b-it",
}
GEMMA_MODEL = "gemma-4-31b-it"
# Per-chat JSON files live here; index.json stores their order and active ID.
CHATS_DIR = get_app_data_dir() / "chats"
CHAT_INDEX_FILE = CHATS_DIR / "index.json"
UPLOAD_DIR = get_app_data_dir() / "uploads"
# Previous releases stored every conversation in this single file; it is migration-only.
LEGACY_DATA_FILE = get_app_data_dir() / "chats.json"
# Resolve bundled resources when packaged, with the source directory as the normal fallback.
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
SOUL_FILE = RESOURCE_DIR / "soul"
# Bound saved history and model tool loops so requests and files remain manageable.
MAX_TURNS = 200
MAX_TOOL_ROUNDS = 16
MAX_TOOL_CALLS = 24
MAX_TEXT_ATTACHMENT_CHARS = 1_000_000
TEXT_UPLOAD_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".jsx", ".tsx",
    ".html", ".css", ".xml", ".yaml", ".yml", ".toml", ".sql", ".sh",
    ".c", ".h", ".cpp", ".java", ".go", ".rs", ".php", ".rb", ".log",
}


class RequestCancelled(Exception):
    """Signal that the user stopped a text request before it completed."""


class _CancellableGenAIClient:
    """Run SDK async calls on a private event loop and allow prompt cancellation.

    The app handles messages on worker threads. This wrapper lets those threads
    wait for asynchronous SDK requests while the UI can still cancel the active
    network future.
    """

    def __init__(self, client):
        self.client = client
        # Async SDK methods need a running loop; this wrapper owns one for its lifetime.
        self.loop = asyncio.new_event_loop()
        # Cancellation may come from another thread, so shared request state needs a lock.
        self.lock = threading.Lock()
        self.future = None
        self.cancelled = False
        self.closed = False
        # Keep the event loop running without preventing the application from exiting.
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def _run_loop(self):
        """Attach the private loop to its worker thread and run it until shutdown."""
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _invoke(self, method, cancel_event=None, **kwargs):
        """Schedule an SDK coroutine, wait for its result, and propagate cancellation."""
        if cancel_event is not None and cancel_event.is_set():
            raise RequestCancelled("Request stopped by the user.")
        with self.lock:
            if self.cancelled or self.closed:
                raise RequestCancelled("Request stopped by the user.")
            # This is the thread-safe bridge from the caller into the private event loop.
            future = asyncio.run_coroutine_threadsafe(
                method(**kwargs), self.loop
            )
            self.future = future
        try:
            while True:
                # Polling lets the Stop action interrupt a request that is waiting on the API.
                if cancel_event is not None and cancel_event.is_set():
                    self.cancel()
                try:
                    result = future.result(timeout=0.1)
                    break
                except concurrent.futures.TimeoutError:
                    continue
                except concurrent.futures.CancelledError as exc:
                    raise RequestCancelled("Request stopped by the user.") from exc
            if cancel_event is not None and cancel_event.is_set():
                raise RequestCancelled("Request stopped by the user.")
            return result
        finally:
            # Avoid clearing a different future if another request replaced this one.
            with self.lock:
                if self.future is future:
                    self.future = None

    def create(self, cancel_event=None, **kwargs):
        """Call the Gemini Interactions API through the cancellable wrapper."""
        return self._invoke(self.client.aio.interactions.create, cancel_event, **kwargs)

    def generate_content(self, cancel_event=None, **kwargs):
        """Call GenerateContent, which is the API path used for Gemma requests."""
        return self._invoke(self.client.aio.models.generate_content, cancel_event, **kwargs)

    def stream(self, method, on_delta, cancel_event=None, **kwargs):
        """Consume an SDK async stream on its owning loop and forward text deltas."""
        async def consume():
            stream = await method(**kwargs)
            items = []
            async for item in stream:
                if cancel_event is not None and cancel_event.is_set():
                    raise RequestCancelled("Request stopped by the user.")
                event_type = getattr(item, "event_type", None)
                delta = getattr(item, "delta", None)
                if event_type == "step.delta" and getattr(delta, "type", None) == "text":
                    if delta.text:
                        on_delta(delta.text)
                elif getattr(item, "text", None):
                    on_delta(item.text)
                items.append(item)
            return items

        with self.lock:
            if self.cancelled or self.closed:
                raise RequestCancelled("Request stopped by the user.")
            future = asyncio.run_coroutine_threadsafe(consume(), self.loop)
            self.future = future
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    self.cancel()
                try:
                    return future.result(timeout=0.1)
                except concurrent.futures.TimeoutError:
                    continue
                except concurrent.futures.CancelledError as exc:
                    raise RequestCancelled("Request stopped by the user.") from exc
        finally:
            with self.lock:
                if self.future is future:
                    self.future = None

    def cancel(self):
        """Mark the wrapper cancelled and cancel its current SDK future, if present."""
        with self.lock:
            self.cancelled = True
            future = self.future
        if future is not None:
            future.cancel()

    def close(self):
        """Cancel outstanding work, close the SDK transports, and stop the event loop."""
        self.cancel()
        with self.lock:
            if self.closed:
                return
            self.closed = True
        try:
            # Close the asynchronous transport on the same event loop that owns it.
            close_future = asyncio.run_coroutine_threadsafe(self.client.aio.aclose(), self.loop)
            close_future.result(timeout=5)
        except Exception:
            pass
        try:
            self.client.close()
        except Exception:
            pass
        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
        except RuntimeError:
            pass
        self.thread.join(timeout=1)
        if not self.loop.is_closed():
            self.loop.close()


def read_soul() -> str:
    """Load assistant instructions and append the detected host environment.

    Supplying OS details helps the model choose suitable commands without
    running a separate environment-detection command for each request.
    """
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
    """Manage local conversations and send text requests to supported models."""

    def __init__(self):
        # Client lifecycle and conversation data have separate locks because they
        # are accessed by different request and UI threads.
        self._client_lock = threading.RLock()
        self._active_request = None
        self.client = self._create_client()
        self._lock = threading.Lock()
        # Load existing chats, repair older or incomplete records, and persist the result.
        self.data = self._load_data()
        self._repair_data()

    @staticmethod
    def _create_client():
        """Create a Gemini SDK client using the saved key and optional custom endpoint."""
        options = {
            "api_version": "v1beta",
            "timeout": 30_000,
            "retry_options": types.HttpRetryOptions(attempts=1),
        }
        # When the toggle is off, the SDK's normal Google API endpoint is used.
        if use_custom_api_base_url():
            base_url = get_api_base_url()
            if not base_url:
                raise ValueError("Enter an API base URL in Settings or turn off the custom API base URL option.")
            options["base_url"] = base_url
        return genai.Client(api_key=read_api_key(), http_options=types.HttpOptions(**options))

    def reload_client(self):
        """Apply saved API settings by creating a replacement and closing the old client."""
        client = self._create_client()
        with self._client_lock:
            previous = self.client
            self.client = client
        try:
            previous.close()
        except Exception:
            pass

    def finish_active_request(self):
        """Release the request wrapper once the current message finishes or fails."""
        with self._client_lock:
            request = self._active_request
            self._active_request = None
        if request is not None:
            request.close()

    def cancel_active_request(self):
        """Cancel the active asynchronous HTTP request without waiting for its timeout."""
        with self._client_lock:
            request = self._active_request
        if request is not None:
            request.cancel()

    @staticmethod
    def _blank_data():
        """Return the default in-memory database containing one empty chat."""
        chat_id = uuid.uuid4().hex
        return {
            "active_chat_id": chat_id,
            "chats": [{"id": chat_id, "title": "New chat", "turns": [], "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}],
        }

    def _load_data(self):
        """Load split chat files, migrate older history, or create a new empty chat.

        Once the index exists, it is authoritative. A corrupt index or missing
        referenced file raises an error instead of silently discarding history.
        """
        CHATS_DIR.mkdir(parents=True, exist_ok=True)
        if CHAT_INDEX_FILE.exists():
            try:
                index = json.loads(CHAT_INDEX_FILE.read_text(encoding="utf-8"))
                chat_ids = index.get("chat_ids")
                if not isinstance(chat_ids, list):
                    raise ValueError("expected a chat_ids list")
                chats = []
                for chat_id in chat_ids:
                    # Validate IDs before using them to construct file paths.
                    if not isinstance(chat_id, str) or not re.fullmatch(r"[a-f0-9]{32}", chat_id):
                        raise ValueError("invalid chat ID in index")
                    chat_path = CHATS_DIR / f"{chat_id}.json"
                    chat = json.loads(chat_path.read_text(encoding="utf-8"))
                    if chat.get("id") != chat_id:
                        raise ValueError(f"chat ID mismatch in {chat_path.name}")
                    chats.append(chat)
                return {"active_chat_id": index.get("active_chat_id"), "chats": chats}
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Chat index or chat file is invalid: {exc}") from exc

        # Convert the previous all-chats JSON structure when no split index exists.
        if LEGACY_DATA_FILE.exists():
            try:
                data = json.loads(LEGACY_DATA_FILE.read_text(encoding="utf-8"))
                if not isinstance(data.get("chats"), list):
                    raise ValueError("expected a chats list")
                return data
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Legacy chat file {LEGACY_DATA_FILE.name} is invalid: {exc}") from exc

        # The oldest format kept one conversation as hex-encoded turns in memory.json.
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
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                pass
        return self._blank_data()

    def _repair_data(self):
        """Normalize chat records, enforce limits, and write the repaired data."""
        chats = self.data["chats"]
        seen_ids = set()
        for chat in chats:
            chat_id = chat.get("id")
            # IDs become filenames, so ensure they are safe UUID hex strings and unique.
            if not isinstance(chat_id, str) or not re.fullmatch(r"[a-f0-9]{32}", chat_id) or chat_id in seen_ids:
                chat_id = uuid.uuid4().hex
                chat["id"] = chat_id
            seen_ids.add(chat_id)
            chat.setdefault("title", "New chat")
            chat.setdefault("turns", [])
            fallback_time = datetime.fromtimestamp((CHATS_DIR / f"{chat_id}.json").stat().st_mtime, timezone.utc).isoformat() if (CHATS_DIR / f"{chat_id}.json").exists() else datetime.now(timezone.utc).isoformat()
            chat.setdefault("created_at", fallback_time)
            chat.setdefault("updated_at", chat["created_at"])
            chat["turns"] = chat["turns"][-MAX_TURNS:]
        if not chats:
            # The interface expects one chat to remain available at all times.
            replacement = self._blank_data()
            self.data.update(replacement)
        elif self.get_chat(self.data.get("active_chat_id")) is None:
            self.data["active_chat_id"] = chats[0]["id"]
        self._save_data()

    def _save_data(self):
        """Atomically write each chat file, then commit the index.

        Updating the index last ensures it does not point to chat files that have
        not yet been written. Temporary files are replaced atomically per record.
        """
        CHATS_DIR.mkdir(parents=True, exist_ok=True)
        for chat in self.data["chats"]:
            chat_file = CHATS_DIR / f"{chat['id']}.json"
            temporary = chat_file.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(chat, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(chat_file)

        index = {
            "active_chat_id": self.data["active_chat_id"],
            "chat_ids": [chat["id"] for chat in self.data["chats"]],
        }
        temporary = CHAT_INDEX_FILE.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(CHAT_INDEX_FILE)

        # Remove the old combined file only after the new records and index are saved.
        LEGACY_DATA_FILE.unlink(missing_ok=True)

    @staticmethod
    def _gemma_history(steps):
        """Convert stored Interactions steps into GenerateContent role/part messages.

        This allows Gemma to continue conversations that use the shared local
        history format, including previous assistant tool calls and their results.
        """
        contents = []
        for step in steps:
            step_type = step.get("type")
            if step_type == "user_input":
                parts = [
                    types.Part(text=item["text"])
                    for item in step.get("content", [])
                    if isinstance(item, dict) and isinstance(item.get("text"), str)
                ]
                for item in step.get("content", []):
                    if isinstance(item, dict) and item.get("type") == "attachment":
                        if not re.fullmatch(r"[a-f0-9]{32}\.[a-z0-9]{1,8}", str(item.get("id", ""))):
                            raise ValueError("Saved attachment reference is invalid.")
                        path = UPLOAD_DIR / item["id"]
                        attachment_text = TextChat._read_text_attachment(item, path)
                        if attachment_text is not None:
                            parts.append(types.Part(text=attachment_text))
                        else:
                            parts.append(types.Part.from_bytes(data=path.read_bytes(), mime_type=item["mime_type"]))
                if parts:
                    contents.append(types.Content(role="user", parts=parts))
            elif step_type == "model_output":
                parts = [
                    types.Part(text=item["text"])
                    for item in step.get("content", [])
                    if isinstance(item, dict) and isinstance(item.get("text"), str)
                ]
                if parts:
                    contents.append(types.Content(role="model", parts=parts))
            elif step_type == "function_call":
                contents.append(types.Content(
                    role="model",
                    parts=[types.Part.from_function_call(
                        name=step.get("name", ""), args=step.get("arguments", {})
                    )],
                ))
            elif step_type == "function_result":
                # Tool output is stored as JSON text; decode it for the SDK response part.
                result_text = "".join(
                    item.get("text", "") for item in step.get("result", [])
                    if isinstance(item, dict)
                )
                try:
                    result_value = json.loads(result_text)
                except (ValueError, TypeError):
                    result_value = {"result": result_text}
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part.from_function_response(
                        name=step.get("name", "tool"), response=result_value
                    )],
                ))
        return contents

    def _send_gemma_message(
        self, message, chat_id, on_tool_event, confirm_tool, cancel_event,
        previous_steps, user_step, soul, on_delta,
    ):
        """Run a Gemma turn, execute requested tools, and persist the completed exchange.

        Gemma uses GenerateContent rather than the Interactions API. Each tool
        result is fed back in a bounded loop so the model can choose the next
        action or produce its final response.
        """
        request = _CancellableGenAIClient(self._create_client())
        with self._client_lock:
            self._active_request = request
        contents = self._gemma_history([*previous_steps, user_step])
        turn_steps = []
        response_text = ""
        tool_call_count = 0
        declarations = [
            # GenerateContent function declarations omit the Interactions-only type field.
            {key: value for key, value in item.items() if key != "type"}
            for item in DECLARATIONS
        ]

        def ensure_active():
            if cancel_event is not None and cancel_event.is_set():
                raise RequestCancelled("Request stopped by the user.")

        try:
            for tool_round in range(MAX_TOOL_ROUNDS + 1):
                ensure_active()
                # Tools are disabled on the final round so the model must finish with an answer.
                config = types.GenerateContentConfig(
                    system_instruction=soul or None,
                    tools=[types.Tool(function_declarations=declarations)]
                    if tool_round < MAX_TOOL_ROUNDS else None,
                )
                round_streamed = False

                def forward_delta(delta):
                    nonlocal round_streamed
                    round_streamed = True
                    if on_delta:
                        on_delta(delta)

                for attempt in range(4):
                    try:
                        chunks = request.stream(
                            request.client.aio.models.generate_content_stream,
                            forward_delta, cancel_event=cancel_event, model=GEMMA_MODEL,
                            contents=contents, config=config,
                        ) if on_delta else [request.generate_content(
                            cancel_event=cancel_event, model=GEMMA_MODEL,
                            contents=contents, config=config,
                        )]
                        break
                    except Exception as exc:
                        ensure_active()
                        if round_streamed:
                            raise
                        code = getattr(exc, "code", None)
                        if code not in (429, 500, 502, 503, 504) or attempt == 3:
                            raise
                        if cancel_event is not None:
                            if cancel_event.wait(1.5 * (2 ** attempt)):
                                ensure_active()
                        else:
                            time.sleep(1.5 * (2 ** attempt))

                ensure_active()
                candidates = [candidate for chunk in chunks for candidate in (chunk.candidates or [])]
                candidate = candidates[-1] if candidates else None
                if candidate is None or candidate.content is None:
                    raise ValueError("Gemma returned no candidate content.")
                model_parts = [part for item in candidates if item.content for part in (item.content.parts or [])]
                model_content = types.Content(role="model", parts=model_parts)
                # Keep the model response in context before appending any tool results.
                contents.append(model_content)
                function_calls = []
                for part in model_content.parts or []:
                    if part.text:
                        response_text += part.text
                        turn_steps.append({
                            "type": "model_output",
                            "content": [{"type": "text", "text": part.text}],
                        })
                    if part.function_call:
                        function_calls.append(part.function_call)
                        turn_steps.append({
                            "type": "function_call",
                            "id": getattr(part.function_call, "id", None) or uuid.uuid4().hex,
                            "name": part.function_call.name,
                            "arguments": part.function_call.args or {},
                        })
                if not function_calls:
                    break

                # Run model-requested tools in order, then return all results for the next round.
                function_responses = []
                for call in function_calls:
                    ensure_active()
                    tool_call_count += 1
                    if tool_call_count > MAX_TOOL_CALLS:
                        result = {
                            "success": False,
                            "error": "This task reached the per-message tool-call limit. Summarize verified progress and clearly state what remains.",
                        }
                    else:
                        result = execute_tool(
                            call.name, call.args or {}, on_event=on_tool_event, confirm=confirm_tool
                        )
                    ensure_active()
                    result_text = json.dumps(result, ensure_ascii=False)
                    turn_steps.append({
                        "type": "function_result",
                        "call_id": getattr(call, "id", None) or "",
                        "name": call.name,
                        "result": [{"type": "text", "text": result_text}],
                    })
                    function_responses.append(types.Part.from_function_response(
                        name=call.name, response=result
                    ))
                contents.append(types.Content(role="user", parts=function_responses))

            ensure_active()
            if not response_text:
                response_text = "Gemma completed the requested tool action, but returned no text."
            new_turn = [user_step, *turn_steps]
            with self._lock:
                ensure_active()
                current_chat = self.get_chat(chat_id)
                if current_chat is None:
                    raise KeyError("Chat does not exist")
                previous_turns = list(current_chat["turns"])
                previous_title = current_chat["title"]
                previous_updated_at = current_chat.get("updated_at")
                current_chat["turns"] = [*previous_turns, new_turn][-MAX_TURNS:]
                current_chat["updated_at"] = datetime.now(timezone.utc).isoformat()
                if current_chat["title"] == "New chat":
                    current_chat["title"] = " ".join(message.split())[:36] or "New chat"
                try:
                    self._save_data()
                except Exception:
                    # Restore memory if writing to disk failed, avoiding a false saved state.
                    current_chat["turns"] = previous_turns
                    current_chat["title"] = previous_title
                    if previous_updated_at is not None:
                        current_chat["updated_at"] = previous_updated_at
                    raise
            return response_text
        finally:
            request.close()
            with self._client_lock:
                if self._active_request is request:
                    self._active_request = None

    def list_chats(self):
        """Return chats in the order used by the conversation list."""
        return self.data["chats"]

    def get_chat(self, chat_id=None):
        """Find a chat by ID, defaulting to the currently selected conversation."""
        chat_id = chat_id or self.data.get("active_chat_id")
        return next((chat for chat in self.data["chats"] if chat["id"] == chat_id), None)

    def get_active_chat_id(self):
        """Return the ID currently selected in the interface."""
        return self.data["active_chat_id"]

    def get_turns(self, chat_id=None):
        """Return the selected chat's saved turns, or an empty list if it is missing."""
        chat = self.get_chat(chat_id)
        return chat["turns"] if chat else []

    def create_chat(self):
        """Create, save, and select a new empty conversation."""
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            chat = {"id": uuid.uuid4().hex, "title": "New chat", "turns": [], "created_at": now, "updated_at": now}
            self.data["chats"].insert(0, chat)
            self.data["active_chat_id"] = chat["id"]
            self._save_data()
            return chat

    def select_chat(self, chat_id):
        """Select an existing chat and persist the active ID in the index."""
        if self.get_chat(chat_id) is None:
            raise KeyError("Chat does not exist")
        self.data["active_chat_id"] = chat_id
        self._save_data()

    def delete_chat(self, chat_id):
        """Delete a chat file and select another chat if the active one was removed."""
        with self._lock:
            existing = next((chat for chat in self.data["chats"] if chat["id"] == chat_id), None)
            if existing is None:
                raise KeyError("Chat does not exist")
            self.data["chats"] = [chat for chat in self.data["chats"] if chat["id"] != existing["id"]]
            if not self.data["chats"]:
                replacement = self._blank_data()
                self.data.update(replacement)
            elif self.data.get("active_chat_id") == chat_id:
                self.data["active_chat_id"] = self.data["chats"][0]["id"]
            self._save_data()
            (CHATS_DIR / f"{existing['id']}.json").unlink(missing_ok=True)
            return self.data["active_chat_id"]

    def send_message(
        self, message: str, chat_id=None, on_tool_event=None, confirm_tool=None, model=None,
        cancel_event=None, attachments=None, on_delta=None,
    ) -> str:
        """Send a user message, run model/tool steps, and save the completed turn.

        Gemini and Gemma use different API request formats but share local history
        and tool handlers. Network and tool work stays outside the history lock
        so stopping a request or navigating chats remains responsive.
        """
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
        user_step["content"].extend({"type": "attachment", **item} for item in attachments or [])
        soul = read_soul()
        # Gemma needs GenerateContent; the Gemini models continue through Interactions below.
        if model == GEMMA_MODEL:
            return self._send_gemma_message(
                message, chat_id, on_tool_event, confirm_tool, cancel_event,
                previous_steps, user_step, soul, on_delta,
            )
        full_input = [*previous_steps, user_step]
        turn_steps = []
        request_input = full_input
        response = ""
        tool_call_count = 0
        request = _CancellableGenAIClient(self._create_client())
        with self._client_lock:
            self._active_request = request

        def ensure_active():
            if cancel_event is not None and cancel_event.is_set():
                raise RequestCancelled("Request stopped by the user.")

        # Keep network and tool work outside the history lock so Stop and chat navigation stay responsive.
        for tool_round in range(MAX_TOOL_ROUNDS + 1):
            round_streamed = False
            round_text = []

            def forward_delta(delta):
                nonlocal round_streamed
                round_streamed = True
                round_text.append(delta)
                if on_delta:
                    on_delta(delta)

            for attempt in range(4):
                ensure_active()
                try:
                    ensure_active()
                    api_input = self._interaction_input(request_input)
                    if on_delta:
                        events = request.stream(
                            request.client.aio.interactions.create,
                            forward_delta, cancel_event=cancel_event,
                            model=model if model in TEXT_MODELS.values() else MODEL,
                            input=api_input, store=False, stream=True,
                            system_instruction=soul or None,
                            tools=DECLARATIONS if tool_round < MAX_TOOL_ROUNDS else [],
                        )
                        completed = next((event.interaction for event in reversed(events)
                                          if getattr(event, "event_type", None) == "interaction.completed"), None)
                        if completed is None:
                            raise ValueError("The streamed interaction ended without a completed response.")
                        interaction = completed
                    else:
                        interaction = request.create(
                            cancel_event=cancel_event,
                            model=model if model in TEXT_MODELS.values() else MODEL,
                            input=api_input,
                            store=False,
                            system_instruction=soul or None,
                            tools=DECLARATIONS if tool_round < MAX_TOOL_ROUNDS else [],
                        )
                    break
                except Exception as exc:
                    ensure_active()
                    if round_streamed:
                        raise
                    # Back off only for quota or temporary server errors; surface other failures.
                    code = getattr(exc, "code", None)
                    if code not in (429, 500, 502, 503, 504) or attempt == 3:
                        raise
                    if cancel_event is not None:
                        if cancel_event.wait(1.5 * (2 ** attempt)):
                            ensure_active()
                    else:
                        time.sleep(1.5 * (2 ** attempt))

            ensure_active()
            response_steps = list(getattr(interaction, "steps", None) or [])
            # Preserve the provider's native steps so later Gemini calls can reuse this history.
            turn_steps.extend(step.model_dump() for step in response_steps)
            function_calls = [step for step in response_steps if getattr(step, "type", None) == "function_call"]
            response = getattr(interaction, "output_text", None) or self._interaction_step_text(response_steps)
            if not response and round_text:
                response = "".join(round_text)
            if not function_calls:
                break

            function_results = []
            # Sequential execution preserves action order and lets each command request approval.
            for call in function_calls:
                ensure_active()
                tool_call_count += 1
                if tool_call_count > MAX_TOOL_CALLS:
                    result = {
                        "success": False,
                        "error": "This task reached the per-message tool-call limit. Summarize verified progress and clearly state what remains.",
                    }
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
            previous_updated_at = current_chat.get("updated_at")
            current_chat["turns"] = [*previous_turns, new_turn][-MAX_TURNS:]
            current_chat["updated_at"] = datetime.now(timezone.utc).isoformat()
            if current_chat["title"] == "New chat":
                current_chat["title"] = " ".join(message.split())[:36] or "New chat"
            try:
                self._save_data()
            except Exception:
                # Roll back memory so the UI does not show a turn that was not saved.
                current_chat["turns"] = previous_turns
                current_chat["title"] = previous_title
                if previous_updated_at is not None:
                    current_chat["updated_at"] = previous_updated_at
                raise
        return response

    @staticmethod
    def _interaction_input(steps):
        """Expand local attachment references into Interactions multimodal content."""
        expanded = []
        for step in steps:
            record = dict(step)
            if step.get("type") == "user_input":
                content = []
                for item in step.get("content", []):
                    if item.get("type") != "attachment":
                        content.append(item)
                        continue
                    if not re.fullmatch(r"[a-f0-9]{32}\.[a-z0-9]{1,8}", str(item.get("id", ""))):
                        raise ValueError("Saved attachment reference is invalid.")
                    file_path = UPLOAD_DIR / item["id"]
                    attachment_text = TextChat._read_text_attachment(item, file_path)
                    if attachment_text is not None:
                        content.append({"type": "text", "text": attachment_text})
                    else:
                        content.append({
                            "type": "image" if item["mime_type"].startswith("image/") else "document",
                            "mime_type": item["mime_type"],
                            "data": base64.b64encode(file_path.read_bytes()).decode("ascii"),
                        })
                record["content"] = content
            expanded.append(record)
        return expanded

    @staticmethod
    def _read_text_attachment(item, file_path):
        """Decode textual attachments for APIs that reject text/plain documents."""
        if file_path.suffix.lower() not in TEXT_UPLOAD_EXTENSIONS:
            return None
        raw_text = file_path.read_bytes().decode("utf-8-sig", errors="replace")
        if len(raw_text) > MAX_TEXT_ATTACHMENT_CHARS:
            raw_text = raw_text[:MAX_TEXT_ATTACHMENT_CHARS] + "\n[File content truncated.]"
        return f"[Attached file: {item.get('name', file_path.name)}]\n{raw_text}"

    @staticmethod
    def _interaction_step_text(steps):
        """Recover assistant text from streamed interactions, which omit output_text."""
        chunks = []
        for step in steps:
            if getattr(step, "type", None) != "model_output":
                continue
            for part in getattr(step, "content", None) or []:
                value = getattr(part, "text", None)
                if value:
                    chunks.append(value)
        return "".join(chunks)
