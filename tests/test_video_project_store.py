from __future__ import annotations

import json
import multiprocessing
import tempfile
import unittest
from pathlib import Path

from scripts.video_project_store import ProjectStateConflictError, VideoProjectStore


def _write_version(root: str, project_id: str, queue: multiprocessing.Queue) -> None:
    store = VideoProjectStore(Path(root))
    queue.put(store.write_version(project_id, "analysis", {"schema_version": "1.0"})["version"])


class VideoProjectStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "projects"
        self.store = VideoProjectStore(self.root)

    def test_create_uses_private_permissions_and_valid_contract(self) -> None:
        project = self.store.create(
            title="demo", creative_mode="original_redesign", audio_policy="silent"
        )
        project_root = self.root / project["project_id"]
        self.assertRegex(project["project_id"], r"^vp_[a-f0-9]{24}$")
        self.assertEqual(project["state"], "created")
        self.assertEqual(project_root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((project_root / "project.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.get(project["project_id"]), project)

    def test_transition_requires_expected_current_state(self) -> None:
        project = self.store.create(
            title="demo", creative_mode="original_redesign", audio_policy="silent"
        )
        with self.assertRaises(ProjectStateConflictError):
            self.store.transition(
                project["project_id"],
                expected="designing",
                next_state="quoted",
                evidence={"fingerprint": "a" * 64},
            )
        self.assertEqual(self.store.get(project["project_id"])["state"], "created")

    def test_transition_rejects_forbidden_edge_and_records_evidence(self) -> None:
        project = self.store.create(
            title="demo", creative_mode="reference_faithful", audio_policy="preserve"
        )
        with self.assertRaises(ProjectStateConflictError):
            self.store.transition(
                project["project_id"], expected="created", next_state="completed", evidence={}
            )
        changed = self.store.transition(
            project["project_id"],
            expected="created",
            next_state="analyzing",
            evidence={"fingerprint": "a" * 64},
        )
        self.assertEqual(changed["history"][-1]["evidence"], {"fingerprint": "a" * 64})

    def test_concurrent_version_writes_allocate_unique_monotonic_versions(self) -> None:
        project = self.store.create(
            title="demo", creative_mode="original_redesign", audio_policy="silent"
        )
        queue: multiprocessing.Queue = multiprocessing.Queue()
        processes = [
            multiprocessing.Process(
                target=_write_version, args=(str(self.root), project["project_id"], queue)
            )
            for _ in range(2)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(10)
            self.assertEqual(process.exitcode, 0)
        versions = [queue.get(timeout=2), queue.get(timeout=2)]
        self.assertEqual(sorted(versions), ["v001", "v002"])
        payloads = sorted((self.root / project["project_id"] / "analysis").glob("v*.json"))
        self.assertEqual(
            [json.loads(path.read_text())["version"] for path in payloads], ["v001", "v002"]
        )

    def test_list_projects_is_newest_first_and_bounded(self) -> None:
        first = self.store.create(title="one", creative_mode="original_redesign", audio_policy="silent")
        second = self.store.create(title="two", creative_mode="original_redesign", audio_policy="silent")
        listed = self.store.list_projects(1)
        self.assertEqual(len(listed), 1)
        self.assertIn(listed[0]["project_id"], {first["project_id"], second["project_id"]})
        with self.assertRaises(ValueError):
            self.store.list_projects(0)


if __name__ == "__main__":
    unittest.main()
