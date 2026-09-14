"""The acceptance runner may never mark an unexecuted gate as passed."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_reference_video_acceptance import (  # noqa: E402
    GATE_NAMES,
    PAID_GATES,
    PUBLICATION_GATES,
    AcceptanceAuthorizationError,
    AcceptanceDependencies,
    AcceptanceRunner,
    StaleAuthorizationError,
)


def fake_acceptance_dependencies(**over) -> AcceptanceDependencies:
    base = dict(
        authorized_paid_approvals=("fresh-0.4.0-batch",),
        run_offline_suite=lambda: (True, "Ran 722 tests OK"),
        probe_runtime_tools=lambda: {"ffmpeg": "/opt/homebrew/bin/ffmpeg", "ffprobe": "/opt/homebrew/bin/ffprobe"},
        sha_equality=lambda: (True, "head==tracking==remote"),
    )
    base.update(over)
    return AcceptanceDependencies(**base)


class GateInventoryTests(unittest.TestCase):
    def test_thirteen_named_gates(self) -> None:
        self.assertEqual(len(GATE_NAMES), 13)
        self.assertEqual(
            set(GATE_NAMES),
            {
                "offline_suite", "trusted_media_runtime", "reference_analysis", "semantic_gates",
                "rights_and_redesign", "batch_quote", "paid_generation", "shot_evaluation",
                "audio_subtitles", "final_composition", "installed_mcp", "remote_ci", "sha_equality",
            },
        )

    def test_report_contains_every_gate(self) -> None:
        report = AcceptanceRunner(fake_acceptance_dependencies()).run()
        self.assertEqual(set(report["gates"]), set(GATE_NAMES))


class NoFalsePassTests(unittest.TestCase):
    def test_unexecuted_paid_and_install_gates_are_not_run(self) -> None:
        report = AcceptanceRunner(fake_acceptance_dependencies()).run(
            allow_paid=False, allow_publish=False
        )
        self.assertEqual(report["gates"]["paid_generation"]["status"], "NOT_RUN")
        self.assertEqual(report["gates"]["installed_mcp"]["status"], "NOT_RUN")
        self.assertEqual(report["gates"]["reference_analysis"]["status"], "NOT_RUN")
        self.assertEqual(report["gates"]["semantic_gates"]["status"], "NOT_RUN")
        self.assertFalse(report["all_passed"])

    def test_every_not_run_gate_carries_a_reason(self) -> None:
        report = AcceptanceRunner(fake_acceptance_dependencies()).run()
        for name, gate in report["gates"].items():
            with self.subTest(gate=name):
                if gate["status"] == "NOT_RUN":
                    self.assertTrue(gate["reason"], f"{name} is NOT_RUN without a reason")

    def test_tool_presence_alone_does_not_pass_the_runtime_gate(self) -> None:
        """Finding ffmpeg on PATH is not a measurement."""
        report = AcceptanceRunner(fake_acceptance_dependencies()).run()
        self.assertEqual(report["gates"]["trusted_media_runtime"]["status"], "NOT_RUN")
        self.assertIn("no authorized source fixture", report["gates"]["trusted_media_runtime"]["reason"])

    def test_missing_tools_leave_the_gate_not_run(self) -> None:
        report = AcceptanceRunner(fake_acceptance_dependencies(probe_runtime_tools=lambda: {})).run()
        self.assertEqual(report["gates"]["trusted_media_runtime"]["status"], "NOT_RUN")

    def test_a_failing_offline_suite_is_reported_as_fail(self) -> None:
        report = AcceptanceRunner(
            fake_acceptance_dependencies(run_offline_suite=lambda: (False, "1 test failed"))
        ).run()
        self.assertEqual(report["gates"]["offline_suite"]["status"], "FAIL")

    def test_a_passing_offline_suite_is_reported_as_pass(self) -> None:
        report = AcceptanceRunner(fake_acceptance_dependencies()).run()
        self.assertEqual(report["gates"]["offline_suite"]["status"], "PASS")

    def test_local_media_gate_passes_only_when_it_actually_ran(self) -> None:
        report = AcceptanceRunner(
            fake_acceptance_dependencies(run_local_media=lambda: (True, "composed a 2s clip"))
        ).run()
        self.assertEqual(report["gates"]["trusted_media_runtime"]["status"], "PASS")


class PaidAuthorizationTests(unittest.TestCase):
    def test_paid_run_requires_an_exact_fresh_envelope(self) -> None:
        with self.assertRaises(AcceptanceAuthorizationError):
            AcceptanceRunner(fake_acceptance_dependencies()).run(
                allow_paid=True, approval_id="old-unrelated-approval"
            )

    def test_missing_approval_is_rejected(self) -> None:
        for value in (None, "", "   "):
            with self.subTest(approval_id=value):
                with self.assertRaises(AcceptanceAuthorizationError):
                    AcceptanceRunner(fake_acceptance_dependencies()).run(allow_paid=True, approval_id=value)

    def test_consumed_approval_is_rejected_as_stale(self) -> None:
        deps = fake_acceptance_dependencies(known_consumed_approvals=("56-credit-batch", "98-credit-batch"))
        with self.assertRaises(StaleAuthorizationError):
            AcceptanceRunner(deps).run(allow_paid=True, approval_id="56-credit-batch")

    def test_a_fresh_approval_does_not_fabricate_a_paid_pass(self) -> None:
        """Authorizing a paid run is not the same as having executed one."""
        report = AcceptanceRunner(fake_acceptance_dependencies()).run(
            allow_paid=True, approval_id="fresh-0.4.0-batch"
        )
        for name in sorted(PAID_GATES):
            with self.subTest(gate=name):
                self.assertEqual(report["gates"][name]["status"], "NOT_RUN")
                self.assertIn("no paid batch was executed", report["gates"][name]["reason"])

    def test_publication_requires_its_own_authorization(self) -> None:
        with self.assertRaises(AcceptanceAuthorizationError):
            AcceptanceRunner(fake_acceptance_dependencies()).run(allow_publish=True, approval_id=None)

    def test_publication_authorization_does_not_fabricate_an_install_pass(self) -> None:
        report = AcceptanceRunner(fake_acceptance_dependencies()).run(
            allow_publish=True, approval_id="fresh-publication"
        )
        self.assertEqual(report["gates"]["installed_mcp"]["status"], "NOT_RUN")


class RemoteCiTests(unittest.TestCase):
    def test_no_run_for_the_sha_is_not_run_with_a_reason(self) -> None:
        deps = fake_acceptance_dependencies(
            head_sha=lambda: "a" * 40,
            remote_ci_for_sha=lambda sha: {"status": "none", "reason": "workflow does not trigger on this branch"},
        )
        report = AcceptanceRunner(deps).run(allow_publish=True, approval_id="fresh-publication")
        gate = report["gates"]["remote_ci"]
        self.assertEqual(gate["status"], "NOT_RUN")
        self.assertIn("does not trigger", gate["reason"])

    def test_a_successful_run_for_the_exact_sha_passes(self) -> None:
        deps = fake_acceptance_dependencies(
            head_sha=lambda: "b" * 40,
            remote_ci_for_sha=lambda sha: {"status": "success", "run_id": "123"},
        )
        report = AcceptanceRunner(deps).run(allow_publish=True, approval_id="fresh-publication")
        self.assertEqual(report["gates"]["remote_ci"]["status"], "PASS")

    def test_an_in_progress_run_is_not_reported_as_passing(self) -> None:
        deps = fake_acceptance_dependencies(
            head_sha=lambda: "c" * 40,
            remote_ci_for_sha=lambda sha: {"status": "in_progress"},
        )
        report = AcceptanceRunner(deps).run(allow_publish=True, approval_id="fresh-publication")
        self.assertEqual(report["gates"]["remote_ci"]["status"], "NOT_RUN")


class ReportShapeTests(unittest.TestCase):
    def test_report_is_json_serializable(self) -> None:
        report = AcceptanceRunner(fake_acceptance_dependencies()).run()
        json.dumps(report)

    def test_report_records_the_authorization_flags(self) -> None:
        report = AcceptanceRunner(fake_acceptance_dependencies()).run(
            allow_paid=True, approval_id="fresh-0.4.0-batch", allow_publish=False
        )
        self.assertTrue(report["allow_paid"])
        self.assertFalse(report["allow_publish"])


class CliTests(unittest.TestCase):
    """The CLI is invoked from inside the suite, so its offline gate must refuse
    to re-enter the suite rather than recurse without bound."""

    def _run_cli(self) -> dict:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "run_reference_video_acceptance.py"), "--no-paid", "--json"],
            cwd=ROOT, capture_output=True, text=True, timeout=900, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_no_paid_cli_runs_without_spend(self) -> None:
        payload = self._run_cli()
        self.assertEqual(payload["gates"]["paid_generation"]["status"], "NOT_RUN")
        self.assertEqual(payload["gates"]["installed_mcp"]["status"], "NOT_RUN")

    def test_cli_never_claims_a_full_pass(self) -> None:
        self.assertFalse(self._run_cli()["all_passed"])

    def test_nested_offline_gate_is_blocked_not_passed(self) -> None:
        """Simulate the nested layer: the guard is already set, so the runner
        must refuse to re-enter the suite rather than recurse."""
        from scripts.run_reference_video_acceptance import RECURSION_GUARD_ENV

        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "run_reference_video_acceptance.py"), "--no-paid", "--json"],
            cwd=ROOT, capture_output=True, text=True, timeout=900, check=False,
            env={**os.environ, RECURSION_GUARD_ENV: "1"},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        gate = json.loads(completed.stdout)["gates"]["offline_suite"]
        self.assertEqual(gate["status"], "BLOCKED")
        self.assertIn("nested run refused", gate["reason"])


class RecursionGuardTests(unittest.TestCase):
    def test_guard_variable_is_exported(self) -> None:
        from scripts.run_reference_video_acceptance import RECURSION_GUARD_ENV

        self.assertTrue(RECURSION_GUARD_ENV)

    def test_guard_is_set_for_the_child_suite(self) -> None:
        from scripts.run_reference_video_acceptance import RECURSION_GUARD_ENV, _default_offline_suite

        os.environ[RECURSION_GUARD_ENV] = "1"
        try:
            passed, reason = _default_offline_suite()
        finally:
            os.environ.pop(RECURSION_GUARD_ENV, None)
        self.assertIsNone(passed)
        self.assertIn("nested run refused", reason)


if __name__ == "__main__":
    unittest.main()


class RemoteCiProbeTests(unittest.TestCase):
    """The remote-CI probe must map GitHub run states faithfully and fail closed."""

    def test_missing_run_is_none(self):
        from scripts.run_reference_video_acceptance import _map_ci_run
        self.assertEqual(_map_ci_run(None)["status"], "none")
        self.assertEqual(_map_ci_run({})["status"], "none")

    def test_completed_success_is_success(self):
        from scripts.run_reference_video_acceptance import _map_ci_run
        mapped = _map_ci_run({"status": "completed", "conclusion": "success",
                              "url": "https://x/1", "databaseId": 7, "workflowName": "CI"})
        self.assertEqual(mapped["status"], "success")
        self.assertEqual(mapped["url"], "https://x/1")
        self.assertEqual(mapped["run_id"], "7")

    def test_completed_failure_is_not_success(self):
        from scripts.run_reference_video_acceptance import _map_ci_run
        mapped = _map_ci_run({"status": "completed", "conclusion": "failure"})
        self.assertNotEqual(mapped["status"], "success")
        self.assertIn("failure", mapped["reason"])

    def test_in_progress_stays_not_run(self):
        from scripts.run_reference_video_acceptance import _map_ci_run
        for gh_status in ("queued", "in_progress", "waiting", "requested", "pending"):
            with self.subTest(gh_status=gh_status):
                mapped = _map_ci_run({"status": gh_status})
                self.assertIn(mapped["status"], {"queued", "in_progress"})

    def test_unknown_status_is_never_success(self):
        from scripts.run_reference_video_acceptance import _map_ci_run
        self.assertNotEqual(_map_ci_run({"status": "weird"})["status"], "success")

    def test_unavailable_gh_fails_closed(self):
        from scripts.run_reference_video_acceptance import _default_remote_ci_for_sha
        probe = _default_remote_ci_for_sha("0" * 40)
        self.assertNotEqual(probe["status"], "success")   # fail closed either way
        self.assertIn("reason", probe)
