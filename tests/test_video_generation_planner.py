from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone

from scripts.json_contracts import canonical_fingerprint, validate_contract
from scripts.video_service import build_video_request_fingerprint

try:
    from scripts.video_generation_planner import (
        CostBasisError,
        PlanningError,
        VideoGenerationPlanner,
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
        "rights_receipt_id": "rr_" + "4" * 24, "creative_mode": "authorized_replication",
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

    def test_unicode_base_and_retry_use_shared_canonical_fingerprint(self):
        quote = self.planner._plan_materialized(
            design([shot(prompt="晨雾中的蓝色工作室")]), self.snapshot, self.cost
        )
        self.assertEqual(quote["items"][0]["request_fingerprints"], [
            "0a8030925a839f0877da3effcf931da689a2aa114fc0c58b818eedd10f7c72f3",
            "b69ae47f788ea137e1057859e72a7afd1cd94311c6f5e2fff4fd630478340fa2",
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
        self.assertEqual(quote["output_profile"], {"container": "mp4", "codec": "h264"})
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


if __name__ == "__main__":
    unittest.main()
