"""Shared helpers for reading and interpreting Mochi's local configuration."""

import json
from pathlib import Path


def get_app_data_dir() -> Path:
    """Return the project directory used to store local application data.

    Keeping this path relative to the source file, rather than the process's
    current working directory, ensures settings and chat data are always stored
    in the same place when the app is launched from different directories.
    """
    # Resolve this file first so the result does not depend on where Python was started.
    return Path(__file__).resolve().parent


# Store the API key separately from ordinary preferences so each file has one purpose.
TOKEN_FILE = get_app_data_dir() / "ApiToken.txt"
# Store non-secret preferences such as the selected model and optional API endpoint here.
SETTINGS_FILE = get_app_data_dir() / "settings.json"


def _read_settings() -> dict:
    """Read the settings object, returning an empty mapping if it is missing or invalid."""
    try:
        # UTF-8 supports Unicode values, including non-English text in preferences.
        settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        # Settings must be a JSON object; arrays and scalar values have no named options.
        return settings if isinstance(settings, dict) else {}
    # A first launch may not have a settings file yet; malformed files use defaults too.
    except (OSError, ValueError, TypeError):
        return {}


def get_api_base_url() -> str:
    """Return the configured Gemini-compatible API base URL, or an empty string."""
    value = _read_settings().get("api_base_url", "")
    # Ignore unexpected value types and trim accidental whitespace around valid URLs.
    return value.strip() if isinstance(value, str) else ""


def use_custom_api_base_url() -> bool:
    """Return whether requests should use the saved custom endpoint."""
    settings = _read_settings()
    value = settings.get("use_custom_api_base_url")
    # A saved boolean is authoritative, so users can explicitly turn the endpoint off.
    if isinstance(value, bool):
        return value
    # Keep existing installations on their previously configured endpoint.
    # Older settings files have no toggle; a saved URL means the old behavior was enabled.
    return bool(get_api_base_url())


def friendly_gemini_error(error) -> str:
    """Convert common Gemini API failures into concise, actionable user messages."""
    candidates = []

    def collect(item):
        # A request may raise an ExceptionGroup; inspect every nested exception individually.
        if isinstance(item, BaseExceptionGroup):
            for child in item.exceptions:
                collect(child)
        else:
            candidates.append(item)

    # Flatten grouped errors so known API failures can still receive specific guidance.
    collect(error)
    for item in candidates:
        # SDK versions expose status, code, and message through different attributes.
        code = getattr(item, "code", None)
        status = str(getattr(item, "status", "") or "").upper()
        raw = getattr(item, "message", None) or str(item)
        # Normalize whitespace and cap details so a huge response does not flood the chat.
        detail = " ".join(str(raw).split())[:360]
        lowered = f"{status} {detail}".lower()

        # HTTP 429 and quota markers indicate a usage, credit, or rate limit.
        if code == 429 or "resource_exhausted" in lowered or "quota" in lowered:
            return (
                "Gemini API usage limit reached. Your quota or available credits may be used up, "
                "or the service may be rate-limiting requests. Check this API key's quota and billing, "
                "then wait or choose another model.\n\nDetails: " + detail
            )
        # HTTP 401/403 usually indicates an invalid key or insufficient model access.
        if code in (401, 403) or "api_key_invalid" in lowered or "invalid api key" in lowered:
            return (
                "Gemini denied access. Check that the API key is valid and that this account can "
                "use the selected model. You can also try another model.\n\nDetails: " + detail
            )
        # HTTP 404 usually means the selected model is unavailable for this API or key.
        if code == 404 or "model not found" in lowered:
            return (
                "This Gemini model is unavailable for the current API key or API version. "
                "Try another model from the list.\n\nDetails: " + detail
            )
        # HTTP 503 is commonly temporary; the user can retry after the service recovers.
        if code == 503 or "service_unavailable" in lowered:
            return "Gemini is temporarily busy. Please wait a little and try again.\n\nDetails: " + detail
    # Preserve a short version of unknown errors instead of hiding useful diagnostics.
    return " ".join(str(error).split())[:700] or "The Gemini request failed. Please try again."


def read_api_key() -> str:
    """Read the Gemini API key, raising a clear setup error if it is unavailable."""
    # A missing key file normally means the user has not saved a key in Settings yet.
    if not TOKEN_FILE.exists():
        raise FileNotFoundError(
            f"{TOKEN_FILE.name} was not found at {TOKEN_FILE}. "
            "Open Settings and save your Gemini API key."
        )
    # Parse line by line so harmless extra lines do not prevent reading the key.
    for line in TOKEN_FILE.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.strip().partition("=")
        # Accept only the api_key entry and remove optional surrounding quote characters.
        if separator and key.strip() == "api_key" and value.strip():
            return value.strip().strip("\"'")
    # The file exists but has no usable key; report the expected file format.
    raise ValueError("Invalid API key file format. Expected: api_key=YOUR_API_KEY")
