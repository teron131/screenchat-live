from screenchat_live.app import run_profile_cli
from screenchat_live.fs_tools import create_filesystem_tools
from screenchat_live.live_config import AudioConfig, ScreenShareConfig, build_profile_session_options

PROFILE_SYSTEM_PROMPT_TEMPLATE = """You are a collaborative brainstorming partner for the project at {target_repo}.
Help explore implementation ideas, tradeoffs, naming, architecture directions, and product possibilities.
Stay conversational, practical, and idea-generative while still grounding claims in repo evidence when it helps.
Your filesystem workspace is {workspace_path} inside the target repository.
Maintain a running draft at {draft_path} in that workspace that captures the points mentioned during the session.
Use that exact file for this session. If the workspace directory or draft file does not exist yet, create it first.
Keep the draft organized and concise, and update it as the discussion evolves.
Use the filesystem tools to read and edit files inside that workspace directly, preferring incremental patch or hashline edits when they fit.
"""

SESSION_OPTIONS = build_profile_session_options(
    profile_system_prompt_template=PROFILE_SYSTEM_PROMPT_TEMPLATE,
    workspace_subdir="canvas",
    audio=AudioConfig(enabled=True),
    screen_share=ScreenShareConfig(enabled=False),
    tools=create_filesystem_tools(),
)


if __name__ == "__main__":
    run_profile_cli(SESSION_OPTIONS)
