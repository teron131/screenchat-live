import asyncio
from contextlib import suppress
from dataclasses import dataclass

from google import genai
from google.genai import types
import pyaudio

from .audio import (
    listen_audio,
    open_mic_stream,
    open_speaker_stream,
    play_audio,
    reset_live_state,
    stop_background_task,
)
from .console import console_output
from .live_config import LiveSessionOptions
from .runtime import RuntimeConfig
from .screen import share_screen
from .session import SessionReconnectRequested, receive_audio, send_realtime
from .transcript import TranscriptManager

RECONNECT_DELAY_SECONDS = 1.0


def build_live_config(
    reconnect_count: int,
    transcript_manager: TranscriptManager,
    session_options: LiveSessionOptions,
) -> types.LiveConnectConfig:
    connect_config: dict[str, object] = {
        "response_modalities": list(session_options.audio.response_modalities),
        "media_resolution": session_options.media_resolution,
        "thinking_config": types.ThinkingConfig(
            thinking_level=session_options.thinking_level,
        ),
        "context_window_compression": types.ContextWindowCompressionConfig(
            trigger_tokens=session_options.context_window_trigger_tokens,
            sliding_window=types.SlidingWindow(
                target_tokens=session_options.context_window_target_tokens,
            ),
        ),
        "system_instruction": transcript_manager.build_system_prompt(reconnect_count),
    }
    if session_options.voice_name:
        connect_config["speech_config"] = types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=session_options.voice_name,
                ),
            )
        )
    if session_options.audio.enable_input_transcription:
        connect_config["input_audio_transcription"] = {}
    if session_options.audio.enable_output_transcription:
        connect_config["output_audio_transcription"] = {}
    tool_declarations = session_options.tool_declarations()
    if tool_declarations:
        connect_config["tools"] = [types.Tool(functionDeclarations=tool_declarations)]
    return types.LiveConnectConfig(**connect_config)


@dataclass
class LiveSessionRunner:
    client: genai.Client
    pya: pyaudio.PyAudio | None
    runtime_config: RuntimeConfig
    transcript_manager: TranscriptManager
    session_options: LiveSessionOptions
    reconnect_count: int = 0

    async def run_once(self) -> None:
        async with self.client.aio.live.connect(
            model=self.session_options.model,
            config=build_live_config(self.reconnect_count, self.transcript_manager, self.session_options),
        ) as live_session:
            self.transcript_manager.clear_live()
            print(
                console_output.connection_banner(
                    self.runtime_config,
                    reconnect_count=self.reconnect_count,
                )
            )
            async with asyncio.TaskGroup() as tg:
                if self.session_options.audio.enabled:
                    tg.create_task(send_realtime(live_session))
                if self.session_options.screen_share.enabled and self.runtime_config.screen_monitor:
                    tg.create_task(
                        share_screen(
                            live_session,
                            self.runtime_config.screen_monitor,
                            self.session_options.screen_share,
                            SessionReconnectRequested,
                        )
                    )
                tg.create_task(receive_audio(live_session, self.runtime_config, self.transcript_manager, self.session_options))

    async def run_forever(self) -> None:
        mic_stream = None
        speaker_stream = None
        listen_task = None
        play_task = None
        if self.session_options.audio.enabled:
            if self.pya is None:
                raise RuntimeError("Audio is enabled but no PyAudio instance is available.")
            mic_stream = await asyncio.to_thread(
                open_mic_stream,
                self.pya,
                self.session_options.audio,
            )
            speaker_stream = await asyncio.to_thread(
                open_speaker_stream,
                self.pya,
                self.session_options.audio,
            )
            listen_task = asyncio.create_task(
                listen_audio(
                    mic_stream,
                    self.session_options.audio,
                )
            )
            play_task = asyncio.create_task(
                play_audio(speaker_stream),
            )
        try:
            while True:
                reconnect_requested = False
                try:
                    await self.run_once()
                except* SessionReconnectRequested:
                    reconnect_requested = True

                if not reconnect_requested:
                    break

                self.reconnect_count += 1
                reset_live_state()
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)
        except asyncio.CancelledError:
            pass
        finally:
            self.transcript_manager.clear_live()
            if listen_task:
                await stop_background_task(listen_task)
            if play_task:
                await stop_background_task(play_task)
            if mic_stream:
                mic_stream.close()
            if speaker_stream:
                speaker_stream.close()
            if self.pya:
                self.pya.terminate()
            with suppress(OSError):
                self.runtime_config.reconnect_history_file.unlink()
            print(console_output.connection_closed())
