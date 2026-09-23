from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
import subprocess
from pathlib import Path

from scripts.bounded_process import BoundedProcessResult
from scripts.trusted_media_tools import TrustedExecutable
from tests.test_reelbench_project_service import FakeTrustedStore


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
        self.workspace_fd = os.open(self.project_root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.workspace_fd)
        self.enterContext(self.adapter.execution_workspace(self.workspace_fd))
        self.project_root = Path(f"/dev/fd/{self.workspace_fd}")
        self.source = self.project_root / "source.mp4"

    def _adapter(self):
        from scripts.reelbench_adapter import ReelBenchAdapter

        def runner(argv, **kwargs):
            self.calls.append((list(argv), kwargs))
            return BoundedProcessResult(0, '{"shots": []}\n', "")

        trust = FakeTrustedStore(self.temp.name)
        return ReelBenchAdapter(
            project_root=self.project_root,
            shots_script=self.shots_script,
            tools=trust.tools, tool_store=trust,
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

        self.assertEqual(result.argv[1:3], ["script/video-shots.mjs", "seed"])
        self.assertFalse(result.shell)
        self.assertEqual(self.calls[0][0][6:], result.argv)
        self.assertNotIn("shell", self.calls[0][1])
        self.assertIn("Reference; never a shell fragment", result.argv)

    def test_sync_plan_uses_only_the_pinned_sync_entrypoint_and_relative_paths(self) -> None:
        sync_root = Path(self.temp.name) / "sync-workspace"
        sync_root.mkdir(mode=0o700)
        sync_fd = os.open(sync_root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, sync_fd)
        sync_path = Path(f"/dev/fd/{sync_fd}")
        with self.adapter.execution_workspace(sync_fd, workflow="sync"):
            result = self.adapter.sync_plan(
                shots=sync_path / "inputs" / "shots.json",
                source=sync_path / "source.mp4",
            )

        self.assertEqual(result.argv[:3], ["tools/node", "script/video-sync.mjs", "plan"])
        self.assertEqual(result.argv[3:], ["inputs/shots.json", "--video", "source.mp4"])
        self.assertFalse(result.shell)

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
        labels = self.adapter._ENGLISH_GATE_LABELS
        output = "\n".join(f"{marks[status]} {labels[name]}" for name, status in zip(REELBENCH_VALIDATE_GATES, statuses))

        def runner(_argv, **_kwargs):
            return BoundedProcessResult(0, output, "")

        self.adapter._runner = runner
        result = self.adapter.validate(
            shots=self.project_root / "shots.json",
            track=self.project_root / "track.json",
            frames_dir=self.project_root / "frames",
        )

        self.assertEqual(result.gates[REELBENCH_VALIDATE_GATES.index("motion")]["status"], "SKIPPED")

    def test_validate_rejects_reordered_unknown_or_inconsistent_gate_output(self) -> None:
        labels = list(self.adapter._ENGLISH_GATE_LABELS.values())
        output = "\n".join(f"✅ {label}" for label in reversed(labels))

        self.adapter._runner = lambda *_args, **_kwargs: BoundedProcessResult(0, output, "")
        with self.assertRaisesRegex(Exception, "gate"):
            self.adapter.validate(
                shots=self.project_root / "shots.json", track=self.project_root / "track.json",
                frames_dir=self.project_root / "frames",
            )

    def test_fixed_helper_keeps_pinned_workspace_for_real_node_child_after_path_swap(self) -> None:
        import json
        import shutil
        from scripts.reelbench_exec_helper import HELPER_CODE
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is unavailable")
        workspace = Path(self.temp.name) / "workspace"
        workspace.mkdir(mode=0o700)
        (workspace / "source").mkdir(mode=0o700)
        (workspace / "source/digest").write_bytes(b"pinned-bytes")
        (workspace / "tools/bin").mkdir(parents=True, mode=0o700)
        shutil.copyfile(node, workspace / "tools/node")
        (workspace / "tools/node").chmod(0o500)
        probe = workspace / "tools/bin/ffprobe"
        probe.write_text('#!/bin/sh\n/bin/cat "$1" > observed.bin\n')
        probe.chmod(0o500)
        (workspace / "script").mkdir()
        script = workspace / "script/video-shots.mjs"
        script.write_text("import { execFileSync } from 'node:child_process'; execFileSync('ffprobe', ['source/digest']);\n")
        manifest = {p.relative_to(workspace).as_posix(): {"size_bytes": p.stat().st_size,
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                    for p in (workspace / "source/digest", workspace / "tools/node", probe, script)}
        fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
        moved = Path(self.temp.name) / "workspace-pinned"
        workspace.rename(moved)
        workspace.mkdir(mode=0o700)
        try:
            completed = subprocess.run(
                ["/usr/bin/python3", "-I", "-c", HELPER_CODE, str(fd), json.dumps(manifest),
                 "tools/node", "script/video-shots.mjs"],
                env={"PATH": "tools/bin"}, pass_fds=(fd,), capture_output=True, text=True, timeout=10)
        finally:
            os.close(fd)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual((moved / "observed.bin").read_bytes(), b"pinned-bytes")
        self.assertEqual(list(workspace.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
