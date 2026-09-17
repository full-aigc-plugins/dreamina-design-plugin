"""Adversarial browser capability and exact macOS application policy checks."""
import gc
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.bounded_process import BoundedProcessOutput, BoundedProcessTimeout
from scripts.trusted_media_tools import BrowserLaunchHandle, TrustedMediaToolError, TrustedMediaToolStore
from tests import test_trusted_media_tools as trust_fixtures


class BrowserLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.browser = self.root / "Google Chrome.app/Contents/MacOS/Google Chrome"
        self.browser.parent.mkdir(parents=True)
        self.browser.write_text("#!/bin/sh\nprintf 'pinned:%s' \"$1\"\n"); self.browser.chmod(0o755)
        self.store = TrustedMediaToolStore(self.root / "config/tools.json")
        policy = trust_fixtures.TrustedMediaToolStoreTests._browser_policy(self)
        self.policies = patch("scripts.trusted_media_tools.BROWSER_APPLICATION_POLICIES", {str(self.browser): policy})
        self.policies.start(); self.addCleanup(self.policies.stop)
        self.signature = patch("scripts.trusted_media_tools.run_bounded", side_effect=trust_fixtures.TrustedMediaToolStoreTests._codesign_success)
        with self.signature:
            self.store.enroll("browser", self.browser, approval_provider=trust_fixtures._Approve())

    def handle(self):
        with self.signature:
            return self.store.reverify_browser_for_launch_handle()

    def assert_closed(self, descriptors):
        for descriptor in descriptors:
            with self.assertRaises(OSError): os.fstat(descriptor)

    def test_pinned_bundle_launch_after_ancestor_replacement(self):
        handle = self.handle()
        ancestor = self.root / "Google Chrome.app"
        ancestor.rename(self.root / "old.app")
        self.browser.parent.mkdir(parents=True)
        self.browser.write_text("#!/bin/sh\necho attacker\n"); self.browser.chmod(0o755)
        self.assertEqual(handle.spawn(["argument"]).stdout, "pinned:argument")

    def test_context_manager_closes_without_launch(self):
        with self.handle() as handle:
            descriptors = (handle._descriptor, handle._bundle_descriptor)
        self.assert_closed(descriptors)

    def test_context_exception_closes(self):
        with self.assertRaisesRegex(RuntimeError, "abort"):
            with self.handle() as handle:
                descriptors = (handle._descriptor, handle._bundle_descriptor)
                raise RuntimeError("abort")
        self.assert_closed(descriptors)

    def test_explicit_close_is_idempotent_and_consumes_handle(self):
        handle = self.handle(); descriptors = (handle._descriptor, handle._bundle_descriptor)
        handle.close(); handle.close(); self.assert_closed(descriptors)
        with self.assertRaises(TrustedMediaToolError): handle.spawn([])
        with self.assertRaises(TrustedMediaToolError): handle.__enter__()

    def test_success_is_one_use(self):
        handle = self.handle(); descriptors = (handle._descriptor, handle._bundle_descriptor)
        self.assertEqual(handle.spawn(["hello"]).stdout, "pinned:hello")
        self.assert_closed(descriptors)
        with self.assertRaises(TrustedMediaToolError): handle.spawn([])

    def test_abandoned_handle_releases_descriptors(self):
        handle = self.handle(); descriptors = (handle._descriptor, handle._bundle_descriptor)
        del handle; gc.collect(); self.assert_closed(descriptors)

    def test_invalid_arguments_consume_and_close_handle(self):
        for args in (None, "abc", [1], ["a\0b"]):
            handle = self.handle(); descriptors = (handle._descriptor, handle._bundle_descriptor)
            with self.subTest(args=args), self.assertRaises(TrustedMediaToolError): handle.spawn(args)
            self.assert_closed(descriptors)
            with self.assertRaises(TrustedMediaToolError): handle.spawn([])

    def test_spawn_failure_releases_descriptors(self):
        handle = self.handle(); descriptors = (handle._descriptor, handle._bundle_descriptor)
        with patch("scripts.trusted_media_tools.run_bounded", side_effect=OSError("no helper")):
            with self.assertRaises(TrustedMediaToolError): handle.spawn([])
        self.assert_closed(descriptors)

    def test_replaced_executable_within_pinned_bundle_fails_closed(self):
        handle = self.handle()
        replacement = self.root / "replacement"
        replacement.write_text("#!/bin/sh\necho attacker\n"); replacement.chmod(0o755)
        os.replace(replacement, self.browser)
        with self.assertRaises(TrustedMediaToolError): handle.spawn([])

    def test_legacy_launch_seam_returns_only_owned_capability(self):
        with self.signature:
            handle = self.store.reverify_browser_for_launch()
        self.assertIsInstance(handle, BrowserLaunchHandle)
        handle.close()

    def test_construction_failure_closes_both_fds(self):
        original_open = os.open; opened = []
        def track(*args, **kwargs):
            fd = original_open(*args, **kwargs); opened.append(fd); return fd
        with self.signature, patch("scripts.trusted_media_tools.os.open", side_effect=track), patch(
            "scripts.trusted_media_tools.BrowserLaunchContext", side_effect=RuntimeError("context failed")
        ), self.assertRaisesRegex(RuntimeError, "context failed"):
            self.store.reverify_browser_for_launch_handle()
        self.assert_closed(opened)

    def test_signature_rejects_timeout_output_overflow_and_nonzero(self):
        for response in (BoundedProcessTimeout("late"), BoundedProcessOutput("large"),
                         subprocess.CompletedProcess([], 2, "", "bad")):
            kwargs = {"side_effect": response} if isinstance(response, Exception) else {"return_value": response}
            with self.subTest(response=response), patch("scripts.trusted_media_tools.run_bounded", **kwargs), self.assertRaises(TrustedMediaToolError):
                self.store.reverify_browser_for_launch_handle()

    def test_signature_rejects_identifier_team_requirement_and_missing_fields(self):
        good = trust_fixtures.TrustedMediaToolStoreTests._codesign_success(["-d"]).stderr
        for bad in (good.replace("com.google.Chrome", "com.attacker.Chrome"),
                    good.replace("EQHXZ8M8AV", "OTHERTEAM"),
                    good.replace("designated =>", "missing =>"),
                    good.replace("certificate leaf[subject.OU] = EQHXZ8M8AV", "anchor apple")):
            def codesign(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 0, "", "" if "--verify" in argv else bad)
            with self.subTest(bad=bad), patch("scripts.trusted_media_tools.run_bounded", side_effect=codesign), self.assertRaises(TrustedMediaToolError):
                self.store.reverify_browser_for_launch_handle()

    def test_bundle_replaced_during_signature_is_rejected(self):
        def codesign(argv, **kwargs):
            if "--verify" in argv:
                bundle = self.root / "Google Chrome.app"
                bundle.rename(self.root / "old.app")
                self.browser.parent.mkdir(parents=True)
                os.link(self.root / "old.app/Contents/MacOS/Google Chrome", self.browser)
            return trust_fixtures.TrustedMediaToolStoreTests._codesign_success(argv, **kwargs)
        with patch("scripts.trusted_media_tools.run_bounded", side_effect=codesign), self.assertRaises(TrustedMediaToolError):
            self.store.reverify_browser_for_launch_handle()

    def test_browser_symlink_ancestor_is_rejected(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root / "Google Chrome.app", target_is_directory=True)
        with self.assertRaises(TrustedMediaToolError):
            self.store.enroll("browser", alias / "Contents/MacOS/Google Chrome", approval_provider=trust_fixtures._Approve())

    def test_bundle_child_symlink_after_handle_is_rejected(self):
        handle = self.handle()
        contents = self.browser.parents[1]
        contents.rename(self.root / "moved-contents")
        contents.symlink_to(self.root / "moved-contents", target_is_directory=True)
        with self.assertRaises(TrustedMediaToolError): handle.spawn([])

    def test_invalid_limits_consume_and_close_handle(self):
        handle = self.handle(); descriptors = (handle._descriptor, handle._bundle_descriptor)
        with self.assertRaises(TrustedMediaToolError): handle.spawn([], timeout_seconds=0)
        self.assert_closed(descriptors)

    def test_browser_output_and_timeout_are_typed_and_close_handle(self):
        for code in ("#!/bin/sh\nwhile true; do printf xxxxxxxxxxxxxxxxxxxx; done\n", "#!/bin/sh\nsleep 20\n"):
            self.browser.write_text(code)
            with self.signature:
                self.store.enroll("browser", self.browser, approval_provider=trust_fixtures._Approve())
            handle = self.handle(); descriptors = (handle._descriptor, handle._bundle_descriptor)
            with self.assertRaises(TrustedMediaToolError): handle.spawn([], timeout_seconds=0.2, stdout_cap=32)
            self.assert_closed(descriptors)


