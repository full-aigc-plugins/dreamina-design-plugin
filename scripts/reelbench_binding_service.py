"""Immutable corroboration and crash-recoverable subject/sidecar publication."""
from __future__ import annotations
from datetime import datetime, timezone
import os
import re
from scripts import reelbench_workspace as ws
from scripts.json_contracts import canonical_fingerprint
from scripts.reelbench_composite_journal import CompositeJournal
from scripts.reelbench_contracts import validate_reelbench_binding
from scripts.reelbench_operation import ReelBenchRecoveryRequiredError
from scripts.reelbench_project_service import ReelBenchProjectService
from scripts.video_project_store import VersionCommitIndeterminateError

_SUBJECTS = {"annotation": ("annotation", "shot_annotation.schema.json"),
             "video_design": ("redesign", "video_redesign.schema.json")}


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class CompositeBindingIndeterminateError(RuntimeError):
    """The exact sealed subject and sidecar must be reconciled together."""
    def __init__(self, record, root):
        self.project_id = record["project_id"]
        self.subject_family = record["subject_family"]
        self.subject_version = record["subject"]["version"]
        self.subject_fingerprint = record["subject"]["payload_fingerprint"]
        self.subject_path = root / record["subject"]["family"] / f"{self.subject_version}.json"
        self.comparison_version = record["comparison_version"]
        self.comparison_fingerprint = record["comparison_fingerprint"]
        self.binding_version = record["binding"]["version"]
        self.binding_fingerprint = record["binding"]["payload_fingerprint"]
        self.binding_path = root / "reelbench_binding" / f"{self.binding_version}.json"
        self.journal_id = record["operation_id"]
        self.journal_fingerprint = canonical_fingerprint({k: v for k, v in record.items() if k not in {"phase", "seal"}})
        self.journal_path = root / ".reelbench-composites" / f"{self.journal_id}.json"
        self.completed_journal_path = root / ".reelbench-completed" / f"{self.journal_id}.json"
        self.binding_error = VersionCommitIndeterminateError(
            project_id=self.project_id, family="reelbench_binding", version=self.binding_version,
            path=self.binding_path, payload_fingerprint=self.binding_fingerprint)
        super().__init__(f"composite commit indeterminate: {self.project_id}/{self.subject_family}/{self.subject_version}; journal {self.journal_id}")


