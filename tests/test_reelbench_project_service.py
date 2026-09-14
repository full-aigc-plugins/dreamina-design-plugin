from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.bounded_process import BoundedProcessResult
from scripts.trusted_media_tools import TrustedExecutable
from scripts.json_contracts import canonical_fingerprint
from scripts.video_project_store import VersionCommitIndeterminateError, VideoProjectStore


class ReelBenchProjectServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = VideoProjectStore(Path(self.temp.name) / "projects")
        project = self.store.create(title="reference", creative_mode="original_redesign", audio_policy="silent")
        self.project_id = project["project_id"]
        self.source = self.store.project_root(self.project_id) / "source" / "source.mp4"
        self.source.parent.mkdir(mode=0o700)
        self.source.write_bytes(b"private source bytes")
        self.source.chmod(0o400)
        self.source_receipt = self.store.write_version(
            self.project_id,
            "source_receipt",
            {
                "schema_version": "1.0", "project_id": self.project_id,
                "source_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
                "size_bytes": self.source.stat().st_size, "mime_type": "video/mp4",
                "video_codec": "h264", "width": 640, "height": 360, "fps": 25.0,
                "duration_seconds": 8.0, "audio_streams": [], "approved_roots_digest": "a" * 64,
                "staged_path": str(self.source), "intake_at": "2026-09-15T00:00:00Z",
            },
            schema_name="source_receipt.schema.json",
        )
        self.service = self._service()

    def _service(self):
        from scripts.reelbench_adapter import ReelBenchAdapter
        from scripts.reelbench_project_service import ReelBenchProjectService

        script = Path(__file__).resolve().parents[1] / "skills" / "dreamina-video-shots" / "scripts" / "video-shots.mjs"
        tools = {
            kind: TrustedExecutable(kind, f"/trusted/{kind}", os.getuid(), 0o500, 1, 2, 1, hashlib.sha256(kind.encode()).hexdigest())
            for kind in ("node", "ffmpeg", "ffprobe")
        }

        def runner(argv, **_kwargs):
            if argv[2] == "seed":
                track = Path(argv[argv.index("--track") + 1])
                track.write_text('{"hz": 5, "values": []}\n', encoding="utf-8")
                return BoundedProcessResult(0, '{"meta":{"durationSeconds":8},"shots":[]}\n', "")
            if argv[2] == "frames":
                target = Path(argv[argv.index("--dir") + 1])
                target.mkdir()
                (target / "S01a.jpg").write_bytes(b"frame-a")
                (target / "S01b.jpg").write_bytes(b"frame-b")
                return BoundedProcessResult(0, "", "")
            if argv[2] == "sheet":
                target = Path(argv[argv.index("--out") + 1])
                target.mkdir(exist_ok=True)
                pick = argv[argv.index("--pick") + 1]
                (target / f"sheet-{pick}01.jpg").write_bytes(b"sheet")
                return BoundedProcessResult(0, "", "")
            if argv[2] == "validate":
                marks = ["✅"] * 15
                marks[11] = "⊘"
                labels = [
                    "Timeline is continuous", "Durations add up", "Shot numbering", "Shot size vocabulary",
                    "Category vocabulary", "Camera vocabulary", "Transition vocabulary", "Frame description is checkable",
                    "No duplicate descriptions", "Subjects reconcile with cast", "Categories carry evidence",
                    "Camera vs. measured motion", "Boundaries come from detection", "Keyframes present",
                    "Rhythm annotation is checkable",
                ]
                return BoundedProcessResult(0, "\n".join(f"{mark} {label}" for mark, label in zip(marks, labels)), "")
            if argv[2] == "render":
                return BoundedProcessResult(0, "# bounded report\n", "")
            return BoundedProcessResult(0, "", "")

        adapter = ReelBenchAdapter(
            project_root=self.store.project_root(self.project_id), shots_script=script,
            tools=tools, runner=runner,
        )
        return ReelBenchProjectService(self.store, adapter)

    def test_repeated_action_creates_new_version_without_overwriting(self) -> None:
        first = self.service.run(
            self.project_id, action="seed", expected_parent=None,
            source_receipt_version=self.source_receipt["version"], threshold=0.3, title="Reference",
        )
        second = self.service.run(
            self.project_id, action="seed", expected_parent=first["version"],
            source_receipt_version=self.source_receipt["version"], threshold=0.3, title="Reference",
        )

        self.assertEqual((first["version"], second["version"]), ("v001", "v002"))
        self.assertEqual(first["parent_version"], None)
        self.assertEqual(second["parent_version"], "v001")
        self.assertNotEqual(first["evidence_fingerprint"], second["evidence_fingerprint"])

    def test_expected_parent_and_source_identity_are_strict(self) -> None:
        with self.assertRaises(ValueError):
            self.service.run(
                self.project_id, action="seed", expected_parent="v001",
                source_receipt_version=self.source_receipt["version"], threshold=0.3, title="Reference",
            )
        self.source.chmod(0o600)
        self.source.write_bytes(b"source replacement")
        self.source.chmod(0o400)
        with self.assertRaises(ValueError):
            self.service.run(
                self.project_id, action="seed", expected_parent=None,
                source_receipt_version=self.source_receipt["version"], threshold=0.3, title="Reference",
            )

    def test_same_bytes_source_replacement_during_execution_fails_closed(self) -> None:
        def runner(argv, **_kwargs):
            track = Path(argv[argv.index("--track") + 1])
            track.write_text('{"hz": 5, "values": []}\n', encoding="utf-8")
            replacement = self.source.with_name("source-replacement.mp4")
            replacement.write_bytes(self.source.read_bytes())
            replacement.chmod(0o400)
            os.replace(replacement, self.source)
            return BoundedProcessResult(0, '{"meta":{"durationSeconds":8},"shots":[]}\n', "")

        self.service._adapter._runner = runner
        with self.assertRaisesRegex(ValueError, "identity changed"):
            self.service.run(
                self.project_id, action="seed", expected_parent=None,
                source_receipt_version=self.source_receipt["version"], threshold=0.3, title="Reference",
            )

    def test_indeterminate_commit_reconciles_only_the_exact_published_receipt(self) -> None:
        real_write = self.store.write_version

        def publish_then_report_indeterminate(*args, **kwargs):
            document = real_write(*args, **kwargs)
            raise VersionCommitIndeterminateError(
                project_id=self.project_id, family="reelbench_evidence", version=document["version"],
                path=self.store.project_root(self.project_id) / "reelbench_evidence" / f"{document['version']}.json",
                payload_fingerprint=canonical_fingerprint(document),
            )

        with patch.object(self.store, "write_version", side_effect=publish_then_report_indeterminate):
            recovered = self.service.run(
                self.project_id, action="seed", expected_parent=None,
                source_receipt_version=self.source_receipt["version"], threshold=0.3, title="Reference",
            )

        self.assertEqual(recovered["version"], "v001")
        self.assertEqual(recovered["evidence_fingerprint"], canonical_fingerprint({key: value for key, value in recovered.items() if key != "evidence_fingerprint"}))

    def test_every_action_publishes_an_immutable_version_and_keeps_skipped_gate(self) -> None:
        first = self.service.run(
            self.project_id, action="seed", expected_parent=None,
            source_receipt_version=self.source_receipt["version"], threshold=0.3, title="Reference",
        )
        second = self.service.run(
            self.project_id, action="evidence", expected_parent=first["version"],
            source_receipt_version=self.source_receipt["version"],
        )
        third = self.service.run(
            self.project_id, action="validate", expected_parent=second["version"],
            source_receipt_version=self.source_receipt["version"],
        )
        fourth = self.service.run(
            self.project_id, action="render", expected_parent=third["version"],
            source_receipt_version=self.source_receipt["version"], mode="md",
        )

        self.assertEqual((second["version"], third["version"], fourth["version"]), ("v002", "v003", "v004"))
        self.assertEqual(third["gates"][11]["status"], "SKIPPED")
        self.assertEqual(fourth["artifacts"][0]["path"], "reelbench/v004/report.md")

    def test_render_requires_immediate_validated_parent(self) -> None:
        seed = self.service.run(self.project_id, action="seed", expected_parent=None,
                                source_receipt_version=self.source_receipt["version"], threshold=0.3, title="Reference")
        evidence = self.service.run(self.project_id, action="evidence", expected_parent=seed["version"],
                                    source_receipt_version=self.source_receipt["version"])
        with self.assertRaisesRegex(ValueError, "validated"):
            self.service.run(self.project_id, action="render", expected_parent=evidence["version"],
                             source_receipt_version=self.source_receipt["version"], mode="md")


if __name__ == "__main__":
    unittest.main()
