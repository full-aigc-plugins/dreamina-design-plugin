"""RED tests for the Dreamina router Skill (Task 6).

The router Skill must:

* never duplicate prompt / CLI Skill bodies — its SKILL.md must only
  *reference* the packaged Skills;
* refuse ambiguous routing (multiple matching Skills for one intent);
* refuse hard-coded model / resolution catalogs;
* forbid silent login prompts — every flow must surface explicit
  approval via ``scripts/approval_guard.py``;
* forbid web-prerequisite bypass for the first video;
* forbid approval reuse (fingerprint mismatch must be rejected);
* forbid blind retry on unknown submission state (must query by
  submit_id via ``scripts/operation_ledger.py`` first).
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.router_skill import (  # noqa: E402
    LEGACY_CLI_SUNSET,
    AmbiguousRoutingError,
    HardCodedCatalogError,
    Router,
)


ROUTER_DIR = ROOT / "skills" / "dreamina-design-use"


class RouterSkillPresenceTests(unittest.TestCase):
    def test_router_skill_directory_exists(self) -> None:
        self.assertTrue(ROUTER_DIR.is_dir(), f"missing router skill dir: {ROUTER_DIR}")

    def test_router_skill_md_exists(self) -> None:
        self.assertTrue((ROUTER_DIR / "SKILL.md").is_file(), "missing SKILL.md")


class RouterSkillContentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (ROUTER_DIR / "SKILL.md").read_text(encoding="utf-8")

    def test_router_skill_does_not_embed_other_skill_bodies(self) -> None:
        """Router must reference Skills, not copy their bodies.

        Heuristic: the router SKILL.md must not contain copy-pasted
        sections that look like prompt / CLI instructions (no
        ``dreamina-cli-text2image`` example prompt sections embedded).
        """
        # No instructions like "use dreamina-cli-text2image with these
        # args…" embedded as a recipe.
        self.assertNotIn("dreamina-cli-text2image ", self.text)
        self.assertNotIn("dreamina-cli-text2video ", self.text)

    def test_router_skill_does_not_hard_code_model_catalog(self) -> None:
        # Heuristic: no concrete model tokens like "seedream-5.0-pro".
        self.assertNotIn("seedream-5.0-pro", self.text)
        self.assertNotIn("seedance-2.5", self.text)

    def test_router_skill_does_not_contain_login_curl(self) -> None:
        # No silent-login pattern.
        self.assertNotIn("--login", self.text.lower())
        self.assertNotIn("cookies", self.text.lower())


class RouterRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = Router()

    def test_text2image_intent_routes_to_text2image_skill(self) -> None:
        target = self.router.route(intent="image", mode="text2image")
        self.assertEqual(target, "dreamina-cli-text2image")

    def test_image2image_intent_routes_to_image2image_skill(self) -> None:
        target = self.router.route(intent="image", mode="image2image")
        self.assertEqual(target, "dreamina-cli-image2image")

    def test_text2video_intent_routes_to_text2video_skill(self) -> None:
        target = self.router.route(intent="video", mode="text2video")
        self.assertEqual(target, "dreamina-cli-text2video")

    def test_image2video_intent_routes_to_image2video_skill(self) -> None:
        target = self.router.route(intent="video", mode="image2video")
        self.assertEqual(target, "dreamina-cli-image2video")

    def test_advanced_video_modes_route_to_cli_umbrella(self) -> None:
        for mode in ("frames2video", "multiframe2video", "multimodal2video"):
            with self.subTest(mode=mode):
                self.assertEqual(self.router.route(intent="video", mode=mode), "dreamina-cli")

    def test_unknown_intent_rejected(self) -> None:
        with self.assertRaises(AmbiguousRoutingError):
            self.router.route(intent="video", mode="unknown-video-mode")

    def test_unknown_intent_rejected_hardcoded_check(self) -> None:
        # Hard-coded catalog must come from the registry, not the router.
        with self.assertRaises(HardCodedCatalogError):
            self.router.route_with_hardcoded_catalog(intent="image")


class RouterWebPrerequisiteTests(unittest.TestCase):
    def test_first_video_requires_web_prerequisite(self) -> None:
        router = Router()
        with self.assertRaises(Exception):
            router.can_submit_first_video_without_web_acknowledgement()


class RouterApprovalTests(unittest.TestCase):
    def test_router_reuses_approval_only_when_fingerprint_matches(self) -> None:
        router = Router()
        # Reuse with mismatched fingerprint must be rejected.
        with self.assertRaises(Exception):
            router.replay_approval(request_fingerprint="a" * 64, stored_fingerprint="b" * 64)


if __name__ == "__main__":
    unittest.main()


class RouterSunsetBoundaryTests(unittest.TestCase):
    """The frozen legacy route stays, but the Canvas successor is declared."""

    def test_frozen_route_is_unchanged(self) -> None:
        router = Router()
        self.assertEqual(
            router.route(intent="image", mode="text2image"),
            "dreamina-cli-text2image",
        )
        self.assertEqual(
            router.route(intent="video", mode="frames2video"),
            "dreamina-cli",
        )

    def test_every_frozen_route_declares_a_canvas_successor(self) -> None:
        router = Router()
        for intent, modes in (
            ("image", ("text2image", "image2image")),
            (
                "video",
                (
                    "text2video",
                    "image2video",
                    "frames2video",
                    "multiframe2video",
                    "multimodal2video",
                ),
            ),
        ):
            for mode in modes:
                with self.subTest(intent=intent, mode=mode):
                    frozen = router.route(intent=intent, mode=mode)
                    successor, hint = router.canvas_successor(
                        intent=intent, mode=mode
                    )
                    self.assertTrue(successor.startswith("dreamina-canvas-cli"))
                    self.assertNotEqual(successor, frozen)
                    self.assertIn("npx skills add", hint)

    def test_audio_intents_have_successors_even_though_router_has_no_audio_route(self) -> None:
        router = Router()
        for mode, expected in (
            ("tts", "dreamina-canvas-cli-text2voice"),
            ("music", "dreamina-canvas-cli-text2audio"),
        ):
            successor, _ = router.canvas_successor(intent="audio", mode=mode)
            self.assertEqual(successor, expected)

    def test_image2video_maps_to_ref2video_because_canvas_has_no_i2v(self) -> None:
        router = Router()
        successor, _ = router.canvas_successor(intent="video", mode="image2video")
        self.assertEqual(successor, "dreamina-canvas-cli-ref2video")

    def test_legacy_sunset_is_recorded(self) -> None:
        self.assertEqual(LEGACY_CLI_SUNSET, "2026-11")
