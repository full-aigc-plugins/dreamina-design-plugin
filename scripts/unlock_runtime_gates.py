"""Harness for the two runtime gates that require human action.

The implementation plan defines two gates that a human must satisfy in
their own authorized shell:

* ``read_only_runtime_contract`` — install + authorize the ``dreamina``
  CLI, then capture ``--version`` / ``--help`` / ``schema`` and record
  account readiness without performing any generation.
* ``paid_canary`` — interactively run one real image or video
  generation and record the submit ID, approver, and observed behavior.

This harness automates the mechanical parts of those steps **for the
human to run**, and enforces the plan's anti-fabrication boundary:

* ``probe`` refuses to write anything when ``dreamina`` is not on PATH.
  It never invents version / help / schema output.
* ``record-canary`` refuses placeholder-looking submit IDs and requires
  every field. It never invents a submit ID, timestamp, or approver.
* ``status`` reports the current gate state without mutating anything.

Usage (run these yourself in your own authorized shell):

    # 1. After installing + authorizing the dreamina CLI:
    python3 scripts/unlock_runtime_gates.py probe

    # 2. Fill in docs/verification/account-readiness.md by hand.

    # 3. Interactively run one low-cost generation, then:
    python3 scripts/unlock_runtime_gates.py record-canary \\
        --submit-id <real-submit-id> \\
        --approver <your-name> \\
        --observed "<one paragraph on what you saw>"

    # 4. Re-run the distribution verifier:
    python3 scripts/validate_distribution_v7.py --strict
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# Placeholder-looking submit IDs are rejected outright. A real Dreamina
# submit ID is a server-issued identifier; none of these strings can be
# one.
PLACEHOLDER_TOKENS = (
    "tbd",
    "placeholder",
    "fake",
    "test",
    "unknown",
    "xxx",
    "todo",
    "not_run",
    "not-run",
    "example",
    "dummy",
    "sample",
)


class UnlockError(Exception):
    """Base class for unlock harness errors."""


class CLIAbsentError(UnlockError):
    """The dreamina CLI is not available; refusing to fabricate output."""


class IncompleteCanaryRecordError(UnlockError):
    """The canary record is missing fields or uses a placeholder value."""


class UnlockHarness:
    """Drive the human-performed runtime gate steps without fabricating."""

    def __init__(self, *, verification_dir: Path, cli_command: str = "dreamina") -> None:
        self._verification_dir = Path(verification_dir)
        self._cli_command = cli_command

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def status(self) -> dict:
        cli_ok = self._cli_available()
        canary_marker = self._verification_dir / "paid-canary-approved.md"
        return {
            "cli_available": cli_ok,
            "cli_command": self._cli_command,
            "read_only_runtime_contract": "observed" if self._artifacts_present() else "blocked",
            "paid_canary": "APPROVED" if canary_marker.is_file() else "NOT_RUN",
            "verification_dir": str(self._verification_dir),
        }

    # ------------------------------------------------------------------
    # Probe
    # ------------------------------------------------------------------
    def probe(self) -> dict:
        """Capture version / help / schema from the real CLI.

        Raises :class:`CLIAbsentError` when the CLI is unavailable. In
        that case **nothing is written** — no synthetic outputs, no
        placeholder files.
        """
        binary = self._resolve_cli()
        version = self._run([binary, "--version"])
        help_text = self._run([binary, "--help"])
        schema = self._run([binary, "schema"])
        self._verification_dir.mkdir(parents=True, exist_ok=True)
        (self._verification_dir / "cli-version.txt").write_text(
            version + "\n", encoding="utf-8"
        )
        (self._verification_dir / "cli-help.txt").write_text(
            help_text + "\n", encoding="utf-8"
        )
        (self._verification_dir / "cli-schema.json").write_text(
            schema + "\n", encoding="utf-8"
        )
        nickname = version.split()[-1] if version.split() else "unknown"
        readiness_path = self._verification_dir / "account-readiness.md"
        if not readiness_path.exists():
            readiness_path.write_text(
                "# Account readiness\n\n"
                f"> Captured {self._now()} from `{binary} --version` = `{nickname}`.\n\n"
                "**Status: PENDING** — fill in the fields below after you have\n"
                "authorized the CLI with your Dreamina account. Do **not** record\n"
                "credentials here; the plan forbids storing tokens, cookies, or\n"
                "account snapshots.\n\n"
                "- authenticated user: \n"
                "- membership tier: \n"
                "- generation performed as part of this check: **no**\n"
                "- captured at: \n",
                encoding="utf-8",
            )
        return {
            "version": version,
            "help_bytes": len(help_text),
            "schema_bytes": len(schema),
        }

    # ------------------------------------------------------------------
    # Canary record
    # ------------------------------------------------------------------
    def record_canary(self, *, submit_id: str, approver: str, observed: str) -> Path:
        """Record a human-performed paid canary.

        Every field is required. Placeholder-looking submit IDs are
        rejected so the harness cannot be used to manufacture a PASS.
        """
        if not submit_id or not approver or not observed:
            raise IncompleteCanaryRecordError(
                "submit_id, approver, and observed are all required"
            )
        lowered = submit_id.strip().lower()
        if any(token in lowered for token in PLACEHOLDER_TOKENS):
            raise IncompleteCanaryRecordError(
                f"submit_id looks like a placeholder ({submit_id!r}); "
                "record the real server-issued submit ID from your generation"
            )
        if len(submit_id.strip()) < 8:
            raise IncompleteCanaryRecordError(
                f"submit_id too short to be a real server-issued id ({submit_id!r})"
            )
        self._verification_dir.mkdir(parents=True, exist_ok=True)
        target = self._verification_dir / "paid-canary-approved.md"
        target.write_text(
            "# Paid canary approval record\n\n"
            "> This file is the record of a **real, human-performed** paid\n"
            "> generation. It was created by the human who ran the generation,\n"
            "> not synthesized by the model.\n\n"
            f"- **submit_id:** `{submit_id.strip()}`\n"
            f"- **timestamp:** {self._now()}\n"
            f"- **approver:** {approver.strip()}\n\n"
            "## Observed behavior\n\n"
            f"{observed.strip()}\n\n"
            "## How this was produced\n\n"
            "1. Installed and authorized the `dreamina` CLI in an\n"
            "   authorized shell.\n"
            "2. Ran one low-cost image or video generation interactively.\n"
            "3. Captured the server-issued submit ID above.\n"
            "4. Ran `python3 scripts/unlock_runtime_gates.py record-canary\n"
            "   --submit-id <id> --approver <name> --observed \"<summary>\"`.\n",
            encoding="utf-8",
        )
        return target

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _cli_available(self) -> bool:
        if os.path.sep in self._cli_command:
            return Path(self._cli_command).is_file() and os.access(self._cli_command, os.X_OK)
        return shutil.which(self._cli_command) is not None

    def _resolve_cli(self) -> str:
        if not self._cli_available():
            raise CLIAbsentError(
                f"{self._cli_command} is not available on this machine; "
                "install and authorize the dreamina CLI in your own shell first. "
                "This harness will not fabricate version/help/schema output."
            )
        if os.path.sep in self._cli_command:
            return self._cli_command
        return shutil.which(self._cli_command) or self._cli_command

    def _artifacts_present(self) -> bool:
        return all(
            (self._verification_dir / name).is_file()
            for name in ("cli-version.txt", "cli-help.txt", "cli-schema.json")
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _run(argv: list[str]) -> str:
        try:
            completed = subprocess.run(  # noqa: S603 — argv-only
                argv,
                capture_output=True,
                text=True,
                timeout=30,
                shell=False,
                check=False,
            )
        except OSError as exc:
            raise CLIAbsentError(f"failed to run {argv[0]}: {exc}") from exc
        if completed.returncode != 0:
            raise UnlockError(
                f"{' '.join(argv)} failed with exit {completed.returncode}: "
                f"{completed.stderr.strip()}"
            )
        return completed.stdout.rstrip("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drive the human-performed runtime gate steps."
    )
    parser.add_argument(
        "--verification-dir",
        default="docs/verification",
        help="where to write the evidence artifacts (default: docs/verification)",
    )
    parser.add_argument("--cli-command", default="dreamina")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="report current gate state without mutating anything")
    sub.add_parser("probe", help="capture version / help / schema from the real CLI")
    canary = sub.add_parser("record-canary", help="record a real human-performed canary")
    canary.add_argument("--submit-id", required=True)
    canary.add_argument("--approver", required=True)
    canary.add_argument("--observed", required=True)

    args = parser.parse_args(argv)
    harness = UnlockHarness(
        verification_dir=Path(args.verification_dir).resolve(),
        cli_command=args.cli_command,
    )
    try:
        if args.command == "status":
            print(json.dumps(harness.status(), indent=2, sort_keys=True))
            return 0
        if args.command == "probe":
            result = harness.probe()
            print(json.dumps(result, indent=2, sort_keys=True))
            print(
                "\nNext: fill in docs/verification/account-readiness.md, run one "
                "real generation interactively, then `record-canary`."
            )
            return 0
        if args.command == "record-canary":
            target = harness.record_canary(
                submit_id=args.submit_id,
                approver=args.approver,
                observed=args.observed,
            )
            print(f"wrote {target}")
            return 0
    except CLIAbsentError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    except IncompleteCanaryRecordError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    except UnlockError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 4
    return 0


__all__ = [
    "CLIAbsentError",
    "IncompleteCanaryRecordError",
    "UnlockError",
    "UnlockHarness",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
