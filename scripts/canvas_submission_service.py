"""Canvas submission service: quote-bound approval for the Canvas runtime.

This is the Canvas counterpart of ``ImageService`` / ``VideoService``. It
deliberately does not modify those services: the frozen ``dreamina`` rail
stays exactly as it was, and this module sits beside it as the second
runner behind the same MCP surface.

The one structural improvement Canvas allows is the approval ordering.
The legacy CLI could only estimate cost before submitting, so an approval
bound an estimate. Canvas quotes a *live* amount for a saved draft, so the
approval here is bound to that quote:

    quote()  : prepare() → live quote → fingerprint + scope (with the ceiling)
    submit() : consume_approval(fingerprint) → node confirm → node run

Accounting is identical to the legacy rail: the same ``ApprovalGuard``
receipt and the same ``OperationLedger`` intents, so submit-once, ambiguity
handling and no-blind-resubmission guarantees carry over unchanged.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.canvas_execution import (
    CanvasExecutionRequest,
    CanvasExecutor,
    CanvasPreparation,
)
from scripts.operation_ledger import OperationLedger

LEDGER_SCHEMA = "dreamina-canvas-submission/1"


def build_canvas_approval_fingerprint(request: Mapping[str, Any]) -> str:
    """Fingerprint an approval request *and* its live quoted ceiling.

    Binding the amount into the fingerprint is what makes an approval
    non-transferable: a different quote produces a different fingerprint,
    so a receipt cannot be re-used against a re-priced run. The request
    mapping is exactly ``CanvasQuoteContext.approval_request`` — deriving
    the digest from that object (instead of a parallel shape) is what
    keeps quote-side and consume-side digests identical by construction.
    """
    canonical = {
        "media": str(request.get("media", "")),
        "mode": str(request.get("mode", "")),
        "prompt": str(request.get("prompt", "")),
        "model": request.get("model"),
        "ratio": request.get("ratio"),
        "resolution": request.get("resolution") or request.get("resolution_type"),
        "count": request.get("count"),
        "duration": request.get("duration"),
        "references": [
            str(ref.get("path") or ref.get("ref") or "")
            if isinstance(ref, Mapping)
            else str(ref)
            for ref in (request.get("references") or [])
        ],
        "project_id": str(request.get("project_id", "")),
        "quoted_ceiling": request.get("quoted_ceiling"),
    }
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def quoted_ceiling(quote: Mapping[str, Any]) -> int | None:
    """Extract the live ceiling from a quote payload, if it carries one."""
    for key in ("totalMaxCredits", "total_max_credits", "maxCredits"):
        value = quote.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


@dataclass(frozen=True)
class CanvasQuoteContext:
    """Everything the approval must be bound to, plus the saved draft."""

    preparation: CanvasPreparation
    request: CanvasExecutionRequest
    fingerprint: str
    scope: Mapping[str, Any]
    ceiling: int | None

    @property
    def approval_request(self) -> dict[str, Any]:
        """The request object an ApprovalGuard receipt is recorded against.

        Carries both vocabularies: the Canvas fields the fingerprint binds
        (media/mode/project_id/quoted_ceiling) and the legacy-named aliases
        the guard's scope validator compares against
        (``resolution_type`` / ``duration_seconds``).
        """
        resolution = self.request.resolution or self.request.resolution_type
        count = self.request.count if self.request.count is not None else 1
        return {
            "media": self.request.media,
            "mode": self.request.mode,
            "prompt": self.request.prompt,
            "model": self.request.model,
            "ratio": self.request.ratio,
            "resolution": resolution,
            "resolution_type": resolution,
            "count": count,
            "duration": self.request.duration,
            "duration_seconds": self.request.duration,
            "references": [
                dict(ref) if isinstance(ref, Mapping) else {"ref": str(ref)}
                for ref in self.request.references
            ],
            "project_id": self.preparation.project_id,
            "quoted_ceiling": self.ceiling,
        }

    @property
    def guard_scope(self) -> dict[str, Any]:
        """Scope restricted to the guard's allowlist, None values dropped."""
        resolution = self.request.resolution or self.request.resolution_type
        candidate = {
            "count": self.request.count if self.request.count is not None else 1,
            "model": self.request.model,
            "resolution": resolution,
            "ratio": self.request.ratio,
            "duration_seconds": self.request.duration,
        }
        return {k: v for k, v in candidate.items() if v is not None}