class MacBrowserPolicyTests(unittest.TestCase):
    browser = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

    def inspect(self, *, source=None, file_uid=501, file_mode=0o755, parent=None, parent_uid=0, parent_gid=80, parent_mode=0o755):
        source = source or self.browser
        def observed(path, **kwargs):
            mode = (stat.S_IFREG | file_mode) if path == source else (stat.S_IFDIR | (parent_mode if path == parent else 0o755))
            uid = file_uid if path == source else (parent_uid if path == parent else 0)
            return os.stat_result((mode, 1, 1, 1, uid, parent_gid, 10, 0, 0, 0))
        with patch.object(Path, "stat", autospec=True, side_effect=observed), patch.object(Path, "is_symlink", return_value=False), patch.object(Path, "resolve", autospec=True, side_effect=lambda p, **kw: p), patch("scripts.trusted_media_tools.os.getuid", return_value=501):
            return TrustedMediaToolStore()._validate_source_path("browser", source)

    def test_real_applications_root_admin_0775_is_supported(self):
        self.assertEqual(self.inspect(parent=Path("/Applications"), parent_mode=0o775)[0], self.browser)


def policy_case(options, accepted):
    def test(self):
        if accepted: self.inspect(**options)
        else:
            with self.assertRaises(TrustedMediaToolError): self.inspect(**options)
    return test


