"""Canvas execution layer: turn a Dreamina request into a Canvas submission.

This module owns the parts of the Canvas flow that the frozen
``dreamina`` CLI never had — live discovery, a canvas to write into,
material upload, and the quote→confirm→run gate — while keeping the
identity model this plugin already uses.

Identity
--------
``session_id`` stays the approval/ledger correlation key everywhere else
in the plugin. It is resolved to a ``projectId`` *only here*, at the
execution boundary, because Canvas has no session concept:

1. the caller supplies ``project_id`` → used as-is, no remote write;
2. otherwise a canvas is created **only after explicit authorization**;
3. a canvas is never switched implicitly, and ``canvas ls`` is read-only.

Ordering
--------
``prepare`` performs every free or read-only step and stops at the quote,
so the caller can bind its existing ``ApprovalGuard`` receipt to the live
amount. ``run`` then continues the paid chain with a stable ``submitId``:

    prepare()  : preflight → discovery → canvas → upload → draft → quote
    run()      : confirm(credit_ceiling) → run → (caller waits) → download

The split is deliberate: nothing in ``prepare`` spends credits, so an
approval can never be consumed for work that was never quoted.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.dreamina_canvas_adapter import (
    CanvasCommandError,
    CanvasResult,
    DreaminaCanvasAdapter,
)

# Legacy request modes and the Canvas public mode each one maps to.
IMAGE_MODE_MAP = {"text2image": "t2i", "image2image": "i2i"}
VIDEO_MODE_MAP = {
    "text2video": "t2v",
    # Canvas has no i2v: single-image and multimodal guidance are m2v.
    "image2video": "m2v",
    "multimodal2video": "m2v",
    "multiframe2video": "m2v",
    # Two ordered frames are first_last_frame.
    "frames2video": "first_last_frame",
}
UPSCALE_MODE = "image_upscale"

# Request fields that exist on the legacy CLI and have no Canvas
# equivalent. They are rejected instead of silently dropped.
UNSUPPORTED_IMAGE_FIELDS = ("width", "height")


class CanvasAuthorizationRequired(RuntimeError):
    """A canvas would have to be created without explicit authorization."""


class CanvasModeUnsupported(RuntimeError):
    """The requested mode has no Canvas equivalent."""


class CanvasReferenceError(RuntimeError):
    """A reference could not be turned into a Canvas reference."""


@dataclass(frozen=True)
class CanvasExecutionRequest:
    """A request in this plugin's existing vocabulary."""

    media: str
    mode: str
    prompt: str = ""
    model: str | None = None
    ratio: str | None = None
    resolution: str | None = None
    resolution_type: str | None = None
    count: int | None = None
    duration: int | None = None
    references: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    session_id: str | None = None
    project_id: str | None = None
    canvas_name: str | None = None
    title: str | None = None

    def unsupported_fields(self) -> tuple[str, ...]:
        if self.media != "image":
            return ()
        return tuple(
            name
            for name in UNSUPPORTED_IMAGE_FIELDS
            if getattr(self, name, None) is not None
        )


@dataclass(frozen=True)
class CanvasPreparation:
    """Everything ``run`` needs, produced without spending credits."""

    project_id: str
    node_id: str
    submit_id: str
    quote: Mapping[str, Any]
    resource_ids: tuple[str, ...] = ()
    created_canvas: bool = False


@dataclass(frozen=True)
class CanvasSubmission:
    """The outcome of the paid chain."""

    project_id: str
    node_id: str
    submit_id: str
    run: CanvasResult
    state: str = "submitted"


def _new_submit_id() -> str:
    return str(uuid.uuid4()).lower()


def _reference_value(reference: Mapping[str, Any] | str) -> str:
    if isinstance(reference, str):
        return reference
    for key in ("ref", "value"):
        value = reference.get(key)
        if isinstance(value, str) and value:
            return value
    path = reference.get("path")
    if isinstance(path, str) and path:
        return path
    raise CanvasReferenceError(
        f"reference has neither a Canvas ref nor a local path: {reference!r}"
    )


