"""Argv-only adapter for the ``dreamina-canvas`` CLI.

This is the Canvas execution seam for this plugin. It deliberately reuses
:class:`scripts.dreamina_adapter.DreaminaAdapter` for everything that is
already correct for a local Dreamina binary — absolute-path resolution
with ``PATH`` lookup disabled, optional SHA-256 pinned staging, a minimal
child environment, streaming stdout/stderr byte limits, process-group
termination on timeout, and JSON-only stdout parsing — and adds only what
is specific to the Canvas command vocabulary.

Two Canvas behaviours differ from the frozen ``dreamina`` CLI and are
modelled explicitly here:

* **Exit 10 and 20 are states, not failures.** ``node run`` returns exit 10
  when a saved draft is waiting for credit confirmation (nothing was
  submitted), and exit 20 when a local wait ended but the server-side
  operation is still recoverable. Both are returned to the caller so the
  paid chain can continue instead of raising.
* **Every write needs a canvas.** The legacy CLI had sessions; Canvas has
  no session concept, so a ``projectId`` must be present on node, quote,
  run, operation and resource calls. Callers resolve it before submitting.

Authentication, model catalogs, and money remain out of scope: this module
never logs credentials, never accepts a credit token, and never approves
spend on the user's behalf.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from scripts.dreamina_adapter import (
    CLINotFoundError,
    DreaminaAdapter,
    DreaminaAdapterError,
    DreaminaTextResult,
    InvalidJSONError,
    PermissionDeniedError,
    UntrustedCLIError,
    UpgradeRequiredError,
)

CANVAS_BINARY = "dreamina-canvas"
DEFAULT_MEDIA_TYPES = ("image", "video", "audio")

# Canvas public video modes. There is no `i2v`; single-image guidance is
# m2v and ordered start/end frames are first_last_frame.
VIDEO_MODES = ("t2v", "first_last_frame", "m2v")
IMAGE_MODES = ("t2i", "i2i")
AUDIO_MODES = ("tts", "music")

# Exit codes that carry meaning in the paid chain instead of failing.
EXIT_CONFIRM_REQUIRED = 10
EXIT_RECOVERABLE = 20
EXIT_RETRYABLE = 21

EXIT_LOGIN_REQUIRED = 11
EXIT_FORBIDDEN = 12
EXIT_UPGRADE_REQUIRED = 13
EXIT_HUMAN_INTERVENTION = 22


class CanvasCommandError(DreaminaAdapterError):
    """A ``dreamina-canvas`` invocation failed with a fatal exit code."""


class CanvasConfirmRequired(CanvasCommandError):
    """Exit 10: the draft is saved and quoted but nothing was submitted."""


class CanvasOperationPending(CanvasCommandError):
    """Exit 20: the server-side operation is still running and recoverable."""


@dataclass(frozen=True)
class CanvasResult:
    """One Canvas invocation: exit code, parsed envelope, and extracted ids."""

    exit_code: int
    payload: Mapping[str, Any] | list | None
    stderr: str
    node_id: str | None = None
    submit_id: str | None = None
    project_id: str | None = None
    resource_id: str | None = None

    @property
    def data(self) -> Mapping[str, Any]:
        """The ``data`` object of a success envelope (empty when absent)."""
        if isinstance(self.payload, Mapping):
            value = self.payload.get("data")
            if isinstance(value, Mapping):
                return value
        return {}

    @property
    def error_code(self) -> str | None:
        if isinstance(self.payload, Mapping):
            error = self.payload.get("error")
            if isinstance(error, Mapping):
                code = error.get("code")
                return str(code) if code is not None else None
        return None

    @property
    def required_action(self) -> str | None:
        if isinstance(self.payload, Mapping):
            error = self.payload.get("error")
            if isinstance(error, Mapping):
                action = error.get("requiredAction")
                return str(action) if action is not None else None
        return None

    @property
    def awaiting_confirmation(self) -> bool:
        return self.exit_code == EXIT_CONFIRM_REQUIRED

    @property
    def recoverable(self) -> bool:
        return self.exit_code == EXIT_RECOVERABLE


def _first(data: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _as_argv(args: Sequence[str]) -> list[str]:
    argv = [str(item) for item in args]
    if not argv:
        raise TypeError("argv must contain a command path")
    return argv


class DreaminaCanvasAdapter:
    """Typed wrapper over the ``dreamina-canvas`` command vocabulary.

    The binary path must be absolute; ``PATH`` lookup is disabled, exactly
    as for the legacy adapter. Every method is a thin, argv-only call with
    ``--format json`` placed before the subcommand.
    """

    def __init__(
        self,
        cli_command: str = CANVAS_BINARY,
        *,
        timeout_seconds: int | None = None,
        max_output_bytes: int | None = None,
        trusted_binary_sha256: str | None = None,
        adapter: DreaminaAdapter | None = None,
    ) -> None:
        if adapter is not None:
            self._adapter = adapter
            self.cli_command = adapter.cli_command
            return
        kwargs: dict[str, Any] = {}
        if timeout_seconds is not None:
            kwargs["timeout_seconds"] = timeout_seconds
        if max_output_bytes is not None:
            kwargs["max_output_bytes"] = max_output_bytes
        if trusted_binary_sha256 is not None:
            kwargs["trusted_binary_sha256"] = trusted_binary_sha256
        self.cli_command = cli_command
        self._adapter = DreaminaAdapter(cli_command=cli_command, **kwargs)

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Release any pinned staging directory."""
        self._adapter.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- raw invocation ----------------------------------------------------

    def invoke(self, args: Sequence[str]) -> CanvasResult:
        """Run one Canvas argv list and classify its exit code.

        Exit 0 is the only fully successful outcome. Exit 10 and 20 are
        returned as state (the paid chain continues). Fatal codes raise a
        typed error so callers never parse stderr prose.
        """
        argv = _as_argv(args)
        raw: DreaminaTextResult = self._adapter.run_text(["--format", "json", *argv])
        return self._classify(argv, raw)

    def _classify(self, argv: list[str], raw: DreaminaTextResult) -> CanvasResult:
        payload: Mapping[str, Any] | list | None = None
        if raw.stdout.strip():
            try:
                parsed = json.loads(raw.stdout)
            except ValueError as exc:
                if raw.exit_code == 0:
                    raise InvalidJSONError(
                        "dreamina-canvas did not emit JSON on stdout"
                    ) from exc
                payload = None
            else:
                if isinstance(parsed, (Mapping, list)):
                    payload = parsed
                elif raw.exit_code == 0:
                    raise InvalidJSONError(
                        "dreamina-canvas emitted a non-object JSON envelope"
                    )

        code = raw.exit_code
        if code == 0:
            return self._result(code, payload, raw.stderr)
        if code in (EXIT_CONFIRM_REQUIRED, EXIT_RECOVERABLE, EXIT_RETRYABLE):
            return self._result(code, payload, raw.stderr)

        message = raw.stderr.strip() or f"exit {code}"
        if code == EXIT_LOGIN_REQUIRED:
            raise CanvasCommandError(
                f"dreamina-canvas requires authentication ({message})",
                invocation_started=True,
                outcome_ambiguous=False,
            )
        if code == EXIT_FORBIDDEN:
            raise PermissionDeniedError(message, invocation_started=True)
        if code == EXIT_UPGRADE_REQUIRED:
            raise UpgradeRequiredError(message, invocation_started=True)
        if code == EXIT_HUMAN_INTERVENTION:
            raise CanvasCommandError(
                f"dreamina-canvas needs a human decision ({message})",
                invocation_started=True,
                outcome_ambiguous=True,
            )
        raise CanvasCommandError(
            f"dreamina-canvas {' '.join(argv[:2])} failed (exit {code}): {message}",
            invocation_started=True,
            outcome_ambiguous=code == EXIT_RECOVERABLE,
        )

    @staticmethod
    def _result(
        exit_code: int, payload: Mapping[str, Any] | list | None, stderr: str
    ) -> CanvasResult:
        data: Mapping[str, Any] = {}
        if isinstance(payload, Mapping) and isinstance(payload.get("data"), Mapping):
            data = payload["data"]
        node = data.get("node") if isinstance(data.get("node"), Mapping) else {}
        resource = (
            data.get("resource") if isinstance(data.get("resource"), Mapping) else {}
        )
        return CanvasResult(
            exit_code=exit_code,
            payload=payload,
            stderr=stderr,
            node_id=_first(node, "nodeId", "node_id") or _first(data, "nodeId"),
            submit_id=_first(data, "submitId", "submit_id", "operationRef"),
            project_id=_first(data, "projectId", "project_id"),
            resource_id=_first(resource, "resourceId", "id") or _first(
                data, "resourceId"
            ),
        )

    # -- pre-flight --------------------------------------------------------

    def version(self) -> Mapping[str, Any]:
        return self.invoke(["version"]).data

    def schema(self, command_path: str | None = None) -> Mapping[str, Any]:
        args = ["schema"] + ([command_path] if command_path else [])
        return self.invoke(args).data

    def auth_account(self) -> Mapping[str, Any]:
        """Server-recognised identity — the only trusted login evidence."""
        return self.invoke(["auth", "account"]).data

    def preflight(self) -> dict[str, Any]:
        """Run the three checks every Canvas submission depends on."""
        version = self.version()
        schema = self.schema()
        account = self.auth_account()
        return {"version": version, "schema": schema, "account": account}

    # -- discovery ---------------------------------------------------------

    def model_list(self, media_type: str) -> Any:
        if media_type not in DEFAULT_MEDIA_TYPES:
            raise ValueError(f"unsupported media type: {media_type}")
        return self.invoke(["model", "list", "--type", media_type]).data

    def capability_snapshot(
        self, media_types: Sequence[str] = DEFAULT_MEDIA_TYPES
    ) -> dict[str, Any]:
        """Live capability picture: models per media type plus recent canvases."""
        models: dict[str, Any] = {}
        for media_type in media_types:
            if media_type not in DEFAULT_MEDIA_TYPES:
                raise ValueError(f"unsupported media type: {media_type}")
            models[media_type] = self.model_list(media_type)
        return {"models": models, "canvases": self.canvas_list()}

    # -- canvas ------------------------------------------------------------

    def canvas_create(self, name: str, *, use: bool = True) -> CanvasResult:
        args = ["canvas", "create", name]
        if use:
            args.append("--use")
        return self.invoke(args)

    def canvas_list(self, limit: int = 20) -> Any:
        return self.invoke(["canvas", "ls", "--limit", str(limit)]).data

    # -- material ----------------------------------------------------------

    def resource_upload(self, path: str | Path, name: str | None = None) -> str:
        """Upload a local file and return its ``resourceId``."""
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError(f"local material not found: {source}")
        args = ["resource", "upload", "--file", str(source)]
        if name:
            args.extend(["--name", name])
        result = self.invoke(args)
        if not result.resource_id:
            raise CanvasCommandError(
                "resource upload did not return a resourceId", outcome_ambiguous=False
            )
        return result.resource_id

    def resource_get(self, resource_id: str, project_id: str) -> Mapping[str, Any]:
        return self.invoke(
            ["resource", "get", resource_id, "--project-id", project_id]
        ).data

    def resource_download(
        self, resource_id: str, project_id: str, output_dir: str | Path
    ) -> Mapping[str, Any]:
        target = Path(output_dir)
        if not target.is_dir():
            raise FileNotFoundError(f"download directory must exist: {target}")
        return self.invoke(
            [
                "resource",
                "download",
                resource_id,
                "--project-id",
                project_id,
                "--output",
                str(target),
            ]
        ).data

    # -- node drafts (free) -------------------------------------------------

    def node_create_image(
        self,
        *,
        project_id: str,
        prompt: str,
        mode: str = "t2i",
        model: str | None = None,
        title: str | None = None,
        ratio: str | None = None,
        resolution: str | None = None,
        count: int | None = None,
        refs: Sequence[str] = (),
    ) -> str:
        if mode not in IMAGE_MODES:
            raise ValueError(f"unsupported image mode: {mode}")
        if mode == "i2i" and not refs:
            raise ValueError("i2i requires at least one node:/res: reference")
        args = ["node", "create", "image", "--project-id", project_id,
                "--mode", mode, "--prompt", prompt]
        if title:
            args.extend(["--title", title])
        if model:
            args.extend(["--model", model])
        if ratio:
            args.extend(["--ratio", ratio])
        if resolution:
            args.extend(["--resolution", resolution])
        if count is not None:
            args.extend(["--count", str(count)])
        for ref in refs:
            args.extend(["--ref", ref])
        result = self.invoke(args)
        return self._require_node(result, "node create image")

    def node_create_video(
        self,
        *,
        project_id: str,
        prompt: str,
        mode: str = "t2v",
        model: str | None = None,
        title: str | None = None,
        ratio: str | None = None,
        resolution: str | None = None,
        duration: int | None = None,
        count: int | None = None,
        refs: Sequence[str] = (),
    ) -> str:
        if mode not in VIDEO_MODES:
            raise ValueError(
                f"unsupported video mode: {mode} (Canvas has no i2v; use m2v)"
            )
        if mode != "t2v" and not refs:
            raise ValueError(f"{mode} requires at least one node:/res: reference")
        if duration is None:
            duration = 5
        if duration <= 0:
            raise ValueError("--duration must be a positive number of seconds")
        args = ["node", "create", "video", "--project-id", project_id,
                "--mode", mode, "--prompt", prompt, "--duration", str(duration)]
        if title:
            args.extend(["--title", title])
        if model:
            args.extend(["--model", model])
        if ratio:
            args.extend(["--ratio", ratio])
        if resolution:
            args.extend(["--resolution", resolution])
        if count is not None:
            args.extend(["--count", str(count)])
        for ref in refs:
            args.extend(["--ref", ref])
        result = self.invoke(args)
        return self._require_node(result, "node create video")

    def node_edit_image(
        self,
        *,
        node_id: str,
        project_id: str,
        prompt: str,
        mode: str = "i2i",
        model: str | None = None,
        ratio: str | None = None,
        resolution: str | None = None,
        count: int | None = None,
        refs: Sequence[str] = (),
    ) -> CanvasResult:
        """Replace a node's generation block (full replace, not a patch)."""
        args = ["node", "edit", "image", "--node-id", node_id,
                "--project-id", project_id, "--mode", mode, "--prompt", prompt]
        if model:
            args.extend(["--model", model])
        if ratio:
            args.extend(["--ratio", ratio])
        if resolution:
            args.extend(["--resolution", resolution])
        if count is not None:
            args.extend(["--count", str(count)])
        for ref in refs:
            args.extend(["--ref", ref])
        return self.invoke(args)

    def node_upscale(
        self,
        *,
        node_id: str,
        project_id: str,
        mode: str | None = None,
        resolution: str | None = None,
        submit_id: str | None = None,
        credit_ceiling: int | None = None,
        dry_run: bool = False,
    ) -> CanvasResult:
        """Separately priced upscale; its approval token is not node confirm's."""
        args = ["node", "upscale", "image", "--node-id", node_id,
                "--project-id", project_id]
        if mode:
            args.extend(["--mode", mode])
        if resolution:
            args.extend(["--resolution", resolution])
        if dry_run:
            args.append("--dry-run")
        if submit_id:
            args.extend(["--submit-id", submit_id])
        if credit_ceiling is not None:
            args.extend(["--credit-ceiling", str(credit_ceiling)])
        return self.invoke(args)

    @staticmethod
    def _require_node(result: CanvasResult, what: str) -> str:
        if not result.node_id:
            raise CanvasCommandError(
                f"{what} did not return a nodeId", outcome_ambiguous=False
            )
        return result.node_id

    # -- paid chain --------------------------------------------------------

    def node_quote(self, node_id: str, project_id: str) -> Mapping[str, Any]:
        """Query the live credit quote. A quote is never an approval."""
        return self.invoke(
            ["node", "quote", "--node-id", node_id, "--project-id", project_id]
        ).data

    def node_confirm(
        self,
        *,
        node_id: str,
        project_id: str,
        submit_id: str,
        credit_ceiling: int | None = None,
        credit_token: str | None = None,
    ) -> CanvasResult:
        args = ["node", "confirm", "--node-id", node_id,
                "--project-id", project_id, "--submit-id", submit_id]
        if credit_ceiling is not None:
            args.extend(["--credit-ceiling", str(credit_ceiling)])
        if credit_token is not None:
            args.extend(["--credit-token", credit_token])
        return self.invoke(args)

    def node_run(
        self, *, node_id: str, project_id: str, submit_id: str
    ) -> CanvasResult:
        return self.invoke(
            ["node", "run", "--node-id", node_id,
             "--project-id", project_id, "--submit-id", submit_id]
        )

    # -- observation -------------------------------------------------------

    def operation_status(self, submit_id: str, project_id: str) -> CanvasResult:
        return self.invoke(
            ["operation", "status", submit_id, "--project-id", project_id]
        )

    def operation_wait(
        self,
        submit_id: str,
        project_id: str,
        *,
        timeout: str = "10m",
        interval: str = "5s",
    ) -> CanvasResult:
        return self.invoke(
            ["operation", "wait", submit_id, "--project-id", project_id,
             "--timeout", timeout, "--interval", interval]
        )

    def node_find(
        self,
        *,
        project_id: str,
        node_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> Any:
        args = ["node", "find", "--project-id", project_id, "--limit", str(limit)]
        if node_type:
            args.extend(["--type", node_type])
        if status:
            args.extend(["--status", status])
        return self.invoke(args).data

    def node_show(self, node_id: str, project_id: str) -> Mapping[str, Any]:
        return self.invoke(
            ["node", "show", "--node-id", node_id, "--project-id", project_id]
        ).data


__all__ = [
    "AUDIO_MODES",
    "CANVAS_BINARY",
    "IMAGE_MODES",
    "VIDEO_MODES",
    "CLINotFoundError",
    "CanvasCommandError",
    "CanvasConfirmRequired",
    "CanvasOperationPending",
    "CanvasResult",
    "DreaminaAdapterError",
    "DreaminaCanvasAdapter",
    "InvalidJSONError",
    "PermissionDeniedError",
    "UntrustedCLIError",
    "UpgradeRequiredError",
]
