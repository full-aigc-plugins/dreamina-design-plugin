"""Dreamina CLI environment inspection and verified installation."""

from __future__ import annotations

from scripts.output_redactor import redact_text


COMMANDS = frozenset({"login", "relogin", "logout", "user_credit", "text2image", "image2image", "image_upscale", "text2video", "image2video", "frames2video", "multiframe2video", "multimodal2video", "query_result", "list_task", "session", "version"})


class EnvironmentService:
    def __init__(self, adapter): self.adapter = adapter

    def status(self, *, command: str | None = None, detail: str = "summary") -> dict[str, object]:
        if detail not in {"summary", "full"}: raise ValueError("detail must be summary or full")
        if command is not None and command not in COMMANDS: raise ValueError(f"unsupported Dreamina command: {command}")
        snapshot = self.adapter.capability_snapshot()
        result: dict[str, object] = {"installed": True, "trusted": True, "cli_version": snapshot.get("cli_version"), "modes": snapshot.get("modes", []), "captured_at": snapshot.get("captured_at")}
        if command:
            help_result = self.adapter.run_text([command, "--help"] if command != "version" else ["version"])
            result.update({"command": command, "command_available": help_result.exit_code == 0})
            if detail == "full": result["help"] = redact_text(help_result.stdout + help_result.stderr, max_bytes=128 * 1024)
        elif detail == "full": result["capabilities"] = snapshot
        return result
