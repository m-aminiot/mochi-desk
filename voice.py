"""Run a real-time Gemini Live voice session with local audio processing.

Audio callbacks capture and play 24 kHz mono frames. Captured microphone frames
are resampled to 16 kHz, cleaned with echo cancellation and noise suppression,
then sent to Gemini Live. Returned audio is buffered and played through the
default output device.
"""

import asyncio
import queue
import threading

import numpy as np
import pyaudio
from google import genai
from google.genai import types
from pywebrtc_audio import AudioProcessor

from config import friendly_gemini_error, get_api_base_url, read_api_key, use_custom_api_base_url
from text import read_soul
from tools import LIVE_TOOLS, execute_tool

# Use signed 16-bit mono PCM for both PortAudio streams and Gemini audio payloads.
FORMAT = pyaudio.paInt16
CHANNELS = 1
# The local device callbacks run at 24 kHz; outgoing audio is converted to 16 kHz.
DEVICE_SAMPLE_RATE = 24000
SEND_SAMPLE_RATE = 16000
# Gemini Live audio playback is received at the device's 24 kHz rate.
RECEIVE_SAMPLE_RATE = 24000
# Each device callback handles one 10 ms audio frame at its respective sample rate.
FRAME_SAMPLES = 240  # 10 ms at 24 kHz (the callback/device rate).
SEND_FRAME_SAMPLES = 160  # 10 ms at 16 kHz (Gemini input rate).
# Gemini Live model used for this audio-only interactive session.
MODEL = "gemini-3.8-live"
# Bound playback buffering so an overloaded speaker callback cannot grow memory indefinitely.
MAX_PLAYBACK_BYTES = 2 * 1024 * 1024

# Live session configuration: audio replies, the selected voice, compressed context,
# Mochi's shared instructions, and the registered function tools.
CONFIG = types.LiveConnectConfig(
    response_modalities=["AUDIO"],
    speech_config=types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Zephyr")
        )
    ),
    context_window_compression=types.ContextWindowCompressionConfig(
        trigger_tokens=104857,
        sliding_window=types.SlidingWindow(target_tokens=52428),
    ),
    system_instruction=read_soul() or None,
    tools=LIVE_TOOLS,
)


