import asyncio
from dataclasses import dataclass
import os

from dotenv import load_dotenv
from google import genai
import pyaudio

from .console import console_output
from .live_config import LiveSessionOptions
from .runtime import (
    RuntimeConfig,
    build_reconnect_history_file,
    build_workspace_note_file,
    build_workspace_root,
    remove_empty_workspace_note_file,
    resolve_screen_monitor,
    resolve_target_repo,
)
from .session_runner import LiveSessionRunner
from .transcript import TranscriptManager


@dataclass(frozen=True)
class ProfileRuntime:
    runtime_config: RuntimeConfig
    client: genai.Client
    pya: pyaudio.PyAudio | None
    transcript_manager: TranscriptManager


def build_runtime_config(session_options: LiveSessionOptions) -> RuntimeConfig:
    target_repo = resolve_target_repo()
    workspace_root, workspace_display_path = build_workspace_root(
        target_repo,
        session_options.workspace_subdir,
    )
    if session_options.workspace_subdir:
        workspace_note_file, workspace_note_display_path = build_workspace_note_file(workspace_root)
        workspace_note_file.touch(exist_ok=True)
    else:
        workspace_note_file, workspace_note_display_path = None, None
    if session_options.screen_share.enabled:
        screen_index, screen_name, screen_monitor = resolve_screen_monitor()
    else:
        screen_index, screen_name, screen_monitor = None, None, None
    return RuntimeConfig(
        target_repo=target_repo,
        workspace_root=workspace_root,
        workspace_display_path=workspace_display_path,
        workspace_note_file=workspace_note_file,
        workspace_note_display_path=workspace_note_display_path,
        screen_index=screen_index,
        screen_name=screen_name,
        screen_monitor=screen_monitor,
        reconnect_history_file=build_reconnect_history_file(
            target_repo,
            screen_index,
        ),
    )


def build_profile_runtime(session_options: LiveSessionOptions) -> ProfileRuntime:
    runtime_config = build_runtime_config(session_options)
    load_dotenv()
    client = genai.Client(
        http_options={"api_version": "v1beta"},
        api_key=os.environ.get("GEMINI_API_KEY"),
    )
    pya = pyaudio.PyAudio() if session_options.audio.enabled else None
    transcript_manager = TranscriptManager(
        runtime_config=runtime_config,
        config=session_options.transcript,
    )
    return ProfileRuntime(
        runtime_config=runtime_config,
        client=client,
        pya=pya,
        transcript_manager=transcript_manager,
    )


async def run_profile(session_options: LiveSessionOptions, runtime: ProfileRuntime) -> None:
    runtime.transcript_manager.initialize_history()
    await LiveSessionRunner(
        runtime.client,
        runtime.pya,
        runtime.runtime_config,
        runtime.transcript_manager,
        session_options,
    ).run_forever()


def run_profile_cli(session_options: LiveSessionOptions) -> None:
    runtime = build_profile_runtime(session_options)
    try:
        asyncio.run(run_profile(session_options, runtime))
    except KeyboardInterrupt:
        runtime.transcript_manager.clear_live()
        print(console_output.interrupted_by_user())
    finally:
        remove_empty_workspace_note_file(runtime.runtime_config)
