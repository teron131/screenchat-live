from screenchat_live.app import run_profile_cli
from screenchat_live.live_config import (
    AudioConfig,
    ScreenShareConfig,
    build_profile_session_options,
)
from screenchat_live.repo_inspection import create_repo_inspection_tool

PROFILE_SYSTEM_PROMPT_TEMPLATE = """You are a code review and repository Q&A assistant for the project at {target_repo}.
Guide the user through code review findings, explain code behavior clearly, and answer repository questions from the repo evidence.
Stay read-only: inspect files and git state, but do not suggest or attempt edits through tools.
When you need repository context, use the inspect_repo tool instead of guessing.
Prefer concise read-only commands such as pwd, ls, rg, cat, sed, head, tail, wc, find, and git status/diff/log/show.
"""

SESSION_OPTIONS = build_profile_session_options(
    profile_system_prompt_template=PROFILE_SYSTEM_PROMPT_TEMPLATE,
    audio=AudioConfig(enabled=True),
    screen_share=ScreenShareConfig(enabled=True),
    tools=(create_repo_inspection_tool(),),
)


if __name__ == "__main__":
    run_profile_cli(SESSION_OPTIONS)
