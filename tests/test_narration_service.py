from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from scripts.media_adapter import MediaResult
from scripts.narration_service import (
    AudioPlanService,
    AudioRightsError,
    ExistingAudioProvider,
    FileAudioReceiptKeyStore,
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


class FixedKeyStore:
    def __init__(self, key=b"k" * 32): self.key = key
    def load(self, *, allow_create): return self.key


class NarrationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)
        self.keys = FileAudioReceiptKeyStore(self.root / "receipt-keys" / "audio.key")
        self.keys.initialize()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_macos_say_uses_closed_voice_allowlist_and_private_script(self) -> None:
        adapter = SyntheticNarrationAdapter()
        output = self.root / "narration.aiff"
        provider = MacOSSayProvider(adapter, self.root, voices={"Ting-Ting", "Samantha"}, key_store=self.keys)
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
        provider = ExistingAudioProvider([self.root], key_store=self.keys)
        receipt = provider.accept(track, expected_sha256=hashlib.sha256(b"licensed").hexdigest(), rights={"music": True})
        self.assertEqual(receipt["provenance"]["rights_declared"], ["music"])
        with self.assertRaises(AudioRightsError):
            provider.accept(track, expected_sha256="0" * 64, rights={"music": True})
        with self.assertRaises(AudioRightsError):
            provider.accept(track, expected_sha256=hashlib.sha256(b"licensed").hexdigest(), rights={})

    def test_four_audio_policies_are_closed_and_original_redesign_replaces_source_audio(self) -> None:
        service = AudioPlanService(key_store=self.keys)
        adapter = SyntheticNarrationAdapter()
        narration = MacOSSayProvider(adapter, self.root, voices={"Samantha"}, key_store=self.keys).synthesize(
            [{"start": 0, "end": 1, "text": "new"}], voice="Samantha", output_path=self.root / "new.aiff")
        music_path = self.root / "music.wav"; music_path.write_bytes(b"music")
        music = ExistingAudioProvider([self.root], key_store=self.keys).accept(music_path, expected_sha256=hashlib.sha256(b"music").hexdigest(), rights={"music": True})
        for policy in ("full_redesign", "preserve_authorized_audio", "subtitles_only", "silent"):
            with self.subTest(policy=policy):
                preserving = policy == "preserve_authorized_audio"
                result = service.create_plan(
                    project_id="vp_" + "1" * 24, design_fingerprint="2" * 64,
                    batch_fingerprint="3" * 64, creative_mode="authorized_replication" if preserving else "original_redesign",
                    audio_policy=policy, source_rights={"receipt_id":"rr_"+"5"*24,"receipt_fingerprint":"6"*64,"allowed_reuse": ["music"],"artifact_bindings":{"music":{"path":music["path"],"sha256":music["sha256"]}}} if preserving else None, transcript=None,
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
        service = AudioPlanService(key_store=self.keys)
        music_path = self.root / "licensed-music.wav"; music_path.write_bytes(b"licensed-music")
        receipt = ExistingAudioProvider([self.root], key_store=self.keys).accept(music_path, expected_sha256=hashlib.sha256(b"licensed-music").hexdigest(), rights={"music": True})
        base = dict(project_id="vp_" + "1" * 24, design_fingerprint="2" * 64,
            batch_fingerprint="3" * 64, creative_mode="authorized_replication",
            audio_policy="preserve_authorized_audio", transcript=None, rewritten_script=[], narration=None,
            music={**receipt, "loop": True, "trim_to_seconds": 8.0}, effects=[], subtitles=[],
            target_duration_seconds=8)
        with self.assertRaises(AudioRightsError):
            service.create_plan(source_rights={"receipt_id":"rr_"+"5"*24,"receipt_fingerprint":"6"*64,"allowed_reuse": ["music"],"artifact_bindings":{"music":{"path":receipt["path"],"sha256":receipt["sha256"]}}}, preserve=["voice", "music"], **base)
        result = service.create_plan(source_rights={"receipt_id":"rr_"+"5"*24,"receipt_fingerprint":"6"*64,"allowed_reuse": ["music"],"artifact_bindings":{"music":{"path":receipt["path"],"sha256":receipt["sha256"]}}}, preserve=["music"], **base)
        self.assertEqual(result["music"]["intent"], {"loop": True, "trim_to_seconds": 8.0})

    def test_forged_or_incomplete_artifact_receipts_are_rejected(self) -> None:
        service = AudioPlanService()
        common = dict(project_id="vp_"+"1"*24,design_fingerprint="2"*64,batch_fingerprint="3"*64,creative_mode="original_redesign",audio_policy="full_redesign",source_rights=None,transcript=None,rewritten_script=[{"start":0,"end":1,"text":"new"}],music=None,effects=[],subtitles=[],target_duration_seconds=1)
        for bad in ({"sha256":"4"*64}, {"provider":"existing-audio","path":"/tmp/x","sha256":"4"*64,"size_bytes":1,"mime_type":"audio/wav","provenance":{"kind":"user_supplied","rights_declared":["voice"],"approved_root":"/tmp","source":"source_audio","voice":None,"model":None}}):
            with self.subTest(bad=bad), self.assertRaises((ValueError, ContractValidationError, AudioRightsError)):
                service.create_plan(narration=bad, **common)

    def test_forged_macos_receipt_with_real_file_and_digest_is_rejected(self) -> None:
        fake = self.root / "fake.aiff"; fake.write_bytes(b"synthetic-aiff")
        forged = {"provider":"macos-say","path":str(fake),"sha256":hashlib.sha256(fake.read_bytes()).hexdigest(),"size_bytes":fake.stat().st_size,"mime_type":"audio/aiff","provenance":{"kind":"new_narration","rights_declared":["voice"],"approved_root":None,"source":"rewritten_script","voice":"Samantha","model":"macos-say","source_voice_cloned":False},"attestation":"0"*64}
        with self.assertRaises(ContractValidationError):
            AudioPlanService().create_plan(project_id="vp_"+"1"*24,design_fingerprint="2"*64,batch_fingerprint="3"*64,creative_mode="original_redesign",audio_policy="full_redesign",source_rights=None,transcript=None,rewritten_script=[{"start":0,"end":1,"text":"new"}],narration=forged,music=None,effects=[],subtitles=[],target_duration_seconds=1)

    def test_receipt_survives_service_restart_with_same_durable_key(self) -> None:
        keys = FixedKeyStore()
        receipt = MacOSSayProvider(SyntheticNarrationAdapter(), self.root, voices={"Samantha"}, key_store=keys).synthesize(
            [{"start":0,"end":1,"text":"new"}], voice="Samantha", output_path=self.root/"restart.aiff")
        plan = AudioPlanService(key_store=FixedKeyStore()).create_plan(project_id="vp_"+"1"*24,design_fingerprint="2"*64,batch_fingerprint="3"*64,creative_mode="original_redesign",audio_policy="full_redesign",source_rights=None,transcript=None,rewritten_script=[{"start":0,"end":1,"text":"new"}],narration=receipt,music=None,effects=[],subtitles=[],target_duration_seconds=1)
        self.assertEqual(plan["narration"]["attestation_key_id"], hashlib.sha256(b"k"*32).hexdigest())
        with self.assertRaises(PermissionError):
            AudioPlanService(key_store=FixedKeyStore(b"x"*32)).create_plan(project_id="vp_"+"1"*24,design_fingerprint="2"*64,batch_fingerprint="3"*64,creative_mode="original_redesign",audio_policy="full_redesign",source_rights=None,transcript=None,rewritten_script=[{"start":0,"end":1,"text":"new"}],narration=receipt,music=None,effects=[],subtitles=[],target_duration_seconds=1)

    def test_file_key_store_is_private_durable_and_never_rotates_when_missing(self) -> None:
        key_path = self.root / "keys" / "audio.key"
        store = FileAudioReceiptKeyStore(key_path)
        first = store.initialize()
        second = store.load_existing()
        self.assertEqual(first, second)
        self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)
        key_path.unlink()
        with self.assertRaises(PermissionError):
            FileAudioReceiptKeyStore(key_path).load_existing()

    def test_key_store_rejects_existing_unsafe_parent_without_mutating_it(self) -> None:
        public = self.root / "public"
        public.mkdir(mode=0o755)
        before = public.stat().st_mode & 0o777
        with self.assertRaises(PermissionError):
            FileAudioReceiptKeyStore(public / "audio.key")
        self.assertEqual(public.stat().st_mode & 0o777, before)

        target = self.root / "target"
        target.mkdir(mode=0o755)
        link = self.root / "link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(PermissionError):
            FileAudioReceiptKeyStore(link / "audio.key")
        self.assertEqual(target.stat().st_mode & 0o777, 0o755)

    def test_key_store_marker_detects_deleted_or_changed_state(self) -> None:
        key_path = self.root / "state" / "audio.key"
        store = FileAudioReceiptKeyStore(key_path)
        original = store.initialize()
        marker = store.marker_path
        self.assertEqual(store.initialize(), original)
        key_path.unlink()
        with self.assertRaises(PermissionError):
            store.initialize()
        key_path.write_bytes(original)
        os.chmod(key_path, 0o600)
        marker.unlink()
        with self.assertRaises(PermissionError):
            store.initialize()
        marker.write_text("corrupt", encoding="utf-8")
        os.chmod(marker, 0o600)
        with self.assertRaises(PermissionError):
            store.load_existing()

    def test_key_store_concurrent_initialize_is_idempotent(self) -> None:
        store = FileAudioReceiptKeyStore(self.root / "concurrent" / "audio.key")
        keys, errors = [], []
        def initialize() -> None:
            try:
                keys.append(store.initialize())
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)
        threads = [threading.Thread(target=initialize) for _ in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(keys)), 1)
        self.assertEqual(store.load_existing(), keys[0])

    def test_key_store_load_fails_if_key_path_is_swapped_during_read(self) -> None:
        key_path = self.root / "race" / "audio.key"
        store = FileAudioReceiptKeyStore(key_path)
        store.initialize()
        original_read = os.read
        swapped = False
        def racing_read(fd, size):
            nonlocal swapped
            data = original_read(fd, size)
            if not swapped:
                swapped = True
                replacement = key_path.with_suffix(".replacement")
                replacement.write_bytes(b"z" * 32)
                os.chmod(replacement, 0o600)
                os.replace(replacement, key_path)
            return data
        with mock.patch("scripts.narration_service.os.read", side_effect=racing_read):
            with self.assertRaises(PermissionError):
                store.load_existing()

    def test_signing_never_recreates_deleted_initialized_key(self) -> None:
        key_path = self.root / "deleted" / "audio.key"
        store = FileAudioReceiptKeyStore(key_path)
        provider = MacOSSayProvider(SyntheticNarrationAdapter(), self.root, voices={"Samantha"}, key_store=store)
        key_path.unlink()
        with self.assertRaises(PermissionError):
            provider.synthesize([{"start":0,"end":1,"text":"new"}], voice="Samantha", output_path=self.root/"deleted.aiff")
        self.assertFalse(key_path.exists())

    def test_preserved_effects_require_exact_order_independent_member_set(self) -> None:
        keys = FixedKeyStore(); provider = ExistingAudioProvider([self.root], key_store=keys)
        receipts = []
        for name in ("one.wav", "two.wav"):
            path = self.root/name; path.write_bytes(name.encode())
            receipts.append(provider.accept(path, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), rights={"effects":True}))
        bindings = [{"path": item["path"], "sha256": item["sha256"]} for item in reversed(receipts)]
        base = dict(project_id="vp_"+"1"*24,design_fingerprint="2"*64,batch_fingerprint="3"*64,creative_mode="authorized_replication",audio_policy="preserve_authorized_audio",transcript=None,rewritten_script=[],narration=None,music=None,effects=receipts,subtitles=[],target_duration_seconds=1,preserve=["effects"])
        plan = AudioPlanService(key_store=keys).create_plan(source_rights={"receipt_id":"rr_"+"5"*24,"receipt_fingerprint":"6"*64,"allowed_reuse":["effects"],"artifact_bindings":{"effects":bindings}}, **base)
        self.assertEqual(len(plan["effects"]), 2)
        with self.assertRaises(AudioRightsError):
            AudioPlanService(key_store=keys).create_plan(source_rights={"receipt_id":"rr_"+"5"*24,"receipt_fingerprint":"6"*64,"allowed_reuse":["effects"],"artifact_bindings":{"effects":bindings[:1]}}, **base)


if __name__ == "__main__":
    unittest.main()
