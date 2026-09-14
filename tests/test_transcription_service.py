from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.media_adapter import MediaOutputError, MediaResult
from scripts.video_project_store import VideoProjectStore
from scripts.transcription_service import (
    TranscriptionService,
    TranscriptionUnavailableError,
    WhisperCliProvider,
)
from scripts.trusted_media_tools import TrustedMediaToolError
from scripts.json_contracts import validate_contract


class RecordingAdapter:
    def __init__(self, payload: dict | None = None, error: Exception | None = None,
                 stdout: str | None = None, write_output: bool = True, extra_json: bool = False) -> None:
        self.payload = payload or {"language": "zh", "segments": []}
        self.error = error
        self.stdout = stdout
        self.write_output = write_output
        self.extra_json = extra_json
        self.executable_identity = {"kind": "whisper", "path": "/private/whisper",
                                    "sha256": "e" * 64}
        self.calls: list[tuple[str, list[str], int]] = []

    def run(self, kind: str, argv: list[str], *, timeout_seconds: int, pass_fds=()) -> MediaResult:
        self.calls.append((kind, list(argv), timeout_seconds))
        if self.error:
            raise self.error
        if self.write_output:
            output_dir = Path(argv[argv.index("--output_dir") + 1])
            source = Path(argv[-1])
            (output_dir / f"{source.stem}.json").write_text(json.dumps(self.payload), encoding="utf-8")
            os.chmod(output_dir / f"{source.stem}.json", 0o600)
            if self.extra_json:
                (output_dir / "extra.json").write_text("{}", encoding="utf-8")
        return MediaResult(0, self.stdout if self.stdout is not None else json.dumps(self.payload), "")


class TranscriptionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)
        self.model = self.root / "model.bin"
        self.model.write_bytes(b"fixed-model")
        os.chmod(self.model, 0o600)
        self.source = self.root / "source.wav"
        self.source.write_bytes(b"RIFFsynthetic")
        os.chmod(self.source, 0o600)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_whisper_provider_uses_fixed_model_language_and_json_output_flags(self) -> None:
        adapter = RecordingAdapter({"language": "zh", "segments": []})
        provider = WhisperCliProvider(adapter, self.model, self.root)
        provider.transcribe(self.source, language="zh")
        kind, argv, timeout = adapter.calls[0]
        self.assertEqual(kind, "whisper")
        self.assertEqual(argv[:5], ["--model", str(self.model.resolve()), "--output_format", "json", "--output_dir"])
        self.assertTrue(Path(argv[5]).parent.samefile(self.root))
        self.assertTrue(Path(argv[5]).name.startswith("whisper-"))
        self.assertEqual(argv[6:8], ["--language", "zh"])
        self.assertEqual(Path(argv[8]).name, "source.wav")
        self.assertNotIn("--shell", argv)
        self.assertEqual(timeout, 1800)

    def test_complete_segments_keep_attribution_digest_and_review_flag(self) -> None:
        adapter = RecordingAdapter({"language": "en", "segments": [
            {"start": 0.0, "end": 1.25, "text": " unclear ", "avg_logprob": -0.8, "no_speech_prob": 0.1},
            {"start": 1.25, "end": 2.0, "text": "Clear", "avg_logprob": -0.1, "no_speech_prob": 0.05},
        ]})
        result = WhisperCliProvider(adapter, self.model, self.root).transcribe(self.source, language="en")
        self.assertEqual(result[0], {
            "start": 0.0, "end": 1.25, "language": "en", "text": "unclear",
            "avg_logprob": -0.8, "no_speech_probability": 0.1, "requires_review": True, "provider": "whisper",
            "model": str(self.model.resolve()), "artifact_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
        })
        self.assertFalse(result[1]["requires_review"])

    def test_language_and_timeline_are_validated(self) -> None:
        provider = WhisperCliProvider(RecordingAdapter(), self.model, self.root)
        for language in ("../../x", "en;curl", ""):
            with self.subTest(language=language), self.assertRaises(ValueError):
                provider.transcribe(self.source, language=language)
        bad = RecordingAdapter({"language": "en", "segments": [{"start": 2, "end": 1, "text": "x", "confidence": .8}]})
        with self.assertRaises(ValueError):
            WhisperCliProvider(bad, self.model, self.root).transcribe(self.source, language="en")

    def test_missing_provider_returns_typed_blocked_result_and_never_fabricates(self) -> None:
        adapter = RecordingAdapter(error=TrustedMediaToolError("not enrolled"))
        service = TranscriptionService(WhisperCliProvider(adapter, self.model, self.root))
        result = service.transcribe(self.source, language=None)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "asr_provider_unavailable")
        self.assertEqual(result["segments"], [])
        with self.assertRaises(TranscriptionUnavailableError):
            service.require_transcript(self.source, language=None)

    def test_reads_exact_private_json_file_not_stdout_and_rejects_absent_or_multiple_outputs(self) -> None:
        payload = {"language": "en", "segments": [{"start": 0, "end": 1, "text": "ok",
                    "avg_logprob": -0.1, "no_speech_prob": 0.01}]}
        result = WhisperCliProvider(
            RecordingAdapter(payload, stdout="not json"), self.model, self.root
        ).transcribe(self.source, language="en")
        self.assertEqual(result[0]["text"], "ok")
        for adapter in (RecordingAdapter(payload, write_output=False),
                        RecordingAdapter(payload, extra_json=True)):
            with self.subTest(adapter=adapter), self.assertRaises(MediaOutputError):
                WhisperCliProvider(adapter, self.model, self.root).transcribe(self.source, language="en")

    def test_logprob_and_no_speech_are_distinct_review_evidence(self) -> None:
        payload = {"language": "en", "segments": [
            {"start": 0, "end": 1, "text": "low log probability", "avg_logprob": -0.7, "no_speech_prob": .1},
            {"start": 1, "end": 2, "text": "possible silence", "avg_logprob": -.1, "no_speech_prob": .8},
        ]}
        segments = WhisperCliProvider(RecordingAdapter(payload), self.model, self.root).transcribe(self.source, language="en-US")
        self.assertEqual(segments[0]["avg_logprob"], -0.7)
        self.assertEqual(segments[1]["no_speech_probability"], .8)
        self.assertTrue(all(item["requires_review"] for item in segments))
        argv = RecordingAdapter(payload)
        WhisperCliProvider(argv, self.model, self.root).transcribe(self.source, language="zh-Hans")
        self.assertEqual(argv.calls[0][1][argv.calls[0][1].index("--language") + 1], "zh")

    def test_complete_and_blocked_outputs_are_exact_plan_contract_variants(self) -> None:
        complete = TranscriptionService(WhisperCliProvider(RecordingAdapter({"language": "en", "segments": []}), self.model, self.root)).transcribe(self.source, language="en")
        blocked = TranscriptionService(WhisperCliProvider(RecordingAdapter(error=TrustedMediaToolError("x")), self.model, self.root)).transcribe(self.source, language=None)
        self.assertEqual(set(complete), {"status", "provider", "model", "executable", "source_sha256", "language", "segments"})
        self.assertEqual(set(blocked), {"status", "reason", "detail", "segments"})
        store = VideoProjectStore(self.root / "projects")
        project = store.create(title="transcript", creative_mode="original_redesign", audio_policy="subtitles_only")
        receipt = TranscriptionService(
            WhisperCliProvider(RecordingAdapter({"language": "en", "segments": []}), self.model, self.root),
            project_store=store,
        ).transcribe_and_commit(project["project_id"], self.source, language="en")
        validate_contract(receipt, "transcript_receipt.schema.json")
        self.assertEqual(receipt["version"], "v001")
        self.assertEqual(receipt["source_sha256"], complete["source_sha256"])

    def test_malformed_or_failed_enrolled_provider_is_degraded_not_blocked(self) -> None:
        cases = [RecordingAdapter(payload={"language": "en", "segments": [{"start": 2, "end": 1, "text": "x", "confidence": .8}]}), RecordingAdapter(payload={"bad": True})]
        for adapter in cases:
            with self.subTest(payload=adapter.payload):
                result = TranscriptionService(WhisperCliProvider(adapter, self.model, self.root)).transcribe(self.source, language="en")
                self.assertEqual(result["status"], "degraded")
                self.assertEqual(result["reason"], "asr_output_invalid")
                self.assertEqual(result["segments"], [])
                self.assertNotIn("provider", result)

    def test_private_model_source_and_output_root_are_required_without_permission_mutation(self) -> None:
        public_root = self.root / "public-output"
        public_root.mkdir(mode=0o755)
        before = public_root.stat().st_mode & 0o777
        with self.assertRaises(ValueError):
            WhisperCliProvider(RecordingAdapter(), self.model, public_root)
        self.assertEqual(public_root.stat().st_mode & 0o777, before)

        public_source = self.root / "public.wav"
        public_source.write_bytes(b"RIFFpublic")
        os.chmod(public_source, 0o644)
        provider = WhisperCliProvider(RecordingAdapter(), self.model, self.root)
        with self.assertRaises(ValueError):
            provider.transcribe(public_source, language="en")

    def test_segments_are_strictly_bounded_before_becoming_evidence(self) -> None:
        oversized = "x" * 501
        cases = [
            {"language": "en", "segments": [{"start": 0, "end": 1, "text": oversized, "confidence": .8}]},
            {"language": "en", "segments": [
                {"start": index, "end": index + .5, "text": "x", "confidence": .8}
                for index in range(10_001)
            ]},
            {"language": "en", "segments": [{"start": 0, "end": 21_601, "text": "x", "confidence": .8}]},
        ]
        for payload in cases:
            with self.subTest(segment_count=len(payload["segments"])):
                result = TranscriptionService(
                    WhisperCliProvider(RecordingAdapter(payload), self.model, self.root)
                ).transcribe(self.source, language="en")
                self.assertEqual(result["status"], "degraded")
                self.assertEqual(result["segments"], [])

    @staticmethod
    def _minimal_plan(transcript):
        from scripts.json_contracts import canonical_fingerprint
        core = {"schema_version":"1.0","version":"v001","project_id":"vp_"+"1"*24,"design_fingerprint":"2"*64,"batch_fingerprint":"3"*64,"creative_mode":"original_redesign","audio_policy":"subtitles_only","target_duration_seconds":1.0,"source_rights":None,"preserve":[],"transcript":transcript,"rewritten_script":[],"narration":None,"music":None,"effects":[],"ambience":[],"subtitles":[],"provenance":{"remote_services_used":False,"source_voice_cloned":False}}
        return {**core,"plan_fingerprint":canonical_fingerprint(core)}


if __name__ == "__main__":
    unittest.main()
