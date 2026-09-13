"""Fail-closed native confirmation for paid requests and local trust enrollment."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from typing import Any, Mapping


class ApprovalDeniedError(PermissionError):
    """The human denied, timed out, or could not receive the approval dialog."""


class NativeApprovalProvider:
    """Require a native macOS confirmation dialog for the exact guarded action."""

    def confirm(self, request: Mapping[str, Any]) -> str:
        summary = json.dumps(dict(request), ensure_ascii=False, sort_keys=True, indent=2)
        self._confirm_dialog("Dreamina 付费生成请求\n\n" + summary, "批准一次")
        return "native-user-confirmed"

    def confirm_cli_enrollment(self, *, path: str, owner_uid: int, sha256: str) -> str:
        summary = json.dumps(
            {"action": "trust-dreamina-cli", "path": path, "owner_uid": owner_uid, "sha256": sha256},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        self._confirm_dialog("注册可信 Dreamina CLI\n\n" + summary, "信任此文件")
        return "native-cli-trust-confirmed"

    def confirm_media_tool_enrollment(
        self, *, kind: str, path: str, owner_uid: int, sha256: str
    ) -> str:
        """Confirm the exact kind, canonical path, owner, and digest being trusted."""
        summary = json.dumps(
            {
                "action": "trust-media-tool",
                "kind": kind,
                "path": path,
                "owner_uid": owner_uid,
                "sha256": sha256,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        self._confirm_dialog("注册可信本地媒体工具\n\n" + summary, "信任此文件")
        return "native-media-tool-trust-confirmed"

    @staticmethod
    def _confirm_dialog(message: str, approve_button: str) -> None:
        if platform.system() != "Darwin":
            raise ApprovalDeniedError(
                "native paid approval is unavailable on this platform; refusing submission"
            )
        script = (
            "on run argv\n"
            "set requestText to item 1 of argv\n"
            "set approveText to item 2 of argv\n"
            "tell application \"Finder\"\n"
            "activate\n"
            "set dialogResult to display dialog requestText buttons {\"取消\", approveText} "
            "default button \"取消\" cancel button \"取消\" with icon caution giving up after 300\n"
            "return \"button returned:\" & (button returned of dialogResult)\n"
            "end tell\n"
            "end run"
        )
        env = {
            key: os.environ[key]
            for key in ("HOME", "TMPDIR", "LANG", "LC_ALL")
            if key in os.environ
        }
        try:
            result = subprocess.run(
                ["/usr/bin/osascript", "-e", script, "--", message, approve_button],
                capture_output=True,
                text=True,
                timeout=310,
                shell=False,
                check=False,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ApprovalDeniedError("native approval dialog failed or timed out") from exc
        if result.returncode != 0 or f"button returned:{approve_button}" not in result.stdout:
            raise ApprovalDeniedError("native approval was not granted")


__all__ = ["ApprovalDeniedError", "NativeApprovalProvider"]
