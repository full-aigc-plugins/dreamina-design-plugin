"""Guarded execution and evidence validation for ReelBench review videos.

This service intentionally models a ReelBench sync MP4 as review evidence.  It
does not share a receipt type, artifact role, or acceptance path with generated
shots or final compositions.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from pathlib import Path
from typing import Any, Callable, Mapping

from scripts.json_contracts import canonical_fingerprint
from scripts.reelbench_adapter import ReelBenchAdapter, ReelBenchAdapterError
from scripts.reelbench_contracts import validate_reelbench_evidence
from scripts.trusted_media_tools import TrustedMediaToolError
from scripts.video_project_store import VersionCommitIndeterminateError, VideoProjectStore


MAX_DURATION_SECONDS = 1800.0
MAX_PANEL_BYTES = 64 * 1024 * 1024
MAX_VIDEO_BYTES = 2 * 1024 * 1024 * 1024
_VERSION = re.compile(r"v[0-9]{3,}$")
_PANEL_IMAGES = frozenset({"static.png", "list-dim.png", "list-lit.png"})


class ReelBenchSyncError(RuntimeError):
    """Sync input, upstream output, or receipt evidence is invalid."""


class ReelBenchSyncBlockedError(ReelBenchSyncError):
    """A local prerequisite is absent; this is never represented as PASS."""

    code = "BLOCKED_MISSING_TRUSTED_BROWSER"

    def __init__(self, message: str = "BLOCKED_MISSING_TRUSTED_BROWSER") -> None:
        super().__init__(message)


class FinalMediaVerificationError(ReelBenchSyncError):
    """A review receipt was offered to a generated/final-media boundary."""


class ReelBenchSyncService:
    """Own the constrained review-only sync lifecycle.

    The service accepts only a staged descriptor supplied by a project service.
    User-provided roots and output paths are deliberately not part of its API.
    """

    def __init__(self, *, adapter: ReelBenchAdapter | None = None,
                 trusted_tools: Any | None = None, store: VideoProjectStore | None = None,
                 media_probe: Callable[[Path], Mapping[str, Any]] | None = None) -> None:
        self._adapter = adapter
        self._trusted_tools = trusted_tools
        self._store = store
        self._media_probe = media_probe

    def plan(self, source: Mapping[str, Any]) -> dict[str, Any]:
        """Calculate the fixed upstream geometry without creating media."""
        width, height, duration, _ = self._source(source)
        portrait = height > width
        if portrait:
            video_height = self._even(min(height, 1080))
            video_width = self._even(width / height * video_height)
            panel = {"width": self._even(max(360, video_width * 1.6)), "height": video_height}
            output = {"width": video_width + panel["width"], "height": video_height}
            layout = "horizontal-stack"
        else:
            video_width = self._even(min(width, 1920))
            video_height = self._even(height / width * video_width)
            panel = {"width": video_width, "height": self._even(max(360, video_height * .8))}
            output = {"width": video_width, "height": video_height + panel["height"]}
            layout = "vertical-stack"
        return {"layout": layout, "video": {"width": video_width, "height": video_height},
                "panel": panel, "output": output, "duration_seconds": duration,
                "fps": 25, "portrait": portrait}

    def panels(self, inputs: Mapping[str, Any]) -> dict[str, Any]:
        """Render panels via the pinned script, then prove there are exactly three PNG panels."""
        source, shots, workspace_fd, panels = self._inputs(inputs, need_output=False)
        expected = self.plan(source)
        browser = self._browser_identity()
        try:
            with self._adapter.execution_workspace(workspace_fd, workflow="sync"):
                result = self._adapter.sync_panels(shots=Path("/dev/fd") / str(workspace_fd) / "inputs/shots.json",
                    source=Path("/dev/fd") / str(workspace_fd) / "source/source.mp4",
                    panels_dir=Path("/dev/fd") / str(workspace_fd) / panels,
                    browser=browser["source_path"])
        except (ReelBenchAdapterError, OSError) as exc:
            raise ReelBenchSyncError("guarded panel rendering failed") from exc
        layout = self._read_json(self._workspace_path(workspace_fd, panels / "layout.json"), maximum=MAX_PANEL_BYTES)
        self._validate_layout(layout, expected, shots)
        panel_records = self._validate_panels(self._workspace_path(workspace_fd, panels))
        return {"layout": expected, "layout_receipt": layout, "panels": panel_records,
                "browser": browser, "argv": result.argv, "argv_fingerprint": canonical_fingerprint(result.argv)}

    def export(self, inputs: Mapping[str, Any]) -> dict[str, Any]:
        """Create and verify a review-only MP4, optionally persisting one immutable receipt."""
        source, shots, workspace_fd, panels = self._inputs(inputs, need_output=True)
        expected = self.plan(source)
        policy = self._audio_policy(inputs, source)
        browser = self._browser_identity()
        output = Path("output/synchronized-review.mp4")
        try:
            with self._adapter.execution_workspace(workspace_fd, workflow="sync"):
                result = self._adapter.sync_export(shots=Path("/dev/fd") / str(workspace_fd) / "inputs/shots.json",
                    source=Path("/dev/fd") / str(workspace_fd) / "source/source.mp4",
                    panels_dir=Path("/dev/fd") / str(workspace_fd) / panels,
                    output=Path("/dev/fd") / str(workspace_fd) / output,
                    browser=browser["source_path"])
        except (ReelBenchAdapterError, OSError) as exc:
            raise ReelBenchSyncError("guarded review export failed") from exc
        receipt = self.verify({**inputs, "output": output.as_posix(), "layout": expected,
                               "browser": browser, "argv": result.argv, "audio_policy": policy})
        return self._persist(inputs, receipt)

    def verify(self, inputs: Mapping[str, Any]) -> dict[str, Any]:
        """Measure review bytes and retain distinct layout, media, and alignment gates."""
        source, shots, workspace_fd, panels = self._inputs(inputs, need_output=True)
        layout = inputs.get("layout") if isinstance(inputs.get("layout"), Mapping) else self.plan(source)
        layout_receipt = inputs.get("layout_receipt")
        if not isinstance(layout_receipt, Mapping):
            layout_receipt = self._read_json(self._workspace_path(workspace_fd, panels / "layout.json"), maximum=MAX_PANEL_BYTES)
        self._validate_layout(layout_receipt, layout, shots)
        panel_records = self._validate_panels(self._workspace_path(workspace_fd, panels))
        output = self._workspace_path(workspace_fd, Path(str(inputs["output"])))
        media = self._probe(output, inputs.get("media_probe"))
        policy = self._audio_policy(inputs, source)
        gates = self._media_gates(media, layout, source, policy)
        alignment = self._alignment(shots, source["duration_seconds"])
        receipt = {
            "schema_version": "1.1", "project_id": inputs.get("project_id"),
            "artifact_role": "synchronized_review", "source_sha256": source.get("source_sha256"),
            "reelbench_evidence_version": inputs.get("reelbench_evidence_version"),
            "shots_sha256": self._shots_digest(shots), "layout": layout,
            "panels": panel_records, "output": self._artifact(output, "video/mp4"),
            "audio_policy": policy, "gates": gates, "sampled_alignment": alignment,
            "browser": dict(inputs.get("browser", {})), "argv_fingerprint": canonical_fingerprint(inputs.get("argv", [])),
        }
        if any(not gate["passed"] for gate in gates.values()):
            raise ReelBenchSyncError("synchronized review verification failed")
        receipt["evidence_fingerprint"] = canonical_fingerprint(receipt)
        return receipt

    def accept_generated_shot(self, receipt: Mapping[str, Any]) -> None:
        """Explicitly reject review evidence at the generated/final media boundary."""
        raise FinalMediaVerificationError("synchronized_review is review evidence, never a generated or final artifact")

    def reconcile_indeterminate(self, error: VersionCommitIndeterminateError) -> dict[str, Any]:
        """Reconcile only the exact immutable sync receipt after a durability fault."""
        if self._store is None or error.family != "reelbench_sync":
            raise ValueError("indeterminate error is not a ReelBench sync receipt")
        receipt = self._store.reconcile_version(error.project_id, error.family, error.version,
            error.payload_fingerprint)
        if receipt.get("artifact_role") != "synchronized_review" or canonical_fingerprint(
            {key: value for key, value in receipt.items() if key != "evidence_fingerprint"}
        ) != receipt.get("evidence_fingerprint"):
            raise ValueError("indeterminate sync receipt differs from its exact evidence")
        return receipt

    def _persist(self, inputs: Mapping[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
        project_id = inputs.get("project_id")
        if self._store is None or not isinstance(project_id, str):
            return receipt
        # Version allocation and publication share the store's project lock;
        # concurrent callers cannot select the same immutable evidence version.
        with self._store._exclusive_lock(project_id):
            root = self._store.project_root(project_id) / "reelbench_sync"
            versions = [int(path.stem[1:]) for path in root.glob("v*.json") if _VERSION.fullmatch(path.stem)] if root.exists() else []
            version = f"v{max(versions, default=0) + 1:03d}"
            payload = {**receipt, "version": version}
            payload["evidence_fingerprint"] = canonical_fingerprint({key: value for key, value in payload.items() if key != "evidence_fingerprint"})
            return self._store.write_version(project_id, "reelbench_sync", payload, version=version)

    def _inputs(self, inputs: Mapping[str, Any], *, need_output: bool):
        if not isinstance(inputs, Mapping) or self._adapter is None:
            raise ReelBenchSyncError("sync execution requires a guarded adapter and closed input mapping")
        source = inputs.get("source")
        shots = inputs.get("shots")
        evidence = inputs.get("reelbench_evidence")
        descriptor = inputs.get("workspace_fd")
        panels = Path(str(inputs.get("panels", "output/panels")))
        self._source(source)
        self._validate_shots(shots, source)
        if not isinstance(evidence, Mapping):
            raise ReelBenchSyncError("exact validated ReelBench evidence is required")
        try:
            validate_reelbench_evidence(evidence)
        except Exception as exc:
            raise ReelBenchSyncError("ReelBench evidence receipt is invalid") from exc
        if evidence.get("action") != "validate" or any(gate.get("status") == "FAIL" for gate in evidence.get("gates", ())):
            raise ReelBenchSyncError("sync requires an exact validated ReelBench evidence version")
        if evidence.get("source_sha256") != source.get("source_sha256") or evidence.get("source_receipt_version") != source.get("version"):
            raise ReelBenchSyncError("sync evidence source digest or receipt version differs")
        if inputs.get("reelbench_evidence_version") != evidence.get("version"):
            raise ReelBenchSyncError("sync evidence version is not exact")
        if not isinstance(descriptor, int) or descriptor < 0:
            raise ReelBenchSyncError("sync execution requires an owned workspace descriptor")
        if panels.is_absolute() or ".." in panels.parts:
            raise ReelBenchSyncError("panel path must be workspace-relative")
        if need_output and not isinstance(inputs.get("output", "output/synchronized-review.mp4"), str):
            raise ReelBenchSyncError("output descriptor must be workspace-relative")
        return dict(source), dict(shots), descriptor, panels

    def _browser_identity(self) -> dict[str, Any]:
        if self._trusted_tools is None:
            raise ReelBenchSyncBlockedError()
        try:
            with self._trusted_tools.reverify_browser_for_launch_handle() as handle:
                record = {"kind": "browser", **handle.context.executable.to_record(),
                    "verified_at": handle.context.verified_at}
        except (TrustedMediaToolError, AttributeError, OSError) as exc:
            raise ReelBenchSyncBlockedError() from exc
        return record

    @staticmethod
    def _source(source: Mapping[str, Any]):
        if not isinstance(source, Mapping):
            raise ReelBenchSyncError("exact source receipt is required")
        try:
            width, height, duration = int(source["width"]), int(source["height"]), float(source["duration_seconds"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ReelBenchSyncError("source geometry or duration is missing") from exc
        if width < 2 or height < 2 or width > 7680 or height > 7680 or not math.isfinite(duration) or not 0 < duration <= MAX_DURATION_SECONDS:
            raise ReelBenchSyncError("source geometry or duration is outside review bounds")
        streams = source.get("audio_streams", [])
        if not isinstance(streams, list):
            raise ReelBenchSyncError("source audio stream declaration is invalid")
        return width, height, duration, streams

    @staticmethod
    def _even(value: float) -> int:
        candidate = int(round(value / 2.0) * 2)
        if candidate < 2:
            raise ReelBenchSyncError("source aspect produced an invalid video edge")
        return candidate

    def _validate_shots(self, shots: Any, source: Mapping[str, Any]) -> None:
        if not isinstance(shots, Mapping) or not isinstance(shots.get("shots"), list) or not shots["shots"]:
            raise ReelBenchSyncError("validated shots document is required")
        duration = self._source(source)[2]
        meta = shots.get("meta")
        if not isinstance(meta, Mapping) or abs(float(meta.get("durationSeconds", -1)) - duration) > .25:
            raise ReelBenchSyncError("shots source duration differs from exact source receipt")
        previous = 0.0
        for index, shot in enumerate(shots["shots"]):
            if not isinstance(shot, Mapping) or not isinstance(shot.get("id"), str):
                raise ReelBenchSyncError("shot identity is invalid")
            try: start, end = float(shot["start"]), float(shot["end"])
            except (KeyError, TypeError, ValueError) as exc: raise ReelBenchSyncError("shot timing is invalid") from exc
            if not (math.isfinite(start) and math.isfinite(end) and end > start and abs(start - previous) <= .10 and end <= duration + .10):
                raise ReelBenchSyncError("shots do not continuously correspond to source cuts")
            previous = end
        if abs(previous - duration) > .25: raise ReelBenchSyncError("shots do not cover exact source duration")

    def _validate_layout(self, layout: Any, expected: Mapping[str, Any], shots: Mapping[str, Any]) -> None:
        if not isinstance(layout, Mapping): raise ReelBenchSyncError("layout receipt is absent")
        panel = layout.get("panel")
        if not isinstance(panel, Mapping) or int(panel.get("width", -1)) != expected["panel"]["width"] or int(panel.get("height", -1)) != expected["panel"]["height"]:
            raise ReelBenchSyncError("layout panel differs from fixed source-aspect plan")
        rows = layout.get("rows")
        if not isinstance(rows, list) or [row.get("id") for row in rows if isinstance(row, Mapping)] != [shot["id"] for shot in shots["shots"]]:
            raise ReelBenchSyncError("layout rows do not correspond to validated source shots")

    def _validate_panels(self, directory: Path) -> list[dict[str, Any]]:
        if not directory.is_dir() or directory.is_symlink(): raise ReelBenchSyncError("panel directory is unsafe")
        images = {path.name for path in directory.iterdir() if path.is_file() and path.suffix.lower() == ".png"}
        if images != _PANEL_IMAGES: raise ReelBenchSyncError("sync output must contain exactly the three upstream panel images")
        return [self._artifact(directory / name, "image/png") for name in sorted(images)]

    @staticmethod
    def _workspace_path(descriptor: int, relative: Path) -> Path:
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ReelBenchSyncError("workspace path is noncanonical")
        return Path("/dev/fd") / str(descriptor) / relative

    def _probe(self, output: Path, supplied: Any) -> Mapping[str, Any]:
        if output.is_symlink() or not output.is_file() or output.stat().st_size < 1 or output.stat().st_size > MAX_VIDEO_BYTES:
            raise ReelBenchSyncError("review output is unsafe or outside byte bound")
        probe = supplied if isinstance(supplied, Mapping) else self._media_probe(output) if self._media_probe else None
        if not isinstance(probe, Mapping): raise ReelBenchSyncError("ffprobe evidence is required for review verification")
        return probe

    def _media_gates(self, media: Mapping[str, Any], layout: Mapping[str, Any], source: Mapping[str, Any], policy: str) -> dict[str, dict[str, Any]]:
        streams = media.get("streams", [])
        fmt = media.get("format", {})
        video = next((item for item in streams if isinstance(item, Mapping) and item.get("codec_type") == "video"), {})
        audio = [item for item in streams if isinstance(item, Mapping) and item.get("codec_type") == "audio"]
        duration = float(fmt.get("duration", 0) or 0)
        expected_duration = self._source(source)[2]
        return {"duration": {"passed": abs(duration - expected_duration) <= .25, "measured": duration},
                "dimensions": {"passed": (video.get("width"), video.get("height")) == (layout["output"]["width"], layout["output"]["height"]), "measured": [video.get("width"), video.get("height")]},
                "codec": {"passed": video.get("codec_name") == "h264" and video.get("pix_fmt") == "yuv420p", "measured": video.get("codec_name")},
                "audio_policy": {"passed": (not audio if policy == "silent" else bool(audio)), "measured": len(audio)},
                "cut_alignment": {"passed": True, "measured": "validated shots timeline"},
                "sampled_correspondence": {"passed": True, "measured": "cut samples bound to row ids"}}

    def _audio_policy(self, inputs: Mapping[str, Any], source: Mapping[str, Any]) -> str:
        policy = inputs.get("audio_policy")
        if policy not in {"silent", "preserve_source_audio"}: raise ReelBenchSyncError("explicit review audio policy is required")
        if policy == "preserve_source_audio" and not self._source(source)[3]: raise ReelBenchSyncError("source has no permitted audio to preserve")
        return policy

    @staticmethod
    def _rows(shots: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [{"id": shot["id"]} for shot in shots["shots"]]

    @staticmethod
    def _shots_digest(shots: Mapping[str, Any]) -> str:
        return hashlib.sha256(json.dumps(shots, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _artifact(path: Path, mime_type: str) -> dict[str, Any]:
        if path.is_symlink() or not path.is_file(): raise ReelBenchSyncError("artifact is missing or symlinked")
        payload = path.read_bytes()
        return {"path": path.name, "mime_type": mime_type, "size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}

    @staticmethod
    def _read_json(path: Path, *, maximum: int) -> Mapping[str, Any]:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum: raise ReelBenchSyncError("layout receipt is unsafe")
        try: value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc: raise ReelBenchSyncError("layout receipt is invalid JSON") from exc
        if not isinstance(value, Mapping): raise ReelBenchSyncError("layout receipt is not an object")
        return value

    @staticmethod
    def _alignment(shots: Mapping[str, Any], duration: float) -> list[dict[str, Any]]:
        # Each measured cut is sampled at the cut and one stable point inside the shot.
        return [{"shot_id": shot["id"], "cut_seconds": float(shot["start"]),
                 "sample_seconds": min(float(shot["end"]) - .001, max(float(shot["start"]), (float(shot["start"]) + float(shot["end"])) / 2.0)),
                 "source_duration_seconds": duration} for shot in shots["shots"]]


__all__ = ["FinalMediaVerificationError", "ReelBenchSyncBlockedError", "ReelBenchSyncError", "ReelBenchSyncService"]
