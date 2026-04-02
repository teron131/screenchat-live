from contextlib import suppress
from dataclasses import dataclass, field
import json
import sys

from google.genai import types

from .live_config import TranscriptConfig
from .runtime import RuntimeConfig

RECONNECT_HISTORY_MAX_MESSAGES = 6
RECONNECT_HISTORY_MAX_CHARS = 4000
ASSISTANT_LABEL = "Assistant"


@dataclass
class TranscriptManager:
    runtime_config: RuntimeConfig
    config: TranscriptConfig = field(default_factory=TranscriptConfig)
    active_label: str = ""
    active_text: str = ""
    pending_input: str = ""
    pending_output: str = ""
    last_committed: dict[str, str] = field(default_factory=dict)
    recent_turns: list[dict[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.last_committed.setdefault(self.user_label, "")
        self.last_committed.setdefault(self.assistant_label, "")

    @property
    def user_label(self) -> str:
        return self.config.user_label

    @property
    def assistant_label(self) -> str:
        return ASSISTANT_LABEL

    @staticmethod
    def normalize_text(text: str) -> str:
        return " ".join(text.split())

    @staticmethod
    def merge_text(current: str, update: str) -> str:
        clean_update = TranscriptManager.normalize_text(update)
        if not clean_update:
            return current
        if not current:
            return clean_update
        if clean_update in current:
            return current
        if current in clean_update or clean_update.startswith(current):
            return clean_update
        if current.endswith(clean_update):
            return current

        max_overlap = min(len(current), len(clean_update))
        for overlap in range(max_overlap, 0, -1):
            if current.endswith(clean_update[:overlap]):
                return f"{current}{clean_update[overlap:]}"

        separator = "" if current.endswith((" ", "\n")) or clean_update.startswith((" ", "\n")) else " "
        return f"{current}{separator}{clean_update}"

    def trim_history(self, recent_turns: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
        trimmed_turns = list(self.recent_turns if recent_turns is None else recent_turns)[-RECONNECT_HISTORY_MAX_MESSAGES:]
        while trimmed_turns and sum(len(turn["text"]) for turn in trimmed_turns) > RECONNECT_HISTORY_MAX_CHARS:
            trimmed_turns.pop(0)
        return trimmed_turns

    def save_history(self) -> None:
        payload = {
            "target_repo": str(self.runtime_config.target_repo),
            "screen_index": self.runtime_config.screen_index,
            "recent_turns": self.trim_history(),
        }
        self.runtime_config.reconnect_history_file.write_text(json.dumps(payload), encoding="utf-8")

    def initialize_history(self) -> None:
        self.recent_turns = []
        with suppress(OSError):
            self.save_history()

    def load_history(self) -> list[dict[str, str]]:
        history_file = self.runtime_config.reconnect_history_file
        if not history_file.exists():
            return []

        try:
            payload = json.loads(history_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

        if payload.get("target_repo") != str(self.runtime_config.target_repo) or payload.get("screen_index") != self.runtime_config.screen_index:
            return []

        recent_turns = payload.get("recent_turns", [])
        if not isinstance(recent_turns, list):
            return []
        valid_turns = [turn for turn in recent_turns if isinstance(turn, dict) and isinstance(turn.get("label"), str) and isinstance(turn.get("text"), str)]
        return self.trim_history(valid_turns)

    def build_system_prompt(self, reconnect_count: int) -> str:
        prompt_sections = [
            self.config.default_system_prompt_template.format(
                target_repo=self.runtime_config.target_repo,
                workspace_path=self.runtime_config.workspace_display_path,
                draft_path=self.runtime_config.workspace_note_display_path or "",
            ).strip()
        ]
        profile_prompt = self.config.profile_system_prompt_template.format(
            target_repo=self.runtime_config.target_repo,
            workspace_path=self.runtime_config.workspace_display_path,
            draft_path=self.runtime_config.workspace_note_display_path or "",
        ).strip()
        if profile_prompt:
            prompt_sections.append(profile_prompt)
        system_prompt = "\n\n".join(section for section in prompt_sections if section)
        if reconnect_count <= 0:
            return system_prompt

        recent_turns = self.load_history()
        if not recent_turns:
            return system_prompt

        history_lines = "\n".join(f"[{turn['label']}] {turn['text']}" for turn in recent_turns)
        return f"{system_prompt}\n\n{self.config.reconnect_context_instruction}\n{history_lines}"

    def clear_live(self) -> None:
        if not self.active_text:
            return

        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
        self.active_label = ""
        self.active_text = ""

    def record_turn(self, label: str, text: str) -> None:
        self.recent_turns.append({"label": label, "text": text})
        self.recent_turns = self.trim_history()
        with suppress(OSError):
            self.save_history()

    def commit_live(self) -> None:
        if not self.active_text:
            return

        sys.stdout.write("\n")
        self.last_committed[self.active_label] = self.active_text
        self.record_turn(self.active_label, self.active_text)
        self.active_label = ""
        self.active_text = ""
        sys.stdout.flush()

    def finalize(self, label: str, pending_text: str) -> str:
        if pending_text:
            self.print(label, pending_text)
        return ""

    def print(self, label: str, text: str, *, final: bool = True) -> None:
        clean_text = self.normalize_text(text)
        if not clean_text:
            return

        if self.active_text and self.active_label != label:
            self.commit_live()

        if self.active_label == label:
            if clean_text == self.active_text:
                if final:
                    self.commit_live()
                return
            if clean_text.startswith(self.active_text):
                sys.stdout.write(clean_text[len(self.active_text) :])
                self.active_text = clean_text
                if final:
                    self.commit_live()
                else:
                    sys.stdout.flush()
                return
            self.commit_live()

        if final and self.last_committed.get(label) == clean_text:
            return

        sys.stdout.write(f"[{label}] {clean_text}")
        if final:
            sys.stdout.write("\n")
            self.last_committed[label] = clean_text
            self.record_turn(label, clean_text)
            self.active_label = ""
            self.active_text = ""
        else:
            self.active_label = label
            self.active_text = clean_text
        sys.stdout.flush()

    def apply_update(self, label: str, pending_text: str, transcription: types.Transcription) -> str:
        text = (transcription.text or "").strip()
        if not text:
            return pending_text
        pending_text = self.merge_text(pending_text, text)
        self.print(label, pending_text, final=False)
        return pending_text

    def reset(self) -> None:
        self.clear_live()
        self.pending_input = ""
        self.pending_output = ""
