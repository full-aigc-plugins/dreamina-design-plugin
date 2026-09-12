"""RED tests for the strict per-Skill TRACE (Task 7 strict TRACE gate).

The plan defines strict TRACE as: per-Skill frontmatter / body / examples /
link audit. The script must report:

* per-Skill pass/fail status;
* per-check category (frontmatter, body, examples, links);
* aggregated summary (`PASS` only when every Skill passes every check).

This is the offline equivalent of running the superpowers skill-trace
check on every packaged Skill.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:  # pragma: no cover - RED phase
    from scripts.run_strict_trace import (  # type: ignore  # noqa: E402
        StrictTraceReport,
        StrictTracer,
    )
except ModuleNotFoundError:  # pragma: no cover
    StrictTracer = None  # type: ignore[assignment]
    StrictTraceReport = None  # type: ignore[assignment]


def _make_skill(root: Path, name: str, body: str, *, with_upstream_sha: bool = True) -> Path:
    skill = root / name
    skill.mkdir(parents=True, exist_ok=True)
    sha_line = f"upstream_commit_sha: {'a' * 40}\n" if with_upstream_sha else ""
    text = (
        f"---\n"
        f"name: {name}\n"
        f"description: Stub for {name}.\n"
        f"{sha_line}"
        f"---\n\n"
        f"{body}\n"
    )
    skill.joinpath("SKILL.md").write_text(text, encoding="utf-8")
    return skill


class ModuleExportTests(unittest.TestCase):
    def test_module_exports_tracer(self) -> None:
        self.assertIsNotNone(StrictTracer)
        self.assertIsNotNone(StrictTraceReport)


class FrontmatterCheckTests(unittest.TestCase):
    def test_missing_name_reports_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp) / "skills"
            skill = skills_root / "bad"
            skill.mkdir(parents=True)
            skill.joinpath("SKILL.md").write_text(
                "---\ndescription: missing name\n---\nbody\n", encoding="utf-8"
            )
            tracer = StrictTracer(skills_root=skills_root)
            report = tracer.run()
            self.assertEqual(report.skill_results["bad"].frontmatter_status, "FAIL")
            self.assertIn("name", report.skill_results["bad"].frontmatter_errors[0])

    def test_multiline_plain_scalar_description_reports_failure(self) -> None:
        """A plain YAML scalar continued on an unindented line is invalid.

        Codex silently skips any Skill whose frontmatter fails to parse, so
        such a Skill is invisible to the model even though every key is
        present. The tracer must reject it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp) / "skills"
            skill = skills_root / "broken-desc"
            skill.mkdir(parents=True)
            skill.joinpath("SKILL.md").write_text(
                "---\n"
                "name: broken-desc\n"
                "description: Stub for the upstream Skill. Body is fetched and\n"
                "must remain byte-identical to the pinned commit.\n"
                "upstream_commit_sha: " + "a" * 40 + "\n"
                "---\n\n"
                "A substantial body so the body check passes independently of "
                "the frontmatter check being exercised here.\n\n"
                "```bash\nok\n```\n",
                encoding="utf-8",
            )
            tracer = StrictTracer(skills_root=skills_root)
            report = tracer.run()
            result = report.skill_results["broken-desc"]
            self.assertEqual(result.frontmatter_status, "FAIL")
            self.assertTrue(
                any("plain scalar" in err or "not a valid" in err or "indent" in err
                    for err in result.frontmatter_errors),
                f"expected a frontmatter-parse error, got {result.frontmatter_errors}",
            )

    def test_valid_single_line_description_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp) / "skills"
            skill = skills_root / "good-desc"
            skill.mkdir(parents=True)
            skill.joinpath("SKILL.md").write_text(
                "---\n"
                "name: good-desc\n"
                "description: A single-line description that parses cleanly.\n"
                "upstream_commit_sha: " + "a" * 40 + "\n"
                "---\n\n"
                "A substantial body so the body check passes independently.\n\n"
                "```bash\nok\n```\n",
                encoding="utf-8",
            )
            tracer = StrictTracer(skills_root=skills_root)
            report = tracer.run()
            self.assertEqual(report.skill_results["good-desc"].frontmatter_status, "PASS")

    def test_block_scalar_description_passes(self) -> None:
        """A ``|`` block scalar is valid YAML and must be accepted."""
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp) / "skills"
            skill = skills_root / "block-desc"
            skill.mkdir(parents=True)
            skill.joinpath("SKILL.md").write_text(
                "---\n"
                "name: block-desc\n"
                "description: |\n"
                "  A block scalar description.\n"
                "  It spans two lines but is valid YAML.\n"
                "upstream_commit_sha: " + "a" * 40 + "\n"
                "---\n\n"
                "A substantial body so the body check passes independently.\n\n"
                "```bash\nok\n```\n",
                encoding="utf-8",
            )
            tracer = StrictTracer(skills_root=skills_root)
            report = tracer.run()
            self.assertEqual(report.skill_results["block-desc"].frontmatter_status, "PASS")


