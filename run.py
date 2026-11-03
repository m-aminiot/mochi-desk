"""Modern multi-chat desktop interface for Gemini text and voice."""

import colorsys
import json
import math
import os
import queue
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import tkinter.messagebox as messagebox
from pathlib import Path

import customtkinter as ctk
from config import TOKEN_FILE, friendly_gemini_error, get_app_data_dir

from text import TEXT_MODELS, TextChat
from voice import VoiceSession
from tools import cancel_tool

ROOT_DIR = Path(__file__).resolve().parent
PREFERENCES_FILE = get_app_data_dir() / "settings.json"
THEMES = {
    "Violet": {
        "BG": "#080D18", "SIDEBAR": "#0E1627", "PANEL": "#111B2F", "FIELD": "#0A1322",
        "TEXT": "#F5F7FF", "MUTED": "#9AA8C2", "ACCENT": "#A78BFA",
        "ACCENT_HOVER": "#8B6FE0", "TEAL": "#5EEAD4", "GREEN": "#43D9B1",
        "GOLD": "#FBBF24", "RED": "#FB7185", "BORDER": "#26334D",
    },
    "Ocean": {
        "BG": "#07131A", "SIDEBAR": "#0B202B", "PANEL": "#102631", "FIELD": "#091B24",
        "TEXT": "#F1FAFC", "MUTED": "#9AB9C2", "ACCENT": "#38BDF8",
        "ACCENT_HOVER": "#0EA5E9", "TEAL": "#5EEAD4", "GREEN": "#43D9B1",
        "GOLD": "#FBBF24", "RED": "#FB7185", "BORDER": "#24424F",
    },
    "Rose": {
        "BG": "#160B15", "SIDEBAR": "#241222", "PANEL": "#2C192A", "FIELD": "#1D1020",
        "TEXT": "#FFF5FA", "MUTED": "#C8A7BA", "ACCENT": "#F472B6",
        "ACCENT_HOVER": "#DB579B", "TEAL": "#5EEAD4", "GREEN": "#43D9B1",
        "GOLD": "#FBBF24", "RED": "#FB7185", "BORDER": "#50334A",
    },
}