class ReelBenchBindingService:
    def __init__(self, store):
        self._store = store
        self._verifier = ReelBenchProjectService(store, None)

    def _comparison_locked(self, fd, project_id, comparison_version, subject):
        comparison = self._store.read_version(project_id, "reelbench_comparison",
            comparison_version, "reelbench_comparison.schema.json")
        result = self._verifier._validate_comparison_locked(fd, project_id,
            comparison["native_analysis_version"], comparison["reelbench_evidence_version"])
        self._verifier._assert_comparison(result, project_id, comparison["native_analysis_version"],
            comparison["reelbench_evidence_version"], comparison)
        if result["overall"] != "matched":
            raise ValueError("automatic corroboration requires a recomputed matched comparison")
        analysis = result["analysis"]
        if subject["analysis_version"] != analysis["version"] or subject["machine_fingerprint"] != analysis["machine_fingerprint"]:
            raise ValueError("subject is not bound to comparison analysis")
        return comparison

    def _receipt(self, project_id, family, subject, comparison):
        if family == "video_design":
            core = {k: v for k, v in subject.items()
                    if k not in {"version", "parent_version", "rights_receipt_id", "design_fingerprint", "committed_at"}}
            if canonical_fingerprint(core) != subject["design_fingerprint"]:
                raise ValueError("video design fingerprint does not match its exact content")
        fingerprint = canonical_fingerprint(subject) if family == "annotation" else subject["design_fingerprint"]
        return {"schema_version": "1.0", "project_id": project_id,
            "subject_family": family, "subject_version": subject["version"], "subject_fingerprint": fingerprint,
            "comparison_version": comparison["version"], "comparison_fingerprint": comparison["comparison_fingerprint"],
            "native_analysis_version": comparison["native_analysis_version"],
            "native_analysis_fingerprint": comparison["native_analysis_fingerprint"],
            "reelbench_evidence_version": comparison["reelbench_evidence_version"],
            "reelbench_evidence_fingerprint": comparison["reelbench_evidence_fingerprint"]}

    def _existing(self, fd, project_id, expected):
        try:
            with ws.directory(fd, "reelbench_binding") as family:
                versions = sorted(name[:-5] for name in os.listdir(family) if re.fullmatch(r"v[0-9]{3,}\.json", name))
        except FileNotFoundError:
            return None
        for version in versions:
            receipt = self._store.read_version(project_id, "reelbench_binding", version, "reelbench_binding.schema.json")
            validate_reelbench_binding(receipt)
            if all(receipt.get(k) == v for k, v in expected.items()):
                return receipt
        return None

    def _reserve_binding(self, fd, project_id, expected, guard):
        operation_id = canonical_fingerprint({"kind": "binding", **expected})
        try:
            reservation = self._store._read_reservation_at(fd, operation_id)
        except FileNotFoundError:
            reservation = None
        if reservation is not None:
            if not all(reservation["payload"].get(k) == v for k, v in expected.items()):
                raise ReelBenchRecoveryRequiredError("reserved binding differs from request")
            self._store._sync_reservation_at(fd, operation_id)
            return reservation
        return self._store.reserve_version(project_id, "reelbench_binding", {**expected, "bound_at": _now()},
            schema_name="reelbench_binding.schema.json", operation_id=operation_id,
            project_fd=fd, publication_guard=guard, fingerprint_field="binding_fingerprint")

    def _publish_or_reconcile(self, fd, project_id, reservation, guard, *, must_exist=False):
        name = f"{reservation['family']}/{reservation['version']}.json"
        try:
            os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            if must_exist:
                raise ReelBenchRecoveryRequiredError("previously published exact version is missing")
            result = self._store.publish_reserved_version(project_id, reservation,
                project_fd=fd, publication_guard=guard)
        else:
            result = self._store.reconcile_reserved_version(project_id, reservation)
        # Re-cross the original durability barriers after an uncertain publication.
        with ws.file_at(fd, name) as file_fd:
            os.fsync(file_fd)
        with ws.directory(fd, reservation["family"]) as family_fd:
            os.fsync(family_fd)
        os.fsync(fd)
        guard()
        return result

    def bind(self, project_id, *, subject_family, subject_version, comparison_version, indeterminate_commit=None):
        if subject_family not in _SUBJECTS:
            raise ValueError("unsupported binding subject family")
        family, schema = _SUBJECTS[subject_family]
        with self._verifier._locked_project(project_id) as (_, fd, root, guard):
            subject = self._store.read_version(project_id, family, subject_version, schema)
            comparison = self._comparison_locked(fd, project_id, comparison_version, subject)
            expected = self._receipt(project_id, subject_family, subject, comparison)
            if indeterminate_commit is not None:
                commit = indeterminate_commit
                if (commit.project_id != project_id or commit.family != "reelbench_binding"
                    or commit.path != root / "reelbench_binding" / f"{commit.version}.json"):
                    raise ValueError("retry does not identify the exact indeterminate binding")
                receipt = self._store.reconcile_version(project_id, "reelbench_binding", commit.version,
                    commit.payload_fingerprint, "reelbench_binding.schema.json")
                validate_reelbench_binding(receipt)
                if any(receipt.get(k) != v for k, v in expected.items()):
                    raise ValueError("retry inputs do not match committed binding")
                reservation = {"family": "reelbench_binding", "version": receipt["version"],
                    "payload_fingerprint": canonical_fingerprint(receipt), "schema_name": "reelbench_binding.schema.json"}
                return self._publish_or_reconcile(fd, project_id, reservation, guard, must_exist=True)
            existing = self._existing(fd, project_id, expected)
            if existing is not None:
                guard()
                return existing
            reservation = self._reserve_binding(fd, project_id, expected, guard)
            return self._publish_or_reconcile(fd, project_id, reservation, guard)

    def commit_subject(self, project_id, *, subject_family, document, comparison_version,
                       binding_indeterminate_commit=None, after_commit=None):
        """Seal both exact versions before the subject becomes visible."""
        if subject_family not in _SUBJECTS:
            raise ValueError("unsupported binding subject family")
        family, schema = _SUBJECTS[subject_family]
        request = {k: v for k, v in document.items() if k not in {"version", "parent_version", "committed_at"}}
        operation_id = canonical_fingerprint({"project_id": project_id, "subject_family": subject_family,
            "document": request, "comparison_version": comparison_version})
        record, attempted = None, False
        try:
            with self._verifier._locked_project(project_id) as (store_fd, fd, root, guard):
                journal = CompositeJournal(store_fd, fd, project_id, root)
                record = journal.load(operation_id)
                retry = binding_indeterminate_commit
                if retry is not None:
                    if not isinstance(retry, CompositeBindingIndeterminateError) or record is None:
                        raise ReelBenchRecoveryRequiredError("retry requires the exact sealed composite journal")
                    exact = CompositeBindingIndeterminateError(record, root)
                    fields = ("project_id", "subject_family", "subject_version", "subject_fingerprint", "subject_path",
                              "comparison_version", "comparison_fingerprint", "binding_version", "binding_fingerprint",
                              "binding_path", "journal_id", "journal_fingerprint", "journal_path", "completed_journal_path")
                    if any(getattr(retry, key, None) != getattr(exact, key) for key in fields):
                        raise ReelBenchRecoveryRequiredError("retry identities differ from sealed composite journal")
                attempted = record is not None
                comparison = self._comparison_locked(fd, project_id, comparison_version, document)
                if record is None:
                    subject_id = canonical_fingerprint({"operation": operation_id, "kind": "subject"})
                    try:
                        subject = self._store._read_reservation_at(fd, subject_id)
                    except FileNotFoundError:
                        subject = None
                    if subject is not None:
                        prior = {k: v for k, v in subject["payload"].items() if k not in {"version", "parent_version", "committed_at"}}
                        if prior != request:
                            raise ReelBenchRecoveryRequiredError("reserved subject changed")
                        self._store._sync_reservation_at(fd, subject_id)
                    else:
                        subject = self._store.reserve_version(project_id, family, dict(document), schema_name=schema,
                            operation_id=subject_id, parent_version_field="parent_version" if family == "redesign" else None,
                            project_fd=fd, publication_guard=guard)
                    binding = self._reserve_binding(fd, project_id,
                        self._receipt(project_id, subject_family, subject["payload"], comparison), guard)
                    record = {"journal_version": "1", "operation_id": operation_id, "project_id": project_id,
                        "project_identity": journal.identity, "request_fingerprint": operation_id,
                        "subject_family": subject_family, "comparison_version": comparison_version,
                        "comparison_fingerprint": comparison["comparison_fingerprint"],
                        "subject": subject, "binding": binding, "phase": "reserved"}
                    journal.save(record, guard)
                else:
                    # An earlier marker rename may itself have failed its fsync.
                    # Make the complete intent durable again before resuming.
                    journal.save(record, guard)
                if (record["request_fingerprint"] != operation_id
                    or record["comparison_fingerprint"] != comparison["comparison_fingerprint"]):
                    raise ReelBenchRecoveryRequiredError("journal comparison or request changed")
                expected_binding = self._receipt(project_id, subject_family, record["subject"]["payload"], comparison)
                if any(record["binding"]["payload"].get(k) != v for k, v in expected_binding.items()):
                    raise ReelBenchRecoveryRequiredError("journal binding differs from exact subject")
                attempted = True
                subject = self._publish_or_reconcile(fd, project_id, record["subject"], guard,
                    must_exist=record["phase"] != "reserved")
                if record["phase"] == "reserved":
                    record["phase"] = "subject"
                    journal.save(record, guard)
                binding = self._publish_or_reconcile(fd, project_id, record["binding"], guard,
                    must_exist=record["phase"] in {"binding", "complete"})
                validate_reelbench_binding(binding)
                record["phase"] = "binding"
                journal.save(record, guard)
                journal.finish(record, guard)
                if after_commit is not None:
                    after_commit(subject)
                guard()
            return subject, binding
        except BaseException as exc:
            if attempted and record is not None:
                raise CompositeBindingIndeterminateError(record, self._store._root / project_id) from exc
            raise


__all__ = ["CompositeBindingIndeterminateError", "ReelBenchBindingService"]