for name, options, accepted in (
    ("user_owned", {}, True),
    ("root_owned", {"file_uid": 0}, True),
    ("foreign_owner", {"file_uid": 502}, False),
    ("admin_group_write_executable", {"file_mode": 0o775}, True),
    ("foreign_group_write_executable", {"file_mode": 0o775, "parent_gid": 20}, False),
    ("world_write_executable", {"file_mode": 0o757}, False),
    ("world_write_applications", {"parent": Path("/Applications"), "parent_mode": 0o777}, False),
    ("foreign_applications_owner", {"parent": Path("/Applications"), "parent_uid": 502, "parent_mode": 0o775}, False),
    ("foreign_applications_group", {"parent": Path("/Applications"), "parent_gid": 20, "parent_mode": 0o775}, False),
    ("admin_writable_bundle", {"parent": Path("/Applications/Google Chrome.app"), "parent_mode": 0o775}, True),
    ("admin_writable_contents", {"parent": Path("/Applications/Google Chrome.app/Contents"), "parent_mode": 0o775}, True),
    ("admin_writable_macos", {"parent": MacBrowserPolicyTests.browser.parent, "parent_mode": 0o775}, True),
    ("foreign_group_writable_bundle", {"parent": Path("/Applications/Google Chrome.app"), "parent_mode": 0o775, "parent_gid": 20}, False),
    ("world_writable_bundle", {"parent": Path("/Applications/Google Chrome.app"), "parent_mode": 0o777}, False),
    ("foreign_bundle_owner", {"parent": Path("/Applications/Google Chrome.app"), "parent_uid": 502}, False),
    ("wrong_app_path", {"source": Path("/Applications/Fake.app/Contents/MacOS/Google Chrome")}, False),
    ("alternate_install_root", {"source": Path("/Users/me/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")}, False),
):
    setattr(MacBrowserPolicyTests, "test_" + name, policy_case(options, accepted))