def load_preferences():
    preferences = {
        "font_family": "Arial", "font_size": 14, "theme": "Violet",
        "model": "gemini-3.5-flash-lite",
    }
    try:
        data = json.loads(PREFERENCES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            preferences.update(data)
    except (OSError, ValueError, TypeError):
        pass
    if preferences.get("theme") not in THEMES:
        preferences["theme"] = "Violet"
    if preferences.get("model") not in TEXT_MODELS.values():
        preferences["model"] = "gemini-3.5-flash-lite"
    try:
        preferences["font_size"] = max(10, min(24, int(preferences["font_size"])))
    except (TypeError, ValueError):
        preferences["font_size"] = 14
    return preferences


def save_preferences(preferences):
    data = {
        "font_family": str(preferences["font_family"]),
        "font_size": max(10, min(24, int(preferences["font_size"]))),
        "theme": preferences["theme"] if preferences["theme"] in THEMES else "Violet",
        "model": preferences.get("model", "gemini-3.5-flash-lite")
        if preferences.get("model", "gemini-3.5-flash-lite") in TEXT_MODELS.values()
        else "gemini-3.5-flash-lite",
    }
    temporary = PREFERENCES_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(PREFERENCES_FILE)


def save_api_key(api_key):
    api_key = api_key.strip()
    if not api_key or "\n" in api_key or "\r" in api_key:
        raise ValueError("Enter a valid API key on one line.")
    temporary = TOKEN_FILE.with_suffix(".txt.tmp")
    temporary.write_text(f"api_key={api_key}\n", encoding="utf-8")
    temporary.replace(TOKEN_FILE)


def read_saved_api_key():
    try:
        for line in TOKEN_FILE.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == "api_key":
                return value.strip().strip("\"'")
    except OSError:
        pass
    return ""


PREFERENCES = load_preferences()
THEME = THEMES[PREFERENCES["theme"]]
BG = THEME["BG"]
SIDEBAR = THEME["SIDEBAR"]
PANEL = THEME["PANEL"]
FIELD = THEME["FIELD"]
TEXT = THEME["TEXT"]
MUTED = THEME["MUTED"]
ACCENT = THEME["ACCENT"]
ACCENT_HOVER = THEME["ACCENT_HOVER"]
TEAL = THEME["TEAL"]
GREEN = THEME["GREEN"]
GOLD = THEME["GOLD"]
RED = THEME["RED"]
BORDER = THEME["BORDER"]
VOICE_START_LABEL = "♫  Start voice chat"
VOICE_STOP_LABEL = "■  End voice chat"


def ui_font(size, weight="normal"):
    scaled_size = max(8, round(size * PREFERENCES["font_size"] / 14))
    return ctk.CTkFont(PREFERENCES["font_family"], scaled_size, weight)


def ui_font_tuple(size, weight="normal"):
    scaled_size = max(8, round(size * PREFERENCES["font_size"] / 14))
    return (PREFERENCES["font_family"], scaled_size, weight)


class ChatApp:
    def __init__(self, root):
        self.root = root
        root.title("Mochi Desk")
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        width = min(1680, max(940, int(screen_w * 0.96)))
        height = min(1060, max(620, int(screen_h * 0.94)))
        root.geometry(f"{width}x{height}+{max(0, (screen_w-width)//2)}+{max(0, (screen_h-height)//2)}")
        root.minsize(min(1040, width), min(700, height))
        root.configure(fg_color=BG)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.ui_queue = queue.Queue()
        self._busy = False
        self._text_cancel_event = None
        self._text_request_id = 0
        self.mode = "text"
        self._voice_chat_id = None
        self._voice_active = False
        self._microphone_muted = False
        self._last_voice_reply = "Start a voice chat and talk naturally with Mochi."
        self._orb_phase = 0.0
        self._voice_tool_cards_started = False
        self._voice_tool_cards = {}
        self._voice_pending_approvals = {}
        self._orb_items = None
        self._rgb_buttons = []
        self._rgb_phase = 0.0
        try:
            self.text_chat = TextChat()
            startup_error = None
        except Exception as exc:
            self.text_chat = None
            startup_error = str(exc)

        self.voice = VoiceSession(
            on_status=lambda message: self.ui_queue.put((self._voice_status, (message,), {})),
            on_text=lambda message: self.ui_queue.put((self.show_voice_reply, (message,), {})),
            on_tool_event=self._voice_tool_event,
            confirm_tool=self._confirm_voice_terminal_command,
        )

        self.layout = ctk.CTkFrame(root, fg_color="transparent")
        self.layout.pack(fill="both", expand=True, padx=18, pady=18)
        self.layout.grid_columnconfigure(1, weight=1)
        self.layout.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(
            self.layout, width=310, fg_color=SIDEBAR, corner_radius=26,
            border_width=1, border_color=BORDER,
        )
        self.sidebar.grid(row=0, column=0, sticky="nsw", padx=(0, 16))
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_columnconfigure(0, weight=1)
        self.sidebar.grid_rowconfigure(2, weight=1)
        brand = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=16, pady=(18, 17))
        ctk.CTkLabel(
            brand, text="✦  Mochi Desk", font=ui_font(22, "bold"),
            text_color=TEXT,
        ).pack(anchor="w")
        ctk.CTkLabel(
            brand, text="YOUR SPACE, YOUR CONVERSATIONS",
            text_color=MUTED, font=ui_font(10, "bold"),
        ).pack(anchor="w", padx=(2, 0), pady=(5, 0))
        self.new_chat_button = ctk.CTkButton(
            self.sidebar, text="＋   New conversation", height=48, corner_radius=16,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            font=ui_font(14, "bold"),
            command=self.new_chat,
        )
        self.new_chat_button.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 16))
        self._enable_rgb_accent(self.new_chat_button)
        self.chat_list = ctk.CTkScrollableFrame(
            self.sidebar, fg_color="transparent", scrollbar_button_color="#34425F",
        )
        self.chat_list.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        ctk.CTkButton(
            self.sidebar, text="⚙   Settings", height=40, corner_radius=13,
            fg_color=FIELD, hover_color="#202B42", text_color=TEXT,
            font=ui_font(12), command=self.open_settings,
        ).grid(row=3, column=0, sticky="ew", padx=14, pady=(4, 0))
        self.sidebar_footer = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.sidebar_footer.grid(row=4, column=0, sticky="ew", padx=15, pady=(8, 16))
        ctk.CTkLabel(
            self.sidebar_footer, text="✦  Your chats stay saved on this device",
            text_color=MUTED, font=ui_font(11),
        ).pack(anchor="w")

        self.main = ctk.CTkFrame(
            self.layout, fg_color=PANEL, corner_radius=26,
            border_width=1, border_color=BORDER,
        )
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(self.main, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=30, pady=(25, 0))
        top.grid_columnconfigure(1, weight=1)
        self.chat_avatar = ctk.CTkLabel(
            top, text="✦", width=50, height=50, corner_radius=25,
            fg_color=ACCENT, text_color=BG, font=ui_font(23, "bold"),
        )
        self.chat_avatar.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 14))
        self.chat_title = ctk.CTkLabel(
            top, text="New conversation", text_color=TEXT, anchor="w",
            font=ui_font(25, "bold"),
        )
        self.chat_title.grid(row=0, column=1, sticky="sw")
        self.chat_subtitle = ctk.CTkLabel(
            top, text="Your personal space to think out loud.", text_color=MUTED,
            anchor="w", font=ui_font(12),
        )
        self.chat_subtitle.grid(row=1, column=1, sticky="nw", pady=(4, 0))
        self.mode_switch = ctk.CTkSegmentedButton(
            top, values=["✎  Text", "♫  Voice"], corner_radius=14, height=42,
            selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
            unselected_color=FIELD, unselected_hover_color="#202B42",
            command=self.switch_mode,
        )
        self.mode_switch.set("✎  Text")
        model_frame = ctk.CTkFrame(top, fg_color="transparent")
        model_frame.grid(row=0, column=2, rowspan=2, sticky="e", padx=(12, 10))
        ctk.CTkLabel(
            model_frame, text="TEXT MODEL", text_color=MUTED, anchor="w",
            font=ui_font(9, "bold"),
        ).pack(anchor="w", padx=4, pady=(0, 3))
        self.model_selector = ctk.CTkOptionMenu(
            model_frame, values=list(TEXT_MODELS), width=215, height=38, corner_radius=12,
            fg_color=FIELD, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            text_color=TEXT, font=ui_font(11, "bold"), command=self.select_text_model,
        )
        self.model_selector.set(self._model_label(PREFERENCES["model"]))
        self.model_selector.pack()
        self.mode_switch.grid(row=0, column=3, rowspan=2, sticky="e")

        self.content = ctk.CTkFrame(self.main, fg_color="transparent")
        self.content.grid(row=1, column=0, sticky="nsew", padx=22, pady=(18, 10))
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self.text_log = self._make_log(self.content)
        self.text_log.grid(row=0, column=0, sticky="nsew")
        self.voice_view = ctk.CTkFrame(
            self.content, fg_color=FIELD, corner_radius=22,
            border_width=1, border_color=BORDER,
        )
        self.voice_view.grid_columnconfigure(0, weight=1)
        self.voice_view.grid_rowconfigure(0, weight=3)
        self.voice_view.grid_rowconfigure(1, weight=2)

        self.voice_center = ctk.CTkFrame(self.voice_view, fg_color="transparent")
        self.voice_center.grid(row=0, column=0, sticky="nsew", padx=18, pady=(10, 0))
        self.voice_center.grid_columnconfigure(0, weight=1)
        self.voice_center.grid_rowconfigure(0, weight=1)
        self.orb_canvas = tk.Canvas(
            self.voice_center, background=FIELD, highlightthickness=0, borderwidth=0,
        )
        self.orb_canvas.grid(row=0, column=0, sticky="nsew")
        self.orb_canvas.bind("<Configure>", lambda _event: self._draw_orb())
        self.voice_caption = ctk.CTkLabel(
            self.voice_center, text="VOICE SPACE  ·  READY",
            text_color=MUTED, font=ui_font(11, "bold"),
        )
        self.voice_caption.grid(row=1, column=0, pady=(0, 5))
        self.voice_reply = ctk.CTkTextbox(
            self.voice_center, width=max(340, min(900, int(root.winfo_screenwidth() * 0.52))),
            height=86, corner_radius=14,
            fg_color=PANEL, border_width=0, text_color=TEXT, font=ui_font(14),
            wrap="word", activate_scrollbars=True,
            scrollbar_button_color="#485572", scrollbar_button_hover_color=ACCENT,
        )
        self.voice_reply.grid(row=2, column=0, sticky="ew", padx=28, pady=(0, 14))
        self.voice_reply.insert("1.0", self._last_voice_reply)
        self.voice_reply.configure(state="disabled")
        self.voice_copy_button = ctk.CTkButton(
            self.voice_center, text="Copy reply", width=100, height=30, corner_radius=10,
            fg_color=PANEL, hover_color="#26334D", text_color=MUTED, font=ui_font(10),
            command=lambda: self._copy_message(self._last_voice_reply, self.voice_copy_button),
        )
        self.voice_copy_button.grid(row=3, column=0, pady=(0, 10))

        self.voice_activity = ctk.CTkFrame(
            self.voice_view, fg_color=PANEL, corner_radius=18,
            border_width=1, border_color=BORDER,
        )
        self.voice_activity.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 16))
        self.voice_activity.grid_columnconfigure(0, weight=1)
        self.voice_activity.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(
            self.voice_activity, text="⌘  TOOL ACTIVITY",
            text_color=GOLD, font=ui_font(10, "bold"), anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=15, pady=(11, 5))
        self.voice_tools_log = ctk.CTkScrollableFrame(
            self.voice_activity, fg_color="transparent", height=190,
            scrollbar_button_color="#34425F", scrollbar_button_hover_color=GOLD,
        )
        self.voice_tools_log.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.voice_tools_log.grid_columnconfigure(0, weight=1)
        self.voice_activity_empty = ctk.CTkLabel(
            self.voice_tools_log, text="Tool actions and results will appear here.",
            text_color=MUTED, font=ui_font(12), anchor="w",
        )
        self.voice_activity_empty.pack(fill="x", padx=8, pady=(5, 8))
        self.voice_view.grid_remove()

        self.composer = ctk.CTkFrame(self.main, fg_color="transparent")
        self.composer.grid(row=2, column=0, sticky="ew", padx=22, pady=(0, 18))
        self.composer.grid_columnconfigure(0, weight=1)
        self.message = ctk.CTkTextbox(
            self.composer, height=92, corner_radius=16, fg_color=FIELD,
            border_color="#34425F", text_color=TEXT,
            font=ui_font(15), wrap="word", activate_scrollbars=True,
            scrollbar_button_color="#34425F", scrollbar_button_hover_color=ACCENT,
        )
        self.message.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=(0, 4))
        self.message.bind("<KeyRelease>", self._resize_message_input)
        self.message.bind("<<Paste>>", lambda _event: self.root.after_idle(self._resize_message_input))
        self.message.bind("<<Cut>>", lambda _event: self.root.after_idle(self._resize_message_input))
        self.message.bind("<Control-Return>", self._send_from_shortcut)
        try:
            self.message.bind("<Command-Return>", self._send_from_shortcut)
        except tk.TclError:
            pass
        self._attach_text_context_menu(self.message)
        self.composer_hint = ctk.CTkLabel(
            self.composer, text="Enter for a new line   ·   Ctrl/⌘ + Enter to send",
            text_color=MUTED, anchor="w", font=ui_font(10),
        )
        self.composer_hint.grid(row=1, column=0, sticky="w", padx=8, pady=(0, 2))
        self.send_button = ctk.CTkButton(
            self.composer, text="Send  ↗", width=112, height=48, corner_radius=16,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            font=ui_font(14, "bold"),
            command=self._send_button_action,
        )
        self.send_button.grid(row=0, column=1, sticky="se", padx=(0, 2), pady=(0, 8))
        self._enable_rgb_accent(self.send_button)
        self.voice_controls = ctk.CTkFrame(self.composer, fg_color="transparent")
        self.voice_controls.grid(row=0, column=1, sticky="se", padx=(0, 2), pady=(0, 8))
        self.voice_button = ctk.CTkButton(
            self.voice_controls, text=VOICE_START_LABEL, width=176, height=48, corner_radius=16,
            fg_color=TEAL, hover_color="#36CDBB", text_color="#082321",
            font=ui_font(13, "bold"), command=self.toggle_voice,
        )
        self.voice_button.pack(side="right")
        self.voice_mute_button = ctk.CTkButton(
            self.voice_controls, text="Mic off", width=112, height=48, corner_radius=16,
            fg_color=FIELD, hover_color="#26334D", text_color=TEXT,
            font=ui_font(12, "bold"), command=self.toggle_microphone_mute,
        )
        self.voice_controls.grid_remove()

        self.status = ctk.CTkLabel(
            self.main, text="●  Ready when you are", text_color=GREEN, anchor="w",
            wraplength=850, justify="left", height=34, corner_radius=12,
            fg_color=FIELD, font=ui_font(12, "bold"),
        )
        self.status.grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 16))

        if startup_error:
            self.status.configure(text=f"Setup error: {startup_error}", text_color=RED)
            self.send_button.configure(state="disabled")
        else:
            self._refresh_chat_list()
            self._load_selected_chat()

        root.protocol("WM_DELETE_WINDOW", self.close)
        self._drain_ui_queue()
        self._animate_orb()
        self._animate_rgb_buttons()

    @staticmethod
    def _make_log(parent):
        log = ctk.CTkScrollableFrame(
            parent, corner_radius=20, fg_color=FIELD, border_width=0,
            scrollbar_button_color="#34425F", scrollbar_button_hover_color=ACCENT,
        )
        log.grid_columnconfigure(0, weight=1)
        return log

    def _attach_text_context_menu(self, textbox):
        inner = textbox._textbox
        menu = tk.Menu(self.root, tearoff=0, bg=PANEL, fg=TEXT,
                       activebackground=ACCENT, activeforeground=BG)
        menu.add_command(label="Cut", command=lambda: inner.event_generate("<<Cut>>"))
        menu.add_command(label="Copy", command=lambda: inner.event_generate("<<Copy>>"))
        menu.add_command(label="Paste", command=lambda: inner.event_generate("<<Paste>>"))
        menu.add_separator()
        menu.add_command(label="Select all", command=lambda: inner.tag_add("sel", "1.0", "end-1c"))

        shortcuts = {
            "<Control-x>": "<<Cut>>", "<Control-c>": "<<Copy>>", "<Control-v>": "<<Paste>>",
            "<Control-a>": None, "<Command-x>": "<<Cut>>", "<Command-c>": "<<Copy>>",
            "<Command-v>": "<<Paste>>", "<Command-a>": None,
        }
        for sequence, virtual_event in shortcuts.items():
            try:
                if virtual_event:
                    inner.bind(sequence, lambda _event, target=virtual_event: (inner.event_generate(target), "break")[1])
                else:
                    inner.bind(sequence, lambda _event: (inner.tag_add("sel", "1.0", "end-1c"), "break")[1])
            except tk.TclError:
                pass

        def show_menu(event):
            inner.focus_set()
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
            return "break"

        inner.bind("<Button-3>", show_menu)
        inner.bind("<Button-2>", show_menu)
        inner.bind("<Control-Button-1>", show_menu)

    def _resize_message_input(self, _event=None):
        if not self.message.winfo_exists():
            return
        content = self.message.get("1.0", "end-1c")
        chars_per_line = max(34, int(max(300, self.message.winfo_width()) / 8.5))
        lines = sum(max(1, math.ceil(len(line) / chars_per_line)) for line in content.split("\n"))
        height = min(190, max(88, 26 + lines * 22))
        self.message.configure(height=height)

    def _send_from_shortcut(self, _event=None):
        self.send_text()
        return "break"

    def _copy_message(self, content, button):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
            button.configure(text="Copied ✓", text_color=GREEN)
            self.root.after(1300, lambda: button.configure(text="Copy", text_color=MUTED))
        except tk.TclError:
            self.status.configure(text="Could not copy this message to the clipboard.", text_color=RED)

    def _draw_orb(self):
        canvas = self.orb_canvas
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 20 or height < 20:
            return
        if self._orb_items is None:
            self._orb_items = [
                canvas.create_oval(0, 0, 0, 0, fill="#17162A", outline="#38315C", width=1),
                canvas.create_oval(0, 0, 0, 0, fill="#201D3A", outline="#51477F", width=1),
                canvas.create_oval(0, 0, 0, 0, fill="#30285B", outline="#7665B5", width=1),
                canvas.create_oval(0, 0, 0, 0, fill="#51418C", outline="#9D8AE3", width=1),
                canvas.create_oval(0, 0, 0, 0, fill="#8975D8", outline="#C5B5FF", width=2),
                canvas.create_text(0, 0, text="✦", fill="#FFFFFF", font=ui_font_tuple(30, "bold")),
            ]
        center_x, center_y = width / 2, height / 2
        base = max(48, min(116, min(width, height) * 0.18))
        if self.mode == "voice":
            strength = 0.105 if self._voice_active else 0.045
            breath = 1 + strength * math.sin(self._orb_phase)
        else:
            breath = 1.0
        radii = [base * factor * breath for factor in (1.48, 1.22, 0.98, 0.75, 0.52)]
        palette = (
            ("#10252B", "#15383F", "#1A5A61", "#238890", "#35B6B1")
            if self._voice_active
            else ("#17162A", "#201D3A", "#30285B", "#51418C", "#8975D8")
        )
        for item, radius, color in zip(self._orb_items[:5], radii, palette):
            canvas.coords(item, center_x - radius, center_y - radius,
                          center_x + radius, center_y + radius)
            canvas.itemconfigure(item, fill=color)
        canvas.coords(self._orb_items[5], center_x, center_y)

    def _animate_orb(self):
        try:
            if not self.root.winfo_exists():
                return
            self._orb_phase += 0.12 if self.mode == "voice" and self._voice_active else 0.055
            self._draw_orb()
            delay = 45 if self.mode == "voice" else 150
            self.root.after(delay, self._animate_orb)
        except tk.TclError:
            return

    def _enable_rgb_accent(self, button):
        self._rgb_buttons.append(button)

    def _animate_rgb_buttons(self):
        try:
            if not self.root.winfo_exists():
                return
            hue = self._rgb_phase % 1.0
            red, green, blue = colorsys.hsv_to_rgb(hue, 0.48, 0.92)
            fill = "#%02x%02x%02x" % (int(red * 255), int(green * 255), int(blue * 255))
            red, green, blue = colorsys.hsv_to_rgb(hue, 0.56, 0.76)
            hover = "#%02x%02x%02x" % (int(red * 255), int(green * 255), int(blue * 255))
            for button in self._rgb_buttons:
                try:
                    button.configure(fg_color=fill, hover_color=hover)
                except tk.TclError:
                    pass
            self._rgb_phase = (hue + 0.003) % 1.0
            self.root.after(140, self._animate_rgb_buttons)
        except tk.TclError:
            return

    def show_voice_reply(self, content):
        self._last_voice_reply = content
        self.voice_reply.configure(state="normal")
        self.voice_reply.delete("1.0", "end")
        self.voice_reply.insert("1.0", content)
        self.voice_reply.configure(state="disabled")

    def open_settings(self):
        window = ctk.CTkToplevel(self.root)
        window.title("Mochi Desk · Settings")
        window.geometry("520x660")
        window.minsize(480, 600)
        window.configure(fg_color=BG)
        window.transient(self.root)
        panel = ctk.CTkFrame(window, fg_color=PANEL, corner_radius=24)
        panel.pack(fill="both", expand=True, padx=22, pady=22)
        ctk.CTkLabel(
            panel, text="✦  Settings", text_color=TEXT, font=ui_font(25, "bold")
        ).pack(anchor="w", padx=24, pady=(22, 3))
        ctk.CTkLabel(
            panel, text="API, type, and color preferences", text_color=MUTED, font=ui_font(13)
        ).pack(anchor="w", padx=26, pady=(0, 18))

        def section(title):
            ctk.CTkLabel(
                panel, text=title, text_color=ACCENT, anchor="w", font=ui_font(11, "bold")
            ).pack(fill="x", padx=25, pady=(4, 7))

        section("GEMINI API KEY")
        api_entry = ctk.CTkEntry(
            panel, height=42, show="•", placeholder_text="Paste your Gemini API key",
            fg_color=FIELD, border_color=BORDER,
        )
        api_entry.pack(fill="x", padx=24, pady=(0, 6))
        saved_key = read_saved_api_key()
        if saved_key:
            api_entry.insert(0, saved_key)
        ctk.CTkLabel(
            panel, text="Saved locally in ApiToken.txt", text_color=MUTED, font=ui_font(11)
        ).pack(anchor="w", padx=27, pady=(0, 14))

        section("FONT")
        font_row = ctk.CTkFrame(panel, fg_color="transparent")
        font_row.pack(fill="x", padx=24, pady=(0, 12))
        families = sorted(set(tkfont.families(window))) or ["Arial"]
        font_family = ctk.CTkOptionMenu(
            font_row, values=families, height=40, fg_color=FIELD,
            button_color=ACCENT, button_hover_color=ACCENT_HOVER,
        )
        font_family.set(PREFERENCES["font_family"] if PREFERENCES["font_family"] in families else families[0])
        font_family.pack(side="left", fill="x", expand=True, padx=(0, 12))
        font_size = ctk.CTkOptionMenu(
            font_row, values=[str(value) for value in range(10, 25, 2)], width=105, height=40,
            fg_color=FIELD, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
        )
        font_size.set(str(PREFERENCES["font_size"]))
        font_size.pack(side="right")

        section("COLOR THEME")
        theme = ctk.CTkSegmentedButton(
            panel, values=list(THEMES), height=40, selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
        )
        theme.pack(fill="x", padx=24, pady=(0, 14))
        theme.set(PREFERENCES["theme"])
        status = ctk.CTkLabel(panel, text="", text_color=GREEN)
        status.pack(anchor="w", padx=25, pady=(0, 4))

        def save():
            try:
                new_key = api_entry.get().strip()
                if new_key:
                    save_api_key(new_key)
                save_preferences({
                    "font_family": font_family.get(),
                    "font_size": font_size.get(),
                    "theme": theme.get(),
                    "model": PREFERENCES["model"],
                })
            except (OSError, ValueError) as exc:
                status.configure(text=str(exc), text_color=RED)
                return
            status.configure(text="Settings saved.", text_color=GREEN)
            self._ask_restart(window)

        ctk.CTkButton(
            panel, text="Save settings", height=46, corner_radius=14,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=BG,
            font=ui_font(14, "bold"), command=save,
        ).pack(fill="x", padx=24, pady=(0, 10))
        ctk.CTkLabel(
            panel, text="Font and theme changes require an app restart.",
            text_color=MUTED, font=ui_font(11),
        ).pack(anchor="w", padx=26, pady=(0, 18))

    def _ask_restart(self, settings_window):
        dialog = ctk.CTkToplevel(settings_window)
        dialog.title("Restart required")
        dialog.geometry("390x205")
        dialog.resizable(False, False)
        dialog.configure(fg_color=PANEL)
        dialog.transient(settings_window)
        dialog.grab_set()
        ctk.CTkLabel(
            dialog, text="Settings saved", text_color=TEXT, font=ui_font(19, "bold")
        ).pack(anchor="w", padx=22, pady=(22, 6))
        ctk.CTkLabel(
            dialog, text="Restart the app now to apply the changes?",
            text_color=MUTED, font=ui_font(13), wraplength=340, justify="left",
        ).pack(anchor="w", padx=22, pady=(0, 18))
        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.pack(fill="x", padx=22, pady=(0, 18))
        ctk.CTkButton(
            buttons, text="OK", width=100, height=38, corner_radius=12,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=BG,
            command=lambda: self._restart_app(dialog),
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            buttons, text="No", width=100, height=38, corner_radius=12,
            fg_color=FIELD, hover_color="#202B42", text_color=TEXT,
            command=dialog.destroy,
        ).pack(side="right")

    def _restart_app(self, dialog):
        dialog.destroy()
        self.voice.stop()
        if getattr(sys, "frozen", False):
            executable = sys.executable
            arguments = [executable, *sys.argv[1:]]
        else:
            executable = sys.executable
            arguments = [executable, str(Path(__file__).resolve()), *sys.argv[1:]]
        os.execv(executable, arguments)

    def _clear_voice_tool_activity(self):
        for child in self.voice_tools_log.winfo_children():
            child.destroy()
        self._voice_tool_cards_started = False
        self._voice_tool_cards.clear()
        self.voice_activity_empty = ctk.CTkLabel(
            self.voice_tools_log, text="Tool actions and results will appear here.",
            text_color=MUTED, font=ui_font(12), anchor="w",
        )
        self.voice_activity_empty.pack(fill="x", padx=8, pady=(5, 8))

    def _append_voice_tool_card(self, title, state, detail, result, tool_call_id=None):
        if not self._voice_tool_cards_started:
            for child in self.voice_tools_log.winfo_children():
                child.destroy()
            self._voice_tool_cards_started = True

        if state == "awaiting_approval":
            badge, color, body = "APPROVAL NEEDED", GOLD, detail or "Review this command before it runs."
        elif state == "running":
            badge, color, body = "RUNNING", ACCENT, detail or "Starting tool…"
        elif state == "completed":
            badge, color = "DONE", GREEN
            if result.get("message"):
                body = result["message"]
            else:
                sections = []
                if "return_code" in result:
                    sections.append(f"Exit code: {result['return_code']}")
                output = (result.get("stdout") or "").strip()
                errors = (result.get("stderr") or "").strip()
                if output:
                    sections.append("OUTPUT\n" + output[-1400:])
                if errors:
                    sections.append("NOTES\n" + errors[-800:])
                body = "\n\n".join(sections) or "Action completed successfully."
        elif state == "cancelled":
            badge, color = "CANCELLED", MUTED
            body = result.get("message", "The command was not run.")
        else:
            badge, color = "FAILED", RED
            sections = [result.get("error", "The tool could not complete.")]
            if result.get("return_code") is not None:
                sections.append(f"Exit code: {result['return_code']}")
            if result.get("stderr"):
                sections.append("DETAILS\n" + result["stderr"][-800:])
            if result.get("stdout"):
                sections.append("OUTPUT\n" + result["stdout"][-800:])
            body = "\n\n".join(sections)

        card_data = self._voice_tool_cards.get(tool_call_id) if tool_call_id else None
        if card_data:
            card = card_data["card"]
            for child in card.winfo_children():
                child.destroy()
        else:
            card = ctk.CTkFrame(
                self.voice_tools_log, fg_color="#1B263B", corner_radius=13,
                border_width=1, border_color=BORDER,
            )
            card.pack(fill="x", padx=5, pady=4)
            if tool_call_id:
                self._voice_tool_cards[tool_call_id] = {"card": card}
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=11, pady=(8, 1))
        ctk.CTkLabel(
            header, text=f"⚙  {title}", text_color=TEXT,
            font=ui_font(12, "bold"), anchor="w",
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            header, text=badge, text_color=color,
            font=ui_font(9, "bold"),
        ).pack(side="right", padx=(8, 0))
        body_label = ctk.CTkLabel(
            card, text=body, text_color=MUTED, justify="left", anchor="w",
            wraplength=max(300, self.voice_activity.winfo_width() - 64), font=ui_font(11),
        )
        body_label.pack(fill="x", padx=12, pady=(2, 8))

        def resize_tool_text(event):
            body_label.configure(wraplength=max(300, event.width - 28))

        body_label.bind("<Configure>", resize_tool_text)
        if state == "awaiting_approval" and tool_call_id:
            actions = ctk.CTkFrame(card, fg_color="transparent")
            actions.pack(fill="x", padx=10, pady=(0, 8))
            ctk.CTkButton(
                actions, text="Run command", width=120, height=30, corner_radius=9,
                fg_color=TEAL, hover_color="#36CDBB", text_color="#082321",
                command=lambda cid=tool_call_id: self._resolve_voice_approval(cid, True),
            ).pack(side="left", padx=(0, 7))
            ctk.CTkButton(
                actions, text="Cancel", width=88, height=30, corner_radius=9,
                fg_color="#39445A", hover_color="#4A5670",
                command=lambda cid=tool_call_id: self._resolve_voice_approval(cid, False),
            ).pack(side="left")
        elif state == "running" and tool_call_id:
            ctk.CTkButton(
                card, text="■  Stop command", width=150, height=32, corner_radius=10,
                fg_color="#7D3347", hover_color="#A7435C",
                command=lambda cid=tool_call_id: self._cancel_voice_command(cid),
            ).pack(anchor="w", padx=11, pady=(0, 9))
        try:
            self.voice_tools_log.update_idletasks()
            self.voice_tools_log._parent_canvas.yview_moveto(1.0)
        except (AttributeError, RuntimeError):
            pass

    def _drain_ui_queue(self):
        while True:
            try:
                callback, args, kwargs = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            callback(*args, **kwargs)
        try:
            self.root.after(40, self._drain_ui_queue)
        except Exception:
            pass

    def _append_message(self, widget, speaker, content, auto_scroll=True):
        display_speaker = "Mochi" if speaker == "Gemini" else speaker
        if speaker == "You":
            fill, edge, name_color = "#123139", "#24515A", TEAL
            sticky, side_pad = "e", (72, 0)
            heading = "YOU"
        elif speaker.startswith("Tool"):
            fill, edge, name_color = "#2B2935", "#51445A", GOLD
            sticky, side_pad = "w", (0, 72)
            heading = f"⚙  {display_speaker.upper()}"
        elif speaker == "Error":
            fill, edge, name_color = "#351F2A", "#613243", RED
            sticky, side_pad = "w", (0, 72)
            heading = "!  ERROR"
        else:
            fill, edge, name_color = "#20263D", "#303956", ACCENT
            sticky, side_pad = "w", (0, 72)
            heading = "✦  MOCHI"

        row = ctk.CTkFrame(widget, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=9)
        row.grid_columnconfigure(0, weight=1)
        bubble_column = 0 if sticky == "w" else 1
        bubble = ctk.CTkFrame(
            row, fg_color=fill, corner_radius=20, border_width=1, border_color=edge,
        )
        bubble.grid(row=0, column=bubble_column, sticky=sticky, padx=side_pad)
        ctk.CTkLabel(
            bubble, text=heading, text_color=name_color,
            font=ui_font(10, "bold"), anchor="w",
        ).pack(fill="x", padx=14, pady=(10, 2))
        log_width = max(500, widget.winfo_width() - 90)
        body_width = max(320, min(1100, int(log_width * 0.82) - sum(side_pad)))
        font_size = max(9, round(15 * PREFERENCES["font_size"] / 14))
        chars_per_line = max(36, int(body_width / (font_size * 0.58)))
        line_count = sum(
            max(1, math.ceil(len(line) / chars_per_line)) for line in content.split("\n")
        )
        body_height = max(50, 20 + line_count * (font_size + 8))
        body = ctk.CTkTextbox(
            bubble, width=body_width, height=body_height, wrap="word",
            corner_radius=12, border_width=0, fg_color=fill, text_color=TEXT,
            font=ui_font(15), activate_scrollbars=False,
        )
        body.pack(fill="x", padx=12, pady=(0, 2))
        body.insert("1.0", content)
        body.update_idletasks()
        measured_lines = body._textbox.count("1.0", "end-1c", "displaylines")
        if measured_lines:
            body.configure(height=max(50, 20 + int(measured_lines[0]) * (font_size + 8)))
        body._textbox.tag_config("message", justify="right" if self._contains_rtl(content) else "left")
        body.tag_add("message", "1.0", "end")
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            body._textbox.bind(
                sequence,
                lambda event, text_widget=body, scroll_frame=widget:
                    self._route_message_scroll(event, text_widget, scroll_frame),
            )
        body.configure(state="disabled")
        try:
            body._textbox.configure(selectbackground=ACCENT, selectforeground=BG)
        except tk.TclError:
            pass
        copy_button = ctk.CTkButton(
            bubble, text="Copy", width=66, height=26, corner_radius=9,
            fg_color="transparent", hover_color="#303A55", text_color=MUTED,
            font=ui_font(10), command=lambda text=content: self._copy_message(text, copy_button),
        )
        copy_button.pack(anchor="e", padx=11, pady=(1, 8))
        if auto_scroll:
            self.root.after_idle(lambda target=widget: self._scroll_chat_to_latest(target))

    @staticmethod
    def _scroll_chat_to_latest(widget):
        try:
            widget._parent_canvas.yview_moveto(1.0)
        except (AttributeError, RuntimeError, tk.TclError):
            pass

    @staticmethod
    def _scroll_chat_to_start(widget):
        try:
            widget._parent_canvas.yview_moveto(0.0)
        except (AttributeError, RuntimeError, tk.TclError):
            pass

    @staticmethod
    def _contains_rtl(content):
        return any("\u0590" <= char <= "\u08ff" for char in content)

    @staticmethod
    def _route_message_scroll(event, text_widget, scroll_frame):
        """Scroll long message text first, otherwise scroll the whole chat."""
        if getattr(event, "num", None) == 4:
            direction = -1
        elif getattr(event, "num", None) == 5:
            direction = 1
        else:
            delta = getattr(event, "delta", 0)
            if not delta:
                return "break"
            direction = -1 if delta > 0 else 1
            if sys.platform.startswith("win"):
                direction *= max(1, abs(int(delta / 120)))
            else:
                direction *= max(1, abs(int(delta)))

        message_view = text_widget.yview()
        if message_view != (0.0, 1.0):
            text_widget.yview_scroll(direction, "units")
        else:
            scroll_frame._parent_canvas.yview_scroll(direction, "units")
        return "break"

    def add_text_message(self, speaker, content):
        self._append_message(self.text_log, speaker, content)

    def add_voice_message(self, speaker, content):
        self.show_voice_reply(content)

    def _voice_tool_event(self, event):
        self.ui_queue.put((self._show_tool_event, (self._voice_chat_id, "voice", event), {}))

    def _confirm_terminal_command(self, command, _tool_call_id=None):
        finished = threading.Event()
        response = {"approved": False}
        self.ui_queue.put((self._show_terminal_confirmation, (command, finished, response), {}))
        if not finished.wait(timeout=120):
            return False
        return response["approved"]

    def _confirm_voice_terminal_command(self, _command, tool_call_id):
        finished = threading.Event()
        approval = {"approved": False, "event": finished}
        self._voice_pending_approvals[tool_call_id] = approval
        if not finished.wait(timeout=300):
            self._voice_pending_approvals.pop(tool_call_id, None)
            return False
        self._voice_pending_approvals.pop(tool_call_id, None)
        return approval["approved"]

    def _resolve_voice_approval(self, tool_call_id, approved):
        approval = self._voice_pending_approvals.get(tool_call_id)
        if approval:
            approval["approved"] = approved
            approval["event"].set()

    def _cancel_voice_command(self, tool_call_id):
        if cancel_tool(tool_call_id):
            self.status.configure(
                text="Stopping terminal command… voice session is still active.", text_color=GOLD
            )

    def _show_terminal_confirmation(self, command, finished, response):
        self.status.configure(
            text="Tool running · Terminal command — waiting for your approval",
            text_color=ACCENT,
        )
        try:
            response["approved"] = messagebox.askyesno(
                "Approve terminal command?",
                f"Mochi wants to run this command:\n\n{command}\n\n"
                f"Working directory:\n{Path(__file__).resolve().parent}\n\n"
                "Run this command?",
                icon="warning",
                default=messagebox.NO,
                parent=self.root,
            )
        finally:
            finished.set()

    def _show_tool_event(self, chat_id, mode, event):
        state = event["state"]
        title = event["title"]
        detail = event.get("detail", "")
        short_detail = detail if len(detail) <= 180 else f"{detail[:177]}…"
        if state == "running":
            message = f"Tool running · {title}"
            if short_detail:
                message += f": {short_detail}"
            color = ACCENT
            log_text = f"Running now\n{detail}" if detail else "Running now…"
        elif state == "awaiting_approval":
            message, color = f"Tool waiting for approval · {title}", GOLD
            log_text = f"Review and approve the command in the voice activity panel.\n{detail}"
        elif state == "completed":
            message, color = f"Tool complete · {title}", GREEN
            result = event.get("result") or {}
            if result.get("message"):
                log_text = f"✓  {result['message']}"
            else:
                sections = [f"✓  Finished · exit code {result.get('return_code', '—')}"]
                if result.get("stdout", "").strip():
                    output = result["stdout"].strip()
                    sections.append("OUTPUT\n" + (output[-2200:] if len(output) > 2200 else output))
                if result.get("stderr", "").strip():
                    errors = result["stderr"].strip()
                    sections.append("NOTES\n" + (errors[-1200:] if len(errors) > 1200 else errors))
                log_text = "\n\n".join(sections)
        elif state == "cancelled":
            message, color, log_text = f"Tool cancelled · {title}", MUTED, "Cancelled; the command was not run."
        else:
            message, color = f"Tool failed · {title}", RED
            result = event.get("result") or {}
            log_text = result.get("error", "The tool could not complete.")
            if result.get("stderr"):
                log_text += "\n\nDETAILS\n" + result["stderr"][-1200:]
        self.status.configure(text=message, text_color=color)

        if chat_id and self.text_chat and self.text_chat.get_active_chat_id() != chat_id:
            return
        if mode == "voice":
            self._append_voice_tool_card(
                title, state, detail, event.get("result") or {}, event.get("tool_call_id")
            )
        else:
            self._append_message(self.text_log, f"Tool · {title}", log_text)

    def _text_tool_event(self, chat_id, event):
        self.ui_queue.put((self._show_tool_event, (chat_id, "text", event), {}))

    def _refresh_chat_list(self):
        for child in self.chat_list.winfo_children():
            child.destroy()
        if not self.text_chat:
            return
        active_id = self.text_chat.get_active_chat_id()
        for chat in self.text_chat.list_chats():
            selected = chat["id"] == active_id
            row = ctk.CTkFrame(
                self.chat_list, fg_color="#1D2940" if selected else "transparent",
                corner_radius=15, border_width=1 if selected else 0,
                border_color=ACCENT if selected else SIDEBAR,
            )
            row.pack(fill="x", padx=3, pady=4)
            row.grid_columnconfigure(0, weight=1)
            row.grid_columnconfigure(1, minsize=82, weight=0)
            preview = "A new conversation"
            for turn in reversed(chat.get("turns", [])):
                user_step = next((step for step in turn if step.get("type") == "user_input"), None)
                if user_step:
                    preview = " ".join(self._step_text(user_step).split()) or preview
                    break
            preview = preview if len(preview) < 34 else preview[:31] + "…"
            button = ctk.CTkButton(
                row, text=f"✦  {chat['title']}\n    {preview}", anchor="w", height=62,
                corner_radius=13, fg_color="transparent",
                hover_color="#26334D", text_color=TEXT if selected else MUTED,
                font=ui_font(12), command=lambda cid=chat["id"]: self.select_chat(cid),
            )
            button.grid(row=0, column=0, sticky="nsew", padx=(7, 3), pady=6)
            delete = ctk.CTkButton(
                row, text="Delete", width=70, height=34, corner_radius=10,
                fg_color="#8F3047", hover_color="#C7435E", text_color="#FFF5F6",
                font=ui_font(10, "bold"), command=lambda cid=chat["id"]: self.delete_chat(cid),
            )
            delete.grid(row=0, column=1, sticky="e", padx=(2, 8), pady=6)

    @staticmethod
    def _model_label(model_id):
        return next((label for label, value in TEXT_MODELS.items() if value == model_id), next(iter(TEXT_MODELS)))

    def select_text_model(self, label):
        model_id = TEXT_MODELS.get(label)
        if not model_id:
            return
        PREFERENCES["model"] = model_id
        try:
            save_preferences(PREFERENCES)
        except OSError as exc:
            self.status.configure(text=f"Could not save model choice: {exc}", text_color=RED)
            return
        self.status.configure(text=f"Text model selected · {label}", text_color=ACCENT)

    def _load_selected_chat(self):
        if not self.text_chat:
            return
        chat = self.text_chat.get_chat()
        if chat is None:
            return
        self.chat_title.configure(text=chat["title"])
        for child in self.text_log.winfo_children():
            child.destroy()
        turns = self.text_chat.get_turns(chat["id"])
        if not turns:
            self._append_message(self.text_log, "Gemini", "Hey, I’m here. What’s on your mind?", auto_scroll=False)
        else:
            for turn in turns:
                for step in turn:
                    role = step.get("type")
                    label = "You" if role == "user_input" else "Mochi"
                    text = self._step_text(step)
                    if text:
                        self._append_message(self.text_log, label, text, auto_scroll=False)
        self.root.after_idle(lambda: self._scroll_chat_to_start(self.text_log))

    @staticmethod
    def _step_text(step):
        for item in step.get("content", []) or []:
            if isinstance(item, dict):
                if item.get("text"):
                    return item["text"]
                if item.get("type") == "text" and item.get("text"):
                    return item["text"]
        return ""

    def new_chat(self):
        if self.voice_button.cget("text") == VOICE_STOP_LABEL:
            self.voice.stop()
        if self.text_chat:
            self.text_chat.create_chat()
            self._refresh_chat_list()
            self._load_selected_chat()
            self.status.configure(text="✦  A fresh conversation, just for you.", text_color=GREEN)

    def select_chat(self, chat_id):
        if self._busy:
            self.status.configure(text="Mochi is finishing this reply—then you can switch chats.", text_color=MUTED)
            return
        if self.voice_button.cget("text") == VOICE_STOP_LABEL:
            self.voice.stop()
        self.text_chat.select_chat(chat_id)
        self._refresh_chat_list()
        self._load_selected_chat()
        self.status.configure(text="●  Ready when you are", text_color=GREEN)

    def delete_chat(self, chat_id):
        chat = self.text_chat.get_chat(chat_id)
        if chat is None:
            return
        if not messagebox.askyesno("Delete conversation", f"Delete ‘{chat['title']}’ and its saved history?"):
            return
        if chat_id == self.text_chat.get_active_chat_id() and self.voice_button.cget("text") == VOICE_STOP_LABEL:
            self.voice.stop()
        self.text_chat.delete_chat(chat_id)
        self._refresh_chat_list()
        self._load_selected_chat()
        self.status.configure(text="Conversation removed.", text_color=GREEN)

    def switch_mode(self, value):
        self.mode = "text" if "Text" in value else "voice"
        if self.mode == "text":
            self.voice_view.grid_remove()
            self.text_log.grid()
            self.message.grid()
            self.send_button.grid()
            self.composer_hint.grid()
            self.voice_controls.grid_remove()
            self.message.focus_set()
        else:
            self.text_log.grid_remove()
            self.voice_view.grid()
            self.message.grid_remove()
            self.send_button.grid_remove()
            self.composer_hint.grid_remove()
            self.voice_controls.grid()

    def _voice_status(self, message):
        if message.startswith("Voice error:"):
            display_message, color = message, RED
            self._voice_active = False
            self.voice_caption.configure(text="VOICE SPACE  ·  CONNECTION ISSUE")
        elif message.startswith("Connected"):
            display_message, color = "🎙  Mochi is listening · Go ahead and speak.", TEAL
            self._voice_active = True
            self.voice_caption.configure(text="LIVE  ·  MOCHI IS LISTENING")
        elif message == "Voice session stopped":
            display_message, color = "Voice chat ended · Ready whenever you are.", MUTED
            self._voice_active = False
            self.voice_caption.configure(text="VOICE SPACE  ·  READY")
        else:
            display_message, color = message, MUTED
        self.status.configure(text=display_message, text_color=color)
        if message.startswith("Voice error:") or message == "Voice session stopped":
            self.voice_button.configure(text=VOICE_START_LABEL, fg_color=TEAL, text_color="#082321")
            self._microphone_muted = False
            self.voice_mute_button.configure(text="Mic off", fg_color=FIELD)
            self.voice_mute_button.pack_forget()
        elif message.startswith("Connected"):
            self.voice_button.configure(text=VOICE_STOP_LABEL, fg_color=RED, text_color="white")
            self.voice_mute_button.pack(side="left", padx=(0, 8))
            self.voice_mute_button.configure(text="Mic off", fg_color=FIELD)

    def toggle_microphone_mute(self):
        if not self._voice_active:
            return
        self._microphone_muted = not self._microphone_muted
        self.voice.set_microphone_muted(self._microphone_muted)
        if self._microphone_muted:
            self.voice_mute_button.configure(text="Mic on", fg_color="#8F3047", hover_color="#C7435E")
            self.voice_caption.configure(text="MICROPHONE MUTED  ·  SESSION STILL CONNECTED")
            self.status.configure(text="Microphone muted · voice session stays connected", text_color=GOLD)
        else:
            self.voice_mute_button.configure(text="Mic off", fg_color=FIELD, hover_color="#26334D")
            self.voice_caption.configure(text="LIVE  ·  MOCHI IS LISTENING")
            self.status.configure(text="Microphone on · Mochi is listening", text_color=TEAL)

    def send_text(self):
        if self._busy:
            self.stop_text_request()
            return
        message = self.message.get("1.0", "end-1c").strip()
        if not message or not self.text_chat:
            return
        chat_id = self.text_chat.get_active_chat_id()
        selected_model = TEXT_MODELS[self.model_selector.get()]
        self.message.delete("1.0", "end")
        self.message.configure(height=92)
        self.add_text_message("You", message)
        self._busy = True
        self._text_request_id += 1
        request_id = self._text_request_id
        self._text_cancel_event = threading.Event()
        cancel_event = self._text_cancel_event
        self.send_button.configure(text="Stop  ■", fg_color=RED, hover_color="#E0526A", state="normal")
        self.status.configure(text="✦  Mochi is thinking…", text_color=ACCENT)

        def send():
            try:
                answer = self.text_chat.send_message(
                    message,
                    chat_id=chat_id,
                    on_tool_event=lambda event: self._text_tool_event(chat_id, event),
                    confirm_tool=self._confirm_terminal_command,
                    model=selected_model,
                    cancel_event=cancel_event,
                )
                if cancel_event.is_set():
                    return
                self.ui_queue.put((self.add_text_message, ("Gemini", answer or "(No text was returned.)"), {}))
                self.ui_queue.put((self.status.configure, (), {"text": "●  Ready when you are", "text_color": GREEN}))
                self.ui_queue.put((self._refresh_chat_list, (), {}))
                self.ui_queue.put((self._refresh_title, (chat_id,), {}))
            except Exception as exc:
                if cancel_event.is_set():
                    return
                friendly = friendly_gemini_error(exc)
                self.ui_queue.put((self.add_text_message, ("Error", friendly), {}))
                status_text = (
                    "Gemini usage limit reached — see details in chat"
                    if "usage limit" in friendly.lower()
                    else "Gemini request failed — see details in chat"
                )
                self.ui_queue.put((self.status.configure, (), {"text": status_text, "text_color": RED}))
            finally:
                self.ui_queue.put((self._finish_sending, (request_id,), {}))

        threading.Thread(target=send, daemon=True).start()

    def _send_button_action(self):
        if self._busy:
            self.stop_text_request()
        else:
            self.send_text()

    def stop_text_request(self):
        if not self._busy:
            return
        if self._text_cancel_event:
            self._text_cancel_event.set()
        self._text_request_id += 1
        self._busy = False
        self.send_button.configure(text="Send  ↗", fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                   state="normal" if self.text_chat else "disabled")
        self.status.configure(text="Request stopped · Ready when you are", text_color=MUTED)
        self._text_cancel_event = None

    def _refresh_title(self, chat_id):
        if self.text_chat.get_active_chat_id() == chat_id:
            chat = self.text_chat.get_chat(chat_id)
            if chat:
                self.chat_title.configure(text=chat["title"])

    def _finish_sending(self, request_id):
        if not self._busy or request_id != self._text_request_id:
            return
        self._busy = False
        self._text_cancel_event = None
        self.send_button.configure(text="Send  ↗", fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                   state="normal" if self.text_chat else "disabled")

    def toggle_voice(self):
        if self.voice_button.cget("text") == VOICE_START_LABEL:
            self._voice_chat_id = self.text_chat.get_active_chat_id() if self.text_chat else None
            self._clear_voice_tool_activity()
            self._microphone_muted = False
            self.voice.set_microphone_muted(False)
            self.show_voice_reply("Mochi is getting ready to listen…")
            self._voice_active = True
            self.voice_caption.configure(text="CONNECTING TO MOCHI…")
            self.voice_button.configure(text="Connecting…", state="disabled")
            self.voice.start()
            self.voice_button.configure(state="normal")
        else:
            self._voice_active = False
            self._microphone_muted = False
            self.voice.set_microphone_muted(False)
            self.voice_caption.configure(text="ENDING VOICE CHAT…")
            self.voice.stop()
            self.status.configure(text="Stopping voice session…", text_color=MUTED)

    def close(self):
        self.voice.stop()
        self._voice_chat_id = None
        self.root.destroy()


if __name__ == "__main__":
    app = ctk.CTk()
    ChatApp(app)
    app.mainloop()
