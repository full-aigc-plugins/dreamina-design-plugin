"""Immutable corroboration sidecars for legacy annotation and redesign documents."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from scripts.json_contracts import canonical_fingerprint, validate_contract
from scripts.reelbench_contracts import validate_reelbench_binding, validate_reelbench_comparison
from scripts.reelbench_project_service import ReelBenchProjectService
from scripts.reference_video_service import ReferenceVideoService
from scripts.video_project_store import VersionCommitIndeterminateError, VersionReconciliationError, VideoProjectStore


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ReelBenchBindingService:
    """Bind a durable legacy subject to a fully revalidated comparison receipt."""

    def __init__(self, store: VideoProjectStore) -> None:
        self._store = store

    def bind(
        self, project_id: str, *, subject_family: str, subject_version: str,
        comparison_version: str, indeterminate_commit: VersionCommitIndeterminateError | None = None,
    ) -> dict[str, Any]:
        subject, subject_fingerprint, schema_name = self._subject(
            project_id, subject_family, subject_version
        )
        comparison = self._store.read_version(
            project_id, "reelbench_comparison", comparison_version,
            "reelbench_comparison.schema.json",
        )
        validate_reelbench_comparison(comparison)
        verifier = ReelBenchProjectService(self._store, adapter=None)
        validated = verifier.validate_comparison_against_inputs(
            project_id, comparison["native_analysis_version"], comparison["reelbench_evidence_version"], comparison
        )
        if validated["overall"] != "matched":
            raise ValueError("automatic corroboration requires a recomputed matched comparison")
        evidence, analysis = validated["evidence"], validated["analysis"]
        if (
            comparison["project_id"] != project_id
            or comparison["native_analysis_fingerprint"] != analysis["machine_fingerprint"]
            or comparison["source_sha256"] != analysis["source"]["source_sha256"]
            or comparison["reelbench_evidence_fingerprint"] != evidence["evidence_fingerprint"]
        ):
            raise ValueError("comparison receipt is forged or orphaned from immutable inputs")
        if subject_family == "annotation":
            if subject["analysis_version"] != analysis["version"] or subject["machine_fingerprint"] != analysis["machine_fingerprint"]:
                raise ValueError("annotation subject is not bound to comparison analysis")
        elif subject["analysis_version"] != analysis["version"] or subject["machine_fingerprint"] != analysis["machine_fingerprint"]:
            raise ValueError("video design subject is not bound to comparison analysis")
        if indeterminate_commit is not None:
            expected_path = self._store.project_root(project_id) / "reelbench_binding" / f"{indeterminate_commit.version}.json"
            if (
                indeterminate_commit.project_id != project_id
                or indeterminate_commit.family != "reelbench_binding"
                or indeterminate_commit.path != expected_path
            ):
                raise VersionReconciliationError(
                    project_id=project_id, family="reelbench_binding", version=indeterminate_commit.version,
                    path=expected_path, reason="retry does not identify the exact indeterminate binding",
                )
            recovered = self._store.reconcile_version(
                project_id, "reelbench_binding", indeterminate_commit.version,
                indeterminate_commit.payload_fingerprint, "reelbench_binding.schema.json",
            )
            expected = {
                "project_id": project_id, "subject_family": subject_family,
                "subject_version": subject_version, "subject_fingerprint": subject_fingerprint,
                "comparison_version": comparison_version,
                "comparison_fingerprint": comparison["comparison_fingerprint"],
                "native_analysis_version": analysis["version"],
                "native_analysis_fingerprint": analysis["machine_fingerprint"],
                "reelbench_evidence_version": evidence["version"],
                "reelbench_evidence_fingerprint": evidence["evidence_fingerprint"],
            }
            if any(recovered.get(key) != value for key, value in expected.items()):
                raise VersionReconciliationError(
                    project_id=project_id, family="reelbench_binding", version=indeterminate_commit.version,
                    path=expected_path, reason="retry inputs do not match the committed binding",
                )
            return recovered
        version = self._next_version(project_id)
        receipt = {
            "schema_version": "1.0", "version": version, "project_id": project_id,
            "subject_family": subject_family, "subject_version": subject_version,
            "subject_fingerprint": subject_fingerprint,
            "comparison_version": comparison_version,
            "comparison_fingerprint": comparison["comparison_fingerprint"],
            "native_analysis_version": analysis["version"],
            "native_analysis_fingerprint": analysis["machine_fingerprint"],
            "reelbench_evidence_version": evidence["version"],
            "reelbench_evidence_fingerprint": evidence["evidence_fingerprint"],
            "bound_at": _now(),
        }
        receipt["binding_fingerprint"] = canonical_fingerprint(receipt)
        validate_reelbench_binding(receipt)
        return self._store.write_version(
            project_id, "reelbench_binding", receipt,
            schema_name="reelbench_binding.schema.json", version=version,
        )

    def _subject(self, project_id: str, family: str, version: str):
        if family == "annotation":
            schema_name, store_family = "shot_annotation.schema.json", "annotation"
        elif family == "video_design":
            schema_name, store_family = "video_redesign.schema.json", "redesign"
        else:
            raise ValueError("unsupported binding subject family")
        if re.fullmatch(r"v[0-9]{3,}", version) is None:
            raise ValueError("invalid binding subject version")
        subject = self._store.read_version(project_id, store_family, version, schema_name)
        fingerprint = (
            canonical_fingerprint(subject) if family == "annotation" else subject["design_fingerprint"]
        )
        return subject, fingerprint, schema_name

    def _next_version(self, project_id: str) -> str:
        root = self._store.project_root(project_id) / "reelbench_binding"
        numbers = [int(path.stem[1:]) for path in root.glob("v*.json")
                   if re.fullmatch(r"v[0-9]{3,}", path.stem)] if root.is_dir() else []
        return f"v{max(numbers, default=0) + 1:03d}"


__all__ = ["ReelBenchBindingService"]
