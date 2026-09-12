"""Argv-only adapter for the `dreamina` CLI.

This module provides a thin, deterministic wrapper around the `dreamina`
binary. It enforces:

* argv-only execution (``shell=False``; no string interpolation);
* typed error surfaces for missing CLI, auth/permission failures, invalid
  JSON, upgrade notices, and timeouts;
* separate stdout / stderr capture;
* a hard output-size cap to avoid buffering huge responses;
* a ``capability_snapshot`` helper that combines ``--version`` and
  ``--help`` / ``schema`` outputs into the canonical capability snapshot.

The adapter never installs, authenticates, or runs paid operations. It is
the only sanctioned seam between Codex Skill code and the installed binary.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_OUTPUT_BYTES = 1 * 1024 * 1024  # 1 MiB


class DreaminaAdapterError(RuntimeError):
    """Base class for all adapter-level errors."""


class CLINotFoundError(DreaminaAdapterError):
    """The configured `dreamina` binary could not be located or executed."""


class PermissionDeniedError(DreaminaAdapterError):
    """The CLI reported missing authentication or insufficient permission."""


class UpgradeRequiredError(DreaminaAdapterError):
    """The CLI signalled that a newer version is required."""


class InvalidJSONError(DreaminaAdapterError):
    """The CLI did not emit a parseable JSON payload on stdout."""


class TimeoutError(DreaminaAdapterError):  # noqa: A001 (mirrors builtin on purpose)
    """The CLI exceeded the configured timeout. No resubmission is performed."""


@dataclass(frozen=True)
class DreaminaResult:
    """Structured result of a single CLI invocation."""

    exit_code: int
    payload: Mapping[str, object] | list | None
    error_code: str | None
    submit_id: str | None
    stderr: str


class DreaminaAdapter:
    """Argv-only wrapper around an installed `dreamina` binary.

    The binary path is injected explicitly. Tests inject a shell-script
    stub; production code resolves `dreamina` from ``PATH`` or a configured
    absolute path. Authentication, model catalogs, and any paid operation
    are out of scope for this module.
    """

    def __init__(
        self,
        cli_command: str = "dreamina",
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.cli_command = cli_command
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.env = dict(env) if env is not None else None

    def _resolve_cli(self) -> str:
        # Honour explicit absolute / relative paths; otherwise search PATH.
        if os.path.sep in self.cli_command or self.cli_command.startswith("."):
            target = Path(self.cli_command)
            if not target.exists() or not os.access(target, os.X_OK):
                raise CLINotFoundError(f"dreamina CLI not found at {self.cli_command}")
            return str(target)
        resolved = shutil.which(self.cli_command)
        if resolved is None:
            raise CLINotFoundError(f"dreamina CLI not on PATH: {self.cli_command}")
        return resolved

    def run(self, args: list[str]) -> DreaminaResult:
        """Invoke the CLI with argv-only arguments and parse its JSON output.

        Raises one of the typed adapter errors when the CLI cannot be run
        cleanly. Returns a :class:`DreaminaResult` on success.
        """
        if not isinstance(args, list):
            raise TypeError("argv must be a list of strings (argv-only)")
        binary = self._resolve_cli()
        cmd = [binary, *args]
        try:
            completed = subprocess.run(  # noqa: S603 — argv-only, no shell
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                shell=False,
                check=False,
                env=self.env,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f"dreamina CLI timed out after {self.timeout_seconds}s; "
                "operation NOT resubmitted"
            ) from exc
        except FileNotFoundError as exc:
            raise CLINotFoundError(f"dreamina CLI not found: {binary}") from exc

        stderr_text = completed.stderr or ""
        stdout_bytes_size = len(completed.stdout or "")
        if stdout_bytes_size > self.max_output_bytes:
            raise InvalidJSONError(
                f"dreamina CLI output exceeded {self.max_output_bytes} bytes"
            )

        stderr_lower = stderr_text.lower()
        if "upgrade required" in stderr_lower:
            raise UpgradeRequiredError(stderr_text.strip() or "upgrade required")
        if completed.returncode != 0:
            if (
                "not authenticated" in stderr_lower
                or "permission denied" in stderr_lower
                or "unauthorized" in stderr_lower
            ):
                raise PermissionDeniedError(stderr_text.strip() or "permission denied")
            # Unknown non-zero exit: still try to parse stdout for an error_code.
            payload = _safe_parse_json(completed.stdout or "")
            if payload is None:
                raise DreaminaAdapterError(
                    f"dreamina CLI failed (exit {completed.returncode}): {stderr_text.strip()}"
                )
            return DreaminaResult(
                exit_code=completed.returncode,
                payload=payload,
                error_code=str(payload.get("error_code")) if isinstance(payload, Mapping) else None,
                submit_id=str(payload.get("submit_id")) if isinstance(payload, Mapping) and payload.get("submit_id") is not None else None,
                stderr=stderr_text,
            )

        payload = _safe_parse_json(completed.stdout or "")
        if payload is None:
            raise InvalidJSONError("dreamina CLI did not emit JSON on stdout")
        submit_id = None
        if isinstance(payload, Mapping):
            raw = payload.get("submit_id")
            if raw is not None:
                submit_id = str(raw)
        return DreaminaResult(
            exit_code=completed.returncode,
            payload=payload,
            error_code=None,
            submit_id=submit_id,
            stderr=stderr_text,
        )

    def capability_snapshot(self) -> dict:
        """Return a parsed capability snapshot from the live CLI.

        Combines ``--version`` and ``schema`` (or ``--help``) into the
        shape described by ``schemas/capability_snapshot.schema.json``.
        """
        version_result = self.run(["--version"])
        cli_version = ""
        if isinstance(version_result.payload, Mapping):
            cli_version = str(version_result.payload.get("version", "")).strip()
        if not cli_version:
            # Fallback: parse the first line of stdout when --version is not JSON.
            cli_version = (version_result.stderr or "").splitlines()[0:1] or [""]

        schema_result = self.run(["schema"])
        snapshot = {
            "cli_version": cli_version or "0.0.0",
            "captured_at": _now_iso(),
            "modes": [],
        }
        if isinstance(schema_result.payload, Mapping):
            if schema_result.payload.get("cli_commit"):
                snapshot["cli_commit"] = schema_result.payload["cli_commit"]
            modes = schema_result.payload.get("modes")
            if isinstance(modes, list):
                snapshot["modes"] = modes
            for key in ("models", "resolutions", "ratios", "durations"):
                if key in schema_result.payload:
                    snapshot[key] = schema_result.payload[key]
        return snapshot


def _safe_parse_json(text: str):
    if not text or not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description="dreamina argv adapter probe")
    parser.add_argument("args", nargs="*")
    args = parser.parse_args()
    adapter = DreaminaAdapter()
    try:
        result = adapter.run(args.args)
    except DreaminaAdapterError as exc:
        print(f"adapter error: {exc}")
        raise SystemExit(2) from exc
    print(json.dumps(result.payload, indent=2))
