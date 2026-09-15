"""RED acceptance tests for guarded ReelBench synchronized review evidence."""
from __future__ import annotations

import unittest


class ReelBenchSyncServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        from scripts.reelbench_sync_service import ReelBenchSyncService

        self.service = ReelBenchSyncService()
        self.landscape = {"width": 1920, "height": 1080, "duration_seconds": 8.0}
        self.portrait = {"width": 1080, "height": 1920, "duration_seconds": 8.0}

    def test_portrait_and_landscape_layouts_preserve_source_aspect(self) -> None:
        landscape = self.service.plan(self.landscape)
        portrait = self.service.plan(self.portrait)

        self.assertEqual(landscape["layout"], "vertical-stack")
        self.assertEqual(portrait["layout"], "horizontal-stack")
        # H.264 requires even edges; the only allowable aspect deviation is the
        # resulting one-pixel rounding at the scaled source edge.
        self.assertLessEqual(abs(landscape["video"]["width"] * self.landscape["height"] -
                                 landscape["video"]["height"] * self.landscape["width"]),
                             2 * max(self.landscape["width"], self.landscape["height"]))
        self.assertLessEqual(abs(portrait["video"]["width"] * self.portrait["height"] -
                                 portrait["video"]["height"] * self.portrait["width"]),
                             2 * max(self.portrait["width"], self.portrait["height"]))

    def test_review_role_is_rejected_by_final_media_guard(self) -> None:
        from scripts.reelbench_sync_service import FinalMediaVerificationError

        receipt = {"artifact_role": "synchronized_review"}
        with self.assertRaises(FinalMediaVerificationError):
            self.service.accept_generated_shot(receipt)
