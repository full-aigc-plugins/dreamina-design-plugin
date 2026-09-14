from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from scripts.json_contracts import validate_contract
from scripts.video_evaluation_service import (
    EvaluationContractError,
    VideoEvaluationService,
    _ArtifactSnapshot,
)
from tests.test_video_batch_allowance import RecordingApprover, make_allowances, make_quote

MEASURED = ("artifact_integrity", "dimensions", "codec", "duration", "aspect_ratio",
            "frame_readability", "start_anchor", "end_anchor")
SEMANTIC = ("intent", "composition", "identity_continuity", "camera_behavior",
            "rhythm_function", "temporal_defects", "source_copying", "subtitle_safe_area")


def semantic(binding: dict, status: str = "passed") -> dict:
    return {"binding": copy.deepcopy(binding), "gates": {
        name: {"status": status, "evidence": f"reviewed {name}"} for name in SEMANTIC}}


class SyntheticTrustedMediaAdapter:
    def __init__(self):
        self.probe = {"streams": [{"codec_type": "video", "width": 1280, "height": 720,
                      "codec_name": "h264"}], "format": {"duration": "4.0"}}
        self.probe_source_fd = None
        self.frame_source_fd = None
    def probe_json(self, path, *, source_fd=None):
        self.probe_source_fd = source_fd
        return copy.deepcopy(self.probe)
    def verify_video_frames(self, path, duration_seconds, *, source_fd=None):
        self.frame_source_fd = source_fd
        frame = {"requested_at_seconds": 0.0, "sha256": "7" * 64, "size_bytes": 16}
        return {"readable": True, "start_anchor": frame,
                "end_anchor": {**frame, "requested_at_seconds": 3.95}}


class RecordingTemporaryDirectory:
    def __init__(self, name: str, *, error: BaseException | None = None):
        self.name = name
        self.error = error
        self.cleanup_calls = 0

    def cleanup(self):
        self.cleanup_calls += 1
        if self.error is not None:
            raise self.error


