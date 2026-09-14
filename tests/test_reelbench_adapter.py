from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from scripts.bounded_process import BoundedProcessResult
from scripts.trusted_media_tools import TrustedExecutable


class ReelBenchAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project_root = Path(self.temp.name) / "project"
        self.project_root.mkdir(mode=0o700)
        self.source = self.project_root / "source.mp4"
        self.source.write_bytes(b"source")
        self.shots_script = Path(__file__).resolve().parents[1] / "skills" / "dreamina-video-shots" / "scripts" / "video-shots.mjs"
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.adapter = self._adapter()

    def _adapter(self):
        from scripts.reelbench_adapter import ReelBenchAdapter

        def runner(argv, **kwargs):
            self.calls.append((list(argv), kwargs))
            return BoundedProcessResult(0, '{"shots": []}\n', "")

        return ReelBenchAdapter(
            project_root=self.project_root,
            shots_script=self.shots_script,
            tools={kind: self._tool(kind) for kind in ("node", "ffmpeg", "ffprobe")},
            runner=runner,
        )

    @staticmethod
    def _tool(kind: str) -> TrustedExecutable:
        digest = hashlib.sha256(kind.encode("utf-8")).hexdigest()
        return TrustedExecutable(kind, f"/trusted/{kind}", os.getuid(), 0o500, 1, 2, 1, digest)

    def test_seed_uses_pinned_script_and_fixed_argv_without_shell(self) -> None:
        result = self.adapter.seed(
            source=self.source,
            shots=self.project_root / "reelbench" / "v001" / "shots.json",
            track=self.project_root / "reelbench" / "v001" / "track.json",
            threshold=0.3,
            title="Reference; never a shell fragment",
        )

        self.assertEqual(result.argv[1:3], [str(self.shots_script), "seed"])
        self.assertFalse(result.shell)
        self.assertEqual(self.calls[0][0], result.argv)
        self.assertNotIn("shell", self.calls[0][1])
        self.assertIn("Reference; never a shell fragment", result.argv)

    def test_rejects_paths_outside_the_project_and_invalid_threshold(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.seed(
                source=self.source,
                shots=Path(self.temp.name) / "outside.json",
                track=self.project_root / "track.json",
                threshold=0.3,
                title="Reference",
            )
        with self.assertRaises(ValueError):
            self.adapter.seed(
                source=self.source,
                shots=self.project_root / "shots.json",
                track=self.project_root / "track.json",
                threshold=1.0,
                title="Reference",
            )

    def test_validate_preserves_skipped_gate_status(self) -> None:
        from scripts.reelbench_contracts import REELBENCH_VALIDATE_GATES

        statuses = ["PASS"] * len(REELBENCH_VALIDATE_GATES)
        statuses[REELBENCH_VALIDATE_GATES.index("motion")] = "SKIPPED"
        marks = {"PASS": "✅", "FAIL": "❌", "SKIPPED": "⊘"}
        output = "\n".join(f"{marks[status]} gate-{index}" for index, status in enumerate(statuses))

        def runner(_argv, **_kwargs):
            return BoundedProcessResult(0, output, "")

        self.adapter._runner = runner
        result = self.adapter.validate(
            shots=self.project_root / "shots.json",
            track=self.project_root / "track.json",
            frames_dir=self.project_root / "frames",
        )

        self.assertEqual(result.gates[REELBENCH_VALIDATE_GATES.index("motion")]["status"], "SKIPPED")


if __name__ == "__main__":
    unittest.main()
