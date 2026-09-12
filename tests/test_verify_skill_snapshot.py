"""RED tests for the Dreamina Skill snapshot verifier (Task 6).

The verifier must:

* confirm exactly 13 Skill directories exist under ``skills/`` with the
  expected ``dreamina-*`` names (12 migrated + ``dreamina-cli``);
* prove zero installable ``jimeng-*`` identities anywhere under
  ``skills/`` (frontmatter or directory name);
* verify each packaged Skill has the required SKILL.md frontmatter
  (name, description);
* when the upstream ``full-aigc-skills/dreamina-skills`` source is
  available, byte-compare each Skill against the explicit upstream SHA
  and report PASS;
* when the upstream source is unavailable, mark the byte-parity check
  as ``NOT_RUN`` with a clear reason and instructions for unlocking it;
* never silently pass when parity cannot be proven.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:  # pragma: no cover - exercised by RED phase
    from scripts.verify_skill_snapshot import (  # type: ignore  # noqa: E402
        SnapshotVerifier,
        SkillSnapshotReport,
        UpstreamUnavailableError,
        ParityCheckNotRunError,
    )
except ModuleNotFoundError:  # pragma: no cover
    SnapshotVerifier = None  # type: ignore[assignment]
    SkillSnapshotReport = None  # type: ignore[assignment]
    UpstreamUnavailableError = None  # type: ignore[assignment]
    ParityCheckNotRunError = None  # type: ignore[assignment]


EXPECTED_SKILLS = (
    "dreamina-cli",
    "dreamina-cli-image2image",
    "dreamina-cli-image2video",
    "dreamina-cli-text2image",
    "dreamina-cli-text2video",
    "dreamina-opencli-image2image",
    "dreamina-opencli-image2video",
    "dreamina-opencli-text2image",
    "dreamina-opencli-text2video",
    "dreamina-prompt-image2image",
    "dreamina-prompt-image2video",
    "dreamina-prompt-text2image",
    "dreamina-prompt-text2video",
)


def _write_skill(root: Path, name: str, *, upstream_sha: str = "a" * 40, body: str | None = None) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: stub for {name}\n"
        f"upstream_commit_sha: {upstream_sha}\n"
        "---\n\n"
        f"{body or 'Placeholder body for ' + name}\n",
        encoding="utf-8",
    )
    return skill_dir


def _init_fake_upstream(upstream_root: Path, *, head_sha: str) -> None:
    """Create a fake git checkout rooted at ``upstream_root`` whose HEAD
    resolves to ``head_sha``. The directory also carries every expected
    Skill directory so the parity check can verify identity."""
    upstream_root.mkdir(parents=True, exist_ok=True)
    git_dir = upstream_root / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    refs_dir = git_dir / "refs" / "heads"
    refs_dir.mkdir(parents=True)
    (refs_dir / "main").write_text(f"{head_sha}\n", encoding="utf-8")
    for name in EXPECTED_SKILLS:
        (upstream_root / name).mkdir(exist_ok=True)


class ModuleExportTests(unittest.TestCase):
    def test_module_exports_verifier(self) -> None:
        self.assertIsNotNone(SnapshotVerifier)
        self.assertIsNotNone(SkillSnapshotReport)


class SkillInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.skills_root = Path(self.tmp.name) / "skills"
        for name in EXPECTED_SKILLS:
            _write_skill(self.skills_root, name)

    def test_exactly_thirteen_skills_present(self) -> None:
        verifier = SnapshotVerifier(skills_root=self.skills_root, upstream_root=None)
        report = verifier.run()
        self.assertEqual(report.skill_count, 13)
        self.assertEqual(set(report.skill_names), set(EXPECTED_SKILLS))

    def test_missing_skill_reported(self) -> None:
        # Remove one skill directory entirely.
        import shutil
        shutil.rmtree(self.skills_root / "dreamina-cli-text2image")
        verifier = SnapshotVerifier(skills_root=self.skills_root, upstream_root=None)
        report = verifier.run()
        self.assertIn("dreamina-cli-text2image", report.missing_skills)

    def test_jimeng_installable_identity_rejected(self) -> None:
        # Inject a forbidden jimeng identity.
        (self.skills_root / "jimeng-cli").mkdir()
        (self.skills_root / "jimeng-cli" / "SKILL.md").write_text(
            "---\nname: jimeng-cli\ndescription: forbidden\n---\nold", encoding="utf-8"
        )
        verifier = SnapshotVerifier(skills_root=self.skills_root, upstream_root=None)
        report = verifier.run()
        self.assertIn("jimeng-cli", report.forbidden_installable_identities)


class ParityNotRunTests(unittest.TestCase):
    def test_parity_marked_not_run_when_upstream_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp) / "skills"
            for name in EXPECTED_SKILLS:
                _write_skill(skills_root, name)
            verifier = SnapshotVerifier(skills_root=skills_root, upstream_root=None)
            report = verifier.run()
            self.assertEqual(report.parity_status, "NOT_RUN")
            self.assertIn("upstream", report.parity_reason.lower())

    def test_parity_passes_when_upstream_sha_matches_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp) / "skills"
            upstream_root = Path(tmp) / "upstream"
            head_sha = "a" * 40
            _init_fake_upstream(upstream_root, head_sha=head_sha)
            for name in EXPECTED_SKILLS:
                _write_skill(skills_root, name, upstream_sha=head_sha)
            verifier = SnapshotVerifier(skills_root=skills_root, upstream_root=upstream_root)
            report = verifier.run()
            self.assertEqual(report.parity_status, "PASS")

    def test_parity_fails_when_local_sha_drifts_from_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp) / "skills"
            upstream_root = Path(tmp) / "upstream"
            head_sha = "b" * 40
            _init_fake_upstream(upstream_root, head_sha=head_sha)
            for name in EXPECTED_SKILLS:
                # Local Skills pin a *different* SHA.
                _write_skill(skills_root, name, upstream_sha="c" * 40)
            verifier = SnapshotVerifier(skills_root=skills_root, upstream_root=upstream_root)
            report = verifier.run()
            self.assertEqual(report.parity_status, "FAIL")
            self.assertTrue(
                any(entry.startswith("dreamina-cli:") for entry in report.parity_mismatches),
                f"expected a dreamina-cli mismatch in {report.parity_mismatches}",
            )


class FrontmatterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.skills_root = Path(self.tmp.name) / "skills"

    def test_missing_frontmatter_name_reported(self) -> None:
        skill_dir = self.skills_root / "dreamina-cli"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text("---\ndescription: bad\n---\nx", encoding="utf-8")
        # The other 12 expected skills are missing entirely.
        verifier = SnapshotVerifier(skills_root=self.skills_root, upstream_root=None)
        report = verifier.run()
        self.assertIn("dreamina-cli", report.invalid_frontmatter)

    def test_skill_md_must_be_present(self) -> None:
        skill_dir = self.skills_root / "dreamina-cli-text2image"
        skill_dir.mkdir(parents=True, exist_ok=True)
        # No SKILL.md file.
        verifier = SnapshotVerifier(skills_root=self.skills_root, upstream_root=None)
        report = verifier.run()
        self.assertIn("dreamina-cli-text2image", report.missing_skill_md)


class UpstreamProbeTests(unittest.TestCase):
    def test_upstream_path_must_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp) / "skills"
            for name in EXPECTED_SKILLS:
                _write_skill(skills_root, name)
            verifier = SnapshotVerifier(
                skills_root=skills_root,
                upstream_root=Path("/nonexistent/path/to/dreamina-skills"),
            )
            with self.assertRaises(UpstreamUnavailableError):
                verifier.run_parity_check()

    def test_parity_check_not_run_raises_when_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp) / "skills"
            for name in EXPECTED_SKILLS:
                _write_skill(skills_root, name)
            verifier = SnapshotVerifier(skills_root=skills_root, upstream_root=None)
            report = verifier.run()
            self.assertEqual(report.parity_status, "NOT_RUN")
            with self.assertRaises(ParityCheckNotRunError):
                verifier.require_parity_pass(report)


class ReportSerializationTests(unittest.TestCase):
    def test_report_serializes_to_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp) / "skills"
            for name in EXPECTED_SKILLS:
                _write_skill(skills_root, name)
            verifier = SnapshotVerifier(skills_root=skills_root, upstream_root=None)
            report = verifier.run()
            blob = report.to_json()
            parsed = json.loads(blob)
            self.assertEqual(parsed["skill_count"], 13)
            self.assertEqual(parsed["parity_status"], "NOT_RUN")


if __name__ == "__main__":
    unittest.main()
