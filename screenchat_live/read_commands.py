from collections.abc import Callable
from pathlib import Path
import shlex
import shutil
import subprocess

from google.genai import types

from .live_config import LiveTool
from .runtime import RuntimeConfig

SAFE_COMMANDS = {"pwd", "ls", "rg", "cat", "sed", "head", "tail", "wc", "find", "git"}
SAFE_GIT_SUBCOMMANDS = {"status", "diff", "log", "show"}
DISALLOWED_SHELL_TOKENS = {"|", "||", "&", "&&", ";", ">", ">>", "<", "<<", "`"}

ArgValidator = Callable[[list[str]], str | None]


def is_safe_path_arg(token: str) -> bool:
    path = Path(token)
    return not path.is_absolute() and ".." not in path.parts


def validate_path_arg(token: str) -> str | None:
    if not is_safe_path_arg(token):
        return f"Path `{token}` escapes the repository tree."
    return None


def validate_plain_path_args(tokens: list[str]) -> str | None:
    for token in tokens:
        if token.startswith("-"):
            continue
        path_error = validate_path_arg(token)
        if path_error:
            return path_error
    return None


def validate_sed_args(tokens: list[str]) -> str | None:
    for token in tokens:
        if token.startswith("-"):
            continue
        if token.replace(",", "").replace("p", "").replace("d", "").isdigit():
            continue
        path_error = validate_path_arg(token)
        if path_error:
            return path_error
    return None


def validate_find_args(tokens: list[str]) -> str | None:
    for token in tokens:
        if token.startswith("-") or token in {"(", ")", "!", "-o", "-a"}:
            break
        path_error = validate_path_arg(token)
        if path_error:
            return path_error
    return None


def validate_rg_args(tokens: list[str]) -> str | None:
    saw_pattern = False
    for token in tokens:
        if token.startswith("-") and not saw_pattern:
            continue
        if not saw_pattern:
            saw_pattern = True
            continue
        path_error = validate_path_arg(token)
        if path_error:
            return path_error
    return None


ARG_VALIDATORS: dict[str, ArgValidator] = {
    "cat": validate_plain_path_args,
    "head": validate_plain_path_args,
    "tail": validate_plain_path_args,
    "wc": validate_plain_path_args,
    "ls": validate_plain_path_args,
    "sed": validate_sed_args,
    "find": validate_find_args,
    "rg": validate_rg_args,
}


def validate_repo_scoped_args(argv: list[str]) -> str | None:
    executable = argv[0]
    if executable == "git":
        return None

    validator = ARG_VALIDATORS.get(executable)
    if not validator:
        return None
    return validator(argv[1:])


def blocked_command(command: str, error: str) -> dict[str, object]:
    return {"ok": False, "command": command, "error": error}


def run_bash_locally(command: str, target_repo: Path) -> dict[str, object]:
    if any(token in command for token in DISALLOWED_SHELL_TOKENS) or "$(" in command:
        return blocked_command(command, "Command contains disallowed shell syntax.")

    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return blocked_command(command, f"Unable to parse command: {exc}")

    if not argv:
        return blocked_command(command, "Command must not be empty.")

    executable = argv[0]
    if executable not in SAFE_COMMANDS:
        return blocked_command(command, f"Command `{executable}` is not allowlisted.")
    resolved_executable = shutil.which(executable)
    if not resolved_executable:
        return blocked_command(command, f"Command `{executable}` is not available.")
    if executable == "git" and (len(argv) < 2 or argv[1] not in SAFE_GIT_SUBCOMMANDS):
        return blocked_command(command, "Only git status, diff, log, and show are allowed.")
    path_error = validate_repo_scoped_args(argv)
    if path_error:
        return blocked_command(command, path_error)

    completed = subprocess.run(  # noqa: S603 - command is tokenized and allowlisted above.
        [resolved_executable, *argv[1:]],
        cwd=target_repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    return {
        "ok": completed.returncode == 0,
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-12000:],
        "stderr": completed.stderr[-4000:],
    }


def handle_run_bash_tool(args: dict[str, object], runtime_config: RuntimeConfig) -> dict[str, object]:
    command = args.get("command", "")
    if not isinstance(command, str):
        return blocked_command("", "Tool argument `command` must be a string.")
    return run_bash_locally(command, runtime_config.target_repo)


def create_run_bash_tool() -> LiveTool:
    return LiveTool(
        declaration=types.FunctionDeclaration(
            name="run_bash",
            description="Run a short read-only shell command in the repository root to inspect files or git state.",
            parametersJsonSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Single read-only shell command such as `pwd`, `rg pattern path`, `ls`, `cat file`, or `git status`.",
                    }
                },
                "required": ["command"],
            },
        ),
        handler=handle_run_bash_tool,
    )