class ArtifactSnapshotCleanupTests(unittest.TestCase):
    def snapshot(self, temporary):
        snapshot = _ArtifactSnapshot.__new__(_ArtifactSnapshot)
        snapshot.snapshot_fd = 101
        snapshot.source_fd = 102
        snapshot.temporary = temporary
        return snapshot

    def test_cleanup_attempts_every_owned_resource_once_and_raises_first_error(self):
        for failures, expected in (
            ({101}, "snapshot close failed"),
            ({102}, "source close failed"),
            ({"temporary"}, "temporary cleanup failed"),
            ({101, 102, "temporary"}, "snapshot close failed"),
        ):
            with self.subTest(failures=failures):
                errors = {101: RuntimeError("snapshot close failed"),
                          102: RuntimeError("source close failed"),
                          "temporary": RuntimeError("temporary cleanup failed")}
                temporary = RecordingTemporaryDirectory(
                    "/unused", error=errors["temporary"] if "temporary" in failures else None)
                snapshot = self.snapshot(temporary)
                close_calls = []

                def close(descriptor):
                    close_calls.append(descriptor)
                    if descriptor in failures:
                        raise errors[descriptor]

                with mock.patch("scripts.video_evaluation_service.os.close", side_effect=close):
                    with self.assertRaisesRegex(RuntimeError, expected) as raised:
                        snapshot.__exit__(None, None, None)
                first_failed_resource = next(resource for resource in (101, 102, "temporary")
                                             if resource in failures)
                self.assertIs(raised.exception, errors[first_failed_resource])
                self.assertEqual(close_calls, [101, 102])
                self.assertEqual(temporary.cleanup_calls, 1)
                self.assertIsNone(snapshot.snapshot_fd)
                self.assertIsNone(snapshot.source_fd)
                self.assertIsNone(snapshot.temporary)
                with mock.patch("scripts.video_evaluation_service.os.close") as repeated_close:
                    snapshot.__exit__(None, None, None)
                repeated_close.assert_not_called()
                self.assertEqual(temporary.cleanup_calls, 1)

    def test_cleanup_does_not_mask_active_body_exception(self):
        body_error = LookupError("body failed")
        temporary = RecordingTemporaryDirectory(
            "/unused", error=RuntimeError("temporary cleanup failed"))
        snapshot = self.snapshot(temporary)
        close_calls = []

        def close(descriptor):
            close_calls.append(descriptor)
            raise RuntimeError("close failed")

        with mock.patch("scripts.video_evaluation_service.os.close", side_effect=close):
            with self.assertRaises(LookupError) as raised:
                with snapshot:
                    raise body_error
        self.assertIs(raised.exception, body_error)
        self.assertEqual(close_calls, [101, 102])
        self.assertEqual(temporary.cleanup_calls, 1)

    def test_constructor_failure_preserves_original_error_after_all_cleanup_attempts(self):
        original_error = LookupError("constructor failed")
        cleanup_errors = {"source": RuntimeError("source close failed"),
                          "snapshot": RuntimeError("snapshot close failed")}
        close_attempts = []
        opened = {}
        real_open = __import__("os").open
        real_close = __import__("os").close
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source.mp4"
            source.write_bytes(b"private video")
            source.chmod(0o600)
            staging = Path(root) / "staging"
            staging.mkdir()
            temporary = RecordingTemporaryDirectory(
                str(staging), error=RuntimeError("temporary cleanup failed"))

            def open_file(path, flags, mode=0o777):
                descriptor = real_open(path, flags, mode)
                if Path(path) == source:
                    opened["source"] = descriptor
                elif Path(path).name == "artifact.snapshot" \
                        and not flags & __import__("os").O_WRONLY:
                    opened["snapshot"] = descriptor
                return descriptor

            def close_file(descriptor):
                kind = next((name for name, fd in opened.items() if fd == descriptor), "output")
                if kind != "output":
                    close_attempts.append(kind)
                real_close(descriptor)
                if kind in cleanup_errors:
                    raise cleanup_errors[kind]

            artifact = {"path": str(source), "size_bytes": source.stat().st_size}
            with mock.patch("scripts.video_evaluation_service.tempfile.TemporaryDirectory",
                            return_value=temporary), \
                    mock.patch("scripts.video_evaluation_service.os.open", side_effect=open_file), \
                    mock.patch("scripts.video_evaluation_service.os.close", side_effect=close_file), \
                    mock.patch.object(_ArtifactSnapshot, "assert_stable", side_effect=original_error):
                with self.assertRaises(LookupError) as raised:
                    _ArtifactSnapshot(artifact, max_bytes=1024)
            self.assertIs(raised.exception, original_error)
            self.assertEqual(close_attempts, ["snapshot", "source"])
            self.assertEqual(temporary.cleanup_calls, 1)

    def test_snapshot_uses_the_single_exclusive_output_descriptor_without_reopen(self):
        real_open = __import__("os").open
        opened = []
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source.mp4"
            source.write_bytes(b"private video"); source.chmod(0o600)
            def recording_open(path, flags, mode=0o777, **kwargs):
                opened.append((Path(path).name, flags))
                return real_open(path, flags, mode, **kwargs)
            with mock.patch("scripts.video_evaluation_service.os.open", side_effect=recording_open):
                with _ArtifactSnapshot({"path": str(source), "size_bytes": source.stat().st_size},
                                       max_bytes=1024) as snapshot:
                    self.assertIsNotNone(snapshot.snapshot_fd)
            snapshot_opens = [flags for name, flags in opened if name == "artifact.snapshot"]
            self.assertEqual(len(snapshot_opens), 1)
            self.assertTrue(snapshot_opens[0] & __import__("os").O_EXCL)
            self.assertTrue(snapshot_opens[0] & __import__("os").O_RDWR)


