import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
import time

from google.genai import types
from google.genai.live import AsyncSession

from .audio import (
    audio_queue_mic,
    audio_queue_output,
    drain_queue,
    live_session_state,
)
from .console import console_output
from .live_config import LiveSessionOptions
from .runtime import RuntimeConfig
from .transcript import TranscriptManager


class SessionReconnectRequested(RuntimeError):
    """Signal that the live session should reconnect."""


@dataclass
class LiveMessageProcessor:
    session: AsyncSession
    runtime_config: RuntimeConfig
    transcript_manager: TranscriptManager
    session_options: LiveSessionOptions
    reconnect_after_turn: bool = False

    async def run(self) -> None:
        """Receives responses from GenAI and routes them into transcript/audio state."""
        try:
            while True:
                turn: AsyncIterator[types.LiveServerMessage] = self.session.receive()
                async for response in turn:
                    await self.handle_response(response)
        except Exception as exc:
            if isinstance(exc, SessionReconnectRequested):
                raise
            raise SessionReconnectRequested(f"Receive loop disconnected: {exc}") from exc

    async def handle_response(self, response: types.LiveServerMessage) -> None:
        if response.go_away:
            time_left = response.go_away.time_left or "unknown"
            print(console_output.reconnect_notice(time_left))
            self.reconnect_after_turn = True

        if response.tool_call:
            await self.handle_tool_call(response.tool_call)

        server_content = response.server_content
        if not server_content:
            if self.reconnect_after_turn:
                raise SessionReconnectRequested("Server requested reconnect.")
            return

        self.process_server_content(server_content)
        if self.reconnect_after_turn and server_content.turn_complete:
            raise SessionReconnectRequested("Resuming after completed turn.")

    async def handle_tool_call(self, tool_call: types.LiveServerToolCall) -> None:
        responses: list[types.FunctionResponse] = []
        for function_call in tool_call.function_calls or []:
            tool_handler = self.session_options.get_tool_handler(function_call.name)
            if not tool_handler:
                responses.append(
                    types.FunctionResponse(
                        id=function_call.id,
                        name=function_call.name,
                        response={"ok": False, "error": f"Unsupported tool `{function_call.name}`."},
                    )
                )
                continue
            result = await asyncio.to_thread(tool_handler, function_call.args or {}, self.runtime_config)
            self.transcript_manager.commit_live()
            print(
                console_output.tool_result(
                    str(result.get("command", function_call.name)),
                    bool(result.get("ok")),
                    result.get("error") if isinstance(result.get("error"), str) else None,
                )
            )
            responses.append(
                types.FunctionResponse(
                    id=function_call.id,
                    name=function_call.name,
                    response=result,
                )
            )
        if responses:
            try:
                await self.session.send_tool_response(function_responses=responses)
            except Exception as exc:
                raise SessionReconnectRequested(f"Tool response failed: {exc}") from exc

    def apply_input_transcription(self, transcription: types.Transcription) -> None:
        self.transcript_manager.pending_input = self.transcript_manager.apply_update(
            self.transcript_manager.user_label,
            self.transcript_manager.pending_input,
            transcription,
        )
        if transcription.finished and self.transcript_manager.pending_input:
            self.transcript_manager.pending_input = self.transcript_manager.finalize(
                self.transcript_manager.user_label,
                self.transcript_manager.pending_input,
            )

    def apply_output_transcription(self, transcription: types.Transcription) -> None:
        self.transcript_manager.pending_output = self.transcript_manager.apply_update(
            self.transcript_manager.assistant_label,
            self.transcript_manager.pending_output,
            transcription,
        )

    def apply_model_turn(self, model_turn: types.Content) -> None:
        text_parts: list[str] = []
        for part in model_turn.parts:
            if part.text:
                text_parts.append(part.text.strip())
            if part.inline_data and isinstance(part.inline_data.data, bytes):
                live_session_state.model_speaking_until = time.monotonic() + self.session_options.audio.model_speaking_hangover_seconds
                drain_queue(audio_queue_mic)
                audio_queue_output.put_nowait(part.inline_data.data)
        if text_parts:
            self.transcript_manager.pending_output = self.transcript_manager.merge_text(
                self.transcript_manager.pending_output,
                " ".join(text_parts),
            )
            self.transcript_manager.print(
                self.transcript_manager.assistant_label,
                self.transcript_manager.pending_output,
                final=False,
            )

    def handle_interrupted_output(self) -> None:
        self.transcript_manager.clear_live()
        self.transcript_manager.pending_output = ""
        drain_queue(audio_queue_output)

    def finalize_turn_transcripts(self) -> None:
        self.transcript_manager.pending_input = self.transcript_manager.finalize(
            self.transcript_manager.user_label,
            self.transcript_manager.pending_input,
        )
        self.transcript_manager.pending_output = self.transcript_manager.finalize(
            self.transcript_manager.assistant_label,
            self.transcript_manager.pending_output,
        )

    def process_server_content(self, server_content: types.LiveServerContent) -> None:
        input_transcription = server_content.input_transcription
        output_transcription = server_content.output_transcription

        if input_transcription:
            self.apply_input_transcription(input_transcription)

        if output_transcription:
            self.apply_output_transcription(output_transcription)

        if server_content.model_turn:
            self.apply_model_turn(server_content.model_turn)

        if server_content.interrupted:
            self.handle_interrupted_output()

        if server_content.turn_complete:
            self.finalize_turn_transcripts()
        elif output_transcription and output_transcription.finished and self.transcript_manager.pending_output:
            self.transcript_manager.pending_output = self.transcript_manager.finalize(
                self.transcript_manager.assistant_label,
                self.transcript_manager.pending_output,
            )


async def receive_audio(
    session: AsyncSession,
    runtime_config: RuntimeConfig,
    transcript_manager: TranscriptManager,
    session_options: LiveSessionOptions,
) -> None:
    await LiveMessageProcessor(session, runtime_config, transcript_manager, session_options).run()


async def send_realtime(session: AsyncSession) -> None:
    """Sends audio from the mic audio queue to the GenAI session."""
    while True:
        msg = await audio_queue_mic.get()
        try:
            await session.send_realtime_input(audio=msg)
        except Exception as exc:
            raise SessionReconnectRequested(f"Audio stream disconnected: {exc}") from exc