class VoiceSession:
    """Own one microphone/speaker session and stream audio to and from Gemini Live.

    The public start/stop methods are safe to call from the web server thread;
    the asyncio session and audio processing run in a dedicated background thread.
    """

    def __init__(
        self,
        on_status=lambda message: None,
        on_text=lambda message: None,
        on_tool_event=lambda event: None,
        confirm_tool=None,
    ):
        # Callbacks report UI updates without coupling this class to the web server.
        self.on_status = on_status
        self.on_text = on_text
        self.on_tool_event = on_tool_event
        self.confirm_tool = confirm_tool
        self._thread = None
        self._loop = None
        self._main_task = None
        # Threading events communicate stop and mute requests across callback threads.
        self._stop_requested = threading.Event()
        self._microphone_muted = threading.Event()
        self._microphone_frames = None
        self.on_level = lambda channel, value: None
        self._levels = {"input": 0.0, "output": 0.0}
        self._levels_lock = threading.Lock()

    def levels(self):
        """Return a thread-safe snapshot of the current input/output audio levels."""
        with self._levels_lock:
            return dict(self._levels)

    def _report_level(self, channel, value):
        """Store a normalized level and notify the optional UI callback."""
        with self._levels_lock:
            self._levels[channel] = value
        self.on_level(channel, value)

    def set_microphone_muted(self, muted: bool):
        """Stop sending captured speech while keeping the Live session connected.

        Drop queued audio immediately when mute changes so stale speech is not sent
        after unmuting or after the user interrupts the assistant.
        """
        if muted:
            self._microphone_muted.set()
        else:
            self._microphone_muted.clear()
        frames = self._microphone_frames
        if frames is not None:
            while True:
                try:
                    frames.get_nowait()
                except queue.Empty:
                    break

    def start(self):
        """Start a session unless one is already running."""
        if self._thread and self._thread.is_alive():
            return
        # A previous session may have set this event; clear it before starting again.
        self._stop_requested.clear()
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()

    def stop(self):
        """Request shutdown and cancel the asyncio session task if it is active."""
        self._stop_requested.set()
        if self._loop and self._main_task:
            self._loop.call_soon_threadsafe(self._main_task.cancel)

    def _thread_main(self):
        """Run the async session and report clean shutdowns or readable errors."""
        try:
            asyncio.run(self._run())
        except asyncio.CancelledError:
            self.on_status("Voice session stopped")
        except Exception as exc:
            detail = friendly_gemini_error(exc)
            self.on_status(f"Voice error: {detail[:600]}")

    @classmethod
    def _exception_messages(cls, group):
        """Flatten nested exception groups into individual type-and-message strings."""
        messages = []
        for item in group.exceptions:
            if isinstance(item, BaseExceptionGroup):
                messages.extend(cls._exception_messages(item))
            else:
                messages.append(f"{type(item).__name__}: {item}")
        return messages

    @staticmethod
    def _resample_24k_to_16k(data):
        """Convert one 24 kHz, 10 ms PCM frame to 16 kHz with linear interpolation.

        Return a silent frame when the input length is unexpected, preserving the
        fixed frame size required by the echo-cancellation processor.
        """
        samples = np.frombuffer(data, dtype=np.int16)
        if samples.size != FRAME_SAMPLES:
            return np.zeros(SEND_FRAME_SAMPLES, dtype=np.int16)
        # One 10 ms frame: linear interpolation from 24 to 16 samples/ms.
        positions = np.arange(SEND_FRAME_SAMPLES, dtype=np.float32) * 1.5
        return np.interp(positions, np.arange(FRAME_SAMPLES), samples).astype(np.int16)

    async def _run(self):
        """Open devices and run concurrent capture, response, and playback tasks.

        The TaskGroup keeps the session tasks together: if one task fails or is
        cancelled, the remaining tasks are cancelled and audio devices are closed.
        """
        self._loop = asyncio.get_running_loop()
        self._main_task = asyncio.current_task()
        http_options = {"api_version": "v1beta"}
        # The custom endpoint is optional; otherwise use the SDK's Google API default.
        if use_custom_api_base_url():
            base_url = get_api_base_url()
            if not base_url:
                raise ValueError("Enter an API base URL in Settings or turn off the custom API base URL option.")
            http_options["base_url"] = base_url
        client = genai.Client(
            http_options=http_options,
            api_key=read_api_key(),
        )
        audio = pyaudio.PyAudio()
        input_stream = output_stream = None
        try:
            # Keep the Live connection open for the duration of all audio tasks.
            async with client.aio.live.connect(model=MODEL, config=CONFIG) as session:
                self.on_status("Connected. You can speak now.")
                input_info = audio.get_default_input_device_info()
                playback_chunks = queue.Queue()
                # Echo cancellation compares speaker output with microphone input.
                # Bounded queues prevent lagging callbacks from accumulating stale audio.
                reference_frames = queue.Queue(maxsize=100)
                microphone_frames = queue.Queue(maxsize=100)
                self._microphone_frames = microphone_frames
                playback_buffer = bytearray()
                playback_lock = threading.Lock()
                processor = AudioProcessor(
                    sample_rate=SEND_SAMPLE_RATE,
                    num_channels=CHANNELS,
                    echo_cancellation=True,
                    noise_suppression=True,
                    auto_gain_control=True,
                    stream_delay_ms=40,
                )

                def output_callback(_input_data, frame_count, _time_info, _status):
                    """Feed the speaker and retain the played frame for echo cancellation."""
                    byte_count = frame_count * 2
                    with playback_lock:
                        while len(playback_buffer) < byte_count:
                            try:
                                playback_buffer.extend(playback_chunks.get_nowait())
                            except queue.Empty:
                                break
                        frame = bytes(playback_buffer[:byte_count])
                        del playback_buffer[:byte_count]
                    if len(frame) < byte_count:
                        # PortAudio expects a complete frame; pad underruns with silence.
                        frame += b"\0" * (byte_count - len(frame))
                    output_samples = np.frombuffer(frame, dtype=np.int16)
                    self._report_level("output", min(1.0, float(np.sqrt(np.mean(output_samples.astype(np.float32) ** 2))) / 12000) if output_samples.size else 0.0)
                    try:
                        reference_frames.put_nowait(frame)
                    except queue.Full:
                        try:
                            reference_frames.get_nowait()
                            reference_frames.put_nowait(frame)
                        except queue.Empty:
                            pass
                    return frame, pyaudio.paContinue

                def input_callback(in_data, _frame_count, _time_info, _status):
                    """Clean microphone audio and enqueue the newest frame for upload."""
                    try:
                        input_samples = np.frombuffer(in_data or b"", dtype=np.int16)
                        input_level = min(1.0, float(np.sqrt(np.mean(input_samples.astype(np.float32) ** 2))) / 12000) if input_samples.size else 0.0
                        self._report_level("input", 0.0 if self._microphone_muted.is_set() else input_level)
                        if self._microphone_muted.is_set():
                            return None, pyaudio.paContinue
                        try:
                            far_data = reference_frames.get_nowait()
                        except queue.Empty:
                            # No matching speaker frame is available; treat far-end audio as silence.
                            far_data = b"\0" * (FRAME_SAMPLES * 2)
                        near = self._resample_24k_to_16k(in_data)
                        far = self._resample_24k_to_16k(far_data)
                        cleaned = processor.process(near, far).tobytes()
                        if not self._microphone_muted.is_set():
                            # Keep the most recent microphone audio, avoiding stale
                            # speech being submitted after an interruption.
                            try:
                                microphone_frames.put_nowait(cleaned)
                            except queue.Full:
                                try:
                                    microphone_frames.get_nowait()
                                    microphone_frames.put_nowait(cleaned)
                                except queue.Empty:
                                    pass
                    except Exception:
                        # Never let an audio-device callback raise into PortAudio.
                        pass
                    return None, pyaudio.paContinue

                output_stream = audio.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=DEVICE_SAMPLE_RATE,
                    output=True,
                    frames_per_buffer=FRAME_SAMPLES,
                    stream_callback=output_callback,
                    start=False,
                )
                input_stream = audio.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=DEVICE_SAMPLE_RATE,
                    input=True,
                    input_device_index=input_info["index"],
                    frames_per_buffer=FRAME_SAMPLES,
                    stream_callback=input_callback,
                    start=False,
                )
                output_stream.start_stream()
                input_stream.start_stream()

                incoming_audio = asyncio.Queue()
                # Tag playback chunks so audio from an interrupted response can be discarded.
                playback_generation = 0
                discarding_interrupted_turn = False

                async def discard_pending_audio():
                    """Invalidate and empty every buffer containing the interrupted response."""
                    nonlocal playback_generation
                    playback_generation += 1
                    while not incoming_audio.empty():
                        try:
                            incoming_audio.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    while True:
                        try:
                            playback_chunks.get_nowait()
                        except queue.Empty:
                            break
                    with playback_lock:
                        playback_buffer.clear()
                    while True:
                        try:
                            reference_frames.get_nowait()
                        except queue.Empty:
                            break

                async def capture_microphone():
                    """Pair cleaned microphone frames and stream them to Gemini Live."""
                    pending_frame = None
                    muted_silence = b"\0" * (SEND_FRAME_SAMPLES * 2 * 2)
                    while True:
                        if self._microphone_muted.is_set():
                            pending_frame = None
                            # Keep the Live audio stream moving while muted. Sending
                            # silence lets server VAD finish any in-flight user turn
                            # and keeps the session from waiting on a missing frame.
                            await session.send_realtime_input(
                                audio=types.Blob(
                                    data=muted_silence,
                                    mime_type=f"audio/pcm;rate={SEND_SAMPLE_RATE}",
                                )
                            )
                            await asyncio.sleep(0.02)
                            continue

                        try:
                            frame = microphone_frames.get_nowait()
                        except queue.Empty:
                            await asyncio.sleep(0.005)
                            continue

                        if self._microphone_muted.is_set():
                            pending_frame = None
                            continue
                        if pending_frame is None:
                            pending_frame = frame
                            continue

                        audio_data = pending_frame + frame
                        pending_frame = None
                        if self._microphone_muted.is_set():
                            continue
                        await session.send_realtime_input(
                            audio=types.Blob(
                                data=audio_data,
                                mime_type=f"audio/pcm;rate={SEND_SAMPLE_RATE}",
                            )
                        )

                async def receive_responses():
                    """Handle tool calls, interruption signals, transcripts, and audio output."""
                    nonlocal discarding_interrupted_turn
                    while True:
                        async for response in session.receive():
                            if response.tool_call and response.tool_call.function_calls:
                                # Tools run off the event loop so a slow command does not block audio I/O.
                                function_responses = []
                                for function_call in response.tool_call.function_calls:
                                    result = await asyncio.to_thread(
                                        execute_tool,
                                        function_call.name,
                                        function_call.args or {},
                                        self.on_tool_event,
                                        self.confirm_tool,
                                    )
                                    function_responses.append(types.FunctionResponse(
                                        id=function_call.id,
                                        name=function_call.name,
                                        response=result,
                                    ))
                                await session.send_tool_response(
                                    function_responses=function_responses
                                )
                                continue
                            content = response.server_content
                            if content and content.interrupted:
                                # Flush queued speech as soon as Gemini reports an interruption.
                                await discard_pending_audio()
                                discarding_interrupted_turn = not content.turn_complete
                                continue
                            if discarding_interrupted_turn:
                                if content and content.turn_complete:
                                    discarding_interrupted_turn = False
                                continue
                            turn = content.model_turn if content else None
                            for part in turn.parts if turn else []:
                                if part.inline_data and part.inline_data.data:
                                    await incoming_audio.put(
                                        (playback_generation, part.inline_data.data)
                                    )
                            if response.text:
                                self.on_text(response.text)

                async def play_responses():
                    """Move current-generation audio chunks into the speaker callback queue."""
                    while True:
                        generation, chunk = await incoming_audio.get()
                        if generation == playback_generation:
                            try:
                                playback_chunks.put_nowait(chunk)
                            except queue.Full:
                                pass

                async with asyncio.TaskGroup() as group:
                    # These long-lived tasks run together until the session ends or is stopped.
                    group.create_task(capture_microphone())
                    group.create_task(receive_responses())
                    group.create_task(play_responses())
        finally:
            # Always stop and release native audio streams, including on errors or cancellation.
            for stream in (input_stream, output_stream):
                if stream is not None:
                    if stream.is_active():
                        stream.stop_stream()
                    stream.close()
            audio.terminate()
            self._microphone_frames = None
            self._loop = self._main_task = None
