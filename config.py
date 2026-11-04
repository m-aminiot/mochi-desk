"""Shared local configuration helpers."""

import os
import sys
from pathlib import Path


def get_app_data_dir() -> Path:
    """Return writable settings storage, outside PyInstaller's temp bundle."""
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    data_dir = base / "MochiDesk"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


TOKEN_FILE = get_app_data_dir() / "ApiToken.txt"
API_BASE_URL = "........."


def friendly_gemini_error(error) -> str:
    """Turn common Gemini API failures into a useful, accurate user message."""
    candidates = []

    def collect(item):
        if isinstance(item, BaseExceptionGroup):
            for child in item.exceptions:
                collect(child)
        else:
            candidates.append(item)

    collect(error)
    for item in candidates:
        code = getattr(item, "code", None)
        status = str(getattr(item, "status", "") or "").upper()
        raw = getattr(item, "message", None) or str(item)
        detail = " ".join(str(raw).split())[:360]
        lowered = f"{status} {detail}".lower()
        if code == 429 or "resource_exhausted" in lowered or "quota" in lowered:
            return (
                "Gemini API usage limit reached. Your quota or available credits may be used up, "
                "or the service may be rate-limiting requests. Check this API key's quota and billing, "
                "then wait or choose another model.\n\nDetails: " + detail
            )
        if code in (401, 403) or "api_key_invalid" in lowered or "invalid api key" in lowered:
            return (
                "Gemini denied access. Check that the API key is valid and that this account can "
                "use the selected model. You can also try another model.\n\nDetails: " + detail
            )
        if code == 404 or "model not found" in lowered:
            return (
                "This Gemini model is unavailable for the current API key or API version. "
                "Try another model from the list.\n\nDetails: " + detail
            )
        if code == 503 or "service_unavailable" in lowered:
            return "Gemini is temporarily busy. Please wait a little and try again.\n\nDetails: " + detail
    return " ".join(str(error).split())[:700] or "The Gemini request failed. Please try again."


def read_api_key() -> str:
    if not TOKEN_FILE.exists():
        raise FileNotFoundError(
            f"{TOKEN_FILE.name} was not found at {TOKEN_FILE}. "
            "Open Settings and save your Gemini API key."
        )
    for line in TOKEN_FILE.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and key.strip() == "api_key" and value.strip():
            return value.strip().strip("\"'")
    raise ValueError("Invalid API key file format. Expected: api_key=YOUR_API_KEY")
