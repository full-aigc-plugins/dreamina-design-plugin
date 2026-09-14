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
    _require_artifact,
)
from scripts.json_contracts import ContractValidationError, canonical_fingerprint, validate_contract
from scripts.video_project_store import VersionCommitIndeterminateError, VideoProjectStore


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


class FakeKeyApproval:
    def __init__(self, *, deny=False):
        self.deny = deny
        self.calls = []

    def confirm_audio_receipt_key_initialization(self, **request):
        self.calls.append(request)
        if self.deny:
            raise PermissionError("denied")
        return "native-audio-receipt-key-confirmed"


class StubRightsStore:
    def __init__(self, receipt, design, audio_policy="preserve_authorized_audio"):
        self.receipt = receipt
        self.design = design
        self.audio_policy = audio_policy

    def get(self, project_id):
        return {"project_id": project_id, "creative_mode": "authorized_replication",
                "audio_policy": self.audio_policy}

    def find_version_by_field(self, project_id, family, **kwargs):
        return self.receipt if family == "rights_receipt" else self.design


class NarrationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)
        self.keys = FileAudioReceiptKeyStore(self.root / "receipt-keys" / "audio.key")
        self.keys.initialize(FakeKeyApproval(), action="first_bootstrap", purpose="test fixture")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _authorized_service(self, bindings, requested, *, expires_at="2027-09-14T00:00:00Z",
                            audio_policy="preserve_authorized_audio"):
        allowed_reuse = sorted(set(requested).difference({"effects"})) or ["audio_beats"]
        receipt = {
            "schema_version": "1.0", "version": "v001", "receipt_id": "rr_" + "5" * 24,
            "project_id": "vp_" + "1" * 24, "source_sha256": "a" * 64,
            "creative_mode": "authorized_replication", "design_fingerprint": "2" * 64,
            "declarant": "rights-holder@example.test", "rights_basis": "written license",
            "evidence": [{"reference": "license", "sha256": "b" * 64}],
            "allowed_media": ["audio"], "allowed_reuse": allowed_reuse,
            "purpose": "campaign remake", "audience": "registered customers", "territory": "US",
            "expires_at": expires_at, "asserted_at": "2026-09-14T00:00:00Z",
            "native_confirmation": "native-video-rights-confirmed",
            "disclaimer": "User-supplied assertion recorded as engineering authorization evidence; not ownership verification or legal advice.",
        }
        design = {
            "project_id": receipt["project_id"], "source_sha256": receipt["source_sha256"],
            "creative_mode": "authorized_replication", "design_fingerprint": receipt["design_fingerprint"],
            "rights_receipt_id": receipt["receipt_id"],
            "payload": {"required_media": ["audio"], "purpose": receipt["purpose"],
                        "audience": receipt["audience"], "territory": receipt["territory"]},
        }
        source_rights = {
            "receipt_id": receipt["receipt_id"], "receipt_fingerprint": canonical_fingerprint(receipt),
            "allowed_reuse": sorted(requested), "artifact_bindings": bindings,
        }
        return AudioPlanService(
            key_store=self.keys, project_store=StubRightsStore(receipt, design, audio_policy),
            now=lambda: "2026-09-14T01:00:00Z",
        ), source_rights

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

    def test_macos_say_rejects_public_root_without_changing_permissions(self) -> None:
        public = self.root / "public-narration"
        public.mkdir(mode=0o755)
        before = public.stat().st_mode & 0o777
        with self.assertRaises(NarrationProviderError):
            MacOSSayProvider(SyntheticNarrationAdapter(), public, voices={"Samantha"}, key_store=self.keys)
        self.assertEqual(public.stat().st_mode & 0o777, before)

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
                bindings = {"music": {"path": music["path"], "sha256": music["sha256"]}}
                selected_service, source_rights = self._authorized_service(bindings, ["music"]) if preserving else (service, None)
                result = selected_service.create_plan(
                    project_id="vp_" + "1" * 24, design_fingerprint="2" * 64,
                    batch_fingerprint="3" * 64, creative_mode="authorized_replication" if preserving else "original_redesign",
                    audio_policy=policy, source_rights=source_rights, transcript=None,
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
        music_path = self.root / "licensed-music.wav"; music_path.write_bytes(b"licensed-music")
        receipt = ExistingAudioProvider([self.root], key_store=self.keys).accept(music_path, expected_sha256=hashlib.sha256(b"licensed-music").hexdigest(), rights={"music": True})
        bindings = {"music": {"path": receipt["path"], "sha256": receipt["sha256"]}}
        service, source_rights = self._authorized_service(bindings, ["music"])
        base = dict(project_id="vp_" + "1" * 24, design_fingerprint="2" * 64,
            batch_fingerprint="3" * 64, creative_mode="authorized_replication",
            audio_policy="preserve_authorized_audio", transcript=None, rewritten_script=[], narration=None,
            music={**receipt, "loop": True, "trim_to_seconds": 8.0}, effects=[], subtitles=[],
            target_duration_seconds=8)
        with self.assertRaises(AudioRightsError):
            service.create_plan(source_rights=source_rights, preserve=["voice", "music"], **base)
        result = service.create_plan(source_rights=source_rights, preserve=["music"], **base)
        self.assertEqual(result["music"]["intent"], {"loop": True, "trim_to_seconds": 8.0, "use_full_track": False})

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
        approval = FakeKeyApproval()
        first = store.initialize(approval, action="first_bootstrap", purpose="enable signed audio receipts")
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
        approval = FakeKeyApproval()
        original = store.initialize(approval, action="first_bootstrap", purpose="test continuity")
        marker = store.marker_path
        self.assertEqual(store.initialize(approval, action="first_bootstrap", purpose="test continuity"), original)
        key_path.unlink()
        with self.assertRaises(PermissionError):
            store.initialize(approval, action="rebootstrap", purpose="recover deleted receipt key")
        key_path.write_bytes(original)
        os.chmod(key_path, 0o600)
        marker.unlink()
        with self.assertRaises(PermissionError):
            store.initialize(approval, action="rebootstrap", purpose="recover deleted receipt key")
        marker.write_text("corrupt", encoding="utf-8")
        os.chmod(marker, 0o600)
        with self.assertRaises(PermissionError):
            store.load_existing()

    def test_key_store_concurrent_initialize_is_idempotent(self) -> None:
        store = FileAudioReceiptKeyStore(self.root / "concurrent" / "audio.key")
        approval = FakeKeyApproval()
        keys, errors = [], []
        def initialize() -> None:
            try:
                keys.append(store.initialize(approval, action="first_bootstrap", purpose="concurrent test"))
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
        store.initialize(FakeKeyApproval(), action="first_bootstrap", purpose="race test")
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
        store.initialize(FakeKeyApproval(), action="first_bootstrap", purpose="deletion test")
        provider = MacOSSayProvider(SyntheticNarrationAdapter(), self.root, voices={"Samantha"}, key_store=store)
        key_path.unlink()
        with self.assertRaises(PermissionError):
            provider.synthesize([{"start":0,"end":1,"text":"new"}], voice="Samantha", output_path=self.root/"deleted.aiff")
        self.assertFalse(key_path.exists())

    def test_bootstrap_requires_exact_native_approval_and_denial_leaves_no_files(self) -> None:
        key_path = self.root / "approval" / "audio.key"
        store = FileAudioReceiptKeyStore(key_path)
        with self.assertRaises(PermissionError):
            store.initialize(None, action="first_bootstrap", purpose="enable receipts")
        self.assertFalse(key_path.exists())
        self.assertFalse(store.marker_path.exists())

        denied = FakeKeyApproval(deny=True)
        with self.assertRaises(PermissionError):
            store.initialize(denied, action="first_bootstrap", purpose="enable receipts")
        self.assertFalse(key_path.exists())
        self.assertFalse(store.marker_path.exists())
        self.assertEqual(denied.calls[0]["key_store_path"], str(key_path))
        self.assertEqual(denied.calls[0]["action"], "first_bootstrap")
        self.assertRegex(denied.calls[0]["new_key_id"], r"^[a-f0-9]{64}$")

    def test_routine_provider_construction_and_signing_never_bootstrap_or_call_approval(self) -> None:
        key_path = self.root / "routine" / "audio.key"
        store = FileAudioReceiptKeyStore(key_path)
        approval = FakeKeyApproval()
        provider = ExistingAudioProvider([self.root], key_store=store)
        track = self.root / "routine.wav"
        track.write_bytes(b"routine")
        with self.assertRaises(PermissionError):
            provider.accept(track, expected_sha256=hashlib.sha256(b"routine").hexdigest(), rights={"music": True})
        self.assertEqual(approval.calls, [])
        self.assertFalse(key_path.exists())

    def test_deleted_pair_requires_approved_rebootstrap_and_creates_new_identity(self) -> None:
        key_path = self.root / "rebootstrap" / "audio.key"
        store = FileAudioReceiptKeyStore(key_path)
        first = store.initialize(FakeKeyApproval(), action="first_bootstrap", purpose="enable receipts")
        old_id = hashlib.sha256(first).hexdigest()
        key_path.unlink()
        store.marker_path.unlink()
        track = self.root / "after-deletion.wav"
        track.write_bytes(b"after-deletion")
        with self.assertRaises(PermissionError):
            ExistingAudioProvider([self.root], key_store=store).accept(
                track, expected_sha256=hashlib.sha256(track.read_bytes()).hexdigest(), rights={"music": True})
        self.assertFalse(key_path.exists())
        self.assertFalse(store.marker_path.exists())
        with self.assertRaises(PermissionError):
            store.initialize(FakeKeyApproval(), action="first_bootstrap", purpose="incorrect retry")
        approval = FakeKeyApproval()
        second = store.initialize(approval, action="rebootstrap", purpose="recover deleted identity")
        self.assertNotEqual(hashlib.sha256(second).hexdigest(), old_id)
        self.assertEqual(approval.calls[0]["action"], "rebootstrap")
        self.assertIn("unverifiable", approval.calls[0]["impact"])

    def test_signed_receipts_bind_exact_artifact_role_and_rights(self) -> None:
        provider = ExistingAudioProvider([self.root], key_store=self.keys)
        cases = [
            ({"voice": True}, "existing_voice", ["voice"]),
            ({"dialogue": True}, "existing_dialogue", ["dialogue"]),
            ({"voice": True, "dialogue": True}, "existing_voice_dialogue", ["dialogue", "voice"]),
            ({"music": True}, "music", ["music"]),
            ({"effects": True}, "effect", ["effects"]),
        ]
        for index, (rights, role, exact_rights) in enumerate(cases):
            path = self.root / f"role-{index}.wav"
            path.write_bytes(str(index).encode())
            receipt = provider.accept(path, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), rights=rights)
            self.assertEqual(receipt["artifact_role"], role)
            self.assertEqual(receipt["provenance"]["rights_declared"], exact_rights)
            tampered = {**receipt, "artifact_role": "music" if role != "music" else "effect"}
            with self.assertRaises(ContractValidationError):
                _require_artifact(tampered, role=tampered["artifact_role"], key_store=self.keys)
        path = self.root / "mixed.wav"
        path.write_bytes(b"mixed")
        with self.assertRaises(AudioRightsError):
            provider.accept(path, expected_sha256=hashlib.sha256(b"mixed").hexdigest(), rights={"music": True, "effects": True})

    def test_preserved_effects_require_exact_order_independent_member_set(self) -> None:
        keys = FixedKeyStore(); provider = ExistingAudioProvider([self.root], key_store=keys)
        receipts = []
        for name in ("one.wav", "two.wav"):
            path = self.root/name; path.write_bytes(name.encode())
            receipts.append(provider.accept(path, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), rights={"effects":True}))
        bindings = [{"path": item["path"], "sha256": item["sha256"]} for item in reversed(receipts)]
        base = dict(project_id="vp_"+"1"*24,design_fingerprint="2"*64,batch_fingerprint="3"*64,creative_mode="authorized_replication",audio_policy="preserve_authorized_audio",transcript=None,rewritten_script=[],narration=None,music=None,effects=receipts,subtitles=[],target_duration_seconds=1,preserve=["effects"])
        service, source_rights = self._authorized_service({"effects": bindings}, ["effects"])
        service._key_store = keys
        plan = service.create_plan(source_rights=source_rights, **base)
        self.assertEqual(len(plan["effects"]), 2)
        with self.assertRaises(AudioRightsError):
            service.create_plan(source_rights={**source_rights, "artifact_bindings": {"effects": bindings[:1]}}, **base)

    def test_preserve_reloads_current_rights_and_fails_closed_on_expiry_or_fingerprint_drift(self) -> None:
        track = self.root / "current.wav"
        track.write_bytes(b"current")
        music = ExistingAudioProvider([self.root], key_store=self.keys).accept(
            track, expected_sha256=hashlib.sha256(track.read_bytes()).hexdigest(), rights={"music": True})
        bindings = {"music": {"path": music["path"], "sha256": music["sha256"]}}
        service, source_rights = self._authorized_service(bindings, ["music"])
        base = dict(
            project_id="vp_" + "1" * 24, design_fingerprint="2" * 64,
            batch_fingerprint="3" * 64, creative_mode="authorized_replication",
            audio_policy="preserve_authorized_audio", transcript=None, rewritten_script=[],
            narration=None, music={**music, "loop": False, "trim_to_seconds": 1}, effects=[],
            subtitles=[], target_duration_seconds=1, preserve=["music"],
        )
        self.assertEqual(service.create_plan(source_rights=source_rights, **base)["preserve"], ["music"])
        with self.assertRaises(AudioRightsError):
            service.create_plan(source_rights={**source_rights, "receipt_fingerprint": "0" * 64}, **base)
        expired, expired_rights = self._authorized_service(bindings, ["music"], expires_at="2026-09-14T00:30:00Z")
        with self.assertRaises(AudioRightsError):
            expired.create_plan(source_rights=expired_rights, **base)

    def test_audio_plans_commit_as_immutable_versions_and_reconcile_exact_indeterminate_commit(self) -> None:
        store = VideoProjectStore(self.root / "projects")
        project = store.create(title="silent", creative_mode="original_redesign", audio_policy="silent")
        service = AudioPlanService(key_store=self.keys, project_store=store)
        plan = service.create_plan(
            project_id=project["project_id"], design_fingerprint="2" * 64,
            batch_fingerprint="3" * 64, creative_mode="original_redesign", audio_policy="silent",
            source_rights=None, transcript=None, rewritten_script=[], narration=None,
            music=None, effects=[], subtitles=[], target_duration_seconds=1,
        )
        first = service.commit_plan(plan)
        second = service.commit_plan(plan)
        self.assertEqual((first["version"], second["version"]), ("v001", "v002"))
        self.assertEqual(first["plan_fingerprint"], second["plan_fingerprint"])
        commit = VersionCommitIndeterminateError(
            project_id=project["project_id"], family="audio_plan",
            version="v001", path=store.project_root(project["project_id"]) / "audio_plan" / "v001.json",
            payload_fingerprint=canonical_fingerprint(first),
        )
        self.assertEqual(service.commit_plan(plan, indeterminate_commit=commit), first)
        changed = {**plan, "batch_fingerprint": "4" * 64}
        changed_core = {key: value for key, value in changed.items() if key not in {"version", "plan_fingerprint"}}
        changed["plan_fingerprint"] = canonical_fingerprint(changed_core)
        with self.assertRaises(ContractValidationError):
            service.commit_plan(changed, indeterminate_commit=commit)

    def test_subtitles_only_can_retain_exact_authorized_audio_or_use_silence(self) -> None:
        track = self.root / "subtitle-music.wav"
        track.write_bytes(b"subtitle-music")
        music = ExistingAudioProvider([self.root], key_store=self.keys).accept(
            track, expected_sha256=hashlib.sha256(track.read_bytes()).hexdigest(), rights={"music": True})
        bindings = {"music": {"path": music["path"], "sha256": music["sha256"]}}
        service, source_rights = self._authorized_service(
            bindings, ["music"], audio_policy="subtitles_only")
        common = dict(
            project_id="vp_" + "1" * 24, design_fingerprint="2" * 64,
            batch_fingerprint="3" * 64, creative_mode="authorized_replication",
            audio_policy="subtitles_only", transcript=None,
            rewritten_script=[{"start": 0, "end": 1, "text": "caption"}], narration=None,
            effects=[], subtitles=[], target_duration_seconds=1,
        )
        retained = service.create_plan(
            source_rights=source_rights, preserve=["music"],
            music={**music, "loop": False, "trim_to_seconds": 1}, **common)
        self.assertEqual(retained["preserve"], ["music"])
        silence = AudioPlanService(key_store=self.keys).create_plan(
            source_rights=None, preserve=[], music=None, **common)
        self.assertIsNone(silence["music"])

    def test_music_intent_and_original_source_rights_fail_closed(self) -> None:
        track = self.root / "intent.wav"
        track.write_bytes(b"intent")
        music = ExistingAudioProvider([self.root], key_store=self.keys).accept(
            track, expected_sha256=hashlib.sha256(track.read_bytes()).hexdigest(), rights={"music": True})
        bindings = {"music": {"path": music["path"], "sha256": music["sha256"]}}
        service, source_rights = self._authorized_service(bindings, ["music"])
        base = dict(
            project_id="vp_" + "1" * 24, design_fingerprint="2" * 64,
            batch_fingerprint="3" * 64, creative_mode="authorized_replication",
            audio_policy="preserve_authorized_audio", source_rights=source_rights,
            transcript=None, rewritten_script=[], narration=None, effects=[], subtitles=[],
            target_duration_seconds=1, preserve=["music"],
        )
        for intent in ({"loop": "yes", "trim_to_seconds": 1}, {"loop": False, "trim_to_seconds": -1}):
            with self.subTest(intent=intent), self.assertRaises(ValueError):
                service.create_plan(music={**music, **intent}, **base)
        for intent in (
            {"loop": False, "trim_to_seconds": 2, "use_full_track": False},
            {"loop": True, "trim_to_seconds": None, "use_full_track": False},
            {"loop": True, "trim_to_seconds": 1, "use_full_track": True},
            {"loop": False, "trim_to_seconds": None, "use_full_track": False},
        ):
            with self.subTest(closed_intent=intent), self.assertRaises(ValueError):
                service.create_plan(music={**music, **intent}, **base)
        with self.assertRaises(AudioRightsError):
            AudioPlanService(key_store=self.keys).create_plan(
                **{**base, "creative_mode": "original_redesign", "audio_policy": "subtitles_only",
                   "music": None, "preserve": []})

    def test_commit_reverifies_every_artifact_immediately_before_write(self) -> None:
        store = VideoProjectStore(self.root / "projects")
        project = store.create(title="audio", creative_mode="original_redesign", audio_policy="full_redesign")
        narration = MacOSSayProvider(
            SyntheticNarrationAdapter(), self.root, voices={"Samantha"}, key_store=self.keys
        ).synthesize([{"start": 0, "end": 1, "text": "new"}], voice="Samantha", output_path=self.root / "commit.aiff")
        service = AudioPlanService(key_store=self.keys, project_store=store)
        plan = service.create_plan(
            project_id=project["project_id"], design_fingerprint="2" * 64,
            batch_fingerprint="3" * 64, creative_mode="original_redesign", audio_policy="full_redesign",
            source_rights=None, transcript=None, rewritten_script=[{"start": 0, "end": 1, "text": "new"}],
            narration=narration, music=None, effects=[], subtitles=[], target_duration_seconds=1,
        )
        Path(narration["path"]).write_bytes(b"changed-after-plan")
        with self.assertRaises(ContractValidationError):
            service.commit_plan(plan)
        self.assertFalse((store.project_root(project["project_id"]) / "audio_plan").exists())

    def test_full_redesign_is_default_and_silent_allows_optional_subtitles(self) -> None:
        narration = MacOSSayProvider(
            SyntheticNarrationAdapter(), self.root, voices={"Samantha"}, key_store=self.keys
        ).synthesize([{"start": 0, "end": 1, "text": "new"}], voice="Samantha", output_path=self.root / "default.aiff")
        plan = AudioPlanService(key_store=self.keys).create_plan(
            project_id="vp_" + "1" * 24, design_fingerprint="2" * 64,
            batch_fingerprint="3" * 64, creative_mode="original_redesign",
            source_rights=None, transcript=None,
            rewritten_script=[{"start": 0, "end": 1, "text": "new"}], narration=narration,
            music=None, effects=[], subtitles=[], target_duration_seconds=1,
        )
        self.assertEqual(plan["audio_policy"], "full_redesign")

        from scripts.subtitle_service import SubtitleService
        subtitle = SubtitleService(key_store=self.keys).write_srt(
            [{"start": 0, "end": 1, "text": "caption"}], self.root / "silent.srt",
            target_duration_seconds=1,
        )
        silent = AudioPlanService(key_store=self.keys).create_plan(
            project_id="vp_" + "1" * 24, design_fingerprint="2" * 64,
            batch_fingerprint="3" * 64, creative_mode="original_redesign", audio_policy="silent",
            source_rights=None, transcript=None,
            rewritten_script=[{"start": 0, "end": 1, "text": "caption"}], narration=None,
            music=None, effects=[], subtitles=[subtitle], target_duration_seconds=1,
        )
        self.assertEqual(len(silent["subtitles"]), 1)


if __name__ == "__main__":
    unittest.main()
