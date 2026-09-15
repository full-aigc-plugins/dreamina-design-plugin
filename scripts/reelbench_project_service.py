"""Immutable project evidence produced entirely within a pinned action workspace."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import stat
import fcntl
import threading
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from scripts import reelbench_workspace as ws
from scripts.json_contracts import canonical_fingerprint, validate_contract
from scripts.reelbench_adapter import ReelBenchAdapter
from scripts.reelbench_contracts import validate_reelbench_comparison, validate_reelbench_evidence
from scripts.reference_video_service import ReferenceVideoService
from scripts.video_project_store import VersionCommitIndeterminateError, VideoProjectStore
from scripts.reelbench_operation import OperationJournal

# Two frames per shot plus ceil(shots / 25) sheets per pick fit 256 artifacts.
MAX_SHOTS = 120
MAX_SOURCE_BYTES = 2 * 1024 * 1024 * 1024
MAX_DOCUMENT_BYTES = ws.MAX_FILE_BYTES
MAX_REPORT_BYTES = 4 * 1024 * 1024
_VERSION = re.compile(r"v[0-9]{3,}")
_MIME = {".json": "application/json", ".md": "text/markdown", ".html": "text/html", ".jpg": "image/jpeg"}
_ANCESTRY_LOCK = threading.RLock()
_COMPARISON_TOLERANCES = {
    "duration_seconds": 0.25,
    "boundary_seconds": 0.10,
    "motion_abs_delta": 0.50,
}


class ReelBenchProjectIdentityError(ValueError):
    """The project entry no longer names the locked project inode."""


class ReelBenchProjectService:
    """Serialize an action on the project directory inode, never a replaceable lockfile."""

    def __init__(self, store: VideoProjectStore, adapter: ReelBenchAdapter | None) -> None:
        self._store = store
        self._adapter = adapter

    def run(self, project_id: str, *, action: str, expected_parent: str | None,
            source_receipt_version: str, **options: Any) -> dict[str, Any]:
        if self._adapter is None:
            raise RuntimeError("an adapter is required to execute ReelBench actions")
        if action not in {"seed", "evidence", "validate", "render"}:
            raise ValueError("unsupported ReelBench action")
        self._version(expected_parent, nullable=True)
        self._version(source_receipt_version)
        result = None
        try:
            with self._locked_project(project_id) as (store_fd, descriptor, root, guard):
                journal = OperationJournal(store_fd, descriptor, project_id, root)
                journal.recover(self, guard)
                result = self._execute(descriptor, root, project_id, action, expected_parent,
                                       source_receipt_version, options, guard, journal)
                guard()
            return result
        except BaseException as exc:
            if result is not None and not isinstance(exc, VersionCommitIndeterminateError):
                raise VersionCommitIndeterminateError(project_id=project_id, family="reelbench_evidence",
                    version=result["version"], path=root / "reelbench_evidence" / f"{result['version']}.json",
                    payload_fingerprint=canonical_fingerprint(result)) from exc
            raise

    def compare_native(
        self,
        project_id: str,
        analysis_version: str,
        reelbench_version: str,
    ) -> dict[str, Any]:
        """Persist one immutable, read-only comparison of exact native and ReelBench versions."""
        self._version(analysis_version)
        self._version(reelbench_version)
        with self._locked_project(project_id) as (_store_fd, project_fd, _root, guard):
            validated = self._validate_comparison_locked(project_fd, project_id, analysis_version, reelbench_version)
            analysis, evidence, domains, mismatches = (validated[key] for key in ("analysis", "evidence", "domains", "mismatches"))
            receipt = {
                "schema_version": "1.1",
                "version": self._next_version(project_fd, "reelbench_comparison"),
                "project_id": project_id,
                "source_sha256": analysis["source"]["source_sha256"],
                "native_analysis_version": analysis_version,
                "native_analysis_fingerprint": analysis["machine_fingerprint"],
                "reelbench_evidence_version": reelbench_version,
                "reelbench_evidence_fingerprint": evidence["evidence_fingerprint"],
                "tolerances": dict(_COMPARISON_TOLERANCES),
                "domains": domains,
                "mismatches": mismatches,
                "overall": "manual_review" if any(
                    domain["verdict"] == "manual_review" for domain in domains.values()
                ) else "matched",
                "compared_at": _now(),
            }
            receipt["comparison_fingerprint"] = canonical_fingerprint(receipt)
            validate_reelbench_comparison(receipt)
            persisted = self._store.write_version(
                project_id,
                "reelbench_comparison",
                receipt,
                schema_name="reelbench_comparison.schema.json",
                version=receipt["version"],
                project_fd=project_fd,
                publication_guard=guard,
            )
            validate_reelbench_comparison(persisted)
            guard()
            return persisted

    def validate_comparison_against_inputs(self, project_id: str, analysis_version: str,
                                          reelbench_version: str, comparison: dict[str, Any] | None = None) -> dict[str, Any]:
        """Reload and recompute every comparison input; optionally prove exact receipt equality."""
        self._version(analysis_version)
        self._version(reelbench_version)
        with self._locked_project(project_id) as (_store_fd, project_fd, _root, guard):
            result = self._validate_comparison_locked(project_fd, project_id, analysis_version, reelbench_version)
            if comparison is not None:
                expected = {
                    "schema_version": "1.1", "project_id": project_id,
                    "source_sha256": result["analysis"]["source"]["source_sha256"],
                    "native_analysis_version": analysis_version,
                    "native_analysis_fingerprint": result["analysis"]["machine_fingerprint"],
                    "reelbench_evidence_version": reelbench_version,
                    "reelbench_evidence_fingerprint": result["evidence"]["evidence_fingerprint"],
                    "tolerances": dict(_COMPARISON_TOLERANCES), "domains": result["domains"],
                    "mismatches": result["mismatches"], "overall": result["overall"],
                }
                if any(comparison.get(key) != value for key, value in expected.items()):
                    raise ValueError("comparison receipt differs from recomputed immutable inputs")
                if comparison.get("comparison_fingerprint") != canonical_fingerprint({k: v for k, v in comparison.items() if k != "comparison_fingerprint"}):
                    raise ValueError("comparison fingerprint differs from canonical receipt")
            guard()
            return result

    def _validate_comparison_locked(self, project_fd, project_id, analysis_version, reelbench_version):
        analysis = self._store.read_version(project_id, "analysis", analysis_version, "shot_analysis.schema.json")
        own_source = self._store.read_version(project_id, "source_receipt", analysis["source"]["version"], "source_receipt.schema.json")
        ReferenceVideoService.validate_analysis_fingerprint(analysis, own_source)
        validated = self._validated_evidence_locked(project_fd, project_id, reelbench_version)
        evidence, reelbench = validated["evidence"], validated["reelbench"]
        if analysis["project_id"] != project_id or evidence["project_id"] != project_id:
            raise ValueError("comparison receipt project mismatch")
        domains, mismatches = self._compare(analysis, evidence, reelbench)
        overall = "manual_review" if any(domain["verdict"] == "manual_review" for domain in domains.values()) else "matched"
        return {"analysis": analysis, "evidence": evidence, "domains": domains, "mismatches": mismatches, "overall": overall}

    def validate_evidence_lineage(self, project_id: str, reelbench_version: str) -> dict[str, Any]:
        """Public Task 3 verifier for one exact, validated ReelBench evidence lineage."""
        self._version(reelbench_version)
        with self._locked_project(project_id) as (_store_fd, project_fd, _root, guard):
            result = self._validated_evidence_locked(project_fd, project_id, reelbench_version)
            guard()
            return result

    def _validated_evidence_locked(self, project_fd, project_id, reelbench_version):
        evidence = self._store.read_version(
            project_id, "reelbench_evidence", reelbench_version,
            "reelbench_evidence.schema.json",
        )
        validate_reelbench_evidence(evidence)
        source = self._store.read_version(
            project_id, "source_receipt", evidence["source_receipt_version"],
            "source_receipt.schema.json",
        )
        lineage = self._lineage(project_fd, project_id, reelbench_version, source)
        if not lineage or lineage[0]["version"] != reelbench_version:
            raise ValueError("ReelBench evidence lineage is incomplete")
        if evidence["action"] != "validate" or any(
            gate["status"] == "FAIL" for gate in evidence["gates"]
        ):
            raise ValueError("comparison requires an exact validated ReelBench evidence version")
        return {
            "evidence": evidence,
            "source": source,
            "reelbench": self._comparison_reelbench_documents(project_fd, evidence),
        }

    def _comparison_reelbench_documents(self, project_fd, evidence):
        """Load exactly the immutable source documents consumed by validation."""
        consumed = {entry["workspace_path"]: entry for entry in evidence["consumed_artifacts"]}
        required = {"inputs/shots.json", "inputs/track.json"}
        if set(consumed).isdisjoint(required):
            raise ValueError("validated ReelBench evidence lacks exact shots and motion artifacts")
        documents = {}
        for workspace_path in sorted(required):
            artifact = consumed.get(workspace_path)
            if artifact is None:
                raise ValueError("validated ReelBench evidence lacks exact shots and motion artifacts")
            payload = ws.read(project_fd, artifact["path"])
            if hashlib.sha256(payload).hexdigest() != artifact["sha256"] or len(payload) != artifact["size_bytes"]:
                raise ValueError("validated ReelBench artifact digest mismatch")
            documents[workspace_path] = self._json(payload)
        shots = self._shots(json.dumps(documents["inputs/shots.json"]).encode())
        track = documents["inputs/track.json"]
        if not isinstance(track, dict) or isinstance(track.get("hz"), bool) or not isinstance(track.get("hz"), (int, float)) or not math.isfinite(track["hz"]) or track["hz"] <= 0:
            raise ValueError("invalid ReelBench motion track")
        values = track.get("values")
        if not isinstance(values, list) or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            for value in values
        ):
            raise ValueError("invalid ReelBench motion samples")
        motions = []
        for index, shot in enumerate(shots["shots"], 1):
            if (
                not self._finite_number(shot.get("start"))
                or not self._finite_number(shot.get("end"))
                or shot["end"] <= shot["start"]
            ):
                raise ValueError(f"ReelBench shot {index} has invalid measured boundaries")
            motion = shot.get("motion")
            if "motion" not in shot or (
                motion is not None and not self._finite_number(motion)
            ):
                raise ValueError(f"ReelBench shot {index} lacks measured motion evidence")
            measured, covered = self._upstream_median_motion(track, shot["start"], shot["end"])
            if covered and not self._same_motion(motion, measured):
                raise ValueError(f"ReelBench shot {index} motion does not match immutable track")
            motions.append({"motion": measured, "covered": covered})
        return {"shots": shots, "track": track, "motions": motions}

    @classmethod
    def _compare(cls, analysis, evidence, reelbench):
        native_shots = analysis["shots"]
        reel_shots = reelbench["shots"]["shots"]
        duration_tolerance = _COMPARISON_TOLERANCES["duration_seconds"]
        boundary_tolerance = _COMPARISON_TOLERANCES["boundary_seconds"]
        motion_tolerance = _COMPARISON_TOLERANCES["motion_abs_delta"]
        domains = {
            "source_identity": [], "duration": [], "timeline_continuity": [],
            "shot_count": [], "boundaries": [], "motion": [],
        }
        mismatches = []

        def mismatch(domain, code, expected, observed, shot_id=None):
            expected_text, observed_text = str(expected), str(observed)
            domains[domain].append(f"{code}: expected={expected_text}; observed={observed_text}")
            mismatches.append({"domain": domain, "shot_id": shot_id, "code": code,
                               "expected": expected_text, "observed": observed_text})

        native_source = analysis["source"]
        if native_source["source_sha256"] != evidence["source_sha256"]:
            mismatch("source_identity", "source_sha256", native_source["source_sha256"], evidence["source_sha256"])
        if native_source["version"] != evidence["source_receipt_version"]:
            mismatch("source_identity", "source_receipt_version", native_source["version"], evidence["source_receipt_version"])
        reel_duration = reelbench["shots"]["meta"]["durationSeconds"]
        native_duration = native_source["duration_seconds"]
        if not math.isclose(native_duration, reel_duration, rel_tol=0.0, abs_tol=duration_tolerance):
            mismatch("duration", "duration", f"{native_duration:.6f}", f"{reel_duration:.6f}")
        for index, reason in enumerate(cls._timeline_mismatches(native_shots, native_duration, "native"), 1):
            mismatch("timeline_continuity", "native_timeline", "continuous timeline", reason, f"S{index:02d}")
        for index, reason in enumerate(cls._timeline_mismatches(reel_shots, reel_duration, "reelbench"), 1):
            mismatch("timeline_continuity", "reelbench_timeline", "continuous timeline", reason, f"S{index:02d}")
        if len(native_shots) != len(reel_shots):
            mismatch("shot_count", "shot_count", len(native_shots), len(reel_shots))
        for native in native_shots[len(reel_shots):]:
            mismatch("shot_count", "reelbench_shot_missing", native["id"], "missing", native["id"])
        for reel in reel_shots[len(native_shots):]:
            mismatch("shot_count", "native_shot_missing", "missing", reel["id"], reel["id"])
        for index, (native, reel) in enumerate(zip(native_shots, reel_shots), 1):
            native_measured = native["measured"]
            native_id = native["id"]
            reel_id = reel.get("id")
            if native_id != reel_id:
                mismatch("boundaries", "shot_order", native_id, reel_id, native_id)
            for field, native_value, reel_value in (
                ("start", native_measured["start_seconds"], reel.get("start")),
                ("end", native_measured["end_seconds"], reel.get("end")),
            ):
                if not cls._finite_number(reel_value) or not math.isclose(
                    native_value, reel_value, rel_tol=0.0, abs_tol=boundary_tolerance,
                ):
                    mismatch("boundaries", f"boundary_{field}", f"{native_value:.6f}", reel_value, native_id)
            native_motion = native_measured["motion_median"]
            motion_check = reelbench["motions"][index - 1]
            if not motion_check["covered"]:
                mismatch("motion", "track_coverage", "complete samples", "missing samples", native_id)
                continue
            reel_motion = motion_check["motion"]
            if native_motion is None and reel_motion is None:
                continue
            if not cls._finite_number(native_motion) or not cls._finite_number(reel_motion) or not math.isclose(
                native_motion, reel_motion, rel_tol=0.0, abs_tol=motion_tolerance,
            ):
                mismatch("motion", "motion_median", native_motion, reel_motion, native_id)
        domains = {
            name: {
                "verdict": "manual_review" if reasons else "matched",
                "reasons": cls._domain_summary(reasons),
            }
            for name, reasons in domains.items()
        }
        domain_order = {
            name: index for index, name in enumerate(
                ("source_identity", "duration", "timeline_continuity", "shot_count", "boundaries", "motion")
            )
        }
        mismatches.sort(key=lambda item: (domain_order[item["domain"]], item["shot_id"] or "", item["code"], item["expected"], item["observed"]))
        return domains, mismatches

    @classmethod
    def _compare_domains(cls, analysis, evidence, reelbench):
        """Compatibility helper for focused domain-only callers."""
        return cls._compare(analysis, evidence, reelbench)[0]

    @classmethod
    def _timeline_mismatches(cls, shots, total_duration, label):
        reasons = []
        previous_end = 0.0
        for index, shot in enumerate(shots, 1):
            measured = shot.get("measured", shot)
            start = measured.get("start_seconds", measured.get("start"))
            end = measured.get("end_seconds", measured.get("end"))
            if not cls._finite_number(start) or not cls._finite_number(end) or end <= start:
                reasons.append(f"{label} shot {index} has invalid boundaries")
                continue
            if not math.isclose(start, previous_end, rel_tol=0.0, abs_tol=0.001):
                reasons.append(
                    f"{label} shot {index} is discontinuous: start={start:.6f}, expected={previous_end:.6f}"
                )
            previous_end = end
        if not math.isclose(previous_end, total_duration, rel_tol=0.0, abs_tol=0.001):
            reasons.append(
                f"{label} timeline end differs from duration: end={previous_end:.6f}, duration={total_duration:.6f}"
            )
        return reasons

    @staticmethod
    def _finite_number(value):
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)

    @classmethod
    def _upstream_median_motion(cls, track, start, end):
        """Parity implementation of pinned video-shots.mjs `medianMotion`."""
        hz, values = track["hz"], track["values"]
        span = end - start
        if span <= 0:
            return None, False
        edge = min(0.4, max(0.1, span * 0.15))
        start_index = math.ceil((start + edge) * hz)
        end_index = math.floor((end - edge) * hz)
        if end_index >= len(values):
            return None, False
        samples = [value for value in values[max(0, start_index):max(0, end_index) + 1]
                   if cls._finite_number(value)]
        if not samples:
            return None, True
        samples.sort()
        middle = len(samples) // 2
        median = samples[middle] if len(samples) % 2 else (samples[middle - 1] + samples[middle]) / 2
        # JS Math.round for non-negative signalstats values, followed by /100.
        return math.floor(median * 100 + 0.5) / 100, True

    @staticmethod
    def _same_motion(actual, expected):
        if actual is None or expected is None:
            return actual is expected
        return math.isclose(actual, expected, rel_tol=0.0, abs_tol=0.000001)

    @staticmethod
    def _receipt_reasons(reasons):
        # The contract caps a domain at 32 strings of 512 characters. Pack
        # every discrepancy into those bounded strings; never silently drop a
        # mismatch merely to make a receipt validate.
        grouped = []
        current = ""
        for reason in reasons:
            if len(reason) > 512:
                raise ValueError("comparison mismatch reason exceeds receipt bound")
            candidate = reason if not current else current + "; " + reason
            if len(candidate) <= 512:
                current = candidate
                continue
            grouped.append(current)
            current = reason
        if current:
            grouped.append(current)
        if len(grouped) > 32:
            raise ValueError("comparison mismatch set exceeds receipt bound")
        return grouped

    @staticmethod
    def _domain_summary(reasons):
        if not reasons:
            return []
        codes = sorted({reason.split(":", 1)[0] for reason in reasons})
        text = f"mismatch_count={len(reasons)}; codes=" + ",".join(codes)
        return [text[:512]]

    @staticmethod
    def _next_version(project_fd, family):
        try:
            with ws.directory(project_fd, family) as directory:
                versions = [name[:-5] for name in os.listdir(directory)
                            if name.endswith(".json") and _VERSION.fullmatch(name[:-5])]
        except FileNotFoundError:
            versions = []
        return f"v{max((int(version[1:]) for version in versions), default=0) + 1:03d}"

    @contextmanager
    def _locked_project(self, project_id):
        self._store._validate_project_id(project_id)
        with ws.absolute_chain(self._store._root) as (store_fd, anchor, verify_store):
            # The filesystem-root inode survives replacement of any store ancestor.
            # Its advisory lock serializes cooperating ReelBench writers globally.
            with _ANCESTRY_LOCK:
                fcntl.flock(anchor, fcntl.LOCK_EX)
                owned = []
                try:
                    verify_store()
                    descriptor = os.open(project_id, ws.DIRECTORY, dir_fd=store_fd)
                    owned.append(descriptor)
                    info = os.fstat(descriptor)
                    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                        raise ValueError("project root must be private")
                    def guard():
                        verify_store()
                        self._assert_project(store_fd, descriptor, project_id)
                    guard()
                    yield store_fd, descriptor, self._store._root / project_id, guard
                finally:
                    ws._close_owned(owned)

    @classmethod
    def _assert_project(cls, store_fd, project_fd, project_id):
        try:
            entry = os.stat(project_id, dir_fd=store_fd, follow_symlinks=False)
            if cls._directory_identity(entry) != cls._directory_identity(os.fstat(project_fd)):
                raise ReelBenchProjectIdentityError("project entry identity changed")
        except OSError as exc:
            raise ReelBenchProjectIdentityError("project entry identity changed") from exc

    def _execute(self, project_fd, root, project_id, action, parent, source_version, options, guard, journal):
        source = self._read_version(project_fd, "source_receipt", source_version)
        validate_contract(source, "source_receipt.schema.json")
        if source["project_id"] != project_id:
            raise ValueError("source receipt project mismatch")
        duration = source["duration_seconds"]
        if not math.isfinite(duration) or not 0 < duration <= 1800 or source["size_bytes"] > MAX_SOURCE_BYTES:
            raise ValueError("source exceeds preventive duration or byte bound")
        latest = self._latest(project_fd)
        if parent != latest:
            raise ValueError("expected parent does not match latest ReelBench evidence version")
        version = f"v{int(latest[1:]) + 1 if latest else 1:03d}"
        lineage = self._lineage(project_fd, project_id, parent, source)
        allowed = {"seed": {"threshold", "title"}, "evidence": set(), "validate": set(), "render": {"mode"}}[action]
        if set(options) - allowed:
            raise ValueError("unsupported ReelBench action options")
        if action != "seed" and not lineage:
            raise ValueError(f"{action} requires an exact parent")
        if action == "render" and (lineage[0]["action"] != "validate" or any(g["status"] == "FAIL" for g in lineage[0]["gates"])):
            raise ValueError("render requires an immediate validated parent")
        name = ".reelbench-work-" + secrets.token_hex(12)
        os.mkdir(name, 0o700, dir_fd=project_fd)
        work = None
        output_fd = None
        published = False
        output_created = False
        marker_created = False
        error = None
        try:
            work = os.open(name, ws.DIRECTORY, dir_fd=project_fd)
            marker = journal.create(work, name, action=action, version=version, parent=parent,
                parent_fingerprint=lineage[0]["evidence_fingerprint"] if lineage else None, source=source)
            marker_created = True
            os.fsync(project_fd)
            source_relative = Path(source["staged_path"]).relative_to(root).as_posix()
            if not source_relative.startswith("source/"):
                raise ValueError("source receipt escaped project source")
            source_name = "source/" + source["source_sha256"]
            with ws.file_at(project_fd, source_relative) as source_fd:
                info = os.fstat(source_fd)
                if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                    raise ValueError("source is not private")
                source_identity = ws.identity(info)
            ws.copy(project_fd, source_relative, work, source_name, maximum=MAX_SOURCE_BYTES,
                    expected={"sha256": source["source_sha256"], "size_bytes": source["size_bytes"]}, mode=0o400, quota=True)
            consumed = [{"version": source_version, "receipt_fingerprint": canonical_fingerprint(source),
                "path": source_relative, "workspace_path": source_name, "sha256": source["source_sha256"],
                "size_bytes": source["size_bytes"]}]
            selected = {}
            if action != "seed":
                # Newest produced artifact wins. Every ancestor was validated above.
                for receipt in lineage:
                    for artifact in receipt["artifacts"]:
                        relative = artifact["path"].split("/", 2)[2]
                        if relative not in selected and (relative in {"shots.json", "track.json"} or
                                (action in {"validate", "render"} and relative.startswith("frames/"))):
                            selected[relative] = (receipt, artifact)
                    if receipt["action"] == "seed":
                        break
                if not {"shots.json", "track.json"} <= set(selected):
                    raise ValueError("lineage lacks required shots and track artifacts")
                if action in {"validate", "render"} and not any(name.startswith("frames/") for name in selected):
                    raise ValueError("current seed has no frame evidence")
                for relative, (receipt, artifact) in selected.items():
                    target = "inputs/" + relative
                    ws.copy(project_fd, artifact["path"], work, target, maximum=MAX_DOCUMENT_BYTES,
                            expected=artifact, mode=0o400, quota=True)
                    consumed.append({"version": receipt["version"], "receipt_fingerprint": receipt["evidence_fingerprint"],
                        "path": artifact["path"], "workspace_path": target,
                        "sha256": artifact["sha256"], "size_bytes": artifact["size_bytes"]})
            ws.mkdir(work, "output")
            with self._adapter.execution_workspace(work, consumed):
                results = self._run_action(work, action, source_name, options, source_relative)
                # Validate and fsync every generated byte before creating visible artifacts.
                generated = ws.inventory(work, "output")
                self._complete(work, action, generated, options)
                for entry in consumed:
                    with ws.file_at(project_fd, entry["path"]) as fd:
                        if entry["path"] == source_relative and ws.identity(os.fstat(fd)) != source_identity:
                            raise ValueError("source identity changed during execution")
                    # Recheck copied inputs, including all frames, after the child.
                    copied = self._digest(work, entry["workspace_path"], MAX_SOURCE_BYTES)
                    if copied != {k: entry[k] for k in ("sha256", "size_bytes")}:
                        raise ValueError("consumed artifact changed during execution")
                guard()
                ws.mkdir(project_fd, "reelbench")
                with ws.directory(project_fd, "reelbench") as family:
                    os.mkdir(version, 0o700, dir_fd=family)
                    output_created = True
                    output_fd = os.open(version, ws.DIRECTORY, dir_fd=family)
                    os.fsync(family)
                    journal.bind_output(work, marker, output_fd)
                artifacts = []
                for relative, expected in sorted(generated.items()):
                    guard()
                    target = f"reelbench/{version}/{relative}"
                    ws.copy(work, "output/" + relative, output_fd, relative,
                            maximum=MAX_DOCUMENT_BYTES, expected=expected)
                    artifacts.append({"path": target, "mime_type": _MIME[Path(relative).suffix], **expected})
                os.fsync(output_fd)
                with ws.directory(project_fd, f"reelbench/{version}") as check:
                    if self._directory_identity(os.fstat(check)) != self._directory_identity(os.fstat(output_fd)):
                        raise ValueError("artifact publication directory identity changed")
                os.fsync(project_fd)
                shots_path = "output/shots.json" if action == "seed" else "inputs/shots.json"
                shots_bytes = ws.read(work, shots_path)
                shots = {"sha256": hashlib.sha256(shots_bytes).hexdigest(),
                         "ids": [item["id"] for item in self._shots(shots_bytes)["shots"]]}
                receipt = self._receipt(project_id, version, parent, source, lineage, action, results, artifacts, consumed, shots)
                validate_reelbench_evidence(receipt)
                journal.bind_receipt(work, marker, receipt)
                error = VersionCommitIndeterminateError(project_id=project_id, family="reelbench_evidence",
                    version=version, path=root / "reelbench_evidence" / f"{version}.json",
                    payload_fingerprint=canonical_fingerprint(receipt))
                try:
                    guard()
                    persisted = self._store.write_version(project_id, "reelbench_evidence", receipt,
                        schema_name="reelbench_evidence.schema.json", version=version, project_fd=project_fd,
                        publication_guard=guard)
                except VersionCommitIndeterminateError:
                    published = True
                    raise
                except BaseException as exc:
                    try:
                        visible = self._read_version(project_fd, "reelbench_evidence", version)
                        published = canonical_fingerprint(visible) == error.payload_fingerprint
                    except BaseException:
                        # If publication cannot be disproved after an unexpected
                        # store failure, preserve artifacts and demand exact recovery.
                        try:
                            with ws.file_at(project_fd, f"reelbench_evidence/{version}.json"):
                                published = True
                        except FileNotFoundError:
                            published = False
                        except BaseException:
                            published = True
                    if published:
                        raise error from exc
                    raise
                published = True
                try:
                    validate_reelbench_evidence(persisted)
                    guard()
                except BaseException as exc:
                    raise error from exc
                return persisted
        except BaseException as exc:
            if published and error is not None and not isinstance(exc, VersionCommitIndeterminateError):
                raise error from exc
            raise
        finally:
            primary = sys.exc_info()[1]
            cleanup_error = None
            try:
                if work is None:
                    os.rmdir(name, dir_fd=project_fd)
                elif self._directory_identity(os.stat(name, dir_fd=project_fd, follow_symlinks=False)) != self._directory_identity(os.fstat(work)):
                    raise ValueError("workspace identity changed before cleanup")
                elif marker_created:
                    journal.cleanup(work, name, published=published, guard=guard)
                else:
                    ws.remove_tree(project_fd, name)
                if output_created and not published and not marker_created:
                    with ws.directory(project_fd, "reelbench") as family:
                        if output_fd is None or self._directory_identity(os.stat(version, dir_fd=family, follow_symlinks=False)) != self._directory_identity(os.fstat(output_fd)):
                            raise ValueError("output identity changed before cleanup")
                        ws.remove_tree(family, version)
            except BaseException as exc:
                cleanup_error = exc
            finally:
                for descriptor in (output_fd, work):
                    if descriptor is not None:
                        try:
                            ws._close_owned([descriptor])
                        except BaseException as exc:
                            if cleanup_error is None:
                                cleanup_error = exc
            if cleanup_error is not None:
                if primary is not None:
                    ws.cleanup_failure(primary, cleanup_error)
                elif published and error is not None:
                    raise error from cleanup_error
                else:
                    raise cleanup_error

    def _run_action(self, work, action, source_name, options, source_relative):
        base = Path(f"/dev/fd/{work}")
        source = base / source_name
        out = base / "output"
        if action == "seed":
            result = self._adapter.seed(source=source, shots=out / "shots.json", track=out / "track.json",
                threshold=options.get("threshold", 0.3), title=options.get("title", "Reference"))
            document = self._shots(result.stdout.encode())
            ws.write(work, "output/shots.json", result.stdout.encode(), quota=True)
            self._json(ws.read(work, "output/track.json"))
            return [result]
        shots = base / "inputs/shots.json"
        track = base / "inputs/track.json"
        document = self._shots(ws.read(work, "inputs/shots.json"))
        self._json(ws.read(work, "inputs/track.json"))
        if action == "evidence":
            frames, sheets = out / "frames", out / "sheets"
            first = self._adapter.frames(shots=shots, source=source, frames_dir=frames)
            self._expect_frames(ws.inventory(work, "output/frames"), document)
            return [first, self._adapter.sheet(shots=shots, frames_dir=frames, sheets_dir=sheets, pick="a"),
                    self._adapter.sheet(shots=shots, frames_dir=frames, sheets_dir=sheets, pick="b")]
        self._expect_frames(ws.inventory(work, "inputs/frames"), document)
        frames = base / "inputs/frames"
        if action == "validate":
            return [self._adapter.validate(shots=shots, track=track, frames_dir=frames)]
        mode = options.get("mode", "md")
        result = self._adapter.render(shots=shots, track=track, frames_dir=frames, source=source, mode=mode)
        payload = result.stdout.encode()
        if not payload or len(payload) > MAX_REPORT_BYTES:
            raise ValueError("report is empty or oversized")
        if mode == "html":
            # Rebase only the player's service-owned URL. The unchanged upstream
            # report's private workspace goes away; the project source remains.
            marker = f' src="{source_name}"'.encode()
            if payload.count(marker) != 1:
                raise ValueError("report player source is not complete")
            url = "../../" + quote(source_relative, safe="/")
            payload = payload.replace(marker, f' src="{url}"'.encode(), 1)
        ws.write(work, f"output/report.{mode}", payload, quota=True)
        # Offline reports must retain their relative frame references after publishing.
        if mode == "html":
            for name, expected in ws.inventory(work, "inputs/frames").items():
                ws.copy(work, "inputs/frames/" + name, work, "output/inputs/frames/" + name,
                        maximum=MAX_DOCUMENT_BYTES, expected=expected, quota=True)
        return [result]

    def _complete(self, work, action, generated, options):
        if action == "seed":
            expected = {"shots.json", "track.json"}
        elif action == "evidence":
            doc = self._shots(ws.read(work, "inputs/shots.json"))
            count = math.ceil(len(doc["shots"]) / 25)
            expected = {f"frames/{shot['id']}{pick}.jpg" for shot in doc["shots"] for pick in ("a", "b")}
            expected |= {f"sheets/sheet-{pick}{page:02d}.jpg" for pick in ("a", "b") for page in range(1, count + 1)}
        elif action == "validate":
            expected = set()
        else:
            mode = options.get("mode", "md")
            expected = {f"report.{mode}"}
            if mode == "html":
                expected |= {"inputs/frames/" + name for name in ws.inventory(work, "inputs/frames")}
        if set(generated) != expected:
            raise ValueError("generated artifact inventory is not complete")

    def _lineage(self, fd, project_id, parent, source):
        receipts = []
        current = parent
        expected_fingerprint = None
        while current is not None:
            if len(receipts) >= 256:
                raise ValueError("lineage exceeds bound")
            receipt = self._read_version(fd, "reelbench_evidence", current)
            validate_reelbench_evidence(receipt)
            if receipt["version"] != current or receipt["project_id"] != project_id or receipt["source_sha256"] != source["source_sha256"] or receipt["source_receipt_version"] != source["version"]:
                raise ValueError("parent receipt source or identity mismatch")
            if expected_fingerprint is not None and receipt["evidence_fingerprint"] != expected_fingerprint:
                raise ValueError("parent fingerprint mismatch")
            observed = ws.inventory(fd, f"reelbench/{current}", private=True)
            expected = {}
            for artifact in receipt["artifacts"]:
                prefix = f"reelbench/{current}/"
                if not artifact["path"].startswith(prefix):
                    raise ValueError("ancestor artifact escaped version")
                expected[artifact["path"][len(prefix):]] = {k: artifact[k] for k in ("sha256", "size_bytes")}
            if observed != expected:
                raise ValueError("ancestor artifact inventory or digest mismatch")
            if receipt["action"] == "seed":
                shots_artifact = next(a for a in receipt["artifacts"] if a["path"].endswith("/shots.json"))
            else:
                shots_artifact = next(a for a in receipt["consumed_artifacts"] if a["workspace_path"] == "inputs/shots.json")
            shots_bytes = ws.read(fd, shots_artifact["path"])
            if hashlib.sha256(shots_bytes).hexdigest() != receipt["shots"]["sha256"]:
                raise ValueError("ancestor shots artifact digest mismatch")
            parsed = {"sha256": hashlib.sha256(shots_bytes).hexdigest(),
                      "ids": [shot["id"] for shot in self._shots(shots_bytes)["shots"]]}
            if parsed != receipt["shots"]:
                raise ValueError("ancestor parsed shots identity mismatch")
            receipts.append(receipt)
            expected_fingerprint = receipt["parent_fingerprint"]
            current = receipt["parent_version"]
        return receipts

    def _receipt(self, project_id, version, parent, source, lineage, action, results, artifacts, consumed, shots):
        lock_root = ws.open_absolute(self._adapter._shots_script.parents[3] / "upstream")
        try:
            lock_bytes = ws.read(lock_root, "reelbench.lock.json")
        finally:
            ws._close_owned([lock_root])
        commands = [{"action": result.action, "argv": result.argv, "returncode": result.returncode,
                     "tool_identities": list(result.tool_identities), "script_manifest": list(result.script_manifest)} for result in results]
        receipt = {"schema_version": "1.1", "version": version, "parent_version": parent,
            "parent_fingerprint": lineage[0]["evidence_fingerprint"] if lineage else None,
            "project_id": project_id, "source_receipt_version": source["version"], "source_sha256": source["source_sha256"],
            "action": action, "upstream": {"source": "https://github.com/eternityspring/reelbench-skills.git",
                "revision": "18f2f63987337df0975a89973d38d50f3231ee31", "lock_fingerprint": hashlib.sha256(lock_bytes).hexdigest()},
            "tool_identities": list(results[0].tool_identities), "commands": commands, "consumed_artifacts": consumed,
            "script_manifest": list(results[0].script_manifest), "shots": shots,
            "argv_fingerprint": canonical_fingerprint({"commands": commands}),
            "gates": list(results[0].gates), "artifacts": artifacts,
            "created_at": _now(), "committed_at": _now()}
        receipt["evidence_fingerprint"] = canonical_fingerprint(receipt)
        return receipt

    def reconcile_indeterminate(self, error):
        if error.family == "reelbench_comparison":
            return self._reconcile_comparison_indeterminate(error)
        if error.family != "reelbench_evidence":
            raise ValueError("indeterminate error is not ReelBench evidence or comparison")
        with self._locked_project(error.project_id) as (store_fd, descriptor, _root, guard):
            receipt = self._read_version(descriptor, error.family, error.version)
            if canonical_fingerprint(receipt) != error.payload_fingerprint:
                raise ValueError("indeterminate receipt fingerprint mismatch")
            validate_reelbench_evidence(receipt)
            source = self._read_version(descriptor, "source_receipt", receipt["source_receipt_version"])
            self._lineage(descriptor, error.project_id, receipt["version"], source)
            guard()
            return receipt

    def _reconcile_comparison_indeterminate(self, error):
        """Recover only the exact comparison receipt visible after a durability error."""
        with self._locked_project(error.project_id) as (_store_fd, project_fd, _root, guard):
            receipt = self._store.reconcile_version(
                error.project_id,
                "reelbench_comparison",
                error.version,
                error.payload_fingerprint,
                "reelbench_comparison.schema.json",
            )
            validate_reelbench_comparison(receipt)
            self._validate_comparison_locked(project_fd, error.project_id,
                                             receipt["native_analysis_version"], receipt["reelbench_evidence_version"])
            # Reuse the public comparison contract's exact-field checks without
            # reopening a second project lock.
            expected = self._validate_comparison_locked(project_fd, error.project_id,
                                                        receipt["native_analysis_version"], receipt["reelbench_evidence_version"])
            fields = {"schema_version": "1.1", "project_id": error.project_id,
                      "source_sha256": expected["analysis"]["source"]["source_sha256"],
                      "native_analysis_version": receipt["native_analysis_version"],
                      "native_analysis_fingerprint": expected["analysis"]["machine_fingerprint"],
                      "reelbench_evidence_version": receipt["reelbench_evidence_version"],
                      "reelbench_evidence_fingerprint": expected["evidence"]["evidence_fingerprint"],
                      "tolerances": _COMPARISON_TOLERANCES, "domains": expected["domains"],
                      "mismatches": expected["mismatches"], "overall": expected["overall"]}
            if any(receipt.get(key) != value for key, value in fields.items()):
                raise ValueError("comparison recovery does not match current immutable inputs")
            guard()
            return receipt

    @staticmethod
    def _read_version(fd, family, version):
        ReelBenchProjectService._version(version)
        document = ReelBenchProjectService._json(ws.read(fd, f"{family}/{version}.json"))
        if not isinstance(document, dict) or document.get("version") != version:
            raise ValueError("receipt filename and embedded version differ")
        return document

    @staticmethod
    def _latest(fd):
        try:
            with ws.directory(fd, "reelbench_evidence") as family:
                versions = [name[:-5] for name in os.listdir(family) if name.endswith(".json") and _VERSION.fullmatch(name[:-5])]
            return max(versions, key=lambda v: int(v[1:]), default=None)
        except FileNotFoundError:
            return None

    @staticmethod
    def _version(value, nullable=False):
        if value is None and nullable:
            return
        if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
            raise ValueError("invalid exact version")

    @staticmethod
    def _json(payload):
        if len(payload) > MAX_DOCUMENT_BYTES:
            raise ValueError("JSON exceeds byte bound")
        try:
            value = json.loads(payload, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
        except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise ValueError("invalid JSON") from exc
        def check(item, depth):
            if depth > 24:
                raise ValueError("JSON exceeds depth bound")
            if isinstance(item, dict):
                for child in item.values(): check(child, depth + 1)
            elif isinstance(item, list):
                if len(item) > 20000:
                    raise ValueError("JSON exceeds item bound")
                for child in item: check(child, depth + 1)
        check(value, 0)
        return value

    @classmethod
    def _shots(cls, payload):
        doc = cls._json(payload)
        shots = doc.get("shots") if isinstance(doc, dict) else None
        duration = doc.get("meta", {}).get("durationSeconds") if isinstance(doc, dict) and isinstance(doc.get("meta"), dict) else None
        if not isinstance(shots, list) or not 1 <= len(shots) <= MAX_SHOTS:
            raise ValueError("invalid or excessive shot count")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0 < duration <= 1800:
            raise ValueError("invalid shot duration")
        ids = []
        for shot in shots:
            if not isinstance(shot, dict) or not isinstance(shot.get("id"), str) or re.fullmatch(r"S[0-9]{2,4}", shot["id"]) is None:
                raise ValueError("unsafe shot id")
            ids.append(shot["id"])
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate shot id")
        return doc

    @staticmethod
    def _expect_frames(inventory, document):
        expected = {f"{s['id']}{pick}.jpg" for s in document["shots"] for pick in ("a", "b")}
        if set(inventory) != expected:
            raise ValueError("frame inventory is not complete")

    @staticmethod
    def _directory_identity(info):
        return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)

    @staticmethod
    def _digest(fd, name, maximum):
        with ws.file_at(fd, name) as source:
            before = os.fstat(source)
            if before.st_size > maximum:
                raise ValueError("input exceeds bound")
            digest, size = hashlib.sha256(), 0
            while chunk := os.read(source, 1024 * 1024):
                size += len(chunk)
                if size > maximum:
                    raise ValueError("input exceeds bound")
                digest.update(chunk)
            if ws.identity(before) != ws.identity(os.fstat(source)):
                raise ValueError("input changed while hashing")
            return {"sha256": digest.hexdigest(), "size_bytes": size}


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
