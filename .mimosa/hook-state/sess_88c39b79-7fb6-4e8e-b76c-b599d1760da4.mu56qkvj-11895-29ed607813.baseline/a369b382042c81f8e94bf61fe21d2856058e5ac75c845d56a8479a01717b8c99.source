"""Tests for scripts/capability_probe.py.

The capability probe MUST:
  - discover companions only through stable installed-plugin manifests or an
    explicit adapter executable on the search roots (never by crawling user
    directories);
  - reject duplicates and stale installations;
  - require explicit user choice when two compatible companions are present;
  - return exact installation guidance when no companion is found;
  - never perform any installation as a side effect.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "seedance_pipeline"))

from capability_probe import (  # noqa: E402
    AmbiguousCompanionError,
    Companion,
    IncompatibleContractError,
    MissingCompanionError,
    discover_companions,
    install_guidance,
    select_companion,
)


def _make_companion(
    root: Path,
    plugin_id: str,
    version: str = "0.1.0",
    contract_versions: tuple[str, ...] = ("1.0.0",),
    executable_name: str | None = None,
) -> Companion:
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": plugin_id,
        "version": version,
        "receipt_contract_versions": list(contract_versions),
    }
    (plugin_dir / ".codex-plugin" / "plugin.json").parent.mkdir(parents=True, exist_ok=True)
    (plugin_dir / ".codex-plugin" / "plugin.json").write_text(json.dumps(manifest))
    if executable_name:
        executable = plugin_dir / "bin" / executable_name
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text("#!/bin/sh\n")
        executable.chmod(0o755)
        return Companion(
            plugin_id=plugin_id,
            version=version,
            executable=executable,
            contract_versions=contract_versions,
            manifest_path=plugin_dir / ".codex-plugin" / "plugin.json",
        )
    return Companion(
        plugin_id=plugin_id,
        version=version,
        executable=None,
        contract_versions=contract_versions,
        manifest_path=plugin_dir / ".codex-plugin" / "plugin.json",
    )


class DiscoverCompanionsTests(unittest.TestCase):
    def test_zero_companions_returns_empty_list(self) -> None:
        with __import__("tempfile").TemporaryDirectory() as tmp:
            roots = (Path(tmp),)
            self.assertEqual(discover_companions(roots), [])

    def test_blender_only(self) -> None:
        with __import__("tempfile").TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            companion = _make_companion(tmp_path, "codex-blender", executable_name="blender_adapter")
            found = discover_companions((tmp_path,))
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].plugin_id, companion.plugin_id)

    def test_maya_only(self) -> None:
        with __import__("tempfile").TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _make_companion(tmp_path, "codex-maya", executable_name="maya_adapter")
            found = discover_companions((tmp_path,))
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].plugin_id, "codex-maya")

    def test_both_companions_present_returns_both(self) -> None:
        with __import__("tempfile").TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _make_companion(tmp_path, "codex-blender", executable_name="blender_adapter")
            _make_companion(tmp_path, "codex-maya", executable_name="maya_adapter")
            found = discover_companions((tmp_path,))
            ids = sorted(c.plugin_id for c in found)
            self.assertEqual(ids, ["codex-blender", "codex-maya"])

    def test_duplicate_plugin_id_dedupes_to_first(self) -> None:
        with __import__("tempfile").TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _make_companion(tmp_path, "codex-blender", executable_name="blender_adapter")
            _make_companion(tmp_path, "codex-blender", version="0.1.1", executable_name="blender_adapter")
            found = discover_companions((tmp_path,))
            self.assertEqual(len(found), 1)

    def test_stale_installation_with_missing_executable_is_skipped(self) -> None:
        with __import__("tempfile").TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _make_companion(tmp_path, "codex-blender")  # no executable -> stale
            found = discover_companions((tmp_path,))
            self.assertEqual(found, [])

    def test_unrelated_user_directories_are_not_crawled(self) -> None:
        with __import__("tempfile").TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _make_companion(tmp_path / "Documents" / "Projects" / "MyScene", "codex-blender", executable_name="blender_adapter")
            found = discover_companions((tmp_path,))
            self.assertEqual(found, [])

    def test_incompatible_contract_version_is_excluded(self) -> None:
        with __import__("tempfile").TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _make_companion(tmp_path, "codex-blender", contract_versions=("99.0.0",), executable_name="blender_adapter")
            found = discover_companions((tmp_path,))
            self.assertEqual(found, [])


class SelectCompanionTests(unittest.TestCase):
    def test_no_companions_raises_missing_with_install_guidance(self) -> None:
        with self.assertRaises(MissingCompanionError) as ctx:
            select_companion([], requested=None)
        self.assertIn("install", str(ctx.exception).lower())

    def test_single_companion_is_auto_selected(self) -> None:
        with __import__("tempfile").TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            companion = _make_companion(tmp_path, "codex-blender", executable_name="blender_adapter")
            selected = select_companion([companion], requested=None)
            self.assertEqual(selected.plugin_id, "codex-blender")

    def test_two_companions_without_request_raises_ambiguous(self) -> None:
        with __import__("tempfile").TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            blender = _make_companion(tmp_path, "codex-blender", executable_name="blender_adapter")
            maya = _make_companion(tmp_path, "codex-maya", executable_name="maya_adapter")
            with self.assertRaises(AmbiguousCompanionError):
                select_companion([blender, maya], requested=None)

    def test_two_companions_with_explicit_blender_request_selects_blender(self) -> None:
        with __import__("tempfile").TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            blender = _make_companion(tmp_path, "codex-blender", executable_name="blender_adapter")
            maya = _make_companion(tmp_path, "codex-maya", executable_name="maya_adapter")
            selected = select_companion([blender, maya], requested="blender")
            self.assertEqual(selected.plugin_id, "codex-blender")

    def test_two_companions_with_explicit_maya_request_selects_maya(self) -> None:
        with __import__("tempfile").TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            blender = _make_companion(tmp_path, "codex-blender", executable_name="blender_adapter")
            maya = _make_companion(tmp_path, "codex-maya", executable_name="maya_adapter")
            selected = select_companion([blender, maya], requested="maya")
            self.assertEqual(selected.plugin_id, "codex-maya")

    def test_unknown_request_raises_value_error(self) -> None:
        with __import__("tempfile").TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            blender = _make_companion(tmp_path, "codex-blender", executable_name="blender_adapter")
            with self.assertRaises(ValueError):
                select_companion([blender], requested="houdini")


class InstallGuidanceTests(unittest.TestCase):
    def test_guidance_names_both_companions(self) -> None:
        msg = install_guidance()
        self.assertIn("codex-blender", msg)
        self.assertIn("codex-maya", msg)


if __name__ == "__main__":
    unittest.main()