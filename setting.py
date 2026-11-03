"""Mochi Desk preferences and standalone settings window."""

import json
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path

import customtkinter as ctk

ROOT = Path(__file__).resolve().parent
PREFERENCES_FILE = ROOT / "settings.json"
TOKEN_FILE = ROOT / "ApiToken.txt"

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
    defaults = {"font_family": "Arial", "font_size": 14, "theme": "Violet"}
    try:
        saved = json.loads(PREFERENCES_FILE.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            defaults.update(saved)
    except (OSError, ValueError, TypeError):
        pass
    if defaults.get("theme") not in THEMES:
        defaults["theme"] = "Violet"
    try:
        defaults["font_size"] = max(10, min(24, int(defaults["font_size"])))
    except (TypeError, ValueError):
        defaults["font_size"] = 14
    return defaults


def save_preferences(preferences):
    clean = {
        "font_family": str(preferences["font_family"]),
        "font_size": max(10, min(24, int(preferences["font_size"]))),
        "theme": preferences["theme"] if preferences["theme"] in THEMES else "Violet",
    }
    temporary = PREFERENCES_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
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


class SettingsWindow:
    def __init__(self, root):
        self.root = root
        self.root.title("Mochi Desk · Settings")
        self.root.geometry("520x660")
        self.root.minsize(480, 600)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.preferences = load_preferences()
        self.colors = THEMES[self.preferences["theme"]]
        root.configure(fg_color=self.colors["BG"])

        panel = ctk.CTkFrame(root, fg_color=self.colors["PANEL"], corner_radius=24)
        panel.pack(fill="both", expand=True, padx=22, pady=22)
        ctk.CTkLabel(
            panel, text="✦  Settings", text_color=self.colors["TEXT"],
            font=ctk.CTkFont(self.preferences["font_family"], 25, "bold"),
        ).pack(anchor="w", padx=24, pady=(22, 3))
        ctk.CTkLabel(
            panel, text="API, type, and color preferences", text_color=self.colors["MUTED"],
            font=ctk.CTkFont(self.preferences["font_family"], 13),
        ).pack(anchor="w", padx=26, pady=(0, 18))

        self._section(panel, "GEMINI API KEY")
        self.api_entry = ctk.CTkEntry(
            panel, height=42, show="•", placeholder_text="Paste your Gemini API key",
            fg_color=self.colors["FIELD"], border_color=self.colors["BORDER"],
        )
        self.api_entry.pack(fill="x", padx=24, pady=(0, 6))
        current_key = read_saved_api_key()
        if current_key:
            self.api_entry.insert(0, current_key)
        ctk.CTkLabel(
            panel, text="Saved locally in ApiToken.txt", text_color=self.colors["MUTED"],
            font=ctk.CTkFont(self.preferences["font_family"], 11),
        ).pack(anchor="w", padx=27, pady=(0, 14))

        self._section(panel, "FONT")
        families = sorted(set(tkfont.families(root)))
        if not families:
            families = ["Arial"]
        font_row = ctk.CTkFrame(panel, fg_color="transparent")
        font_row.pack(fill="x", padx=24, pady=(0, 12))
        self.font_family = ctk.CTkOptionMenu(
            font_row, values=families, height=40, fg_color=self.colors["FIELD"],
            button_color=self.colors["ACCENT"], button_hover_color=self.colors["ACCENT_HOVER"],
        )
        self.font_family.set(
            self.preferences["font_family"] if self.preferences["font_family"] in families else families[0]
        )
        self.font_family.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.font_size = ctk.CTkOptionMenu(
            font_row, values=[str(size) for size in range(10, 25, 2)], width=105, height=40,
            fg_color=self.colors["FIELD"], button_color=self.colors["ACCENT"],
            button_hover_color=self.colors["ACCENT_HOVER"],
        )
        self.font_size.set(str(self.preferences["font_size"]))
        self.font_size.pack(side="right")

        self._section(panel, "COLOR THEME")
        self.theme = ctk.CTkSegmentedButton(
            panel, values=list(THEMES), height=40, selected_color=self.colors["ACCENT"],
            selected_hover_color=self.colors["ACCENT_HOVER"],
        )
        self.theme.pack(fill="x", padx=24, pady=(0, 14))
        self.theme.set(self.preferences["theme"])

        self.status = ctk.CTkLabel(panel, text="", text_color=self.colors["GREEN"])
        self.status.pack(anchor="w", padx=25, pady=(0, 4))
        ctk.CTkButton(
            panel, text="Save settings", height=46, corner_radius=14,
            fg_color=self.colors["ACCENT"], hover_color=self.colors["ACCENT_HOVER"],
            text_color=self.colors["BG"], font=ctk.CTkFont(size=14, weight="bold"),
            command=self.save,
        ).pack(fill="x", padx=24, pady=(0, 10))
        ctk.CTkLabel(
            panel, text="Restart Mochi Desk to apply font and theme changes.",
            text_color=self.colors["MUTED"], font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=26, pady=(0, 18))

    def _section(self, parent, title):
        ctk.CTkLabel(
            parent, text=title, text_color=self.colors["ACCENT"], anchor="w",
            font=ctk.CTkFont(self.preferences["font_family"], 11, "bold"),
        ).pack(fill="x", padx=25, pady=(4, 7))

    def save(self):
        try:
            api_key = self.api_entry.get().strip()
            if api_key:
                save_api_key(api_key)
            save_preferences({
                "font_family": self.font_family.get(),
                "font_size": self.font_size.get(),
                "theme": self.theme.get(),
            })
        except (OSError, ValueError) as exc:
            self.status.configure(text=str(exc), text_color="#FB7185")
            return
        self.status.configure(text="Saved. Restart Mochi Desk to apply the changes.")


def main():
    root = ctk.CTk()
    SettingsWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
