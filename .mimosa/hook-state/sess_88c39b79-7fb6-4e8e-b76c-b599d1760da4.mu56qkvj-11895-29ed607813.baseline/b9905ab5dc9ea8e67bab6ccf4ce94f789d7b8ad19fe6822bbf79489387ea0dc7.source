"""Self-contained responsive comparison report gates."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.video_comparison_report import (  # noqa: E402
    ComparisonReportError,
    ComparisonReportService,
    redact_for_embedding,
)

PRIVATE_SOURCE_PATH = "/Users/someone/private/reference/source.mov"


def _payload() -> dict:
    return {
        "generated_at": "2026-09-14T00:00:00Z",
        "project": {
            "project_id": "vp_" + "a" * 24,
            "composition_version": "v001",
            "title": "Reference re-direct comparison",
        },
        "source_facts": {"rows": [{"field": "duration_seconds", "value": 42.0}]},
        "preserved_dimensions": {"rows": [{"field": "width", "value": 1280}]},
        "redesigned_content": {"rows": [{"field": "continuity", "value": "preserved"}]},
        "shots": {
            "rows": [
                {"shot_id": "s001", "attempt": 1, "accepted": True, "sha256": "a" * 64},
                {"shot_id": "s002", "attempt": 2, "accepted": False, "sha256": "b" * 64},
            ]
        },
        "audio_subtitles": {"rows": [{"field": "subtitle_mode", "value": "muxed"}]},
        "final_media": {
            "gates": {
                "container": {"passed": True, "measured": "mov,mp4", "expected": "mp4"},
                "av_sync": {"passed": False, "measured": 0.9, "expected": "<= 0.08s"},
            }
        },
    }


class ResponsiveLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ComparisonReportService()

    def test_report_carries_all_three_viewport_breakpoints(self) -> None:
        html = self.service.render(_payload())
        self.assertIn("@media (max-width: 767px)", html)
        self.assertIn("@media (min-width: 768px)", html)
        self.assertIn("@media (min-width: 1200px)", html)

    def test_report_declares_a_responsive_viewport(self) -> None:
        html = self.service.render(_payload())
        self.assertIn('name="viewport"', html)
        self.assertIn("width=device-width", html)

    def test_report_is_self_contained(self) -> None:
        html = self.service.render(_payload())
        self.assertNotIn("<script", html)
        self.assertNotIn("http://", html.split("<body>")[1])
        self.assertNotIn("https://", html.split("<body>")[1])

    def test_report_carries_the_rights_disclaimer(self) -> None:
        html = self.service.render(_payload())
        self.assertIn("rights assertion", html.lower())

    def test_report_renders_every_section(self) -> None:
        html = self.service.render(_payload())
        for heading in (
            "Source facts",
            "Preserved structural dimensions",
            "Redesigned expressive content",
            "Per-shot generation and evaluation",
            "Audio and subtitle provenance",
            "Final media gates",
        ):
            self.assertIn(heading, html)


class PrivacyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ComparisonReportService()

    def test_report_never_embeds_a_private_source_path(self) -> None:
        payload = _payload()
        payload["source_facts"] = {"rows": [{"field": "source", "value": PRIVATE_SOURCE_PATH}]}
        html = self.service.render(payload)
        self.assertNotIn(PRIVATE_SOURCE_PATH, html)
        self.assertNotIn("/Users/someone", html)
        # The JSON sidecar carries the placeholder verbatim; the HTML branch
        # escapes it, so accept either rendering.
        self.assertTrue(
            "<redacted-path>" in html or "&lt;redacted-path&gt;" in html,
            "the redaction placeholder is missing from the report",
        )

    def test_forbidden_field_names_are_rejected(self) -> None:
        for key in ("source_path", "transcript", "rights_evidence", "token", "api_key"):
            payload = _payload()
            payload["source_facts"] = {key: "anything"}
            with self.assertRaises(ComparisonReportError):
                self.service.render(payload)

    def test_nested_absolute_path_is_rejected_on_write(self) -> None:
        payload = _payload()
        payload["shots"]["rows"][0]["artifact"] = "/private/var/folders/secret/frame.png"
        html = self.service.render(payload)
        self.assertNotIn("/private/var/folders/secret", html)

    def test_redaction_is_recursive(self) -> None:
        value = {"a": [{"b": "/Users/x/y.mov"}]}
        self.assertEqual(redact_for_embedding(value), {"a": [{"b": "<redacted-path>"}]})


class WriteTests(unittest.TestCase):
    def test_write_emits_html_and_json_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            html_path = Path(tmp) / "comparison-report.html"
            json_path = Path(tmp) / "comparison-report.json"
            service = ComparisonReportService()
            result = service.write(_payload(), html_path, json_path)
            self.assertTrue(html_path.is_file())
            self.assertTrue(json_path.is_file())
            self.assertEqual(result["html"], str(html_path))
            self.assertEqual(result["json"], str(json_path))
            parsed = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(parsed["project"]["composition_version"], "v001")

    def test_json_sidecar_is_redacted_too(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _payload()
            payload["source_facts"] = {"rows": [{"field": "source", "value": PRIVATE_SOURCE_PATH}]}
            html_path = Path(tmp) / "r.html"
            json_path = Path(tmp) / "r.json"
            ComparisonReportService().write(payload, html_path, json_path)
            self.assertNotIn(PRIVATE_SOURCE_PATH, json_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
