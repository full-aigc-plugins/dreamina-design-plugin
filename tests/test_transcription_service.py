from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.media_adapter import MediaResult
from scripts.transcription_service import (
    TranscriptionService,
    TranscriptionUnavailableError,
    WhisperCliProvider,
)
from scripts.trusted_media_tools import TrustedMediaToolError
from scripts.json_contracts import validate_contract


class RecordingAdapter:
    def __init__(self, payload: dict | None = None, error: Exception | None = None) -> None:
        self.payload = payload or {"language": "zh", "segments": []}
        self.error = error
        self.calls: list[tuple[str, list[str], int]] = []

    def run(self, kind: str, argv: list[str], *, timeout_seconds: int) -> MediaResult:
        self.calls.append((kind, list(argv), timeout_seconds))
        if self.error:
            raise self.error
        return MediaResult(0, json.dumps(self.payload), "")


class TranscriptionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)
        self.model = self.root / "model.bin"
        self.model.write_bytes(b"fixed-model")
        self.source = self.root / "source.wav"
        self.source.write_bytes(b"RIFFsynthetic")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_whisper_provider_uses_fixed_model_language_and_json_output_flags(self) -> None:
        adapter = RecordingAdapter({"language": "zh", "segments": []})
        provider = WhisperCliProvider(adapter, self.model, self.root)
        provider.transcribe(self.source, language="zh")
        kind, argv, timeout = adapter.calls[0]
        self.assertEqual(kind, "whisper")
        self.assertEqual(argv[:6], ["--model", str(self.model.resolve()), "--output_format", "json", "--output_dir", str(self.root.resolve())])
        self.assertEqual(argv[6:], ["--language", "zh", str(self.source.resolve())])
        self.assertNotIn("--shell", argv)
        self.assertEqual(timeout, 1800)

    def test_complete_segments_keep_attribution_digest_and_review_flag(self) -> None:
        adapter = RecordingAdapter({"language": "en", "segments": [
            {"start": 0.0, "end": 1.25, "text": " unclear ", "confidence": 0.42},
            {"start": 1.25, "end": 2.0, "text": "Clear", "confidence": 0.9},
        ]})
        result = WhisperCliProvider(adapter, self.model, self.root).transcribe(self.source, language="en")
        self.assertEqual(result[0], {
            "start": 0.0, "end": 1.25, "language": "en", "text": "unclear",
            "confidence": 0.42, "requires_review": True, "provider": "whisper",
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

    def test_complete_and_blocked_outputs_are_exact_plan_contract_variants(self) -> None:
        complete = TranscriptionService(WhisperCliProvider(RecordingAdapter({"language": "en", "segments": []}), self.model, self.root)).transcribe(self.source, language="en")
        blocked = TranscriptionService(WhisperCliProvider(RecordingAdapter(error=TrustedMediaToolError("x")), self.model, self.root)).transcribe(self.source, language=None)
        self.assertEqual(set(complete), {"status", "provider", "model", "artifact_sha256", "segments"})
        self.assertEqual(set(blocked), {"status", "reason", "detail", "segments"})
        for transcript in (complete, blocked):
            plan = self._minimal_plan(transcript)
            validate_contract(plan, "audio_plan.schema.json")

    @staticmethod
    def _minimal_plan(transcript):
        from scripts.json_contracts import canonical_fingerprint
        core = {"schema_version":"1.0","version":"v001","project_id":"vp_"+"1"*24,"design_fingerprint":"2"*64,"batch_fingerprint":"3"*64,"creative_mode":"original_redesign","audio_policy":"subtitles_only","target_duration_seconds":1.0,"source_rights":None,"preserve":[],"transcript":transcript,"rewritten_script":[],"narration":None,"music":None,"effects":[],"subtitles":[],"provenance":{"remote_services_used":False,"source_voice_cloned":False}}
        return {**core,"plan_fingerprint":canonical_fingerprint(core)}


if __name__ == "__main__":
    unittest.main()
