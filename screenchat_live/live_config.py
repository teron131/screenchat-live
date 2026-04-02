from collections.abc import Callable
from dataclasses import dataclass, field

from google.genai import types

from .runtime import RuntimeConfig

ToolHandler = Callable[[dict[str, object], RuntimeConfig], dict[str, object]]

DEFAULT_SYSTEM_PROMPT_TEMPLATE = """Answer using the available repository context and the current conversation state.
Do not ask follow-up questions.
If a new voice input seems completely irrelevant midway through the session, treat it as background noise and continue without reacting to it.
Prefer continuing the current thread of work instead of restarting from scratch after stray audio.
Keep moving with the most reasonable interpretation instead of stalling for clarification unless the request is genuinely ambiguous or unsafe.
Be concise, direct, and grounded in the available repository context.
Do not end with questions or suggested next steps unless they are genuinely necessary."""

DEFAULT_RECONNECT_CONTEXT_INSTRUCTION = """The previous live session disconnected.
Resume naturally from this recent transcript context, keep continuity, and avoid repeating setup chatter."""
DEFAULT_MODEL = "gemini-3.1-flash-live-preview"
DEFAULT_VOICE_NAME = "Charon"


@dataclass(frozen=True)
class TranscriptConfig:
    default_system_prompt_template: str = DEFAULT_SYSTEM_PROMPT_TEMPLATE
    profile_system_prompt_template: str = ""
    reconnect_context_instruction: str = DEFAULT_RECONNECT_CONTEXT_INSTRUCTION
    user_label: str = "User"


@dataclass(frozen=True)
class AudioConfig:
    enabled: bool = True
    response_modalities: tuple[str, ...] = ("AUDIO",)
    send_sample_rate: int = 16000
    receive_sample_rate: int = 24000
    chunk_size: int = 1024
    input_mime_type: str = "audio/pcm;rate=16000"
    model_speaking_hangover_seconds: float = 1.0
    enable_input_transcription: bool = True
    enable_output_transcription: bool = True


@dataclass(frozen=True)
class ScreenShareConfig:
    enabled: bool = True
    fps: float = 1.0
    jpeg_quality: int = 70
    mime_type: str = "image/jpeg"


@dataclass(frozen=True)
class LiveTool:
    declaration: types.FunctionDeclaration
    handler: ToolHandler


@dataclass(frozen=True)
class LiveSessionOptions:
    model: str
    voice_name: str | None = None
    workspace_subdir: str | None = None
    media_resolution: str = "MEDIA_RESOLUTION_MEDIUM"
    thinking_level: types.ThinkingLevel = types.ThinkingLevel.MEDIUM
    context_window_trigger_tokens: int = 104857
    context_window_target_tokens: int = 52428
    transcript: TranscriptConfig = field(default_factory=TranscriptConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    screen_share: ScreenShareConfig = field(default_factory=ScreenShareConfig)
    tools: tuple[LiveTool, ...] = ()

    def tool_declarations(self) -> list[types.FunctionDeclaration]:
        return [tool.declaration for tool in self.tools]

    def get_tool_handler(self, name: str) -> ToolHandler | None:
        for tool in self.tools:
            if tool.declaration.name == name:
                return tool.handler
        return None


def build_profile_session_options(
    *,
    profile_system_prompt_template: str,
    tools: tuple[LiveTool, ...] = (),
    model: str = DEFAULT_MODEL,
    voice_name: str | None = DEFAULT_VOICE_NAME,
    workspace_subdir: str | None = None,
    user_label: str = "User",
    audio: AudioConfig | None = None,
    screen_share: ScreenShareConfig | None = None,
) -> LiveSessionOptions:
    return LiveSessionOptions(
        model=model,
        voice_name=voice_name,
        workspace_subdir=workspace_subdir,
        transcript=TranscriptConfig(
            profile_system_prompt_template=profile_system_prompt_template,
            user_label=user_label,
        ),
        audio=audio or AudioConfig(),
        screen_share=screen_share or ScreenShareConfig(),
        tools=tools,
    )
