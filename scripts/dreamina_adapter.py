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
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_OUTPUT_BYTES = 1 * 1024 * 1024  # 1 MiB
CAPABILITY_MODES = (
    "text2image",
    "image2image",
    "text2video",
    "image2video",
    "frames2video",
    "multimodal2video",
)


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
        version_text = self._run_text(["--version"]).strip()
        version_payload = _safe_parse_json(version_text)
        cli_version = ""
        cli_commit = None
        if isinstance(version_payload, Mapping):
            cli_version = str(version_payload.get("version", "")).strip()
            cli_commit = version_payload.get("commit")
        if not cli_version:
            match = re.search(r"\b(\d+\.\d+\.\d+(?:[+-][A-Za-z0-9.-]+)?)\b", version_text)
            if match:
                cli_version = match.group(1)
        if not cli_version:
            raise InvalidJSONError("dreamina --version emitted no usable build identity")

        schema_payload: Mapping[str, object] = {}
        try:
            schema_result = self.run(["schema"])
            if isinstance(schema_result.payload, Mapping):
                schema_payload = schema_result.payload
        except DreaminaAdapterError as exc:
            if 'unknown command "schema"' not in str(exc):
                raise
            help_text = self._run_text(["--help"])
            modes = [
                mode
                for mode in CAPABILITY_MODES
                if re.search(rf"(?m)^\s{{2}}{re.escape(mode)}\s+", help_text)
            ]
            schema_payload = _parse_command_help(
                {mode: self._run_text([mode, "--help"]) for mode in modes}
            )
        snapshot = {
            "cli_version": cli_version or "0.0.0",
            "captured_at": _now_iso(),
            "modes": [],
        }
        if cli_commit:
            snapshot["cli_commit"] = cli_commit
        if schema_payload:
            if schema_payload.get("cli_commit"):
                snapshot["cli_commit"] = schema_payload["cli_commit"]
            modes = schema_payload.get("modes")
            if isinstance(modes, list):
                snapshot["modes"] = modes
            for key in ("models", "resolutions", "ratios", "durations"):
                if key in schema_payload:
                    snapshot[key] = schema_payload[key]
        return snapshot

    def _run_text(self, args: list[str]) -> str:
        """Run a read-only help command whose stdout is plain text."""
        binary = self._resolve_cli()
        completed = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            shell=False,
            check=False,
            env=self.env,
        )
        if completed.returncode != 0:
            raise DreaminaAdapterError(
                f"dreamina CLI failed (exit {completed.returncode}): "
                f"{completed.stderr.strip()}"
            )
        if len(completed.stdout or "") > self.max_output_bytes:
            raise InvalidJSONError(
                f"dreamina CLI output exceeded {self.max_output_bytes} bytes"
            )
        return completed.stdout or ""


def _safe_parse_json(text: str):
    if not text or not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _parse_command_help(command_help: Mapping[str, str]) -> dict:
    """Derive capability unions from the installed CLI's generator help."""
    models: dict[str, dict] = {}
    resolutions = {"image": set(), "video": set()}
    ratios: set[str] = set()
    duration_bounds: list[int] = []
    for mode, help_text in command_help.items():
        model_match = re.search(
            r"(?m)(?:^- model_version(?: values)?:|--model_version\s+\w+\s+supported values:|flag values:)\s*([^\n)]+)",
            help_text,
        )
        if model_match:
            for token in model_match.group(1).split(","):
                name = token.strip()
                if name:
                    entry = models.setdefault(name, {"name": name, "modes": []})
                    entry["modes"].append(mode)
        mode_ratios = set(re.findall(r"\b(?:21:9|16:9|9:16|4:3|3:4|3:2|2:3|1:1)\b", help_text))
        ratios.update(mode_ratios)
        values = {
            value.lower()
            for value in re.findall(
                r"\b(?:1(?:\.5)?k|2k|4k|480p|720p|1080p)\b",
                help_text,
                re.IGNORECASE,
            )
        }
        resolutions["image" if mode.endswith("image") else "video"].update(values)
        mode_bounds = [
            (int(lower), int(upper))
            for lower, upper in re.findall(r"duration[^\n]*?(\d+)-(\d+)", help_text)
        ]
        for lower, upper in mode_bounds:
            duration_bounds.extend((int(lower), int(upper)))
        count_match = re.search(r"generate_num:\s*(\d+)-(\d+)", help_text)
        reference_count_match = re.search(r"Upload\s+(\d+)\s+to\s+(\d+)\s+local images", help_text)
        for entry in models.values():
            if mode not in entry["modes"]:
                continue
            entry.setdefault("ratios", set()).update(mode_ratios)
            if count_match and mode.endswith("image"):
                entry["max_count"] = int(count_match.group(2))
            if reference_count_match and mode == "image2image":
                entry["max_references"] = int(reference_count_match.group(2))
            if mode == "multimodal2video":
                entry.setdefault("max_references", 12)
            entry.setdefault("resolutions", set())
            for line in help_text.splitlines():
                if not line.startswith("- ") or "->" not in line or re.search(
                    rf"(?<![A-Za-z0-9._]){re.escape(entry['name'])}(?![A-Za-z0-9._])",
                    line.split("->", 1)[0],
                ) is None:
                    continue
                entry["resolutions"].update(
                    value.lower()
                    for value in re.findall(
                        r"\b(?:1(?:\.5)?k|2k|4k|480p|720p|1080p)\b", line, re.IGNORECASE
                    )
                )
                bounds = re.search(r"output duration\s+(\d+)-(\d+)s", line)
                if bounds is None and "video/audio duration" not in line:
                    bounds = re.search(r"duration\s+(\d+)-(\d+)s", line)
                if bounds:
                    entry["duration_min_seconds"] = int(bounds.group(1))
                    entry["duration_max_seconds"] = int(bounds.group(2))
                reference_bounds = re.search(r"video/audio duration\s+(\d+)-(\d+)s", line)
                if reference_bounds:
                    entry["audio_reference_max_seconds"] = int(reference_bounds.group(2))
                total_inputs = re.search(r"total inputs<=([0-9]+)", line)
                if total_inputs:
                    entry["max_references"] = int(total_inputs.group(1))
            if mode in {"image2video", "frames2video"} and re.search(
                rf"not accepted with model_version\s+{re.escape(entry['name'])}", help_text
            ):
                entry.setdefault("ratio_forbidden_modes", []).append(mode)
        # Apply explicit "all other models" constraints to entries that did not
        # receive a model-specific line for this mode.
        other_line = next((line for line in help_text.splitlines() if "all other" in line and "->" in line), "")
        if other_line:
            other_resolutions = {
                value.lower() for value in re.findall(r"\b(?:480p|720p|1080p|4k)\b", other_line, re.IGNORECASE)
            }
            bounds = re.search(r"duration\s+(\d+)-(\d+)s", other_line)
            for entry in models.values():
                if mode in entry["modes"] and not entry.get("resolutions"):
                    entry["resolutions"] = set(other_resolutions)
                    if bounds:
                        entry["duration_min_seconds"] = int(bounds.group(1))
                        entry["duration_max_seconds"] = int(bounds.group(2))
    result = {
        "modes": list(command_help),
        "models": [
            {
                key: sorted(value) if isinstance(value, set) else value
                for key, value in entry.items()
            }
            for _, entry in sorted(models.items())
        ],
        "resolutions": {
            key: sorted(values) for key, values in resolutions.items() if values
        },
        "ratios": sorted(ratios),
    }
    if duration_bounds:
        result["durations"] = {
            "min_seconds": min(duration_bounds),
            "max_seconds": max(duration_bounds),
        }
    return result


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