class CanvasExecutor:
    """Drives a request through the Canvas draft → quote → run chain."""

    def __init__(
        self,
        adapter: DreaminaCanvasAdapter,
        *,
        authorize_canvas_creation: Callable[[str], bool] | None = None,
    ) -> None:
        self._adapter = adapter
        self._authorize = authorize_canvas_creation

    # -- canvas ------------------------------------------------------------

    def resolve_canvas(
        self, request: CanvasExecutionRequest
    ) -> tuple[str, bool]:
        """Return ``(project_id, created)`` without ever switching silently."""
        if request.project_id:
            return request.project_id, False
        name = request.canvas_name
        if not name:
            raise CanvasAuthorizationRequired(
                "no canvas available: pass project_id, or canvas_name together "
                "with authorization to create one (canvas create is a remote write)"
            )
        approved = self._authorize(name) if self._authorize is not None else False
        if not approved:
            raise CanvasAuthorizationRequired(
                f"creating canvas {name!r} requires explicit authorization"
            )
        result = self._adapter.canvas_create(name, use=True)
        project_id = result.project_id
        if not project_id:
            raise CanvasCommandError(
                "canvas create did not return a projectId", outcome_ambiguous=False
            )
        return project_id, True

    # -- material ----------------------------------------------------------

    def _ingest_references(self, references: Sequence[Mapping[str, Any] | str]) -> list[str]:
        """Turn references into Canvas refs, uploading local material."""
        resolved: list[str] = []
        for reference in references:
            value = _reference_value(reference)
            if value.startswith(("node:", "res:")):
                resolved.append(value)
                continue
            if not Path(value).is_file():
                raise CanvasReferenceError(f"local material not found: {value}")
            resolved.append(f"res:{self._adapter.resource_upload(value)}")
        return resolved

    # -- discovery ---------------------------------------------------------

    def _check_model_supported(self, media_type: str, model: str | None) -> None:
        """Warn-free guard: an unknown model must not reach the CLI."""
        if not model:
            return
        try:
            listing = self._adapter.model_list(media_type)
        except CanvasCommandError:
            return
        if not _model_known(listing, model):
            raise CanvasModeUnsupported(
                f"model {model!r} is not in the live {media_type} discovery "
                "payload; re-run discovery instead of guessing a token"
            )

    # -- preparation -------------------------------------------------------

    def prepare(self, request: CanvasExecutionRequest) -> CanvasPreparation:
        """Run every free/read-only step and stop at the live quote."""
        self._adapter.preflight()
        if request.media == "image":
            self._check_model_supported("image", request.model)
        elif request.media == "video":
            self._check_model_supported("video", request.model)
        else:
            raise CanvasModeUnsupported(f"unsupported media: {request.media}")

        project_id, created = self.resolve_canvas(request)
        resource_ids: list[str] = []
        if request.mode == UPSCALE_MODE:
            # Upscale works on an existing image node and is priced
            # separately; the caller supplies the node through references.
            node_refs = self._ingest_references(request.references)
            resource_ids = [ref.split(":", 1)[1] for ref in node_refs]
            node_id = node_refs[0].split(":", 1)[1] if node_refs else ""
            quote = self._adapter.node_upscale(
                node_id=node_id, project_id=project_id, dry_run=True
            ).data
        else:
            refs = self._ingest_references(request.references)
            resource_ids = [
                ref.split(":", 1)[1] for ref in refs if ref.startswith("res:")
            ]
            node_id = self._create_node(request, project_id, refs)
            quote = self._adapter.node_quote(node_id, project_id)

        return CanvasPreparation(
            project_id=project_id,
            node_id=node_id,
            submit_id=_new_submit_id(),
            quote=quote,
            resource_ids=tuple(resource_ids),
            created_canvas=created,
        )

    def _create_node(
        self, request: CanvasExecutionRequest, project_id: str, refs: list[str]
    ) -> str:
        resolution = request.resolution or request.resolution_type
        if request.media == "image":
            mode = IMAGE_MODE_MAP.get(request.mode)
            if mode is None:
                raise CanvasModeUnsupported(
                    f"image mode {request.mode!r} has no Canvas equivalent"
                )
            return self._adapter.node_create_image(
                project_id=project_id,
                prompt=request.prompt,
                mode=mode,
                model=request.model,
                title=request.title,
                ratio=request.ratio,
                resolution=resolution,
                count=request.count,
                refs=refs,
            )
        mode = VIDEO_MODE_MAP.get(request.mode)
        if mode is None:
            raise CanvasModeUnsupported(
                f"video mode {request.mode!r} has no Canvas equivalent "
                "(Canvas has no i2v or multi_modal mode)"
            )
        if mode == "first_last_frame" and len(refs) != 2:
            raise CanvasReferenceError(
                "first_last_frame needs exactly two ordered frames "
                f"(got {len(refs)})"
            )
        return self._adapter.node_create_video(
            project_id=project_id,
            prompt=request.prompt,
            mode=mode,
            model=request.model,
            title=request.title,
            ratio=request.ratio,
            resolution=resolution,
            duration=request.duration,
            count=request.count,
            refs=refs,
        )

    # -- paid chain --------------------------------------------------------

    def run(
        self, preparation: CanvasPreparation, *, credit_ceiling: int | None = None
    ) -> CanvasSubmission:
        """Continue the paid chain with the prepared stable submit id."""
        if preparation.node_id == "":
            raise CanvasModeUnsupported(
                "upscale needs an existing image node id in references"
            )
        if credit_ceiling is None:
            raise CanvasAuthorizationRequired(
                "a credit ceiling approved against the live quote is required "
                "before node confirm"
            )
        if preparation.quote and not _quote_confirmable(preparation.quote):
            raise CanvasCommandError(
                "live quote is not confirmable; surface the quote to the user "
                "before submitting",
                outcome_ambiguous=False,
            )
        self._adapter.node_confirm(
            node_id=preparation.node_id,
            project_id=preparation.project_id,
            submit_id=preparation.submit_id,
            credit_ceiling=credit_ceiling,
        )
        run = self._adapter.node_run(
            node_id=preparation.node_id,
            project_id=preparation.project_id,
            submit_id=preparation.submit_id,
        )
        return CanvasSubmission(
            project_id=preparation.project_id,
            node_id=preparation.node_id,
            submit_id=run.submit_id or preparation.submit_id,
            run=run,
        )

    def wait(self, submission: CanvasSubmission, *, timeout: str = "10m",
             interval: str = "5s") -> CanvasResult:
        return self._adapter.operation_wait(
            submission.submit_id, submission.project_id,
            timeout=timeout, interval=interval,
        )

    def download(
        self, submission: CanvasSubmission, resource_id: str, output_dir: str | Path
    ) -> Mapping[str, Any]:
        return self._adapter.resource_download(
            resource_id, submission.project_id, output_dir
        )


