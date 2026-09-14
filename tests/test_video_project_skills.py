"""Additive video-project Skills: discovery, routing, separation, and safety."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.router_skill import PROJECT_INTENT, Router  # noqa: E402
from scripts.verify_skill_snapshot import (  # noqa: E402
    PLUGIN_OWNED_SKILLS,
    SnapshotVerifier,
)

SKILLS_ROOT = ROOT / "skills"
UPSTREAM = Path(
    "/Users/wandl/workspaces/workspace-agent-skills/full-aigc-skills-repositories/dreamina-skills"
)

ORCHESTRATOR = SKILLS_ROOT / "codex-dreamina-video-production" / "SKILL.md"
ANNOTATOR = SKILLS_ROOT / "codex-dreamina-shot-annotator" / "SKILL.md"
EVALUATOR = SKILLS_ROOT / "codex-dreamina-video-evaluator" / "SKILL.md"


class SkillPresenceTests(unittest.TestCase):
    def test_three_additive_skills_exist_with_frontmatter(self) -> None:
        for path in (ORCHESTRATOR, ANNOTATOR, EVALUATOR):
            with self.subTest(skill=path.parent.name):
                self.assertTrue(path.is_file())
                text = path.read_text(encoding="utf-8")
                self.assertTrue(text.startswith("---\n"))
                frontmatter = text.split("---\n")[1]
                self.assertIn(f"name: {path.parent.name}", frontmatter)
                self.assertIn("description:", frontmatter)

    def test_plugin_owned_set_matches_the_plan(self) -> None:
        self.assertEqual(
            set(PLUGIN_OWNED_SKILLS),
            {
                "codex-dreamina-design-use",
                "codex-dreamina-video-production",
                "codex-dreamina-shot-annotator",
                "codex-dreamina-video-evaluator",
            },
        )


class RouterTests(unittest.TestCase):
    def test_reference_video_routes_to_the_project_orchestrator(self) -> None:
        self.assertEqual(
            Router().route(intent=PROJECT_INTENT, mode="reference"),
            "codex-dreamina-video-production",
        )

    def test_existing_direct_routes_are_unchanged(self) -> None:
        router = Router()
        self.assertEqual(router.route(intent="video", mode="text2video"), "dreamina-cli-text2video")
        self.assertEqual(router.route(intent="video", mode="image2video"), "dreamina-cli-image2video")
        self.assertEqual(router.route(intent="image", mode="text2image"), "dreamina-cli-text2image")
        self.assertEqual(router.route(intent="image", mode="image2image"), "dreamina-cli-image2image")

    def test_unknown_project_mode_is_ambiguous_not_silent(self) -> None:
        from scripts.router_skill import AmbiguousRoutingError

        with self.assertRaises(AmbiguousRoutingError):
            Router().route(intent=PROJECT_INTENT, mode="invented")


class SchemaSeparationTests(unittest.TestCase):
    """The two specialist Skills must own disjoint output schemas.

    A Skill may *name* the other schema in order to forbid producing it; what
    matters is that each claims exactly one schema as its own output.
    """

    def test_annotator_owns_only_the_annotation_schema(self) -> None:
        text = ANNOTATOR.read_text(encoding="utf-8")
        self.assertIn("shot_annotation.schema.json", text)
        # Naming the evaluation schema is allowed only as a prohibition.
        self.assertIn("Do not produce `shot_evaluation.schema.json`", text)
        self.assertNotIn("Emit evaluations", text)

    def test_evaluator_owns_only_the_evaluation_schema(self) -> None:
        text = EVALUATOR.read_text(encoding="utf-8")
        self.assertIn("shot_evaluation.schema.json", text)
        self.assertIn("Do not produce `shot_annotation.schema.json`", text)
        self.assertNotIn("Emit annotations", text)

    def test_neither_skill_claims_the_others_output(self) -> None:
        annotator = ANNOTATOR.read_text(encoding="utf-8")
        evaluator = EVALUATOR.read_text(encoding="utf-8")
        self.assertIn("produces `shot_annotation.schema.json` content and", annotator)
        self.assertIn("produces\n`shot_evaluation.schema.json` content and", evaluator)

    def test_orchestrator_keeps_design_and_verdict_separate(self) -> None:
        text = ORCHESTRATOR.read_text(encoding="utf-8")
        self.assertIn("both design and acceptance verdict", text)


class SafetyInstructionTests(unittest.TestCase):
    def test_orchestrator_reads_status_before_transitioning(self) -> None:
        text = ORCHESTRATOR.read_text(encoding="utf-8").lower()
        self.assertIn("read status first", text)
        self.assertIn("never guess the current state", text)

    def test_orchestrator_forbids_direct_binary_calls(self) -> None:
        text = ORCHESTRATOR.read_text(encoding="utf-8").lower()
        self.assertIn("do not call `ffmpeg`", text)

    def test_orchestrator_forbids_resubmission_and_rights_invention(self) -> None:
        lowered = ORCHESTRATOR.read_text(encoding="utf-8").lower()
        self.assertIn("query-only", lowered)
        self.assertIn("do not assert rights on the user's behalf", lowered)

    def test_evaluator_forbids_claims_beyond_the_quote(self) -> None:
        lowered = EVALUATOR.read_text(encoding="utf-8").lower()
        self.assertIn("never invent an attempt beyond the quote", lowered)
        self.assertIn("do not resubmit", lowered)

    def test_no_skill_claims_a_skipped_gate_passed(self) -> None:
        for path in (ORCHESTRATOR, ANNOTATOR, EVALUATOR):
            lowered = path.read_text(encoding="utf-8").lower()
            self.assertIn("never claim a skipped gate passed" if path is ORCHESTRATOR else "never", lowered)


class SnapshotReportingTests(unittest.TestCase):
    def test_upstream_parity_still_counts_exactly_thirteen(self) -> None:
        report = SnapshotVerifier(skills_root=SKILLS_ROOT, upstream_root=None).run()
        self.assertEqual(report.upstream_skill_count, 13)
        self.assertEqual(report.skill_count, 13)

    def test_plugin_owned_skills_are_reported_separately(self) -> None:
        report = SnapshotVerifier(skills_root=SKILLS_ROOT, upstream_root=None).run()
        self.assertEqual(set(report.plugin_owned_skill_names), set(PLUGIN_OWNED_SKILLS))
        self.assertTrue(set(report.plugin_owned_skill_names).isdisjoint(set(report.skill_names)))

    @unittest.skipUnless(UPSTREAM.is_dir(), f"upstream checkout not present at {UPSTREAM}")
    def test_upstream_parity_holds_byte_for_byte(self) -> None:
        report = SnapshotVerifier(skills_root=SKILLS_ROOT, upstream_root=UPSTREAM).run()
        self.assertEqual(report.upstream_skill_count, 13)
        self.assertEqual(report.parity_status, "PASS", report.parity_reason)


if __name__ == "__main__":
    unittest.main()
