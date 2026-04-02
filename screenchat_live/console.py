from .runtime import RuntimeConfig


class ConsoleOutput:
    def connection_banner(
        self,
        runtime_config: RuntimeConfig,
        *,
        reconnect_count: int,
    ) -> str:
        if runtime_config.screen_index is None or not runtime_config.screen_name:
            return f"{'Reconnected' if reconnect_count else 'Connected'}. Start speaking!"
        if reconnect_count:
            return f"Reconnected. Sharing screen {runtime_config.screen_index}: {runtime_config.screen_name}."
        return f"Connected. Sharing screen {runtime_config.screen_index}: {runtime_config.screen_name}. Start speaking!"

    @staticmethod
    def reconnect_notice(time_left: str) -> str:
        return f"\n[session] will reconnect after this turn (time_left={time_left})"

    @staticmethod
    def tool_result(command: str, ok: bool, error: str | None = None) -> str:
        if ok:
            return f"\n[tool] {command}"

        status = "tool blocked" if ConsoleOutput._is_blocked_tool_error(error) else "tool error"
        detail = f": {error}" if error else ""
        return f"\n[{status}] {command}{detail}"

    @staticmethod
    def _is_blocked_tool_error(error: str | None) -> bool:
        if not error:
            return False
        normalized_error = error.lower()
        blocked_markers = (
            "not allowlisted",
            "disallowed",
            "path traversal",
            "outside root",
            "unsupported tool",
        )
        return any(marker in normalized_error for marker in blocked_markers)

    @staticmethod
    def screen_share_disabled(error: Exception) -> str:
        return f"[screen share disabled] {error}"

    @staticmethod
    def connection_closed() -> str:
        return "\nConnection closed."

    @staticmethod
    def interrupted_by_user() -> str:
        return "Interrupted by user."


console_output = ConsoleOutput()
