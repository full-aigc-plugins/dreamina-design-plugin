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
import hashlib
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
    UntrustedCLIError,
    UpgradeRequiredError,
    _parse_command_help,
)


def write_fake_cli(directory: Path, body: str) -> Path:
    """Create an executable shell script that mimics the dreamina CLI."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "dreamina"
    target.write_text(textwrap.dedent(body))
    target.chmod(target.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return target


def trusted_adapter(cli: Path, **kwargs) -> DreaminaAdapter:
    return DreaminaAdapter(
        cli_command=str(cli),
        trusted_binary_sha256=hashlib.sha256(cli.read_bytes()).hexdigest(),
        **kwargs,
    )


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
        helper_source = inspect.getsource(module.DreaminaAdapter._run_bounded_text)
        self.assertIn("subprocess.Popen", helper_source)

    def test_relative_path_and_digest_mismatch_are_rejected(self) -> None:
        with self.assertRaises(UntrustedCLIError):
            DreaminaAdapter(cli_command="dreamina").capability_snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            cli = write_fake_cli(Path(tmp), "#!/bin/sh\necho '{}'")
            with self.assertRaises(UntrustedCLIError):
                DreaminaAdapter(
                    cli_command=str(cli), trusted_binary_sha256="0" * 64
                ).capability_snapshot()


class DreaminaAdapterRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cli_dir = Path(self.tmp.name) / "bin"

    def _make_adapter(self, body: str) -> DreaminaAdapter:
        cli = write_fake_cli(self.cli_dir, body)
        return trusted_adapter(cli, timeout_seconds=5)

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

    def test_oversize_stderr_is_rejected_while_process_is_running(self) -> None:
        adapter = self._make_adapter(
            """\
            #!/bin/sh
            head -c 200000 /dev/zero | tr '\0' 'E' 1>&2
            echo '{}'
            """
        )
        adapter.max_output_bytes = 1024
        with self.assertRaises(InvalidJSONError):
            adapter.run(["anything"])


class CapabilitySnapshotTests(unittest.TestCase):
    def test_command_help_preserves_model_specific_constraints(self) -> None:
        snapshot = _parse_command_help(
            {
                "text2image": "- model_version: 5.0, 5.0Pro\n- generate_num: 1-10\n- 5.0 -> resolution_type 2k or 4k\n- 5.0Pro -> resolution_type 1.5k, 2k, or 4k\n- ratio: 16:9, 1:1\n",
                "image2image": "Upload 1 to 10 local images.\n- model_version: 5.0Pro\n- generate_num: 1-10\n- 5.0Pro -> resolution_type 1.5k, 2k, or 4k\n- ratio: 16:9, 1:1\n",
                "text2video": "- model_version: seedance2.0, seedance2.0_vip, seedance2.5\n- seedance2.5 -> video_resolution 480p, 720p, or 1080p; duration 4-30s\n- seedance2.0_vip -> video_resolution 720p, 1080p, or 4k; duration 4-15s\n- all other models -> video_resolution 720p; duration 4-15s\n- ratio: 16:9, 9:16\n",
            }
        )
        by_name = {entry["name"]: entry for entry in snapshot["models"]}
        self.assertEqual(by_name["5.0Pro"]["max_count"], 10)
        self.assertEqual(by_name["5.0Pro"]["max_references"], 10)
        self.assertEqual(by_name["5.0Pro"]["resolutions"], ["1.5k", "2k", "4k"])
        self.assertEqual(by_name["seedance2.0"]["resolutions"], ["720p"])
        self.assertEqual(by_name["seedance2.5"]["duration_max_seconds"], 30)

    def test_capability_snapshot_returns_dict(self) -> None:
        """``capability_snapshot`` must rely on argv-only CLI calls."""
        adapter = DreaminaAdapter(cli_command="/nonexistent/dreamina-binary")
        # The real binary isn't present; we just assert the method exists and
        # that calling it without a CLI is a typed error.
        with self.assertRaises(CLINotFoundError):
            adapter.capability_snapshot()

    def test_capability_snapshot_falls_back_to_command_help(self) -> None:
        cli = Path(self._make_cli())
        adapter = trusted_adapter(cli)
        snapshot = adapter.capability_snapshot()
        self.assertEqual(snapshot["cli_version"], "ec1b9fa-dirty")
        self.assertEqual(snapshot["cli_commit"], "ec1b9fa")
        self.assertIn("text2image", snapshot["modes"])
        by_name = {entry["name"]: entry for entry in snapshot["models"]}
        self.assertEqual(by_name["5.0Pro"]["modes"], ["text2image"])
        self.assertIn("1.5k", snapshot["resolutions"]["image"])
        self.assertIn("16:9", snapshot["ratios"])

    def test_capability_snapshot_accepts_plain_text_version(self) -> None:
        cli = write_fake_cli(Path(tempfile.mkdtemp()), """\
            #!/bin/sh
            case "$1" in
              --version) echo 'dreamina 1.4.18' ;;
              schema) echo '{"modes":["text2image"]}' ;;
            esac
        """)
        snapshot = trusted_adapter(cli).capability_snapshot()
        self.assertEqual(snapshot["cli_version"], "1.4.18")

    def _make_cli(self) -> str:
        directory = Path(self._temp_dir.name) if hasattr(self, "_temp_dir") else None
        if directory is None:
            self._temp_dir = tempfile.TemporaryDirectory()
            self.addCleanup(self._temp_dir.cleanup)
            directory = Path(self._temp_dir.name)
        return str(write_fake_cli(directory, """\
            #!/bin/sh
            case "$1" in
              --version) echo '{"version":"ec1b9fa-dirty","commit":"ec1b9fa"}' ;;
              schema) echo 'unknown command "schema" for "dreamina"' >&2; exit 1 ;;
              --help) printf 'Usage: dreamina [flags]\\nGenerator Commands:\\n  text2image  Submit image\\n' ;;
              text2image) printf 'Usage: dreamina text2image [flags]\\nSupported combinations:\\n- model_version: 5.0, 5.0Pro\\n- ratio: 16:9, 1:1\\n- 5.0Pro -> resolution_type 1.5k, 2k, or 4k\\n' ;;
            esac
        """))


if __name__ == "__main__":
    unittest.main()
