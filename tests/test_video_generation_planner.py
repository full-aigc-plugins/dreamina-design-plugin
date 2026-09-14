from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.json_contracts import canonical_fingerprint, validate_contract
from scripts.video_service import build_video_request_fingerprint

try:
    from scripts.video_generation_planner import (
        CostBasisError,
        PlanningError,
        TrustedAnchorProvider,
        VideoGenerationPlanner,
        validate_batch_quote,
    )
    from scripts.video_service import UnsupportedCapabilityError
except ModuleNotFoundError:  # RED: Task 7 module does not exist yet.
    VideoGenerationPlanner = None  # type: ignore[assignment]


def snapshot() -> dict:
    return {
        "cli_version": "1.4.18",
        "captured_at": "2026-09-14T00:00:00Z",
        "modes": ["text2video", "image2video", "frames2video", "multiframe2video", "multimodal2video"],
        "models": [{
            "name": "seedance-2.5",
            "modes": ["text2video", "image2video", "frames2video", "multimodal2video"],
            "resolutions": ["720P"], "ratios": ["16:9"],
            "duration_min_seconds": 4, "duration_max_seconds": 30,
            "max_references": 8, "audio_reference_max_seconds": 30,
        }],
        "resolutions": {"video": ["720P"]}, "ratios": ["16:9"],
        "mode_limits": {"multiframe2video": {"min_references": 2, "max_references": 20, "request_duration_min_seconds": 2, "request_duration_max_seconds": 30, "transition_duration_min_seconds": 1, "transition_duration_max_seconds": 8}},
    }


def ref(name: str, role: str, **extra) -> dict:
    return {"path": f"/validated/{name}", "role": role, "sha256": "a" * 64, **extra}


def shot(**overrides) -> dict:
    value = {
        "id": "S01", "kind": "subject", "prompt": "A new presenter in a blue studio",
        "duration_seconds": 4, "model": "seedance-2.5", "video_resolution": "720P",
        "ratio": "16:9", "references": [], "max_attempts": 2,
        "repair_directives": ["temporal_stability"],
    }
    value.update(overrides)
    return value


def design(shots=None, **overrides) -> dict:
    value = {
        "schema_version": "1.0", "version": "v001", "project_id": "vp_" + "1" * 24,
        "source_sha256": "2" * 64, "design_fingerprint": "3" * 64,
        "rights_receipt_id": "rr_" + "4" * 24,
        "rights_receipt_fingerprint": "7" * 64,
        "creative_mode": "authorized_replication",
        "analysis_version": "v001", "machine_fingerprint": "5" * 64,
        "audio_policy": "silent", "output_destination": "/exports/final.mp4",
        "output_profile": {"container": "mp4", "codec": "h264"},
        "shots": shots or [shot()],
    }
    value.update(overrides)
    return value


class _ReferencePolicy:
    def validate(self, reference):
        return dict(reference)


