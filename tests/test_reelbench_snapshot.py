"""Behavior tests for the pinned ReelBench Skill snapshot verifier."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verify_reelbench_snapshot import (  # noqa: E402
    PINNED_REVISION,
    SOURCE_URL,
    main,
    verify_reelbench_snapshot,
)
import scripts.verify_reelbench_snapshot as snapshot  # noqa: E402


ALIASES = {
    "video-shots": "dreamina-video-shots",
    "video-sync": "dreamina-video-sync",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_blob(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


class ReelBenchSnapshotTests(unittest.TestCase):
    """The lock accepts only declared directory/frontmatter alias deltas."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # The verifier correctly rejects ancestor symlinks, while macOS places
        # temporary directories under the lexical /var -> /private/var link.
        self.base = Path(os.path.realpath(self.tmp.name))
        self.root = self.base / "plugin"
        self.upstream = self.base / "upstream"
        self.root.mkdir()
        self._write_independent_fixture()

    def _write_independent_fixture(self) -> None:
        """Write literal source bytes and a hand-derived lock, never copying artifacts."""
        source_files = {
            "video-shots/SKILL.md": b"---\nname: video-shots\ndescription: source shots\n---\n",
            "video-shots/scripts/video-shots.mjs": b"export const shots = true;\n",
            "video-sync/SKILL.md": b"---\nname: video-sync\ndescription: source sync\n---\n",
            "video-sync/scripts/video-sync.mjs": b"export const sync = true;\n",
        }
        entries: dict[str, dict[str, str]] = {}
        for key, source in source_files.items():
            upstream_name, relative = key.split("/", 1)
            packaged_name = ALIASES[upstream_name]
            upstream_file = self.upstream / "skills" / key
            upstream_file.parent.mkdir(parents=True, exist_ok=True)
            upstream_file.write_bytes(source)
            packaged = (
                source.replace(
                    f"name: {upstream_name}\n".encode(),
                    f"name: {packaged_name}\n".encode(),
                    1,
                )
                if relative == "SKILL.md"
                else source
            )
            packaged_file = self.root / "skills" / packaged_name / relative
            packaged_file.parent.mkdir(parents=True, exist_ok=True)
            packaged_file.write_bytes(packaged)
            entries[key] = {
                "git_blob": _git_blob(source),
                "upstream_sha256": _sha256(source),
                "packaged_sha256": _sha256(packaged),
            }
        self.lock = {
            "schema_version": "1.0.0",
            "source": SOURCE_URL,
            "revision": PINNED_REVISION,
            "aliases": ALIASES,
            "files": entries,
        }
        self._write_lock()

    def _write_lock(self) -> None:
        target = self.root / "upstream" / "reelbench.lock.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.lock, indent=2) + "\n", encoding="utf-8")

    def _report(self):
        return verify_reelbench_snapshot(self.root, upstream_root=self.upstream)

    def test_packaged_skill_aliases_are_the_only_allowed_delta(self) -> None:
        report = self._report()
        self.assertEqual(report.revision, PINNED_REVISION)
        self.assertEqual(
            report.packaged_names,
            ["dreamina-video-shots", "dreamina-video-sync"],
        )
        self.assertEqual(report.mismatches, [])
        self.assertEqual(report.source_status, "PARTIAL")

    def test_non_name_edit_missing_or_unlisted_file_is_rejected_with_skill_relative_path(self) -> None:
        edited = self.root / "skills/dreamina-video-shots/scripts/video-shots.mjs"
        edited.write_bytes(edited.read_bytes() + b"local mutation\n")
        self.assertIn("video-shots/scripts/video-shots.mjs", self._report().mismatches)

        edited.unlink()
        self.assertIn("video-shots/scripts/video-shots.mjs", self._report().mismatches)

        unexpected = self.root / "skills/dreamina-video-shots/unexpected.txt"
        unexpected.write_text("not in the upstream snapshot\n", encoding="utf-8")
        self.assertIn("video-shots/unexpected.txt", self._report().mismatches)

    def test_packaged_file_and_directory_symlinks_are_rejected_without_following_them(self) -> None:
        file_path = self.root / "skills/dreamina-video-shots/scripts/video-shots.mjs"
        file_path.unlink()
        file_path.symlink_to("missing-target.mjs")
        self.assertIn("video-shots/scripts/video-shots.mjs", self._report().mismatches)

        shutil.rmtree(self.root / "skills/dreamina-video-sync/scripts")
        scripts_link = self.root / "skills/dreamina-video-sync/scripts"
        scripts_link.symlink_to(self.root / "skills/dreamina-video-shots/scripts", target_is_directory=True)
        self.assertIn("video-sync/scripts", self._report().mismatches)

    def test_skills_parent_symlink_is_rejected_without_descending_into_it(self) -> None:
        skills = self.root / "skills"
        target = self.root / "skills-target"
        skills.rename(target)
        skills.symlink_to(target, target_is_directory=True)
        self.assertIn("skills", self._report().mismatches)

    def test_packaged_special_file_and_lock_symlink_are_rejected(self) -> None:
        fifo = self.root / "skills/dreamina-video-shots/render.pipe"
        os.mkfifo(fifo)
        self.assertIn("video-shots/render.pipe", self._report().mismatches)

        lock = self.root / "upstream/reelbench.lock.json"
        lock_bytes = lock.read_bytes()
        lock.unlink()
        target = self.root / "lock-target.json"
        target.write_bytes(lock_bytes)
        lock.symlink_to(target)
        self.assertIn("upstream/reelbench.lock.json", self._report().mismatches)

    def test_tampered_lock_schema_and_path_traversal_are_rejected_deterministically(self) -> None:
        self.lock["source"] = "https://example.invalid/not-reelbench.git"
        self._write_lock()
        self.assertEqual(self._report().mismatches, ["lock:source"])

        self._write_independent_fixture()
        self.lock["files"]["video-shots/../escape.mjs"] = self.lock["files"].pop(
            "video-shots/scripts/video-shots.mjs"
        )
        self._write_lock()
        self.assertEqual(self._report().mismatches, ["lock:files/video-shots/../escape.mjs"])

    def test_all_closed_lock_fields_and_duplicate_keys_are_rejected(self) -> None:
        for field, invalid_value, expected in (
            ("schema_version", "2.0.0", "lock:schema_version"),
            ("revision", "a" * 40, "lock:revision"),
            ("aliases", {}, "lock:aliases"),
        ):
            self._write_independent_fixture()
            self.lock[field] = invalid_value
            self._write_lock()
            self.assertEqual(self._report().mismatches, [expected])

        self._write_independent_fixture()
        entry = self.lock["files"]["video-shots/SKILL.md"]
        entry["git_blob"] = "g" * 40
        self._write_lock()
        self.assertEqual(self._report().mismatches, ["lock:files/video-shots/SKILL.md"])

        lock_path = self.root / "upstream/reelbench.lock.json"
        lock_path.write_text(
            "{" + '"schema_version":"1.0.0",' * 2 + '"source":"x"}',
            encoding="utf-8",
        )
        self.assertEqual(self._report().mismatches, ["lock:duplicate-key:schema_version"])

    def test_exact_frontmatter_transformation_rejects_whitespace_or_name_edits(self) -> None:
        source = self.upstream / "skills/video-shots/SKILL.md"
        source.write_bytes(source.read_bytes().replace(b"name: video-shots\n", b"name: video-shots \n"))
        self.assertEqual(self._report().mismatches, ["video-shots/SKILL.md"])

    def _init_git_repo(self, origin: str) -> None:
        subprocess.run(["git", "init", str(self.upstream)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.upstream), "remote", "add", "origin", origin], check=True)

    def test_wrong_origin_and_missing_pinned_revision_are_unverifiable(self) -> None:
        self._init_git_repo("https://example.invalid/reelbench-skills.git")
        wrong_origin = self._report()
        self.assertEqual(wrong_origin.source_status, "UNVERIFIABLE")
        self.assertEqual(wrong_origin.mismatches, ["upstream.origin"])

        shutil.rmtree(self.upstream / ".git")
        self._init_git_repo(SOURCE_URL)
        missing_revision = self._report()
        self.assertEqual(missing_revision.source_status, "UNVERIFIABLE")
        self.assertEqual(missing_revision.mismatches, ["upstream.revision"])

    def test_cli_emits_json_and_strict_mode_rejects_partial_provenance(self) -> None:
        command = [
            sys.executable,
            str(ROOT / "scripts/verify_reelbench_snapshot.py"),
            "--plugin-root",
            str(self.root),
        ]
        partial = subprocess.run(command, check=False, capture_output=True, text=True)
        self.assertEqual(partial.returncode, 1, partial.stderr)
        self.assertEqual(json.loads(partial.stdout)["source_status"], "PARTIAL")

        allowed = subprocess.run(command + ["--allow-partial"], check=False, capture_output=True, text=True)
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(json.loads(allowed.stdout)["source_status"], "PARTIAL")

        strict = subprocess.run(command + ["--strict-pinned-source"], check=False, capture_output=True, text=True)
        self.assertEqual(strict.returncode, 1)
        self.assertEqual(json.loads(strict.stdout)["source_status"], "PARTIAL")
        self.assertEqual(main(["--plugin-root", str(self.root), "--strict"]), 1)

        ambiguous = subprocess.run(
            command + ["--allow-partial", "--strict-pinned-source"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(ambiguous.returncode, 2)
        self.assertEqual(json.loads(ambiguous.stdout)["error"], "--allow-partial conflicts with strict mode")

        self.lock["source"] = "invalid"
        self._write_lock()
        invalid = subprocess.run(command, check=False, capture_output=True, text=True)
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(json.loads(invalid.stdout)["mismatches"], ["lock:source"])

    def test_cli_rejects_raw_plugin_and_upstream_root_symlinks_in_all_modes(self) -> None:
        plugin_link = self.base / "plugin-link"
        plugin_link.symlink_to(self.root, target_is_directory=True)
        command = [
            sys.executable,
            str(ROOT / "scripts/verify_reelbench_snapshot.py"),
            "--plugin-root",
            str(plugin_link),
        ]
        for mode in ([], ["--allow-partial"], ["--strict-pinned-source"]):
            result = subprocess.run(command + mode, check=False, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["mismatches"], ["plugin_root"])

        upstream_link = self.base / "upstream-link"
        upstream_link.symlink_to(self.upstream, target_is_directory=True)
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/verify_reelbench_snapshot.py"),
                "--plugin-root",
                str(self.root),
                "--upstream-root",
                str(upstream_link),
                "--allow-partial",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["mismatches"], ["upstream_root"])

        dangling = self.base / "dangling-plugin"
        dangling.symlink_to(self.base / "missing-plugin", target_is_directory=True)
        dangling_result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/verify_reelbench_snapshot.py"),
                "--plugin-root",
                str(dangling),
                "--allow-partial",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(dangling_result.returncode, 2)
        self.assertEqual(json.loads(dangling_result.stdout)["mismatches"], ["plugin_root"])

    def _replace_after_open(self, target: Path, replacement: Path, opener_name: str):
        """Replace one path only after its original directory FD was acquired."""
        original_opener = getattr(snapshot, opener_name)
        replaced = False

        def open_then_replace(*args, **kwargs):
            nonlocal replaced
            handle = original_opener(*args, **kwargs)
            opened_path = Path(args[0]) if opener_name == "_open_directory_path" else None
            is_target = (
                opened_path == target
                if opener_name == "_open_directory_path"
                else args[2] == "video-shots" and target.name == "dreamina-video-shots"
            )
            if not replaced and is_target:
                replaced = True
                old = target.with_name(target.name + "-old")
                target.rename(old)
                replacement.rename(target)
            return handle

        return mock.patch.object(snapshot, opener_name, side_effect=open_then_replace)

    def test_post_open_path_replacements_fail_closed(self) -> None:
        replacement = self.base / "plugin-replacement"
        shutil.copytree(self.root, replacement)
        with self._replace_after_open(self.root, replacement, "_open_directory_path"):
            report = verify_reelbench_snapshot(self.root, self.upstream)
        self.assertIn("plugin_root", report.mismatches)

        self._write_independent_fixture()
        skill = self.root / "skills/dreamina-video-shots"
        skill_replacement = self.base / "skill-replacement"
        shutil.copytree(skill, skill_replacement)
        with self._replace_after_open(skill, skill_replacement, "_open_directory_at"):
            report = verify_reelbench_snapshot(self.root, self.upstream)
        self.assertTrue(report.mismatches)

        self._write_independent_fixture()
        upstream_replacement = self.base / "upstream-replacement"
        shutil.copytree(self.upstream, upstream_replacement)
        with self._replace_after_open(self.upstream, upstream_replacement, "_open_directory_path"):
            exit_code = main(
                [
                    "--plugin-root",
                    str(self.root),
                    "--upstream-root",
                    str(self.upstream),
                    "--strict-pinned-source",
                ]
            )
        self.assertEqual(exit_code, 2)

    def test_missing_o_nofollow_fails_closed_without_attribute_error(self) -> None:
        with mock.patch.object(snapshot.os, "O_NOFOLLOW", None):
            report = verify_reelbench_snapshot(self.root, self.upstream)
        self.assertEqual(report.source_status, "UNVERIFIABLE")
        self.assertEqual(report.mismatches, ["runtime.O_NOFOLLOW"])

    def test_real_packaged_lock_is_consistent_but_explicitly_partial_without_source(self) -> None:
        report = verify_reelbench_snapshot(ROOT)
        self.assertEqual(report.mismatches, [])
        self.assertEqual(report.source_status, "PARTIAL")


if __name__ == "__main__":
    unittest.main()
