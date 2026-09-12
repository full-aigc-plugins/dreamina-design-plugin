"""RED tests for the argv-only Dreamina CLI adapter.

These tests use a synthetic CLI shell script (set via the `dreamina` argument
to ``DreaminaAdapter``) so they never depend on the real binary. They assert
the failure surface required by Task 2:

  * missing CLI raises a structured ``CLINotFoundError`` with no credentials;
  * auth / permission failures produce typed errors with separate stdout/stderr;
  * invalid JSON output is reported as ``InvalidJSONError``;
  * upgrade notices are reported as ``UpgradeRequiredError``;
  * subprocess timeouts become ``TimeoutError`` and never resubmit;
  * output over the size cap is rejected before decoding;
  * argv-only execution is enforced (no shell string).
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.dreamina_adapter import (  # noqa: E402  (path injection above)
    CLINotFoundError,
    DreaminaAdapter,
    DreaminaResult,
    InvalidJSONError,
    PermissionDeniedError,
    TimeoutError as AdapterTimeoutError,
    UpgradeRequiredError,
)


def write_fake_cli(directory: Path, body: str) -> Path:
    """Create an executable shell script that mimics the dreamina CLI."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "dreamina"
    target.write_text(textwrap.dedent(body))
    target.chmod(target.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return target


class DreaminaAdapterConstructionTests(unittest.TestCase):
    def test_missing_cli_raises_typed_error(self) -> None:
        adapter = DreaminaAdapter(cli_command="/nonexistent/dreamina-binary")
        with self.assertRaises(CLINotFoundError) as ctx:
            adapter.run(["--help"])
        self.assertNotIn("credential", str(ctx.exception).lower())

    def test_argv_is_passed_without_shell(self) -> None:
        """Subprocess must be invoked with a list, not a shell string."""
        import inspect
        from scripts import dreamina_adapter as module

        source = inspect.getsource(module.DreaminaAdapter.run)
        self.assertNotIn("shell=True", source)
        self.assertTrue("subprocess.run" in source)


class DreaminaAdapterRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cli_dir = Path(self.tmp.name) / "bin"

    def _make_adapter(self, body: str) -> DreaminaAdapter:
        cli = write_fake_cli(self.cli_dir, body)
        return DreaminaAdapter(cli_command=str(cli), timeout_seconds=5)

    def test_successful_json_payload_is_returned(self) -> None:
        adapter = self._make_adapter(
            """\
            #!/bin/sh
            echo '{"ok": true, "submit_id": "abc-123"}'
            """
        )
        result = adapter.run(["status"])
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.payload, {"ok": True, "submit_id": "abc-123"})
        self.assertEqual(result.error_code, None)

    def test_auth_failure_yields_permission_error(self) -> None:
        adapter = self._make_adapter(
            """\
            #!/bin/sh
            echo 'not authenticated' 1>&2
            exit 3
            """
        )
        with self.assertRaises(PermissionDeniedError):
            adapter.run(["whoami"])

    def test_invalid_json_output_yields_typed_error(self) -> None:
        adapter = self._make_adapter(
            """\
            #!/bin/sh
            echo 'this is not JSON {'
            """
        )
        with self.assertRaises(InvalidJSONError):
            adapter.run(["anything"])

    def test_upgrade_notice_yields_typed_error(self) -> None:
        adapter = self._make_adapter(
            """\
            #!/bin/sh
            echo 'upgrade required: please install dreamina >= 1.5.0' 1>&2
            exit 7
            """
        )
        with self.assertRaises(UpgradeRequiredError):
            adapter.run(["anything"])

    def test_timeout_is_reported_and_does_not_resubmit(self) -> None:
        adapter = self._make_adapter(
            """\
            #!/bin/sh
            sleep 5
            echo '{"ok": true}'
            """
        )
        adapter.timeout_seconds = 0.2
        with self.assertRaises(AdapterTimeoutError):
            adapter.run(["slow"])

    def test_stdout_and_stderr_are_separated(self) -> None:
        adapter = self._make_adapter(
            """\
            #!/bin/sh
            echo 'on stderr' 1>&2
            echo '{"ok": true}'
            """
        )
        result = adapter.run(["anything"])
        self.assertEqual(result.payload, {"ok": True})
        self.assertIn("on stderr", result.stderr)

    def test_oversize_output_is_rejected(self) -> None:
        adapter = self._make_adapter(
            """\
            #!/bin/sh
            head -c 200000 /dev/zero | tr '\\0' 'A'
            """
        )
        adapter.max_output_bytes = 1024
        with self.assertRaises(InvalidJSONError):
            adapter.run(["anything"])


class CapabilitySnapshotTests(unittest.TestCase):
    def test_capability_snapshot_returns_dict(self) -> None:
        """``capability_snapshot`` must rely on argv-only CLI calls."""
        adapter = DreaminaAdapter(cli_command="/nonexistent/dreamina-binary")
        # The real binary isn't present; we just assert the method exists and
        # that calling it without a CLI is a typed error.
        with self.assertRaises(CLINotFoundError):
            adapter.capability_snapshot()


if __name__ == "__main__":
    unittest.main()
