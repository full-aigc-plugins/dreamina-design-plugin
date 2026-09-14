from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from scripts.media_adapter import MediaResult
from scripts.narration_service import (
    AudioPlanService,
    AudioRightsError,
    ExistingAudioProvider,
    MacOSSayProvider,
    NarrationProviderError,
)
from scripts.json_contracts import ContractValidationError, validate_contract


class SyntheticNarrationAdapter:
    def __init__(self) -> None:
        self.calls = []

    def run(self, kind, argv, *, timeout_seconds):
        self.calls.append((kind, list(argv), timeout_seconds))
        Path(argv[argv.index("-o") + 1]).write_bytes(b"synthetic-aiff")
        return MediaResult(0, "", "")


class NarrationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_macos_say_uses_closed_voice_allowlist_and_private_script(self) -> None:
        adapter = SyntheticNarrationAdapter()
        output = self.root / "narration.aiff"
        provider = MacOSSayProvider(adapter, self.root, voices={"Ting-Ting", "Samantha"})
        receipt = provider.synthesize([{"start": 0, "end": 1, "text": "你好"}], voice="Ting-Ting", output_path=output)
        kind, argv, timeout = adapter.calls[0]
        self.assertEqual(kind, "narration")
        self.assertEqual(argv[0:2], ["-v", "Ting-Ting"])
        script = Path(argv[argv.index("-f") + 1])
        self.assertTrue(script.is_relative_to(self.root))
        self.assertEqual(output.read_bytes(), b"synthetic-aiff")
        self.assertEqual(receipt["sha256"], hashlib.sha256(b"synthetic-aiff").hexdigest())
        self.assertEqual(receipt["provider"], "macos-say")
        self.assertEqual(receipt["mime_type"], "audio/aiff")
        self.assertEqual(receipt["size_bytes"], len(b"synthetic-aiff"))
        self.assertEqual(receipt["provenance"]["source"], "rewritten_script")
        self.assertEqual(timeout, 300)
        with self.assertRaises(NarrationProviderError):
            provider.synthesize([], voice="$(open bad)", output_path=output)

    def test_existing_audio_requires_private_approved_digest_and_rights(self) -> None:
        track = self.root / "licensed.wav"
        track.write_bytes(b"licensed")
        provider = ExistingAudioProvider([self.root])
        receipt = provider.accept(track, expected_sha256=hashlib.sha256(b"licensed").hexdigest(), rights={"music": True})
        self.assertEqual(receipt["provenance"]["rights_declared"], ["music"])
        with self.assertRaises(AudioRightsError):
            provider.accept(track, expected_sha256="0" * 64, rights={"music": True})
        with self.assertRaises(AudioRightsError):
            provider.accept(track, expected_sha256=hashlib.sha256(b"licensed").hexdigest(), rights={})

    def test_four_audio_policies_are_closed_and_original_redesign_replaces_source_audio(self) -> None:
        service = AudioPlanService()
        adapter = SyntheticNarrationAdapter()
        narration = MacOSSayProvider(adapter, self.root, voices={"Samantha"}).synthesize(
            [{"start": 0, "end": 1, "text": "new"}], voice="Samantha", output_path=self.root / "new.aiff")
        music_path = self.root / "music.wav"; music_path.write_bytes(b"music")
        music = ExistingAudioProvider([self.root]).accept(music_path, expected_sha256=hashlib.sha256(b"music").hexdigest(), rights={"music": True})
        for policy in ("full_redesign", "preserve_authorized_audio", "subtitles_only", "silent"):
            with self.subTest(policy=policy):
                preserving = policy == "preserve_authorized_audio"
                result = service.create_plan(
                    project_id="vp_" + "1" * 24, design_fingerprint="2" * 64,
                    batch_fingerprint="3" * 64, creative_mode="authorized_replication" if preserving else "original_redesign",
                    audio_policy=policy, source_rights={"receipt_id":"rr_"+"5"*24,"receipt_fingerprint":"6"*64,"allowed_reuse": ["music"]} if preserving else None, transcript=None,
                    rewritten_script=[] if policy in {"subtitles_only", "silent"} else [{"start": 0, "end": 1, "text": "new"}],
                    narration=narration if policy == "full_redesign" else None,
                    music={**music, "loop": False, "trim_to_seconds": 2} if preserving else None,
                    effects=[], subtitles=[], target_duration_seconds=2, preserve=["music"] if preserving else (),
                )
                self.assertEqual(result["audio_policy"], policy)
        with self.assertRaises(ValueError):
            service.create_plan(project_id="vp_" + "1" * 24, design_fingerprint="2" * 64,
                batch_fingerprint="3" * 64, creative_mode="original_redesign", audio_policy="copy_all",
                source_rights=None, transcript=None, rewritten_script=[], narration=None, music=None,
                effects=[], subtitles=[], target_duration_seconds=2)
        with self.assertRaises(AudioRightsError):
            service.create_plan(project_id="vp_" + "1" * 24, design_fingerprint="2" * 64,
                batch_fingerprint="3" * 64, creative_mode="original_redesign", audio_policy="preserve_authorized_audio",
                source_rights={"allowed_reuse": ["voice", "music"]}, transcript=None, rewritten_script=[],
                narration=None, music=None, effects=[], subtitles=[], target_duration_seconds=2)

    def test_authorized_preserve_requires_every_requested_class_and_music_intent(self) -> None:
        service = AudioPlanService()
        music_path = self.root / "licensed-music.wav"; music_path.write_bytes(b"licensed-music")
        receipt = ExistingAudioProvider([self.root]).accept(music_path, expected_sha256=hashlib.sha256(b"licensed-music").hexdigest(), rights={"music": True})
        base = dict(project_id="vp_" + "1" * 24, design_fingerprint="2" * 64,
            batch_fingerprint="3" * 64, creative_mode="authorized_replication",
            audio_policy="preserve_authorized_audio", transcript=None, rewritten_script=[], narration=None,
            music={**receipt, "loop": True, "trim_to_seconds": 8.0}, effects=[], subtitles=[],
            target_duration_seconds=8)
        with self.assertRaises(AudioRightsError):
            service.create_plan(source_rights={"receipt_id":"rr_"+"5"*24,"receipt_fingerprint":"6"*64,"allowed_reuse": ["music"]}, preserve=["voice", "music"], **base)
        result = service.create_plan(source_rights={"receipt_id":"rr_"+"5"*24,"receipt_fingerprint":"6"*64,"allowed_reuse": ["voice", "music"]}, preserve=["voice", "music"], **base)
        self.assertEqual(result["music"]["intent"], {"loop": True, "trim_to_seconds": 8.0})

    def test_forged_or_incomplete_artifact_receipts_are_rejected(self) -> None:
        service = AudioPlanService()
        common = dict(project_id="vp_"+"1"*24,design_fingerprint="2"*64,batch_fingerprint="3"*64,creative_mode="original_redesign",audio_policy="full_redesign",source_rights=None,transcript=None,rewritten_script=[{"start":0,"end":1,"text":"new"}],music=None,effects=[],subtitles=[],target_duration_seconds=1)
        for bad in ({"sha256":"4"*64}, {"provider":"existing-audio","path":"/tmp/x","sha256":"4"*64,"size_bytes":1,"mime_type":"audio/wav","provenance":{"kind":"user_supplied","rights_declared":["voice"],"approved_root":"/tmp","source":"source_audio","voice":None,"model":None}}):
            with self.subTest(bad=bad), self.assertRaises((ValueError, ContractValidationError, AudioRightsError)):
                service.create_plan(narration=bad, **common)


if __name__ == "__main__":
    unittest.main()