class VideoGenerationPlannerTests(unittest.TestCase):
    def setUp(self):
        self.planner = VideoGenerationPlanner(reference_policy=_ReferencePolicy(), now=lambda: datetime(2026, 9, 14, 2, tzinfo=timezone.utc))
        self.snapshot = snapshot()
        self.cost = {"kind": "operator_ceiling", "credit_ceiling": 7, "currency": "credits", "source": "operator:launch-budget", "recorded_at": "2026-09-14T01:00:00Z"}

    def test_public_plan_rejects_fabricated_design_and_snapshot_mappings(self):
        with self.assertRaises(TypeError):
            self.planner.plan(design(), self.snapshot, self.cost)

    def test_persisted_plan_rejects_forged_provider_evidence_before_store_read(self):
        class Store:
            reads = 0
            def read_version(inner, *args, **kwargs):
                inner.reads += 1
                raise AssertionError("design must not be read before capability evidence passes")

        class Provider:
            def capture(inner):
                return {"snapshot": self.snapshot, "identity_receipt": {"fabricated": True}}

        store = Store()
        planner = VideoGenerationPlanner(
            project_store=store, capability_provider_factory=lambda: Provider(),
            now=lambda: datetime(2026, 9, 14, 2, tzinfo=timezone.utc),
        )
        with self.assertRaisesRegex(PlanningError, "TrustedCapabilityProvider"):
            planner.plan("vp_" + "1" * 24, "v001", self.cost, generation={}, output_destination="/x.mp4", output_profile={"container": "mp4", "codec": "h264"})
        self.assertEqual(store.reads, 0)

    def test_persisted_plan_rejects_nonfinite_snapshot_before_store_read(self):
        class Store:
            reads = 0
            def read_version(inner, *args, **kwargs):
                inner.reads += 1
                raise AssertionError("store read is forbidden")
        class Provider:
            def capture(inner):
                return {"snapshot": {**self.snapshot, "poison": float("nan")}, "identity_receipt": {}}
        store = Store()
        planner = VideoGenerationPlanner(project_store=store, capability_provider_factory=lambda: Provider())
        with self.assertRaisesRegex(PlanningError, "TrustedCapabilityProvider"):
            planner.plan("vp_" + "1" * 24, "v001", self.cost, generation={}, output_destination="/x.mp4", output_profile={"container": "mp4", "codec": "h264"})
        self.assertEqual(store.reads, 0)

    def test_persisted_plan_rejects_malicious_trusted_provider_subclass_before_capture(self):
        from scripts.trusted_capability_provider import TrustedCapabilityProvider

        calls = {"capture": 0, "store": 0}

        class MaliciousProvider(TrustedCapabilityProvider):
            def __init__(inner):
                pass
            def capture(inner):
                calls["capture"] += 1
                return {"snapshot": self.snapshot, "identity_receipt": {}}

        class Store:
            def read_version(inner, *args, **kwargs):
                calls["store"] += 1
                raise AssertionError("store must not be read")

        planner = VideoGenerationPlanner(
            project_store=Store(), capability_provider_factory=MaliciousProvider,
            now=lambda: datetime(2026, 9, 14, 2, tzinfo=timezone.utc),
        )
        with self.assertRaisesRegex(PlanningError, "TrustedCapabilityProvider"):
            planner.plan("vp_" + "1" * 24, "v001", self.cost, generation={}, output_destination="/x.mp4", output_profile={"container": "mp4", "codec": "h264"})
        self.assertEqual(calls, {"capture": 0, "store": 0})

    def test_modes_are_selected_deterministically(self):
        cases = [
            (shot(kind="establishing", references=[]), "text2video"),
            (shot(references=[ref("subject.png", "subject")]), "image2video"),
            (shot(references=[ref("style.png", "style")]), "image2video"),
            (shot(references=[ref("first.png", "frame"), ref("last.png", "frame")]), "frames2video"),
            (shot(storyboard=[ref("a.png", "frame"), ref("b.png", "frame"), ref("c.png", "frame")], references=[]), "multiframe2video"),
            (shot(references=[ref("clip.mp4", "reference"), ref("audio.wav", "audio", duration_seconds=4)]), "multimodal2video"),
        ]
        for candidate, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self.planner._plan_shot(candidate, self.snapshot)["mode"], expected)

    def test_quote_has_literal_fingerprints_and_total_for_every_enumerated_attempt(self):
        quote = self.planner._plan_materialized(design(), self.snapshot, self.cost)
        self.assertEqual(quote["items"][0]["request_fingerprints"], [
            "213655663daf0b749b0c05832c6a9e9a9ee6df240ececc8101ca7b91b8dfd5e6",
            "be80d8b1ee18282cff980f0b99579e6b48189fdacd07fc8c17866ce06047257f",
        ])
        self.assertEqual(quote["total_credit_ceiling"], 14)
        self.assertEqual(quote["item_count"], 1)
        self.assertEqual(quote["task_count"], 2)
        self.assertEqual(len(quote["items"][0]["attempts"]), 2)
        for attempt in quote["items"][0]["attempts"]:
            self.assertEqual(attempt["request_fingerprint"], build_video_request_fingerprint(attempt["request"]))
        validate_contract(quote, "video_batch_quote.schema.json")

    def test_original_quote_omits_rights_fields_and_replication_binds_receipt_bytes(self):
        replication = self.planner._plan_materialized(design(), self.snapshot, self.cost)
        self.assertEqual(replication["rights_receipt_fingerprint"], "7" * 64)
        original_design = design(
            creative_mode="original_redesign",
            rights_receipt_id=None,
            rights_receipt_fingerprint=None,
        )
        original = self.planner._plan_materialized(original_design, self.snapshot, self.cost)
        self.assertNotIn("rights_receipt_id", original)
        self.assertNotIn("rights_receipt_fingerprint", original)
        validate_contract(original, "video_batch_quote.schema.json")

        missing = copy.deepcopy(replication)
        del missing["rights_receipt_fingerprint"]
        with self.assertRaises(Exception):
            validate_contract(missing, "video_batch_quote.schema.json")

        forbidden = copy.deepcopy(original)
        forbidden["rights_receipt_id"] = "rr_" + "4" * 24
        with self.assertRaises(Exception):
            validate_contract(forbidden, "video_batch_quote.schema.json")

    def test_rights_receipt_tamper_invalidates_quote_fingerprint(self):
        quote = self.planner._plan_materialized(design(), self.snapshot, self.cost)
        quote["rights_receipt_fingerprint"] = "8" * 64
        with self.assertRaisesRegex(PlanningError, "quote_fingerprint"):
            self.planner.validate_quote(quote)

    def test_original_materialized_quote_rejects_caller_rights_fingerprint(self):
        original_design = design(
            creative_mode="original_redesign", rights_receipt_id=None,
            rights_receipt_fingerprint="9" * 64,
        )
        with self.assertRaisesRegex(PlanningError, "must not carry rights"):
            self.planner._plan_materialized(original_design, self.snapshot, self.cost)

    def test_unknown_price_blocks_instead_of_inventing_cost(self):
        with self.assertRaisesRegex(CostBasisError, "explicit operator ceiling"):
            self.planner._plan_materialized(design(), self.snapshot, None)

    def test_operator_cost_requires_source_and_timestamp(self):
        for missing in ("source", "recorded_at"):
            bad = dict(self.cost)
            del bad[missing]
            with self.subTest(missing=missing), self.assertRaises(CostBasisError):
                self.planner._plan_materialized(design(), self.snapshot, bad)

    def test_unadvertised_capabilities_fail_closed(self):
        for change in (
            {"model": "unknown"}, {"video_resolution": "4K"},
            {"duration_seconds": 31}, {"ratio": "21:9"},
        ):
            with self.subTest(change=change), self.assertRaises(UnsupportedCapabilityError):
                self.planner._plan_materialized(design([shot(**change)]), self.snapshot, self.cost)

    def test_snapshot_must_advertise_every_constraint_used_by_planning(self):
        missing_model_bounds = copy.deepcopy(self.snapshot)
        del missing_model_bounds["models"][0]["duration_max_seconds"]
        with self.assertRaisesRegex(UnsupportedCapabilityError, "duration bounds"):
            self.planner._plan_materialized(design(), missing_model_bounds, self.cost)

        missing_multiframe_bounds = copy.deepcopy(self.snapshot)
        del missing_multiframe_bounds["mode_limits"]
        storyboard = [ref("a.png", "frame"), ref("b.png", "frame")]
        with self.assertRaisesRegex(UnsupportedCapabilityError, "duration bounds"):
            self.planner._plan_materialized(
                design([shot(storyboard=storyboard, references=[], duration_seconds=4)]),
                missing_multiframe_bounds,
                self.cost,
            )

    def test_unicode_base_and_retry_keep_legacy_video_fingerprint_encoding(self):
        quote = self.planner._plan_materialized(
            design([shot(prompt="晨雾中的蓝色工作室")]), self.snapshot, self.cost
        )
        self.assertEqual(quote["items"][0]["request_fingerprints"], [
            "f12e3b94d504052c1448f9faf982a393129e73bcfcccc85a834667d48628fb86",
            "37ad109f93ea11c00045e4b3431d05b8d244b7715b15fd4f068b1b62c7507080",
        ])

    def test_live_pricing_must_be_fresh_and_consistent_with_snapshot_time(self):
        cases = {
            "stale": "2026-09-12T00:00:00Z",
            "future": "2026-09-14T02:06:00Z",
            "later than snapshot": "2026-09-14T01:00:00Z",
        }
        for label, captured_at in cases.items():
            live = copy.deepcopy(self.snapshot)
            live["pricing"] = {"credit_ceiling_per_attempt": 2, "source": "cli", "captured_at": captured_at}
            with self.subTest(label=label), self.assertRaisesRegex(CostBasisError, "pricing"):
                self.planner._plan_materialized(design(), live, None)
        live = copy.deepcopy(self.snapshot)
        live["pricing"] = {"credit_ceiling_per_attempt": 2, "source": "cli", "captured_at": "2026-09-14T00:03:00Z"}
        self.assertEqual(self.planner._plan_materialized(design(), live, None)["cost_basis"]["credit_ceiling"], 2)

    def test_cost_timestamps_require_strict_rfc3339_timezone(self):
        valid = ("2026-09-14T01:02:03Z", "2026-09-14T09:02:03+08:00")
        invalid = ("now", "2026-09-14 01:02:03", "2026-09-14T01:02:03", "2026-02-30T01:02:03Z")
        for value in valid:
            candidate = dict(self.cost, recorded_at=value)
            with self.subTest(valid=value):
                self.assertEqual(self.planner._plan_materialized(design(), self.snapshot, candidate)["quoted_at"], value)
        for value in invalid:
            candidate = dict(self.cost, recorded_at=value)
            with self.subTest(invalid=value), self.assertRaisesRegex(CostBasisError, "RFC3339"):
                self.planner._plan_materialized(design(), self.snapshot, candidate)

        live = copy.deepcopy(self.snapshot)
        live["pricing"] = {"credit_ceiling_per_attempt": 2, "source": "cli", "captured_at": "bad"}
        with self.assertRaisesRegex(CostBasisError, "RFC3339"):
            self.planner._plan_materialized(design(), live, None)

        missing_reference_limit = copy.deepcopy(self.snapshot)
        del missing_reference_limit["models"][0]["max_references"]
        with self.assertRaisesRegex(UnsupportedCapabilityError, "reference limit"):
            self.planner._plan_materialized(
                design([shot(references=[ref("subject.png", "subject")])]),
                missing_reference_limit,
                self.cost,
            )

        missing_audio_limit = copy.deepcopy(self.snapshot)
        del missing_audio_limit["models"][0]["audio_reference_max_seconds"]
        with self.assertRaisesRegex(UnsupportedCapabilityError, "audio reference duration"):
            self.planner._plan_materialized(
                design([shot(references=[ref("audio.wav", "audio", duration_seconds=4)])]),
                missing_audio_limit,
                self.cost,
            )

    def test_quote_counts_and_fingerprint_are_validated_against_materialized_tasks(self):
        quote = self.planner._plan_materialized(design(), self.snapshot, self.cost)
        self.planner.validate_quote(quote)
        for field in ("item_count", "task_count"):
            tampered = copy.deepcopy(quote)
            tampered[field] += 1
            with self.subTest(field=field), self.assertRaisesRegex(PlanningError, field):
                self.planner.validate_quote(tampered)
        tampered = copy.deepcopy(quote)
        tampered["items"][0]["attempts"][0]["credit_ceiling"] += 1
        with self.assertRaisesRegex(PlanningError, "attempt credit_ceiling"):
            self.planner.validate_quote(tampered)

    def test_storyboard_cannot_silently_discard_other_references(self):
        with self.assertRaisesRegex(PlanningError, "storyboard cannot be combined"):
            self.planner._plan_shot(
                shot(
                    storyboard=[ref("a.png", "frame"), ref("b.png", "frame")],
                    references=[ref("audio.wav", "audio", duration_seconds=4)],
                ),
                self.snapshot,
            )

    def test_duplicate_shot_ids_are_rejected_before_quote(self):
        with self.assertRaisesRegex(PlanningError, "duplicate shot id"):
            self.planner._plan_materialized(
                design([shot(id="S01"), shot(id="S01")]), self.snapshot, self.cost
            )

    def test_max_attempts_and_retry_vocabulary_are_closed(self):
        for attempts in (False, True, 0, 4, 1.5, "2"):
            with self.subTest(attempts=attempts), self.assertRaises(PlanningError):
                self.planner._plan_materialized(design([shot(max_attempts=attempts)]), self.snapshot, self.cost)
        with self.assertRaisesRegex(PlanningError, "closed repair directive"):
            self.planner._plan_materialized(design([shot(repair_directives=["make it nicer"])]), self.snapshot, self.cost)
        with self.assertRaisesRegex(PlanningError, "free-form retry prompt"):
            self.planner._plan_materialized(design([shot(retry_prompt="make it nicer")]), self.snapshot, self.cost)

    def test_quote_is_detached_from_mutable_inputs_and_binds_context(self):
        source = design()
        quote = self.planner._plan_materialized(source, self.snapshot, self.cost)
        source["shots"][0]["prompt"] = "tampered"
        self.assertEqual(quote["project_id"], "vp_" + "1" * 24)
        self.assertEqual(quote["design_fingerprint"], "3" * 64)
        self.assertEqual(quote["rights_receipt_id"], "rr_" + "4" * 24)
        self.assertEqual(quote["source_sha256"], "2" * 64)
        self.assertEqual(quote["audio_policy"], "silent")
        self.assertEqual(quote["output_destination"], "/exports/final.mp4")
        self.assertEqual(quote["output_profile"], {
            "container": "mp4", "codec": "h264", "width": 1280, "height": 720})
        self.assertNotEqual(quote["items"][0]["attempts"][0]["request"]["prompt"], "tampered")

    def test_quote_declares_target_duration_and_reserved_retries(self):
        quote = self.planner._plan_materialized(
            design([shot(id="S01", duration_seconds=4), shot(id="S02", duration_seconds=6)]),
            self.snapshot,
            self.cost,
        )
        self.assertEqual(quote["target_total_duration_seconds"], 10)
        self.assertEqual(quote["reserved_retry_count"], 2)

    def test_output_profile_is_h264_only(self):
        with self.assertRaisesRegex(Exception, "h264"):
            self.planner._plan_materialized(
                design(output_profile={"container": "mp4", "codec": "h265"}),
                self.snapshot,
                self.cost,
            )

    def test_output_dimensions_are_exact_or_derived_from_resolution_and_ratio(self):
        derived = self.planner._plan_materialized(design(), self.snapshot, self.cost)
        self.assertEqual((derived["output_profile"]["width"], derived["output_profile"]["height"]),
                         (1280, 720))
        with self.assertRaisesRegex(PlanningError, "dimensions"):
            self.planner._plan_materialized(design(output_profile={
                "container": "mp4", "codec": "h264", "width": 640, "height": 360}),
                self.snapshot, self.cost)

    def test_frame_mode_quote_binds_exact_approved_anchor_frame_digests(self):
        class AnchorMedia:
            def verify_image(self, path, *, source_fd=None):
                self.assert_fd = source_fd
                return {"sha256": hashlib.sha256((Path(path).name + "-decoded").encode()).hexdigest()}
        with tempfile.TemporaryDirectory() as root:
            refs = []
            for name in ("first.png", "last.png"):
                target = Path(root) / name
                target.write_bytes((name + " bytes").encode()); target.chmod(0o600)
                refs.append({"path": str(target), "role": "frame", "size_bytes": target.stat().st_size,
                             "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
            media = AnchorMedia()
            planner = VideoGenerationPlanner(reference_policy=_ReferencePolicy(),
                anchor_provider=TrustedAnchorProvider(media),
                now=lambda: datetime(2026, 9, 14, 2, tzinfo=timezone.utc))
            quote = planner._plan_materialized(design([shot(references=refs)]), self.snapshot, self.cost)
            attempts = quote["items"][0]["attempts"]
            self.assertEqual(len(attempts), 2)
            self.assertEqual(attempts[0]["anchor_contract"], attempts[1]["anchor_contract"])
            contract = attempts[0]["anchor_contract"]
            self.assertEqual(contract["start_anchor"]["reference_sha256"], refs[0]["sha256"])
            self.assertEqual(contract["start_anchor"]["reference_index"], 0)
            self.assertEqual(contract["end_anchor"]["reference_sha256"], refs[1]["sha256"])
            self.assertEqual(contract["end_anchor"]["reference_index"], 1)
            self.assertTrue(all("anchor_frame_sha256" not in reference
                                for attempt in attempts for reference in attempt["request"]["references"]))
            self.assertIsNotNone(media.assert_fd)
            forged = copy.deepcopy(quote)
            forged["items"][0]["attempts"][0].pop("anchor_contract")
            forged["quote_fingerprint"] = canonical_fingerprint(
                {key: value for key, value in forged.items() if key != "quote_fingerprint"})
            with self.assertRaisesRegex(PlanningError, "anchor contract"):
                validate_batch_quote(forged)

    def test_caller_anchor_digest_cannot_replace_trusted_anchor_provider(self):
        first = ref("first.png", "frame", anchor_frame_sha256="1" * 64)
        last = ref("last.png", "frame", anchor_frame_sha256="2" * 64)
        with self.assertRaisesRegex(PlanningError, "trusted anchor"):
            self.planner._plan_materialized(
                design([shot(references=[first, last])]), self.snapshot, self.cost)


if __name__ == "__main__":
    unittest.main()
