"""RED tests for the runtime-gate unlock harness.

The harness automates the six manual steps a *human* must run in their
own authorized shell. It must:

* refuse to run ``probe`` when ``dreamina`` is not on PATH — no
  fabricated version / help / schema output is ever written;
* capture version / help / schema verbatim from the real binary when
  it IS available, and write them under ``docs/verification/``;
* refuse to write ``paid-canary-approved.md`` unless the caller
  supplies a real submit ID, approver, and observed-behavior string;
* never invent a submit ID, timestamp, or approver;
* report the current gate status without mutating anything.
"""

from __future__ import annotations

import json
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:  # pragma: no cover - RED phase
    from scripts.unlock_runtime_gates import (  # type: ignore  # noqa: E402
        CLIAbsentError,
        IncompleteCanaryRecordError,
        UnlockHarness,
    )
except ModuleNotFoundError:  # pragma: no cover
    UnlockHarness = None  # type: ignore[assignment]
    CLIAbsentError = None  # type: ignore[assignment]
    IncompleteCanaryRecordError = None  # type: ignore[assignment]


def _fake_cli(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "dreamina"
    target.write_text(textwrap.dedent(body))
    target.chmod(target.stat().st_mode | stat.S_IEXEC)
    return target


class ModuleExportTests(unittest.TestCase):
    def test_module_exports_harness(self) -> None:
        self.assertIsNotNone(UnlockHarness)


class ProbeRefusalTests(unittest.TestCase):
    def test_probe_refuses_when_cli_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            verification = Path(tmp) / "verification"
            harness = UnlockHarness(
                verification_dir=verification,
                cli_command="/nonexistent/dreamina",
            )
            with self.assertRaises(CLIAbsentError):
                harness.probe()

    def test_probe_writes_no_files_when_cli_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            verification = Path(tmp) / "verification"
            harness = UnlockHarness(
                verification_dir=verification,
                cli_command="/nonexistent/dreamina",
            )
            try:
                harness.probe()
            except CLIAbsentError:
                pass
            self.assertFalse((verification / "cli-version.txt").exists())
            self.assertFalse((verification / "cli-help.txt").exists())
            self.assertFalse((verification / "cli-schema.json").exists())


class ProbeCaptureTests(unittest.TestCase):
    def test_probe_captures_version_help_schema_verbatim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cli = _fake_cli(
                Path(tmp) / "bin",
                """\
                #!/bin/sh
                case "$1" in
                  --version) echo 'dreamina 1.4.18' ;;
                  --help) echo 'Usage: dreamina <command>' ;;
                  schema) echo '{"modes": ["text2image"]}' ;;
                esac
                """,
            )
            verification = Path(tmp) / "verification"
            harness = UnlockHarness(
                verification_dir=verification,
                cli_command=str(cli),
            )
            result = harness.probe()
            self.assertEqual(
                (verification / "cli-version.txt").read_text(encoding="utf-8").strip(),
                "dreamina 1.4.18",
            )
            self.assertIn("Usage", (verification / "cli-help.txt").read_text(encoding="utf-8"))
            self.assertEqual(result["version"], "dreamina 1.4.18")

    def test_probe_marks_account_readiness_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cli = _fake_cli(
                Path(tmp) / "bin",
                """\
                #!/bin/sh
                case "$1" in
                  --version) echo 'dreamina 1.4.18' ;;
                  --help) echo 'help' ;;
                  schema) echo '{}' ;;
                esac
                """,
            )
            verification = Path(tmp) / "verification"
            harness = UnlockHarness(verification_dir=verification, cli_command=str(cli))
            harness.probe()
            readiness = (verification / "account-readiness.md").read_text(encoding="utf-8")
            self.assertIn("PENDING", readiness)

    def test_probe_builds_help_snapshot_when_schema_command_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cli = _fake_cli(
                Path(tmp) / "bin",
                """\
                #!/bin/sh
                case "$1" in
                  --version) echo '{"version":"ec1b9fa-dirty","commit":"ec1b9fa"}' ;;
                  --help) printf 'Usage: dreamina [flags]\\nBuilt-in Commands:\\n  text2image  Generate image\\n' ;;
                  schema) echo 'unknown command "schema" for "dreamina"' >&2; exit 1 ;;
                  text2image) printf 'Usage: dreamina text2image [flags]\\nFlags:\\n  --prompt string  generation prompt\\n' ;;
                esac
                """,
            )
            verification = Path(tmp) / "verification"
            harness = UnlockHarness(verification_dir=verification, cli_command=str(cli))
            harness.probe()
            snapshot = json.loads(
                (verification / "cli-schema.json").read_text(encoding="utf-8")
            )
            self.assertEqual(snapshot["source"], "command-help")
            self.assertIn("text2image", snapshot["commands"])
            readiness = (verification / "account-readiness.md").read_text(encoding="utf-8")
            self.assertIn("= `ec1b9fa-dirty`", readiness)


class CanaryRecordTests(unittest.TestCase):
    def test_record_canary_requires_all_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            verification = Path(tmp) / "verification"
            harness = UnlockHarness(
                verification_dir=verification,
                cli_command="/nonexistent/dreamina",
            )
            with self.assertRaises(IncompleteCanaryRecordError):
                harness.record_canary(submit_id="", approver="me", observed="x")

    def test_record_canary_refuses_placeholder_submit_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            verification = Path(tmp) / "verification"
            harness = UnlockHarness(
                verification_dir=verification,
                cli_command="/nonexistent/dreamina",
            )
            for placeholder in ("TBD", "placeholder", "fake", "test", "unknown", "xxx"):
                with self.assertRaises(IncompleteCanaryRecordError):
                    harness.record_canary(
                        submit_id=placeholder,
                        approver="me",
                        observed="saw a picture",
                    )

    def test_record_canary_writes_marker_with_real_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            verification = Path(tmp) / "verification"
            harness = UnlockHarness(
                verification_dir=verification,
                cli_command="/nonexistent/dreamina",
            )
            harness.record_canary(
                submit_id="20260912-abc-def-12345",
                approver="wandl",
                observed="Generated one 1k image; submit_id returned immediately; cost 1 credit.",
            )
            text = (verification / "paid-canary-approved.md").read_text(encoding="utf-8")
            self.assertIn("20260912-abc-def-12345", text)
            self.assertIn("wandl", text)
            self.assertIn("1 credit", text)


class StatusTests(unittest.TestCase):
    def test_status_reports_not_run_when_nothing_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = UnlockHarness(
                verification_dir=Path(tmp) / "verification",
                cli_command="/nonexistent/dreamina",
            )
            status = harness.status()
            self.assertEqual(status["paid_canary"], "NOT_RUN")
            self.assertEqual(status["read_only_runtime_contract"], "blocked")
            self.assertFalse(status["cli_available"])

    def test_status_reports_observed_when_artifacts_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cli = _fake_cli(
                Path(tmp) / "bin",
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  --version) echo 'dreamina 1.4.18' ;;\n"
                "  --help) printf 'Usage: dreamina <command> [options]\\n\\n"
                "Commands:\\n  generate   Submit a generation request\\n"
                "  status     Query a submission by id\\n' ;;\n"
                "  schema) echo '{\"modes\": [\"text2image\"]}' ;;\n"
                "esac\n",
            )
            verification = Path(tmp) / "verification"
            harness = UnlockHarness(verification_dir=verification, cli_command=str(cli))
            harness.probe()
            harness.record_canary(
                submit_id="20260912-real-id-999",
                approver="wandl",
                observed="observed a real generation end to end",
            )
            status = harness.status()
            self.assertEqual(status["paid_canary"], "APPROVED")
            self.assertEqual(status["read_only_runtime_contract"], "observed")

    def test_status_accepts_commit_based_json_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            verification = Path(tmp) / "verification"
            verification.mkdir(parents=True)
            (verification / "cli-version.txt").write_text(
                '{"version":"ec1b9fa-dirty","commit":"ec1b9fa",'
                '"build_time":"2026-09-09T09:09:35Z"}\n',
                encoding="utf-8",
            )
            (verification / "cli-help.txt").write_text(
                "Usage: dreamina <command> [options]\n\nCommands:\n"
                "  generate   Submit a generation request\n",
                encoding="utf-8",
            )
            (verification / "cli-schema.json").write_text("{}\n", encoding="utf-8")
            harness = UnlockHarness(
                verification_dir=verification,
                cli_command="/nonexistent/dreamina",
            )
            status = harness.status()
            self.assertEqual(status["read_only_runtime_contract"], "observed")

    def test_status_does_not_report_observed_for_empty_artifacts(self) -> None:
        """Existence alone must not satisfy the gate."""
        with tempfile.TemporaryDirectory() as tmp:
            verification = Path(tmp) / "verification"
            verification.mkdir(parents=True)
            for name in ("cli-version.txt", "cli-help.txt", "cli-schema.json"):
                (verification / name).write_text("", encoding="utf-8")
            harness = UnlockHarness(
                verification_dir=verification,
                cli_command="/nonexistent/dreamina",
            )
            status = harness.status()
            self.assertEqual(status["read_only_runtime_contract"], "blocked")

    def test_status_does_not_approve_canary_without_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            verification = Path(tmp) / "verification"
            verification.mkdir(parents=True)
            (verification / "paid-canary-approved.md").write_text(
                "# approved\n\nlooks fine\n", encoding="utf-8"
            )
            harness = UnlockHarness(
                verification_dir=verification,
                cli_command="/nonexistent/dreamina",
            )
            status = harness.status()
            self.assertEqual(status["paid_canary"], "NOT_RUN")


if __name__ == "__main__":
    unittest.main()
