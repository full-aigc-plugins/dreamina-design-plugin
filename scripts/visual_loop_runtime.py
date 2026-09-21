"""Production composition root for the visual quality loop MCP tool.

The loop's domain layer already owns receipts, allowance and the round state
machine (``scripts/visual_quality_loop.py``).  This module only assembles it and
keeps two constraints intact:

* paid work stays on the existing approved handlers -- the runtime has no
  submission or download code of its own;
* judging stays on the host -- a stdio server cannot start the fresh-context
  vision subagent the rubric requires, so one round is two host-visible steps
  (``run_first_round``/``run_retry`` then ``record_judgement``).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from scripts.native_approval import NativeApprovalProvider
from scripts.visual_quality_loop import (
    InvalidVisualTransition,
    VisualEvidenceError,
    VisualLoopService,
    VisualLoopStore,
)

VISUAL_LOOP_TOOL_NAMES = ("dreamina_visual_loop",)

VISUAL_LOOP_ACTIONS = (
    "create",
    "lock_target",
    "run_first_round",
    "run_retry",
    "record_judgement",
    "propose_retry",
    "approve_retry",
    "status",
    "stop",
)

MEDIA_KINDS = ("image", "video", "dcc_preview")

ARTIFACT_FIELDS = frozenset({"submit_id", "path", "sha256", "size_bytes", "mime_type"})

_TARGET_FIELDS = frozenset(
    {"source_path", "approved_roots", "mime_type", "width", "height", "source"}
)


def visual_loop_tool_definitions() -> list[dict[str, Any]]:
    """The single closed additive visual-loop tool definition."""
    return [
        {
            "name": VISUAL_LOOP_TOOL_NAMES[0],
            "description": (
                "Drive one visual-quality loop: lock an immutable target, run an approved "
                "paid generation round, record the host's independent visual judgement, and "
                "stop or replan honestly. One round takes two calls: generate, then record "
                "the judgement. A failed round never submits again on its own."
            ),
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action"],
                "properties": {
                    "action": {"type": "string", "enum": list(VISUAL_LOOP_ACTIONS)},
                    "loop_id": {"type": "string", "pattern": "^vl_[a-f0-9]{24}$"},
                    "media_kind": {"type": "string", "enum": list(MEDIA_KINDS)},
                    "max_attempts": {"type": "integer", "minimum": 2, "maximum": 3},
                    "target": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["source_path", "approved_roots", "mime_type", "width", "height"],
                        "properties": {
                            "source_path": {"type": "string", "minLength": 1},
                            "approved_roots": {
                                "type": "array",
                                "items": {"type": "string", "minLength": 1},
                                "minItems": 1,
                                "maxItems": 10,
                            },
                            "mime_type": {"type": "string", "minLength": 1},
                            "width": {"type": "integer", "minimum": 1},
                            "height": {"type": "integer", "minimum": 1},
                            "source": {"type": "object"},
                        },
                    },
                    "request": {"type": "object"},
                    "artifact": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": sorted(ARTIFACT_FIELDS),
                        "properties": {
                            "submit_id": {"type": "string", "minLength": 1},
                            "path": {"type": "string", "minLength": 1},
                            "sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                            "size_bytes": {"type": "integer", "minimum": 0},
                            "mime_type": {"type": "string", "minLength": 1},
                        },
                    },
                    "judge_result": {"type": "object"},
                    "request_fingerprint": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                    "credit_ceiling": {"type": "integer", "minimum": 1},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 500},
                },
            },
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        }
    ]


class VisualLoopToolHandlers:
    """Dispatch table mirroring the project-tool registry shape."""

    def __init__(self, handlers: Mapping[str, Callable[[Mapping[str, Any]], dict[str, Any]]]) -> None:
        self._handlers = dict(handlers)

    def handles(self, name: str) -> bool:
        return name in self._handlers

    def call(self, name: str, args: Mapping[str, Any]) -> dict[str, Any]:
        handler = self._handlers.get(name)
        if handler is None:
            raise ValueError(f"unknown tool: {name}")
        return handler(args)


class VisualLoopRuntime:
    """Wire the visual-loop MCP handler to the production domain store."""

    def __init__(
        self,
        *,
        state_root: Path,
        approval_provider: Any | None = None,
        generation_port_factory: Callable[[Mapping[str, Any]], Any] | None = None,
    ) -> None:
        self.state_root = Path(state_root)
        self.state_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_root, 0o700)
        self.loops = VisualLoopStore(self.state_root / "visual-loops")
        self.approval = approval_provider or NativeApprovalProvider()
        self._generation_port_factory = generation_port_factory

    def registry(self) -> VisualLoopToolHandlers:
        handlers = {name: self.visual_loop for name in VISUAL_LOOP_TOOL_NAMES}
        if set(handlers) != set(VISUAL_LOOP_TOOL_NAMES):
            raise RuntimeError("production visual-loop tool registry is incomplete")
        return VisualLoopToolHandlers(handlers)

    def handles(self, name: str) -> bool:
        return name in VISUAL_LOOP_TOOL_NAMES

    def call(self, name: str, args: Mapping[str, Any]) -> dict[str, Any]:
        if not self.handles(name):
            raise ValueError(f"unknown tool: {name}")
        return self.visual_loop(args)

    def visual_loop(self, args: Mapping[str, Any]) -> dict[str, Any]:
        action = str(args.get("action", ""))
        if action not in VISUAL_LOOP_ACTIONS:
            raise ValueError(f"unknown visual loop action: {action}")
        return getattr(self, f"_action_{action}")(args)

    # -- actions ---------------------------------------------------------

    def _action_create(self, args: Mapping[str, Any]) -> dict[str, Any]:
        media_kind = str(args.get("media_kind", ""))
        if media_kind not in MEDIA_KINDS:
            raise ValueError(f"media_kind must be one of {MEDIA_KINDS}")
        max_attempts = args.get("max_attempts", 2)
        loop = self.loops.create_loop(media_kind=media_kind, max_attempts=int(max_attempts))
        return {"loop": loop}

    def _action_lock_target(self, args: Mapping[str, Any]) -> dict[str, Any]:
        loop_id = self._require_loop_id(args)
        target = args.get("target")
        if not isinstance(target, Mapping):
            raise ValueError("target must be an object")
        extra = set(target) - _TARGET_FIELDS
        if extra:
            raise ValueError(f"target has undeclared fields: {sorted(extra)}")
        source = target.get("source") or {}
        if not isinstance(source, Mapping):
            raise ValueError("target.source must be an object")
        loop = self.loops.lock_target(
            loop_id,
            source_path=Path(str(target["source_path"])),
            approved_roots=[Path(str(root)) for root in target["approved_roots"]],
            mime_type=str(target["mime_type"]),
            width=int(target["width"]),
            height=int(target["height"]),
            source=source,
        )
        return {"loop": loop}

    def _action_run_first_round(self, args: Mapping[str, Any]) -> dict[str, Any]:
        return self._run_paid_round(args, retry=False)

    def _action_run_retry(self, args: Mapping[str, Any]) -> dict[str, Any]:
        return self._run_paid_round(args, retry=True)

    def _action_record_judgement(self, args: Mapping[str, Any]) -> dict[str, Any]:
        loop_id = self._require_loop_id(args)
        loop = self.loops.get(loop_id)
        fingerprint = loop.get("pending_request_fingerprint")
        if not isinstance(fingerprint, str):
            raise InvalidVisualTransition("no round is awaiting judgement")
        artifact = args.get("artifact")
        judge_result = args.get("judge_result")
        if not isinstance(artifact, Mapping) or not isinstance(judge_result, Mapping):
            raise VisualEvidenceError("artifact and judge_result are required objects")
        return {
            "loop": self.loops.record_round(
                loop_id,
                request_fingerprint=fingerprint,
                artifact=artifact,
                judge_result=judge_result,
            )
        }

    def _action_propose_retry(self, args: Mapping[str, Any]) -> dict[str, Any]:
        loop_id = self._require_loop_id(args)
        request = args.get("request")
        if not isinstance(request, Mapping):
            raise ValueError("request must be an object")
        return {"loop": self.loops.record_retry_proposal(loop_id, request)}

    def _action_approve_retry(self, args: Mapping[str, Any]) -> dict[str, Any]:
        loop_id = self._require_loop_id(args)
        fingerprint = args.get("request_fingerprint")
        ceiling = args.get("credit_ceiling")
        if not isinstance(fingerprint, str):
            raise ValueError("request_fingerprint is required")
        if isinstance(ceiling, bool) or not isinstance(ceiling, int) or ceiling < 1:
            raise ValueError("credit_ceiling must be a positive integer")
        approver = self.approval.confirm(
            {
                "operation": "dreamina-visual-loop-retry",
                "loop_id": loop_id,
                "request_fingerprint": fingerprint,
                "credit_ceiling": ceiling,
            }
        )
        loop = self.loops.activate_retry_allowance(
            loop_id,
            request_fingerprint=fingerprint,
            credit_ceiling=ceiling,
            approver=str(approver),
        )
        return {"loop": loop}

    def _action_status(self, args: Mapping[str, Any]) -> dict[str, Any]:
        return {"loop": self.loops.get(self._require_loop_id(args))}

    def _action_stop(self, args: Mapping[str, Any]) -> dict[str, Any]:
        loop_id = self._require_loop_id(args)
        reason = str(args.get("reason", "")).strip()
        if not reason:
            raise ValueError("reason is required to stop a visual loop")
        return {"loop": self.loops.stop(loop_id, reason=reason)}

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _require_loop_id(args: Mapping[str, Any]) -> str:
        loop_id = args.get("loop_id")
        if not isinstance(loop_id, str):
            raise ValueError("loop_id is required")
        return loop_id

    def _run_paid_round(self, args: Mapping[str, Any], *, retry: bool) -> dict[str, Any]:
        loop_id = self._require_loop_id(args)
        request = args.get("request")
        if not isinstance(request, Mapping):
            raise ValueError("request must be an object")
        loop = self.loops.get(loop_id)
        if loop["media_kind"] == "dcc_preview":
            raise InvalidVisualTransition(
                "DCC preview rounds use the preview port, not paid generation"
            )
        request_copy = dict(request)
        ceiling = request_copy.get("credit_ceiling", 1)
        if isinstance(ceiling, bool) or not isinstance(ceiling, int) or ceiling < 1:
            raise VisualEvidenceError("generation request credit_ceiling must be a positive integer")
        fingerprint = VisualLoopService.request_fingerprint(request_copy)
        reserved = self.loops.reserve_round(
            loop_id,
            request_fingerprint=fingerprint,
            request_credit_ceiling=ceiling,
            retry=retry,
        )
        try:
            artifact = self._generation_port(request_copy).generate(request_copy)
        except Exception as exc:
            try:
                self.loops.mark_submission_unknown(loop_id, message=str(exc))
            except InvalidVisualTransition:
                pass
            raise
        return {
            "loop": self.loops.get(loop_id),
            "request_fingerprint": fingerprint,
            "artifact": artifact,
            "judge_evidence": {
                "target": reserved["target"],
                "artifact": artifact,
                "previous_round": reserved["rounds"][-1] if reserved["rounds"] else None,
            },
        }

    def _generation_port(self, request: Mapping[str, Any]) -> Any:
        if self._generation_port_factory is None:
            raise RuntimeError(
                "visual loop generation requires the approved production handlers; "
                "no generation port is configured"
            )
        return self._generation_port_factory(request)


__all__ = [
    "ARTIFACT_FIELDS",
    "MEDIA_KINDS",
    "VISUAL_LOOP_ACTIONS",
    "VISUAL_LOOP_TOOL_NAMES",
    "VisualLoopRuntime",
    "VisualLoopToolHandlers",
    "visual_loop_tool_definitions",
]
