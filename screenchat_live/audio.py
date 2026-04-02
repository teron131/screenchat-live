import asyncio
from contextlib import suppress
from dataclasses import dataclass
import time

import pyaudio

from .live_config import AudioConfig

FORMAT = pyaudio.paInt16
CHANNELS = 1


@dataclass
class LiveSessionState:
    model_speaking_until: float = 0.0


audio_queue_output: asyncio.Queue[bytes] = asyncio.Queue()
audio_queue_mic: asyncio.Queue[dict[str, bytes | str]] = asyncio.Queue(maxsize=5)
live_session_state = LiveSessionState()


def drain_queue(queue: asyncio.Queue[object]) -> None:
    while not queue.empty():
        queue.get_nowait()


def reset_live_state() -> None:
    live_session_state.model_speaking_until = 0.0
    drain_queue(audio_queue_output)
    drain_queue(audio_queue_mic)


async def listen_audio(mic_stream: pyaudio.Stream, audio_config: AudioConfig) -> None:
    """Listens for audio and puts it into the mic audio queue."""
    kwargs = {"exception_on_overflow": False} if __debug__ else {}
    while True:
        data = await asyncio.to_thread(
            mic_stream.read,
            audio_config.chunk_size,
            **kwargs,
        )
        if time.monotonic() < live_session_state.model_speaking_until:
            continue
        await audio_queue_mic.put(
            {
                "data": data,
                "mime_type": audio_config.input_mime_type,
            }
        )


async def play_audio(speaker_stream: pyaudio.Stream) -> None:
    """Plays audio from the speaker audio queue."""
    while True:
        bytestream = await audio_queue_output.get()
        await asyncio.to_thread(speaker_stream.write, bytestream)


def open_mic_stream(pyaudio_instance: pyaudio.PyAudio, audio_config: AudioConfig) -> pyaudio.Stream:
    mic_info = pyaudio_instance.get_default_input_device_info()
    return pyaudio_instance.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=audio_config.send_sample_rate,
        input=True,
        input_device_index=mic_info["index"],
        frames_per_buffer=audio_config.chunk_size,
    )


def open_speaker_stream(pyaudio_instance: pyaudio.PyAudio, audio_config: AudioConfig) -> pyaudio.Stream:
    return pyaudio_instance.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=audio_config.receive_sample_rate,
        output=True,
    )


async def stop_background_task(task: asyncio.Task[None]) -> None:
    with suppress(asyncio.CancelledError):
        task.cancel()
        await task
