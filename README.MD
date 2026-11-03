# Mochi Desk

Mochi Desk is a desktop AI chat app built with Python. It connects to Google's Gemini API using an API key created in [Google AI Studio](https://aistudio.google.com/app/apikey). You need to create your own API key and add it in the app's **Settings**, or save it in an `ApiToken.txt` file.

[راهنمای فارسی](README.FA.MD)

## Features

- **Text chat:** Create multiple conversations. The app saves text chat history locally on your computer in `chats.json` (up to 200 turns per conversation).
- **Voice chat:** Talk with Gemini Live. Voice sessions keep context while the session is active, but voice conversation history is not saved as a conversation after the session ends.
- **Two built-in tools:** Open an installed desktop app, or request a terminal command. Terminal commands are shown for your approval before they run.
- **Customizable interface:** Choose a text model, theme, and font settings.

## Gemini models

The text model can be selected from the menu at the top of the chat:

| Mode | Model ID | Notes |
| --- | --- | --- |
| Text | `gemini-3.5-flash-lite` | Default text model |
| Text | `gemini-3-flash-preview` | Selectable text model |
| Text | `gemini-2.5-flash-lite` | Selectable text model |
| Voice | `gemini-3.8-live` | Live audio conversation model |

Model access and availability depend on Google's API and your API key.

## API key

1. Create an API key in [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Start Mochi Desk and open **Settings** to save the key, or create `ApiToken.txt` in the project folder with this format:

   ```text
   api_key=YOUR_GOOGLE_GEMINI_API_KEY
   ```

Keep your key private. Do not commit `ApiToken.txt` or share screenshots that reveal the key. The project ignores this file in Git.

## Run from source

Install Python first. Then open a terminal in the project directory and create a virtual environment:

### macOS and Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requerments.txt
python run.py
```

### Windows PowerShell

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requerments.txt
python run.py
```

The dependency file is currently named `requerments.txt`; use that spelling in the install command.

## Audio installation notes

PyAudio uses the system's PortAudio audio library. Depending on your operating system, Python version, and CPU architecture, pip may need to compile PyAudio instead of downloading a prebuilt wheel. If installation fails, consult the PyAudio and operating-system documentation for the required compiler, Python development headers, and PortAudio development package.

For example, on Fedora, a missing `gcc` can cause a build error like `No such file or directory: 'gcc'`. Install the system build and audio dependencies, then retry the pip install:

```bash
sudo dnf install gcc python3-devel portaudio-devel python3-tkinter
```

Other Linux distributions use different package names. Tkinter is also required for the desktop interface and may need to be installed from your OS package manager. Read the full installation error and the relevant package documentation if another native dependency is missing.

## Tools and permissions

### Open an app

The `open_app` tool attempts to launch an installed application by name. It uses the platform's app-launch mechanisms on macOS, Windows, and Linux. The application must be installed and discoverable through the operating system's application list, Start Menu, launcher, or executable search path. A successful tool result means the OS accepted the launch request; it does not verify what the app does afterward.

### Run a terminal command

The `run_terminal_command` tool runs a requested shell command from the project directory. Before execution, Mochi Desk displays the exact command and asks you to approve it. You can cancel a running command from the interface. Commands have a 45-second limit and their output is returned to the chat/tool activity view.

This is a captured, non-interactive command runner, not a regular terminal window. Commands that wait for keyboard input—including many `sudo` password prompts, interactive SSH sessions, and text editors—may fail or time out. Run privileged or interactive commands yourself in a terminal you control. Never approve a command unless you understand what it will do.

## Memory and privacy

Text conversations are saved locally in `chats.json` in the project directory when running from source. The app does not provide a hosted chat-history service. To answer follow-up messages, the app sends the selected conversation context to Google's Gemini API. Local storage is not encryption: anyone with access to your computer account and project files may be able to read the history. Protect your computer and API key accordingly.

Voice conversation context exists only during the active Gemini Live session and is not saved to `chats.json` as chat history. The audio is sent to Google's Gemini Live API to provide the voice conversation.

The `soul` file contains Mochi's personality and operating instructions. **Do not edit or remove `soul`**; the model relies on its instructions and changing them can affect how the app behaves.

## Roadmap

The project is still under active development. Future work includes fixing reported bugs, adding more model choices, and building additional tools.

If you find a bug, send its description and, if possible, the error message to **Telegram: [@m_aminiot](https://t.me/m_aminiot)** so I can investigate and fix it.
