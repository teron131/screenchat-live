"""Desktop automation tools for supervised prompt-field typing."""

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess

from google.genai import types

from .live_config import LiveTool
from .runtime import RuntimeConfig

OSASCRIPT_TIMEOUT_SECONDS = 10
CLIPBOARD_TIMEOUT_SECONDS = 5
SCRIPT_DIR = Path(__file__).with_name("desktop_scripts")
TYPE_TEXT_MODES = {"insert", "replace"}
TARGET_SOURCE_COORDINATES = "coordinates"
TARGET_SOURCE_CURRENT_FOCUS = "current_focus"
WINDOW_ROW_COLUMNS = 7


@dataclass(frozen=True)
class DesktopTarget:
    app_name: str = ""
    window_title: str = ""
    window_index: int | None = None
    click_x: int | None = None
    click_y: int | None = None
    target_ready: bool = False

    @classmethod
    def from_args(cls, args: dict[str, object]) -> "DesktopTarget":
        return cls(
            app_name=_get_optional_string(args, "app_name"),
            window_title=_get_optional_string(args, "window_title"),
            window_index=_get_optional_int(args, "window_index"),
            click_x=_get_optional_int(args, "click_x"),
            click_y=_get_optional_int(args, "click_y"),
            target_ready=args.get("target_ready") is True,
        )

    def has_click_target(self) -> bool:
        return self.click_x is not None and self.click_y is not None

    def has_partial_click_target(self) -> bool:
        return (self.click_x is None) != (self.click_y is None)

    def is_confirmed(self) -> bool:
        return self.has_click_target() or self.target_ready

    def source(self) -> str:
        if self.has_click_target():
            return TARGET_SOURCE_COORDINATES
        return TARGET_SOURCE_CURRENT_FOCUS


def _error_result(command: str, error: str) -> dict[str, object]:
    return {"ok": False, "command": command, "error": error}


def _ok_result(command: str, **values: object) -> dict[str, object]:
    return {"ok": True, "command": command, **values}


def _script_path(script_name: str) -> Path:
    return SCRIPT_DIR / f"{script_name}.applescript"