class CanvasSubmissionService:
    """Quote-bound submission over :class:`CanvasExecutor`."""

    def __init__(
        self,
        executor: CanvasExecutor,
        *,
        ledger_dir: str | Path | None = None,
    ) -> None:
        self._executor = executor
        self._ledger_dir = Path(ledger_dir) if ledger_dir is not None else None
        self._ledger = (
            OperationLedger(root=self._ledger_dir)
            if self._ledger_dir is not None
            else None
        )

    @property
    def ledger_dir(self) -> Path:
        return self._ledger_dir

    def quote(self, request: CanvasExecutionRequest) -> CanvasQuoteContext:
        """Run every free step and bind the live quote to a fingerprint.

        Nothing here spends credits, so the caller's approval receipt is
        always recorded against work that was actually quoted.
        """
        preparation = self._executor.prepare(request)
        quote = preparation.quote
        ceiling = quoted_ceiling(quote)
        # The receipt scope stays inside the guard's allowlist; the richer
        # Canvas binding (media/mode/project_id/ceiling) lives in the
        # fingerprint, which the guard verifies against the same object.
        scope: dict[str, Any] = dict()
        context = CanvasQuoteContext(
            preparation=preparation,
            request=request,
            fingerprint="",
            scope={},
            ceiling=ceiling,
        )
        # Derive the digest from the approval request object itself so the
        # consume side (which re-derives from the same object) cannot drift.
        fingerprint = build_canvas_approval_fingerprint(context.approval_request)
        return CanvasQuoteContext(
            preparation=preparation,
            request=request,
            fingerprint=fingerprint,
            scope=context.guard_scope,
            ceiling=ceiling,
        )

    def submit(
        self,
        context: CanvasQuoteContext,
        *,
        approval_guard: Any,
        session_id: str,
        approval_id: str,
        credit_ceiling: int | None = None,
    ) -> dict[str, Any]:
        """Consume the bound approval, then continue the paid chain."""
        ceiling = credit_ceiling if credit_ceiling is not None else context.ceiling
        if ceiling is None:
            raise RuntimeError(
                "live quote carried no credit ceiling; a bound approval is required "
                "before node confirm"
            )
        approval_guard.consume_approval(
            session_id,
            request=context.approval_request,
            approval_id=approval_id,
            fingerprint_builder=build_canvas_approval_fingerprint,
        )
        if self._ledger is not None:
            self._ledger.begin_submission(
                session_id=session_id,
                mode=f"canvas-{context.request.mode}",
                request_fingerprint=context.fingerprint,
            )
        try:
            submission = self._executor.run(
                context.preparation, credit_ceiling=ceiling
            )
        except Exception as exc:
            if self._ledger is not None:
                # The intent is closed with the outcome recorded either way,
                # so a lost response can be queried instead of resubmitted.
                self._ledger.complete_submission_intent(
                    request_fingerprint=context.fingerprint,
                    submit_id=None,
                    error_code=type(exc).__name__,
                )
            raise
        if self._ledger is not None:
            self._ledger.complete_submission_intent(
                request_fingerprint=context.fingerprint,
                submit_id=submission.submit_id,
            )
        return {
            "runtime": "canvas",
            "submit_id": submission.submit_id,
            "project_id": submission.project_id,
            "node_id": submission.node_id,
            "run_exit_code": submission.run.exit_code,
            "run_state": submission.run.data.get("state", submission.state),
            "quoted_ceiling": ceiling,
            "created_canvas": context.preparation.created_canvas,
            "request_fingerprint": context.fingerprint,
        }


__all__ = [
    "LEDGER_SCHEMA",
    "CanvasQuoteContext",
    "CanvasSubmissionService",
    "build_canvas_approval_fingerprint",
    "quoted_ceiling",
]
