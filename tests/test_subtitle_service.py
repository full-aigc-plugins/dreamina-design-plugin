from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from scripts.json_contracts import ContractValidationError
from scripts.subtitle_service import SubtitleService, SubtitleTimelineError
from scripts.narration_service import FileAudioReceiptKeyStore
from tests.test_narration_service import FakeKeyApproval


class SubtitleServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)
        self.keys = FileAudioReceiptKeyStore(self.root / "keys" / "audio.key")
        self.keys.initialize(FakeKeyApproval(), action="first_bootstrap", purpose="subtitle tests")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_srt_is_deterministic_utf8_and_escapes_markup(self) -> None:
        cues = [{"start": 0, "end": 1.25, "text": "<b>Hello</b>\nworld"}]
        service = SubtitleService(max_text_length=100, key_store=self.keys)
        first = service.render_srt(cues, target_duration_seconds=2)
        second = service.render_srt(cues, target_duration_seconds=2)
        self.assertEqual(first, second)
        self.assertEqual(first, "1\n00:00:00,000 --> 00:00:01,250\n&lt;b&gt;Hello&lt;/b&gt; world\n")
        target = self.root / "captions.srt"
        receipt = service.write_srt(cues, target, target_duration_seconds=2)
        self.assertEqual(target.read_bytes(), first.encode("utf-8"))
        self.assertEqual(receipt["mime_type"], "application/x-subrip")
        self.assertEqual(receipt["size_bytes"], len(first.encode("utf-8")))
        self.assertEqual(receipt["artifact_role"], "subtitle_srt")
        self.assertEqual(receipt["provenance"]["rights_declared"], ["subtitles"])

    def test_rejects_negative_reversed_overlapping_or_out_of_bounds_cues(self) -> None:
        invalid = (
            [{"start": -1, "end": 1, "text": "bad"}],
            [{"start": 1, "end": .5, "text": "bad"}],
            [{"start": 0, "end": 1.1, "text": "a"}, {"start": 1, "end": 2, "text": "b"}],
            [{"start": 0, "end": 3, "text": "too late"}],
        )
        for cues in invalid:
            with self.subTest(cues=cues), self.assertRaises(SubtitleTimelineError):
                SubtitleService(key_store=self.keys).render_srt(cues, target_duration_seconds=2)

        with self.assertRaises(SubtitleTimelineError):
            SubtitleService(key_store=self.keys).render_srt(
                [{"start": 0.0001, "end": 0.0004, "text": "rounds to zero"}],
                target_duration_seconds=1,
            )

    def test_ass_escapes_control_sequences_and_is_not_script_injectable(self) -> None:
        text = SubtitleService(key_store=self.keys).render_ass(
            [{"start": 0, "end": 1, "text": "{\\pos(1,1)} hello\\Nworld\nnext"}],
            target_duration_seconds=1,
        )
        dialogue = [line for line in text.splitlines() if line.startswith("Dialogue:")][0]
        self.assertNotIn("\\pos", dialogue)
        self.assertNotIn("\\N", dialogue)
        self.assertIn("｛＼pos(1,1)｝ hello＼Nworld next", dialogue)

    def test_subtitles_require_approved_script_or_narration_timing(self) -> None:
        service = SubtitleService(key_store=self.keys)
        with self.assertRaises(SubtitleTimelineError):
            service.from_source([{"start": 0, "end": 1, "text": "raw asr"}], source="asr")
        cues = service.from_source([{"start": 0, "end": 1, "text": "approved"}], source="rewritten_script")
        self.assertEqual(cues[0]["text"], "approved")

    def test_plan_rejects_subtitle_receipt_with_empty_or_extra_rights_before_schema_handoff(self) -> None:
        from scripts.narration_service import AudioPlanService, _artifact_receipt
        target = self.root / "bad.srt"
        target.write_text("1\n00:00:00,000 --> 00:00:01,000\ntext\n", encoding="utf-8")
        for rights in ([], ["subtitles", "voice"]):
            receipt = _artifact_receipt(provider="subtitle-service", path=target,
                mime_type="application/x-subrip", artifact_role="subtitle_srt",
                kind="generated_subtitle", rights_declared=rights, approved_root=None,
                source="rewritten_script", voice=None, model=None, key_store=self.keys)
            with self.subTest(rights=rights), self.assertRaises(ContractValidationError):
                AudioPlanService(key_store=self.keys).create_plan(
                    project_id="vp_" + "1" * 24, design_fingerprint="2" * 64,
                    batch_fingerprint="3" * 64, creative_mode="original_redesign",
                    audio_policy="subtitles_only", source_rights=None, transcript=None,
                    rewritten_script=[], narration=None, music=None, effects=[], subtitles=[receipt],
                    target_duration_seconds=1)


if __name__ == "__main__":
    unittest.main()
