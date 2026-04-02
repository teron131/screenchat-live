from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from mss import mss

DEFAULT_TARGET_REPO = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class RuntimeConfig:
    target_repo: Path
    workspace_root: Path
    workspace_display_path: str
    workspace_note_file: Path | None
    workspace_note_display_path: str | None
    screen_index: int | None
    screen_name: str | None
    screen_monitor: dict[str, int] | None
    reconnect_history_file: Path


def resolve_target_repo() -> Path:
    target_repo = sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_TARGET_REPO)
    target_path = Path(target_repo).expanduser().resolve()
    if not target_path.exists():
        raise FileNotFoundError(f"Target repository not found: {target_path}")
    if not target_path.is_dir():
        raise NotADirectoryError(f"Target repository must be a directory: {target_path}")
    return target_path


def build_workspace_root(target_repo: Path, workspace_subdir: str | None) -> tuple[Path, str]:
    if not workspace_subdir:
        return target_repo, "/"

    normalized_subdir = workspace_subdir.strip("/")
    workspace_root = (target_repo / normalized_subdir).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)
    return workspace_root, f"/{normalized_subdir}"


def build_workspace_note_file(workspace_root: Path) -> tuple[Path, str]:
    note_name = f"{datetime.now().astimezone().strftime('%Y-%m-%d_%H-%M-%S')}.md"
    note_file = workspace_root / note_name
    return note_file, f"/{note_name}"


def remove_empty_workspace_note_file(runtime_config: RuntimeConfig) -> None:
    note_file = runtime_config.workspace_note_file
    if not note_file or not note_file.exists():
        return

    try:
        if note_file.read_text(encoding="utf-8").strip():
            return
        note_file.unlink()
    except OSError:
        return


def get_available_monitors() -> list[dict[str, int]]:
    with mss() as screen_capture:
        return [dict(monitor) for monitor in screen_capture.monitors[1:]]


def parse_display_resolution(display_info: dict[str, object]) -> tuple[int, int] | None:
    resolution = display_info.get("_spdisplays_resolution")
    if not isinstance(resolution, str):
        return None

    match = re.search(r"(\d+)\s*x\s*(\d+)", resolution)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def get_display_names_by_resolution() -> dict[tuple[int, int], list[str]]:
    system_profiler = shutil.which("system_profiler")
    if not system_profiler:
        return {}

    completed = subprocess.run(  # noqa: S603 - system_profiler is resolved before execution.
        [system_profiler, "SPDisplaysDataType", "-json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        return {}

    try:
        display_payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {}

    display_names: dict[tuple[int, int], list[str]] = {}
    for gpu in display_payload.get("SPDisplaysDataType", []):
        if not isinstance(gpu, dict):
            continue
        for display in gpu.get("spdisplays_ndrvs", []):
            if not isinstance(display, dict):
                continue
            resolution = parse_display_resolution(display)
            display_name = display.get("_name")
            if not resolution or not isinstance(display_name, str):
                continue
            if display.get("spdisplays_main") == "spdisplays_yes":
                display_name = f"{display_name} (main)"
            display_names.setdefault(resolution, []).append(display_name)
    return display_names


def get_named_monitors() -> list[tuple[str, dict[str, int]]]:
    monitors = get_available_monitors()
    names_by_resolution = get_display_names_by_resolution()
    named_monitors: list[tuple[str, dict[str, int]]] = []
    for index, monitor in enumerate(monitors, start=1):
        resolution = (monitor["width"], monitor["height"])
        matching_names = names_by_resolution.get(resolution, [])
        monitor_name = matching_names.pop(0) if matching_names else f"Screen {index}"
        named_monitors.append((monitor_name, monitor))
    return named_monitors


def resolve_screen_monitor() -> tuple[int, str, dict[str, int]]:
    named_monitors = get_named_monitors()
    if not named_monitors:
        raise RuntimeError("No monitors available for screen sharing.")
    if len(named_monitors) == 1:
        monitor_name, monitor = named_monitors[0]
        return 1, monitor_name, monitor

    selected_screen = sys.argv[2] if len(sys.argv) > 2 else None
    if selected_screen is None:
        print("Available screens:")
        for index, (monitor_name, monitor) in enumerate(named_monitors, start=1):
            print(f"  {index}: {monitor_name} - {monitor['width']}x{monitor['height']} at ({monitor['left']}, {monitor['top']})")
        selected_screen = input("Screen index to share: ").strip()

    try:
        selected_index = int(selected_screen)
    except ValueError as exc:
        raise ValueError(f"Invalid screen index: {selected_screen!r}") from exc

    if selected_index < 1 or selected_index > len(named_monitors):
        raise ValueError(f"Screen index must be between 1 and {len(named_monitors)}.")
    monitor_name, monitor = named_monitors[selected_index - 1]
    return selected_index, monitor_name, monitor


def build_reconnect_history_file(target_repo: Path, screen_index: int | None) -> Path:
    repo_digest = hashlib.sha256(str(target_repo).encode("utf-8")).hexdigest()[:12]
    screen_suffix = "no-screen" if screen_index is None else str(screen_index)
    history_name = f"screenchat-live-{repo_digest}-{screen_suffix}.json"
    return Path(tempfile.gettempdir()) / history_name