class VideoEvaluationServiceTests(unittest.TestCase):
    def setUp(self):
        self.media = SyntheticTrustedMediaAdapter()
        self.service = VideoEvaluationService(media_adapter=self.media,
            now=lambda: datetime(2026, 9, 14, 2, tzinfo=timezone.utc))
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.clip = Path(self.tmp.name) / "clip.mp4"
        self.clip.write_bytes(b"\0\0\0\x18ftypisom" + b"x" * 20)
        self.clip.chmod(0o600)
        digest = hashlib.sha256(self.clip.read_bytes()).hexdigest()
        self.quote = make_quote()
        self.binding = {"project_id": self.quote["project_id"], "batch_version": self.quote["quote_version"],
                        "shot_id": "S01", "attempt": 1, "submit_id": "submit_1", "artifact_sha256": digest,
                        "design_version": self.quote["design_version"], "design_fingerprint": self.quote["design_fingerprint"],
                        "quote_fingerprint": self.quote["quote_fingerprint"], "allowance_id": "ba_" + "d" * 32}
        self.artifact = {**self.binding, "path": str(self.clip), "sha256": digest, "verified_sha256": digest,
                         "mime_type": "video/mp4", "size_bytes": self.clip.stat().st_size,
                         "provenance": "externally-queried",
                         "probe": {"width": 1280, "height": 720, "codec": "h264", "duration_seconds": 4.0},
                         "frames": {"readable": True, "start_anchor": True, "end_anchor": True}}
        self.shot = {**self.binding, "id": "S01", "width": 1280, "height": 720,
                     "codec": "h264", "duration_seconds": 4.0, "aspect_ratio": "16:9"}

    def test_measured_gates_are_one_closed_keyed_object(self):
        result = self.service.measure_clip(self.artifact, self.shot, binding=self.binding)
        self.assertEqual(set(result["gates"]), set(MEASURED))
        self.assertTrue(all(gate["status"] == "passed" for gate in result["gates"].values()))
        self.assertIsNotNone(self.media.probe_source_fd)
        self.assertEqual(self.media.probe_source_fd, self.media.frame_source_fd)

    def test_valid_semantic_skip_has_semantic_not_measured_unavailable_reason(self):
        payload = semantic(self.binding)
        payload["gates"]["intent"] = {"status": "skipped", "evidence": "model could not determine intent"}
        allowance = {"allowance_id": self.binding["allowance_id"], "project_id": self.binding["project_id"],
                     "quote_version": self.binding["batch_version"], "quote_fingerprint": self.binding["quote_fingerprint"],
                     "design_version": self.binding["design_version"], "design_fingerprint": self.binding["design_fingerprint"],
                     "state": "active", "requests": [], "reservations": []}
        receipt = self.service.evaluate(self.artifact, self.shot, payload, binding=self.binding,
            allowance=allowance, quote=self.quote, evaluation_id="eval_semantic_skip",
            evaluator={"provider": "codex", "model": "m", "evaluated_at": "2026-09-14T02:00:00Z"})
        self.assertEqual(receipt["decision"]["manual_review_reasons"], ["semantic_evidence_unavailable"])

    def test_semantic_gate_object_rejects_missing_extra_and_wrong_domain(self):
        missing = semantic(self.binding); missing["gates"].pop("intent")
        extra = semantic(self.binding); extra["gates"]["watermark"] = {"status": "passed", "evidence": "x"}
        wrong = semantic(self.binding); wrong["gates"]["duration"] = wrong["gates"].pop("intent")
        for payload in (missing, extra, wrong):
            with self.subTest(keys=sorted(payload["gates"])), self.assertRaises(EvaluationContractError):
                self.service.validate_semantic_evaluation(payload)

    def test_malformed_semantic_payload_returns_complete_manual_receipt(self):
        payload = semantic(self.binding); payload["gates"].pop("intent"); payload["gates"]["duration"] = {"status": "passed", "evidence": "forged"}
        allowance = {"allowance_id": self.binding["allowance_id"], "project_id": self.binding["project_id"],
                     "quote_version": self.binding["batch_version"], "quote_fingerprint": self.binding["quote_fingerprint"],
                     "design_version": self.binding["design_version"], "design_fingerprint": self.binding["design_fingerprint"],
                     "state": "active", "requests": [], "reservations": []}
        receipt = self.service.evaluate(self.artifact, self.shot, payload, binding=self.binding,
            allowance=allowance, quote=self.quote, evaluation_id="eval_malformed",
            evaluator={"provider": "codex", "model": "test", "evaluated_at": "2026-09-14T02:00:00Z"})
        self.assertEqual(receipt["decision"]["action"], "manual_review")
        self.assertTrue(receipt["decision"]["manual_review_reasons"])
        validate_contract(receipt, "shot_evaluation.schema.json")

    def test_semantic_binding_conflict_returns_manual_review_receipt(self):
        payload = semantic(self.binding); payload["binding"]["quote_fingerprint"] = "f" * 64
        allowance = {"allowance_id": self.binding["allowance_id"], "project_id": self.binding["project_id"],
                     "quote_version": self.binding["batch_version"], "quote_fingerprint": self.binding["quote_fingerprint"],
                     "design_version": self.binding["design_version"], "design_fingerprint": self.binding["design_fingerprint"],
                     "state": "active", "requests": [], "reservations": []}
        receipt = self.service.evaluate(self.artifact, self.shot, payload, binding=self.binding, allowance=allowance, quote=self.quote,
            evaluation_id="eval_conflict", evaluator={"provider": "codex", "model": "test",
            "evaluated_at": "2026-09-14T02:00:00Z"})
        self.assertEqual(receipt["decision"]["action"], "manual_review")
        self.assertTrue(all(gate["status"] == "skipped" for gate in receipt["semantic_gates"].values()))

    def test_artifact_and_design_binding_conflicts_return_manual_receipt(self):
        allowance = {"allowance_id": self.binding["allowance_id"], "project_id": self.binding["project_id"],
                     "quote_version": self.binding["batch_version"], "quote_fingerprint": self.binding["quote_fingerprint"],
                     "design_version": self.binding["design_version"], "design_fingerprint": self.binding["design_fingerprint"],
                     "state": "active", "requests": [], "reservations": []}
        for source in ("artifact", "design"):
            artifact, design = copy.deepcopy(self.artifact), copy.deepcopy(self.shot)
            (artifact if source == "artifact" else design)["shot_id"] = "S99"
            receipt = self.service.evaluate(artifact, design, semantic(self.binding), binding=self.binding,
                allowance=allowance, quote=self.quote, evaluation_id=f"eval_{source}_conflict",
                evaluator={"provider": "codex", "model": "test", "evaluated_at": "2026-09-14T02:00:00Z"})
            self.assertEqual(receipt["decision"]["action"], "manual_review")
            validate_contract(receipt, "shot_evaluation.schema.json")

    def test_missing_measured_evidence_is_unavailable(self):
        artifact = copy.deepcopy(self.artifact); self.media.probe["format"].pop("duration")
        self.assertEqual(self.service.measure_clip(artifact, self.shot, binding=self.binding)["gates"]["duration"]["status"], "skipped")
        allowance = {"allowance_id": self.binding["allowance_id"], "project_id": self.binding["project_id"],
                     "quote_version": self.binding["batch_version"], "quote_fingerprint": self.binding["quote_fingerprint"],
                     "design_version": self.binding["design_version"], "design_fingerprint": self.binding["design_fingerprint"],
                     "state": "active", "requests": [], "reservations": []}
        receipt = self.service.evaluate(artifact, self.shot, semantic(self.binding), binding=self.binding, allowance=allowance,
            quote=self.quote, evaluation_id="eval_missing", evaluator={"provider": "codex", "model": "test",
            "evaluated_at": "2026-09-14T02:00:00Z"})
        self.assertEqual(receipt["decision"]["action"], "manual_review")

    def test_quote_media_contract_overrides_untrusted_receipt_probe_claims(self):
        self.media.probe["streams"][0].update(width=640, height=360)
        claimed = copy.deepcopy(self.shot)
        claimed.update(width=640, height=360, aspect_ratio="16:9")
        allowance = {"allowance_id": self.binding["allowance_id"], "project_id": self.binding["project_id"],
                     "quote_version": self.binding["batch_version"], "quote_fingerprint": self.binding["quote_fingerprint"],
                     "design_version": self.binding["design_version"], "design_fingerprint": self.binding["design_fingerprint"],
                     "state": "active", "requests": [], "reservations": []}
        receipt = self.service.evaluate(self.artifact, claimed, semantic(self.binding), binding=self.binding,
            allowance=allowance, quote=self.quote, evaluation_id="eval_quote_contract",
            evaluator={"provider": "codex", "model": "test", "evaluated_at": "2026-09-14T02:00:00Z"})
        self.assertEqual(receipt["measured_gates"]["dimensions"]["status"], "failed")
        self.assertEqual(receipt["decision"]["action"], "manual_review")

    def test_empty_frame_output_is_unavailable_and_requires_manual_review(self):
        self.media.verify_video_frames = lambda path, duration, **kwargs: {}
        allowance = {"allowance_id": self.binding["allowance_id"], "project_id": self.binding["project_id"],
                     "quote_version": self.binding["batch_version"], "quote_fingerprint": self.binding["quote_fingerprint"],
                     "design_version": self.binding["design_version"], "design_fingerprint": self.binding["design_fingerprint"],
                     "state": "active", "requests": [], "reservations": []}
        receipt = self.service.evaluate(self.artifact, self.shot, semantic(self.binding), binding=self.binding,
            allowance=allowance, quote=self.quote, evaluation_id="eval_empty_frames",
            evaluator={"provider": "codex", "model": "test", "evaluated_at": "2026-09-14T02:00:00Z"})
        self.assertEqual(receipt["measured_gates"]["start_anchor"]["status"], "skipped")
        self.assertEqual(receipt["decision"]["action"], "manual_review")

    def test_approved_anchor_digests_are_compared_and_missing_contract_is_unavailable(self):
        common = {"width": 1280, "height": 720, "codec": "h264", "duration_seconds": 4,
                  "anchor_required": True}
        matching = {**common, "anchor_contract": {
            "start_anchor": {"frame_sha256": "7" * 64},
            "end_anchor": {"frame_sha256": "7" * 64}}}
        measured = self.service.measure_clip(self.artifact, self.shot, binding=self.binding,
                                             expected_media=matching)
        self.assertEqual(measured["gates"]["start_anchor"]["status"], "passed")
        mismatched = copy.deepcopy(matching)
        mismatched["anchor_contract"]["end_anchor"]["frame_sha256"] = "8" * 64
        measured = self.service.measure_clip(self.artifact, self.shot, binding=self.binding,
                                             expected_media=mismatched)
        self.assertEqual(measured["gates"]["end_anchor"]["status"], "failed")
        missing = self.service.measure_clip(self.artifact, self.shot, binding=self.binding,
                                            expected_media=common)
        self.assertEqual(missing["gates"]["start_anchor"]["status"], "skipped")
        self.assertIn("anchor_evidence_unavailable", missing["manual_review_reasons"])

    def test_real_allowance_and_quote_select_only_exact_available_retry(self):
        allowance_service = make_allowances(Path(self.tmp.name) / "allowance-root", Path(self.tmp.name) / "seal.key")
        allowance_id = allowance_service.activate(self.quote, RecordingApprover())
        self.binding["allowance_id"] = allowance_id; self.artifact.update(self.binding); self.shot.update(self.binding)
        payload = semantic(self.binding); payload["gates"]["temporal_defects"]["status"] = "failed"
        evaluator = {"provider": "codex", "model": "test-model", "evaluated_at": "2026-09-14T02:00:00Z"}
        receipt = self.service.evaluate(self.artifact, self.shot, payload, binding=self.binding,
            allowance=allowance_service.get(allowance_id), quote=self.quote,
            evaluation_id="eval_real_1", evaluator=evaluator)
        retry = self.quote["items"][0]["attempts"][1]
        self.assertEqual(receipt["decision"], {"action": "retry", "repair_directive": "temporal_stability",
                                               "request_fingerprint": retry["request_fingerprint"]})
        validate_contract(receipt, "shot_evaluation.schema.json")
        retry_reservation = allowance_service.reserve(allowance_id, shot_id="S01", attempt=2,
                                                       request_fingerprint=retry["request_fingerprint"])
        blocked = self.service.evaluate(self.artifact, self.shot, payload, binding=self.binding,
            allowance=allowance_service.get(allowance_id), quote=self.quote,
            evaluation_id="eval_real_2", evaluator={**evaluator, "evaluated_at": "2026-09-14T02:00:01Z"})
        self.assertEqual(blocked["decision"]["action"], "manual_review")
        initial = self.quote["items"][0]["attempts"][0]
        initial_reservation = allowance_service.reserve(allowance_id, shot_id="S01", attempt=1,
            request_fingerprint=initial["request_fingerprint"])
        allowance_service.commit(retry_reservation["reservation_id"], "submit_retry")
        allowance_service.commit(initial_reservation["reservation_id"], "submit_initial")
        exhausted = self.service.evaluate(self.artifact, self.shot, payload, binding=self.binding,
            allowance=allowance_service.get(allowance_id), quote=self.quote,
            evaluation_id="eval_real_3", evaluator={**evaluator, "evaluated_at": "2026-09-14T02:00:02Z"})
        self.assertEqual(exhausted["decision"]["action"], "manual_review")

    def test_complete_receipt_keeps_artifact_gate_and_evaluator_evidence(self):
        allowance = {"allowance_id": self.binding["allowance_id"], "project_id": self.binding["project_id"],
                     "quote_version": self.binding["batch_version"], "quote_fingerprint": self.binding["quote_fingerprint"],
                     "design_version": self.binding["design_version"], "design_fingerprint": self.binding["design_fingerprint"],
                     "state": "active", "requests": [], "reservations": []}
        receipt = self.service.evaluate(self.artifact, self.shot, semantic(self.binding), binding=self.binding, allowance=allowance,
            quote=self.quote, evaluation_id="eval_complete", evaluator={"provider": "codex", "model": "test-model",
            "evaluated_at": "2026-09-14T02:00:00Z"})
        self.assertEqual(set(receipt), {"schema_version", "evaluation_id", "created_at", "binding",
            "artifact_evidence", "measured_gates", "semantic_gates", "failed_gates", "decision",
            "semantic_evaluator", "evaluation_fingerprint"})
        self.assertEqual(receipt["decision"], {"action": "accepted"})
        self.assertEqual(receipt["binding"]["submit_id"], "submit_1")
        self.assertEqual(receipt["created_at"], "2026-09-14T02:00:00Z")
        self.assertEqual(receipt["semantic_evaluator"]["trust_level"], "untrusted")
        validate_contract(receipt, "shot_evaluation.schema.json")

    def test_path_replacement_or_in_place_mutation_cannot_mix_artifact_evidence(self):
        allowance = {"allowance_id": self.binding["allowance_id"], "project_id": self.binding["project_id"],
                     "quote_version": self.binding["batch_version"], "quote_fingerprint": self.binding["quote_fingerprint"],
                     "design_version": self.binding["design_version"], "design_fingerprint": self.binding["design_fingerprint"],
                     "state": "active", "requests": [], "reservations": []}
        original_probe = self.media.probe_json
        for mutation in ("replace", "in_place"):
            with self.subTest(mutation=mutation):
                original = b"\0\0\0\x18ftypisom" + b"x" * 20
                self.clip.write_bytes(original); self.clip.chmod(0o600)
                self.binding["artifact_sha256"] = hashlib.sha256(original).hexdigest()
                self.artifact.update(self.binding, sha256=self.binding["artifact_sha256"], size_bytes=len(original))
                self.shot.update(self.binding)
                def mutating_probe(path, selected=mutation, **kwargs):
                    if selected == "replace":
                        self.clip.rename(self.clip.with_suffix(".old"))
                        self.clip.write_bytes(b"y" * len(original)); self.clip.chmod(0o600)
                    else:
                        self.clip.write_bytes(b"z" * len(original)); self.clip.chmod(0o600)
                    return original_probe(path, **kwargs)
                self.media.probe_json = mutating_probe
                receipt = self.service.evaluate(self.artifact, self.shot, semantic(self.binding),
                    binding=self.binding, allowance=allowance, quote=self.quote,
                    evaluation_id=f"eval_mutation_{mutation}", evaluator={"provider": "codex", "model": "test",
                    "evaluated_at": "2026-09-14T02:00:00Z"})
                self.assertEqual(receipt["decision"]["action"], "manual_review")
                self.assertIn("artifact_evidence_unavailable", receipt["decision"]["manual_review_reasons"])
                self.media.probe_json = original_probe

    def test_malformed_semantic_evidence_is_bounded_manual_and_canonical_ordered(self):
        allowance = {"allowance_id": self.binding["allowance_id"], "project_id": self.binding["project_id"],
                     "quote_version": self.binding["batch_version"], "quote_fingerprint": self.binding["quote_fingerprint"],
                     "design_version": self.binding["design_version"], "design_fingerprint": self.binding["design_fingerprint"],
                     "state": "active", "requests": [], "reservations": []}
        payload = semantic(self.binding)
        payload["gates"] = dict(reversed(list(payload["gates"].items())))
        payload["gates"]["intent"]["evidence"] = "x" * 257
        receipt = self.service.evaluate(self.artifact, self.shot, payload, binding=self.binding,
            allowance=allowance, quote=self.quote, evaluation_id="eval_long_semantic",
            evaluator={"provider": "caller-claim", "model": "m", "evaluated_at": "2099-01-01T00:00:00Z"})
        self.assertEqual(receipt["decision"]["action"], "manual_review")
        self.assertEqual(list(receipt["semantic_gates"]), list(SEMANTIC))
        self.assertEqual(receipt["failed_gates"], list(SEMANTIC))
        self.assertEqual(receipt["semantic_evaluator"]["provider_claim"], "unavailable")
        self.assertIn("semantic_evidence_invalid", receipt["decision"]["manual_review_reasons"])
        self.assertIn("semantic_evaluator_invalid", receipt["decision"]["manual_review_reasons"])
        validate_contract(receipt, "shot_evaluation.schema.json")

    def test_semantic_insertion_order_does_not_change_receipt_fingerprint(self):
        allowance = {"allowance_id": self.binding["allowance_id"], "project_id": self.binding["project_id"],
                     "quote_version": self.binding["batch_version"], "quote_fingerprint": self.binding["quote_fingerprint"],
                     "design_version": self.binding["design_version"], "design_fingerprint": self.binding["design_fingerprint"],
                     "state": "active", "requests": [], "reservations": []}
        first = semantic(self.binding)
        second = {"binding": copy.deepcopy(self.binding),
                  "gates": dict(reversed(list(first["gates"].items())))}
        kwargs = {"binding": self.binding, "allowance": allowance, "quote": self.quote,
                  "evaluation_id": "eval_order", "evaluator": {"provider": "codex", "model": "m",
                  "evaluated_at": "2026-09-14T02:00:00Z"}}
        a = self.service.evaluate(self.artifact, self.shot, first, **kwargs)
        b = self.service.evaluate(self.artifact, self.shot, second, **kwargs)
        self.assertEqual(a, b)
        self.assertEqual(a["semantic_evaluator"]["provider_claim"], "codex")
        self.assertEqual(a["semantic_evaluator"]["trust_level"], "untrusted")

    def test_binding_conflict_with_passing_gates_has_closed_manual_reason(self):
        allowance = {"allowance_id": self.binding["allowance_id"], "project_id": self.binding["project_id"],
                     "quote_version": self.binding["batch_version"], "quote_fingerprint": self.binding["quote_fingerprint"],
                     "design_version": self.binding["design_version"], "design_fingerprint": self.binding["design_fingerprint"],
                     "state": "active", "requests": [], "reservations": []}
        quote = copy.deepcopy(self.quote)
        quote["quote_fingerprint"] = "f" * 64
        receipt = self.service.evaluate(self.artifact, self.shot, semantic(self.binding), binding=self.binding,
            allowance=allowance, quote=quote, evaluation_id="eval_quote_conflict",
            evaluator={"provider": "codex", "model": "m", "evaluated_at": "2026-09-14T02:00:00Z"})
        self.assertEqual(receipt["decision"]["action"], "manual_review")
        self.assertEqual(receipt["decision"]["manual_review_reasons"], ["quote_binding_conflict"])
        self.assertEqual(receipt["failed_gates"], [])
        validate_contract(receipt, "shot_evaluation.schema.json")

    def test_public_or_symlink_artifact_is_unavailable_not_measured(self):
        allowance = {"allowance_id": self.binding["allowance_id"], "project_id": self.binding["project_id"],
                     "quote_version": self.binding["batch_version"], "quote_fingerprint": self.binding["quote_fingerprint"],
                     "design_version": self.binding["design_version"], "design_fingerprint": self.binding["design_fingerprint"],
                     "state": "active", "requests": [], "reservations": []}
        for unsafe in ("public", "symlink"):
            self.clip.write_bytes(b"\0\0\0\x18ftypisom" + b"x" * 20); self.clip.chmod(0o600)
            artifact = copy.deepcopy(self.artifact)
            if unsafe == "public":
                self.clip.chmod(0o644)
            else:
                link = self.clip.with_name("link.mp4")
                link.symlink_to(self.clip)
                artifact["path"] = str(link)
            with self.subTest(unsafe=unsafe):
                receipt = self.service.evaluate(artifact, self.shot, semantic(self.binding),
                    binding=self.binding, allowance=allowance, quote=self.quote,
                    evaluation_id=f"eval_{unsafe}", evaluator={"provider": "codex", "model": "m",
                    "evaluated_at": "2026-09-14T02:00:00Z"})
                self.assertEqual(receipt["decision"]["action"], "manual_review")
                self.assertIn("artifact_evidence_unavailable", receipt["decision"]["manual_review_reasons"])


if __name__ == "__main__": unittest.main()
