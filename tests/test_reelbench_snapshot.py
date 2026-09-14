"""Behavior tests for the pinned ReelBench Skill snapshot verifier."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:  # RED phase: the verifier has not been implemented yet.
    from scripts.verify_reelbench_snapshot import verify_reelbench_snapshot  # noqa: E402
except ModuleNotFoundError:  # pragma: no cover - proves the RED starting point.
    verify_reelbench_snapshot = None  # type: ignore[assignment]


PINNED_REVISION = "18f2f63987337df0975a89973d38d50f3231ee31"
ALIASES = {
    "video-shots": "dreamina-video-shots",
    "video-sync": "dreamina-video-sync",
}


class ReelBenchSnapshotTests(unittest.TestCase):
    """The lock accepts only the declared directory/frontmatter alias delta."""

    def setUp(self) -> None:
        self.assertIsNotNone(
            verify_reelbench_snapshot,
            "production snapshot verifier is not implemented",
        )
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "plugin"
        self.upstream = Path(self.tmp.name) / "upstream"
        self.root.mkdir()
        shutil.copytree(ROOT / "skills", self.root / "skills")
        shutil.copytree(ROOT / "upstream", self.root / "upstream")
        self._make_upstream_tree()

    def _make_upstream_tree(self) -> None:
        """Create an independent upstream fixture by reversing only aliases."""
        for upstream_name, packaged_name in ALIASES.items():
            source = self.root / "skills" / packaged_name
            destination = self.upstream / "skills" / upstream_name
            shutil.copytree(source, destination)
            skill_md = destination / "SKILL.md"
            skill_md.write_bytes(
                skill_md.read_bytes().replace(
                    f"name: {packaged_name}".encode(),
                    f"name: {upstream_name}".encode(),
                    1,
                )
            )

    def mutate(self, relative: str) -> None:
        target = self.root / "skills" / "dreamina-video-shots" / relative
        target.write_bytes(target.read_bytes() + b"\nlocal mutation\n")

    def test_packaged_skill_aliases_are_the_only_allowed_delta(self) -> None:
        report = verify_reelbench_snapshot(self.root, upstream_root=self.upstream)
        self.assertEqual(report.revision, PINNED_REVISION)
        self.assertEqual(
            report.packaged_names,
            ["dreamina-video-shots", "dreamina-video-sync"],
        )
        self.assertEqual(report.mismatches, [])

    def test_non_name_edit_or_unlisted_file_is_rejected(self) -> None:
        self.mutate("scripts/video-shots.mjs")
        self.assertIn(
            "scripts/video-shots.mjs",
            verify_reelbench_snapshot(self.root, self.upstream).mismatches,
        )

        unexpected = self.root / "skills" / "dreamina-video-shots" / "unexpected.txt"
        unexpected.write_text("not in the upstream snapshot\n", encoding="utf-8")
        self.assertIn(
            "unexpected.txt",
            verify_reelbench_snapshot(self.root, self.upstream).mismatches,
        )


if __name__ == "__main__":
    unittest.main()