class BodyCheckTests(unittest.TestCase):
    def test_empty_body_reports_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp) / "skills"
            _make_skill(skills_root, "empty", "")
            tracer = StrictTracer(skills_root=skills_root)
            report = tracer.run()
            self.assertEqual(report.skill_results["empty"].body_status, "FAIL")

    def test_substantial_body_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp) / "skills"
            long_body = (
                "This is a substantial body of text used for testing the "
                "frontmatter / body / examples / links checks performed by "
                "the strict per-Skill TRACE runner. It must exceed 80 chars."
            )
            _make_skill(skills_root, "ok", long_body)
            tracer = StrictTracer(skills_root=skills_root)
            report = tracer.run()
            self.assertEqual(report.skill_results["ok"].body_status, "PASS")


class ExamplesCheckTests(unittest.TestCase):
    def test_example_block_present_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp) / "skills"
            _make_skill(
                skills_root,
                "with-examples",
                "Body.\n\n## Example\n\n```bash\ndreamina --help\n```\n",
            )
            tracer = StrictTracer(skills_root=skills_root)
            report = tracer.run()
            self.assertEqual(report.skill_results["with-examples"].examples_status, "PASS")

    def test_no_example_block_reports_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp) / "skills"
            _make_skill(skills_root, "no-examples", "Body with no code fences at all.\n")
            tracer = StrictTracer(skills_root=skills_root)
            report = tracer.run()
            self.assertEqual(report.skill_results["no-examples"].examples_status, "FAIL")


class LinkCheckTests(unittest.TestCase):
    def test_external_https_link_checked_and_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp) / "skills"
            body = "See [docs](https://example.test/skill) for details.\n"
            _make_skill(skills_root, "link-ok", body)
            tracer = StrictTracer(skills_root=skills_root, check_external_links=False)
            report = tracer.run()
            self.assertEqual(report.skill_results["link-ok"].links_status, "PASS")

    def test_broken_local_relative_link_reports_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp) / "skills"
            _make_skill(
                skills_root,
                "link-broken",
                "See [missing](./does-not-exist.md) for details.\n",
            )
            tracer = StrictTracer(skills_root=skills_root)
            report = tracer.run()
            self.assertEqual(report.skill_results["link-broken"].links_status, "FAIL")
            self.assertTrue(
                any("does-not-exist" in err for err in report.skill_results["link-broken"].links_errors),
                report.skill_results["link-broken"].links_errors,
            )


class AggregationTests(unittest.TestCase):
    def test_overall_pass_when_every_skill_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp) / "skills"
            long_body = (
                "Substantial body content for the per-Skill TRACE runner. "
                "It must exceed 80 chars so the body check passes.\n\n"
                "## Example\n\n```bash\nok\n```\n"
            )
            for name in ("a", "b", "c"):
                _make_skill(skills_root, name, long_body)
            tracer = StrictTracer(skills_root=skills_root)
            report = tracer.run()
            self.assertEqual(report.overall_status, "PASS")
            self.assertEqual(report.passed_skills, 3)
            self.assertEqual(report.failed_skills, 0)

    def test_overall_fail_when_any_skill_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp) / "skills"
            long_body = (
                "Substantial body content for the per-Skill TRACE runner. "
                "It must exceed 80 chars so the body check passes.\n\n"
                "## Example\n\n```bash\nok\n```\n"
            )
            _make_skill(skills_root, "ok", long_body)
            _make_skill(skills_root, "bad", "")
            tracer = StrictTracer(skills_root=skills_root)
            report = tracer.run()
            self.assertEqual(report.overall_status, "FAIL")
            self.assertEqual(report.failed_skills, 1)


class ReportSerializationTests(unittest.TestCase):
    def test_report_to_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp) / "skills"
            long_body = (
                "Substantial body content for the per-Skill TRACE runner. "
                "It must exceed 80 chars so the body check passes.\n\n"
                "## Example\n\n```bash\nok\n```\n"
            )
            _make_skill(skills_root, "ok", long_body)
            tracer = StrictTracer(skills_root=skills_root)
            report = tracer.run()
            blob = report.to_json()
            import json
            parsed = json.loads(blob)
            self.assertEqual(parsed["overall_status"], "PASS")
            self.assertIn("skill_results", parsed)


if __name__ == "__main__":
    unittest.main()
