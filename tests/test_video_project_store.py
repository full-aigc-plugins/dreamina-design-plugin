from __future__ import annotations

import json
import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.json_contracts import ContractValidationError, canonical_fingerprint
from scripts.video_project_store import (
    ProjectStateConflictError,
    VersionCommitIndeterminateError,
    VersionReconciliationError,
    VideoProjectStore,
)


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

    def test_create_accepts_only_authoritative_creative_and_audio_tokens(self) -> None:
        for creative_mode in ("original_redesign", "authorized_replication"):
            for audio_policy in (
                "full_redesign",
                "preserve_authorized_audio",
                "subtitles_only",
                "silent",
            ):
                with self.subTest(creative_mode=creative_mode, audio_policy=audio_policy):
                    created = self.store.create(
                        title="exact enums",
                        creative_mode=creative_mode,
                        audio_policy=audio_policy,
                    )
                    self.assertEqual(created["creative_mode"], creative_mode)
                    self.assertEqual(created["audio_policy"], audio_policy)

        for creative_mode, audio_policy in (
            ("reference_faithful", "silent"),
            ("original_redesign", "replace"),
            ("original_redesign", "preserve"),
        ):
            with self.subTest(rejected=(creative_mode, audio_policy)):
                with self.assertRaises(ContractValidationError):
                    self.store.create(
                        title="obsolete enums",
                        creative_mode=creative_mode,
                        audio_policy=audio_policy,
                    )

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
            title="demo",
            creative_mode="authorized_replication",
            audio_policy="preserve_authorized_audio",
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

    def test_post_replace_durability_failure_reports_visible_version_as_indeterminate(self) -> None:
        """Catches treating a post-replace fsync failure as a generic safe-to-retry write."""
        project = self.store.create(
            title="indeterminate", creative_mode="original_redesign", audio_policy="silent"
        )
        payload = {"schema_version": "1.0", "value": "committed"}
        calls = 0
        real_fsync = os.fsync

        def fail_directory_fsync(descriptor: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected directory fsync failure")
            real_fsync(descriptor)

        with patch("scripts.video_project_store.os.fsync", side_effect=fail_directory_fsync):
            with self.assertRaises(VersionCommitIndeterminateError) as raised:
                self.store.write_version(project["project_id"], "analysis", payload)

        exact_path = self.root.resolve() / project["project_id"] / "analysis" / "v001.json"
        expected_document = {"schema_version": "1.0", "value": "committed", "version": "v001"}
        self.assertEqual(raised.exception.project_id, project["project_id"])
        self.assertEqual(raised.exception.family, "analysis")
        self.assertEqual(raised.exception.version, "v001")
        self.assertEqual(raised.exception.path, exact_path)
        self.assertEqual(raised.exception.payload_fingerprint, canonical_fingerprint(expected_document))
        self.assertEqual(json.loads(exact_path.read_text(encoding="utf-8")), expected_document)
        self.assertEqual([path.name for path in exact_path.parent.glob("v*.json")], ["v001.json"])

    def test_requested_version_fails_closed_instead_of_replacing_an_existing_receipt(self) -> None:
        """Catches version publication overwriting a path injected before commit."""
        project = self.store.create(
            title="no-replace", creative_mode="original_redesign", audio_policy="silent"
        )
        original = self.store.write_version(
            project["project_id"], "analysis", {"schema_version": "1.0", "value": "original"}
        )

        with self.assertRaises(FileExistsError):
            self.store.write_version(
                project["project_id"], "analysis", {"schema_version": "1.0", "value": "replacement"},
                version="v001",
            )

        self.assertEqual(
            json.loads((self.root / project["project_id"] / "analysis" / "v001.json").read_text()), original
        )

    def test_reconcile_version_returns_only_exact_private_valid_committed_document(self) -> None:
        """Catches reconciliation allocating a replacement or accepting the wrong receipt."""
        project = self.store.create(
            title="reconcile", creative_mode="original_redesign", audio_policy="silent"
        )
        document = self.store.write_version(
            project["project_id"], "analysis", {"schema_version": "1.0"}
        )
        fingerprint = canonical_fingerprint(document)
        lock_path = self.root / project["project_id"] / ".lock"
        lock_path.unlink()

        reconciled = self.store.reconcile_version(
            project["project_id"], "analysis", "v001", fingerprint, None
        )

        self.assertEqual(reconciled, {"schema_version": "1.0", "version": "v001"})
        self.assertEqual(
            [path.name for path in (self.root / project["project_id"] / "analysis").glob("v*.json")],
            ["v001.json"],
        )
        self.assertFalse(lock_path.exists())

    def test_reconcile_version_blocks_missing_mismatch_corruption_symlink_and_public_mode(self) -> None:
        """Catches reconciliation trusting an absent, altered, redirected, or exposed receipt."""
        project = self.store.create(
            title="blocked", creative_mode="original_redesign", audio_policy="silent"
        )
        document = self.store.write_version(
            project["project_id"], "analysis", {"schema_version": "1.0"}
        )
        family_root = self.root / project["project_id"] / "analysis"
        target = family_root / "v001.json"
        fingerprint = canonical_fingerprint(document)
        with self.assertRaises(VersionReconciliationError):
            self.store.reconcile_version(project["project_id"], "analysis", "v002", fingerprint, None)
        with self.assertRaises(VersionReconciliationError):
            self.store.reconcile_version(project["project_id"], "analysis", "v001", "0" * 64, None)
        target.write_text("not json", encoding="utf-8")
        with self.assertRaises(VersionReconciliationError):
            self.store.reconcile_version(project["project_id"], "analysis", "v001", fingerprint, None)
        target.unlink()
        wrong_version = {"schema_version": "1.0", "version": "v009"}
        target.write_text(json.dumps(wrong_version), encoding="utf-8")
        target.chmod(0o600)
        with self.assertRaises(VersionReconciliationError):
            self.store.reconcile_version(
                project["project_id"], "analysis", "v001", canonical_fingerprint(wrong_version), None
            )
        invalid_schema = {"schema_version": "1.0", "version": "v001"}
        target.write_text(json.dumps(invalid_schema), encoding="utf-8")
        with self.assertRaises(VersionReconciliationError):
            self.store.reconcile_version(
                project["project_id"], "analysis", "v001",
                canonical_fingerprint(invalid_schema), "shot_analysis.schema.json",
            )
        target.unlink()
        outside = Path(self.tmp.name) / "outside.json"
        outside.write_text(json.dumps(document), encoding="utf-8")
        target.symlink_to(outside)
        with self.assertRaises(VersionReconciliationError):
            self.store.reconcile_version(project["project_id"], "analysis", "v001", fingerprint, None)
        target.unlink()
        target.write_text(json.dumps(document), encoding="utf-8")
        target.chmod(0o644)
        with self.assertRaises(VersionReconciliationError):
            self.store.reconcile_version(project["project_id"], "analysis", "v001", fingerprint, None)

    def test_reconcile_version_rejects_project_or_family_directory_replacement(self) -> None:
        """Catches pathname traversal escaping the directories verified before open."""
        for replaced_level in ("project", "family"):
            with self.subTest(replaced_level=replaced_level):
                isolated_root = Path(self.tmp.name) / f"projects-{replaced_level}"
                store = VideoProjectStore(isolated_root)
                project = store.create(
                    title="directory race",
                    creative_mode="original_redesign",
                    audio_policy="silent",
                )
                document = store.write_version(
                    project["project_id"], "analysis", {"schema_version": "1.0"}
                )
                project_root = isolated_root / project["project_id"]
                family_root = project_root / "analysis"
                target = family_root / "v001.json"
                fingerprint = canonical_fingerprint(document)
                real_open = os.open
                replaced = False

                def replace_before_target_open(path, flags, mode=0o777, *, dir_fd=None):
                    nonlocal replaced
                    if not replaced and Path(path).name == "v001.json":
                        if replaced_level == "family":
                            old = project_root / "analysis-old"
                            os.rename(family_root, old)
                            family_root.mkdir(mode=0o700)
                            replacement = family_root / "v001.json"
                        else:
                            old = isolated_root / f"{project['project_id']}-old"
                            os.rename(project_root, old)
                            project_root.mkdir(mode=0o700)
                            replacement_family = project_root / "analysis"
                            replacement_family.mkdir(mode=0o700)
                            replacement = replacement_family / "v001.json"
                        replacement.write_text(json.dumps(document), encoding="utf-8")
                        replacement.chmod(0o600)
                        replaced = True
                    if dir_fd is None:
                        return real_open(path, flags, mode)
                    return real_open(path, flags, mode, dir_fd=dir_fd)

                with patch("scripts.video_project_store.os.open", side_effect=replace_before_target_open):
                    with self.assertRaises(VersionReconciliationError):
                        store.reconcile_version(
                            project["project_id"], "analysis", "v001", fingerprint, None
                        )
                self.assertTrue(replaced)

    def test_reconcile_version_fails_closed_when_path_is_replaced_after_read(self) -> None:
        """Catches returning stale bytes after the exact version path is atomically replaced."""
        project = self.store.create(
            title="replace-race", creative_mode="original_redesign", audio_policy="silent"
        )
        document = self.store.write_version(
            project["project_id"], "analysis", {"schema_version": "1.0", "value": "original"}
        )
        family_root = self.root / project["project_id"] / "analysis"
        target = family_root / "v001.json"
        replacement = family_root / ".replacement.json"
        replacement_document = {
            "schema_version": "1.0", "value": "replaced-after-read", "version": "v001"
        }
        real_json_load = json.load

        def replace_after_read(handle):
            loaded = real_json_load(handle)
            replacement.write_text(json.dumps(replacement_document), encoding="utf-8")
            replacement.chmod(0o600)
            os.replace(replacement, target)
            return loaded

        with patch("scripts.video_project_store.json.load", side_effect=replace_after_read):
            with self.assertRaises(VersionReconciliationError):
                self.store.reconcile_version(
                    project["project_id"], "analysis", "v001",
                    canonical_fingerprint(document), None,
                )

        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), replacement_document)
        self.assertEqual([path.name for path in family_root.glob("v*.json")], ["v001.json"])


if __name__ == "__main__":
    unittest.main()