def _run_osascript(command: str, script_name: str, *args: object) -> dict[str, object]:
    osascript = shutil.which("osascript")
    if not osascript:
        return _error_result(command, "`osascript` is not available on this system.")

    script_path = _script_path(script_name)
    if not script_path.exists():
        return _error_result(command, f"AppleScript file not found: {script_path}")

    completed = subprocess.run(  # noqa: S603 - executable is resolved above and args are passed without a shell.
        [osascript, str(script_path), *[str(arg) for arg in args]],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=OSASCRIPT_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        return _error_result(command, _format_osascript_error(completed.stderr))
    return _ok_result(command, stdout=completed.stdout.strip())


def _format_osascript_error(stderr: str) -> str:
    error = stderr.strip() or "AppleScript command failed."
    if "-25211" in error or "assistive access" in error.lower():
        return f"{error} Grant Accessibility permission to the terminal app running this agent, then try again."
    return error


def _set_clipboard_text(command: str, text: str) -> dict[str, object] | None:
    pbcopy = shutil.which("pbcopy")
    if not pbcopy:
        return _error_result(command, "`pbcopy` is not available on this system.")

    completed = subprocess.run(  # noqa: S603 - executable is resolved above.
        [pbcopy],
        input=text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=CLIPBOARD_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        return _error_result(command, completed.stderr.strip() or "Unable to set clipboard text.")
    return None


def _get_optional_string(args: dict[str, object], name: str) -> str:
    value = args.get(name)
    return value if isinstance(value, str) else ""


def _get_optional_int(args: dict[str, object], name: str) -> int | None:
    value = args.get(name)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _focus_window(app_name: str, window_title: str = "", window_index: int | None = None) -> dict[str, object]:
    if not app_name:
        return _error_result("desktop_focus_window", "Tool argument `app_name` must be a non-empty string.")

    result = _run_osascript("desktop_focus_window", "focus_window", app_name, window_title, window_index or "")
    if result["ok"]:
        return _ok_result("desktop_focus_window", app_name=app_name, window_title=window_title, window_index=window_index)
    return result


def _click_at(x: int, y: int) -> dict[str, object]:
    result = _run_osascript("desktop_click_at", "click_at", x, y)
    if result["ok"]:
        return _ok_result("desktop_click_at", x=x, y=y)
    return result


def _paste_clipboard(command: str, *, replace: bool) -> dict[str, object]:
    return _run_osascript(command, "replace_clipboard" if replace else "paste_clipboard")


def _parse_window_row(line: str) -> dict[str, object] | None:
    columns = line.split("\t")
    if len(columns) != WINDOW_ROW_COLUMNS:
        return None

    app_name, window_index, title, x, y, width, height = columns
    try:
        return {
            "app_name": app_name,
            "window_index": int(window_index),
            "title": title,
            "x": int(x),
            "y": int(y),
            "width": int(width),
            "height": int(height),
        }
    except ValueError:
        return None


def handle_desktop_list_windows_tool(_args: dict[str, object], _runtime_config: RuntimeConfig) -> dict[str, object]:
    result = _run_osascript("desktop_list_windows", "list_windows")
    if not result["ok"]:
        return result

    windows: list[dict[str, object]] = []
    stdout = result.get("stdout", "")
    if not isinstance(stdout, str):
        stdout = ""
    for line in stdout.splitlines():
        window = _parse_window_row(line)
        if window:
            windows.append(window)
    return _ok_result("desktop_list_windows", windows=windows)


def _prepare_text_target(command: str, target: DesktopTarget) -> dict[str, object] | None:
    if target.app_name:
        focus_result = _focus_window(
            target.app_name,
            target.window_title,
            target.window_index,
        )
        if not focus_result["ok"]:
            return _error_result(command, f"Window focus failed: {focus_result.get('error', 'unknown error')}")

    if target.click_x is None and target.click_y is None:
        return None
    if target.has_partial_click_target():
        return _error_result(command, "Both `click_x` and `click_y` must be provided together.")

    click_result = _click_at(target.click_x, target.click_y)
    if not click_result["ok"]:
        return _error_result(command, f"Click failed: {click_result.get('error', 'unknown error')}")
    return None


def handle_desktop_type_text_tool(args: dict[str, object], _runtime_config: RuntimeConfig) -> dict[str, object]:
    command = "desktop_type_text"
    text = args.get("text")
    mode = args.get("mode")
    target = DesktopTarget.from_args(args)
    if not isinstance(text, str):
        return _error_result(command, "Tool argument `text` must be a string.")
    if mode not in TYPE_TEXT_MODES:
        return _error_result(command, "Tool argument `mode` must be either `insert` or `replace`.")
    if not target.is_confirmed():
        return _error_result(
            command,
            "Refusing to type without a confirmed target. Provide `click_x` and `click_y`, or set `target_ready` only after the user confirms the focused field is correct.",
        )

    target_error = _prepare_text_target(command, target)
    if target_error:
        return target_error

    clipboard_error = _set_clipboard_text(command, text)
    if clipboard_error:
        return clipboard_error

    replace = mode == "replace"
    paste_result = _paste_clipboard(command, replace=replace)
    if not paste_result["ok"]:
        return paste_result

    values: dict[str, object] = {
        "characters": len(text),
        "mode": mode,
        "target_source": target.source(),
    }
    if values["target_source"] == TARGET_SOURCE_CURRENT_FOCUS:
        values["note"] = "Paste was sent to the currently focused field; if text did not appear, click the target field or provide click coordinates."
    return _ok_result(command, **values)


def _target_properties_schema() -> dict[str, object]:
    return {
        "app_name": {
            "type": "string",
            "description": "Optional macOS application name to activate before typing, such as `Code`, `TextEdit`, or `Google Chrome`.",
        },
        "window_title": {
            "type": "string",
            "description": "Optional exact window title to raise after activating app_name.",
        },
        "window_index": {
            "type": "integer",
            "description": "Optional one-based window index from desktop_list_windows.",
        },
        "click_x": {
            "type": "integer",
            "description": "Optional absolute screen x coordinate to click before typing.",
        },
        "click_y": {
            "type": "integer",
            "description": "Optional absolute screen y coordinate to click before typing.",
        },
        "target_ready": {
            "type": "boolean",
            "description": "Set true only when the prompt field is already focused, or the user explicitly asks to type into the current focus. Prefer click_x/click_y when the field is visible.",
        },
    }


def create_desktop_list_windows_tool() -> LiveTool:
    return LiveTool(
        declaration=types.FunctionDeclaration(
            name="desktop_list_windows",
            description="List visible macOS application windows with app names, one-based window indexes, titles, positions, and sizes.",
            parametersJsonSchema={"type": "object", "properties": {}},
        ),
        handler=handle_desktop_list_windows_tool,
    )


def create_desktop_type_text_tool() -> LiveTool:
    return LiveTool(
        declaration=types.FunctionDeclaration(
            name="desktop_type_text",
            description="Insert or replace text in a confirmed target field. Optionally activates a window and clicks coordinates before pasting. Requires click_x/click_y or target_ready=true.",
            parametersJsonSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to insert."},
                    "mode": {
                        "type": "string",
                        "enum": sorted(TYPE_TEXT_MODES),
                        "description": "`insert` pastes at the cursor. `replace` presses Command+A before pasting.",
                    },
                    **_target_properties_schema(),
                },
                "required": ["text", "mode"],
            },
        ),
        handler=handle_desktop_type_text_tool,
    )


def create_desktop_tools() -> tuple[LiveTool, ...]:
    return (
        create_desktop_list_windows_tool(),
        create_desktop_type_text_tool(),
    )