def _model_known(listing: Any, model: str) -> bool:
    """True when ``model`` appears in a discovery payload."""
    if isinstance(listing, Mapping):
        for key in ("items", "models", "data", "nodes"):
            value = listing.get(key)
            if isinstance(value, list):
                listing = value
                break
        else:
            listing = [listing] if "model" in listing else []
    if not isinstance(listing, list):
        return True  # Unknown shape: do not block on a guess.
    return any(
        isinstance(entry, Mapping)
        and (entry.get("model") or entry.get("name")) == model
        for entry in listing
    )


def _quote_confirmable(quote: Mapping[str, Any]) -> bool:
    """Mirror the guide: a quote is confirmable unless it says otherwise."""
    if not quote:
        return False
    if "confirmable" in quote:
        return bool(quote["confirmable"])
    for key in ("confirmationRequired", "requiresConfirmation"):
        if key in quote:
            return not bool(quote[key])
    return any(key in quote for key in ("node", "items", "totalMaxCredits"))


__all__ = [
    "IMAGE_MODE_MAP",
    "UPSCALE_MODE",
    "VIDEO_MODE_MAP",
    "CanvasAuthorizationRequired",
    "CanvasExecutionRequest",
    "CanvasExecutor",
    "CanvasModeUnsupported",
    "CanvasPreparation",
    "CanvasReferenceError",
    "CanvasSubmission",
]
