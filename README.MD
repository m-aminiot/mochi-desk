# Mochi Desk

Mochi Desk is a Python desktop assistant with a local web interface. It provides text chat, Gemini Live voice chat, chat history stored on the computer, and tools for opening installed apps or running approved terminal commands. The web server listens on the local computer only (`127.0.0.1`); it chooses an available port and opens the interface in your browser.

[راهنمای فارسی](README.FA.MD)

**GitHub:** [https://github.com/m-aminiot](https://github.com/m-aminiot)

## Features

- **Text chat:** Create and switch between conversations. Text history is stored locally in `chats.json`, with up to 200 turns per conversation.
- **Voice chat:** Talk to Gemini Live using your microphone and speakers. Voice context lasts for the active session; voice transcripts are not saved as regular chat history.
- **Local tools:** Ask Mochi to open an installed app or run a terminal command. Mochi shows each terminal command and waits for your approval before running it.
- **Settings:** Choose a text model and color theme, configure your Gemini API key, and optionally set a custom API base URL.

## Requirements

- Python 3.11 or newer.
- A Gemini API key. A custom Gemini-compatible API base URL is optional.
- For voice chat, a working microphone and speaker, plus the system audio dependencies needed to install PyAudio. Text chat does not require audio devices.

## First-time setup

Open a terminal, change to the project directory, create a virtual environment, activate it, and install the dependencies. The dependency file in this project is named `requerments.txt` (with that spelling).

### macOS and Linux

```bash
cd /path/to/Gemini
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requerments.txt
```

### Windows PowerShell

```powershell
cd C:\path\to\Gemini
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requerments.txt
```

Replace the example path with the actual location of this project. If PowerShell blocks environment activation, follow Microsoft's instructions for your machine's execution policy or activate the environment from Command Prompt with `.venv\Scripts\activate.bat`.

## Configure the API

1. Create a Gemini API key in [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Start Mochi Desk using the instructions below.
3. Open **Settings**, enter your API key, and save.

By default, Mochi connects directly to Google's Gemini API. To route requests through another Gemini-compatible service, turn on **Use a custom API base URL**, enter that service's base URL, and save. Turning the option off returns to the standard Gemini API. Existing saved custom URLs remain in Settings when the option is off. The start screen points to Settings if the API key is missing or a selected custom URL has not been entered. You can also save your key in an `ApiToken.txt` file in the project directory:

```text
api_key=YOUR_GEMINI_API_KEY
```

Settings, the API key file, chat history, and preferences are stored locally. Keep your API key private and do not commit it or include it in screenshots.

### Optional Cloudflare Worker proxy

The [`proxy/worker.js`](proxy/worker.js) file is an optional reverse proxy for Gemini API requests. It works at the HTTP application layer (OSI layer 7): it receives an HTTP request addressed to your Worker URL, changes the hostname in the request URL to `generativelanguage.googleapis.com`, then uses the Workers `fetch()` API to send a new request to Google. This changes the request destination; it does not change DNS records or rewrite the API request's content.

The Worker keeps the incoming path and query string, HTTP method, body, and request headers, except that it removes `cf-connecting-ip` and `x-real-ip`. This means Gemini API paths, model names, request data, and the API key supplied by Mochi continue through to Google. The key is not created or stored by the Worker. The response from Google is returned to Mochi with CORS response headers added for ordinary HTTP responses. If the Worker encounters an exception while making the upstream request, it returns a JSON `502` response. Google's own HTTP error responses are passed back with their status and body.

For Gemini Live, the Worker also handles WebSocket upgrades: after Google accepts the connection, it relays messages in both directions and forwards binary frames used by audio. It closes the peer connection when either side closes or errors. This is a live relay; the Worker code does not save chat messages or audio.

The Worker URL is publicly reachable unless you separately protect it. This code has no caller authentication or rate limiting, and its permissive CORS headers are not access control. Anyone can send requests through the URL and consume your Cloudflare Worker resources. They do not get your Gemini API key merely by knowing the URL, but they can send their own requests through your Worker. The proxy does not grant Google API access, quota, billing eligibility, model availability, or regional support. It also forwards `OPTIONS` requests upstream rather than handling browser preflight locally. See [`proxy/IMPORTANT.MD`](proxy/IMPORTANT.MD) for deployment steps and further security and troubleshooting details. Cloudflare documents the [`fetch()` API](https://developers.cloudflare.com/workers/runtime-apis/fetch/) and [WebSocket support](https://developers.cloudflare.com/workers/examples/websockets/) used by Workers.

## Run Mochi Desk

After installing the dependencies, **activate the virtual environment each time you open a new terminal to run the app**. First change to the project directory, then activate `.venv`, then run `run.py`.

### macOS and Linux — every run

```bash
cd /path/to/Gemini
source .venv/bin/activate
python run.py
```

### Windows PowerShell — every run

```powershell
cd C:\path\to\Gemini
.venv\Scripts\Activate.ps1
python run.py
```

Keep the terminal open while using Mochi Desk. Press `Ctrl+C` in that terminal to stop the server. The next time you run it, open a terminal and repeat the three steps above. You do not need to recreate the virtual environment or reinstall packages unless the environment was deleted or dependencies changed.

## Audio installation notes

PyAudio uses the system's PortAudio library. Depending on your OS, Python version, and CPU architecture, pip may need to compile it. On Fedora, for example, the required system packages may include:

```bash
sudo dnf install gcc python3-devel portaudio-devel
```

Package names differ across Linux distributions. If installation fails, check the full error and your operating system's package instructions for a compiler, Python development headers, or PortAudio. Voice chat also requires the OS to allow microphone access.

## Built-in tools

### Open an app

The `open_app` tool asks the operating system to launch an installed app by name. The app must be installed and discoverable through the OS app list, launcher, Start Menu, or executable search path. A successful result means the OS accepted the launch request; it does not report what the launched app does afterward.

### Run a terminal command

The `run_terminal_command` tool runs from the project directory, captures its output, and stops after 45 seconds. Mochi displays the exact command in the local interface and requires your approval before it runs. You can cancel a running command from the interface.

This is a non-interactive command runner, not a terminal session. Commands that wait for keyboard input—including many `sudo` password prompts, interactive SSH sessions, and text editors—may fail or time out. Run privileged or interactive commands yourself in a terminal you control, and approve only commands you understand.

## Models

The available text models are selected from the chat menu. The current choices are `gemini-3.5-flash-lite`, `gemini-3-flash-preview`, and `gemini-2.5-flash-lite`. Voice chat uses `gemini-3.8-live`. Availability depends on the API service, account, and key.

## Data and privacy

When run from source, text conversations are saved in `chats.json` in the project directory. Mochi has no hosted chat-history service, but it sends the selected conversation context to the configured Gemini-compatible API to produce replies. Local files are not encrypted; other people with access to your computer account and project files may be able to read them.

Voice audio is sent to the configured Gemini Live service during an active voice session. Voice context is held for that session and is not saved as regular chat history. The local web server binds to `127.0.0.1`, uses HTTP, and selects a local port; it is intended for use on the same computer, not as a network-hosted service.

The `soul` file contains Mochi's personality and behavior instructions. Editing or removing it can change how the assistant behaves.

## Troubleshooting

- **Setup notice appears:** Open Settings and provide your API key. If custom API routing is enabled, also enter its base URL.
- **`ModuleNotFoundError`:** Make sure you are in the project directory and have activated `.venv` in the current terminal, then install the dependencies with `python -m pip install -r requerments.txt`.
- **PyAudio installation fails:** Install the audio and compiler packages required by your operating system, then retry the pip installation.
- **A model or API request fails:** Check the API key, the custom API base URL if enabled, selected model access, and the service's quota or availability.

The project is under active development. Find the source on **GitHub: [https://github.com/m-aminiot](https://github.com/m-aminiot)**. To report a bug, send its description and, if possible, the error message to **Telegram: [@m_aminiot](https://t.me/m_aminiot)**.
