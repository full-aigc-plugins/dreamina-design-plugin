from __future__ import annotations

import copy
import unittest

from scripts.json_contracts import canonical_fingerprint
from scripts.reelbench_contracts import (
    REELBENCH_VALIDATE_GATES,
    validate_reelbench_comparison,
    validate_reelbench_evidence,
)


def evidence(action: str = "validate") -> dict[str, object]:
    gates = [
        {"name": name, "status": "PASS", "evidence": "measured"}
        for name in REELBENCH_VALIDATE_GATES
    ] if action == "validate" else []
    receipt: dict[str, object] = {
        "schema_version": "1.0", "version": "v001", "parent_version": None,
        "project_id": "vp_" + "1" * 24, "source_receipt_version": "v001",
        "source_sha256": "2" * 64, "action": action,
        "upstream": {"source": "https://github.com/eternityspring/reelbench-skills.git", "revision": "18f2f63987337df0975a89973d38d50f3231ee31", "lock_fingerprint": "3" * 64},
        "tool_identities": [{"kind": "node", "source_path": "/trusted/node", "owner_uid": 501, "mode": 493, "device": 1, "inode": 2, "size_bytes": 3, "sha256": "4" * 64}],
        "argv_fingerprint": "5" * 64, "gates": gates,
        "artifacts": [{"path": "reelbench/v001/report.md", "size_bytes": 1, "mime_type": "text/markdown", "sha256": "6" * 64}],
        "created_at": "2026-09-15T00:00:00Z", "committed_at": "2026-09-15T00:00:01Z",
    }
    receipt["evidence_fingerprint"] = canonical_fingerprint(receipt)
    return receipt


def comparison() -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema_version": "1.0", "version": "v001", "project_id": "vp_" + "1" * 24,
        "source_sha256": "2" * 64, "native_analysis_version": "v001",
        "native_analysis_fingerprint": "3" * 64, "reelbench_evidence_version": "v002",
        "reelbench_evidence_fingerprint": "4" * 64,
        "tolerances": {"duration_seconds": 0.1, "boundary_seconds": 0.04, "motion_abs_delta": 0.5},
        "domains": {name: {"verdict": "matched", "reasons": []} for name in ("source_identity", "duration", "timeline_continuity", "shot_count", "boundaries", "motion")},
        "overall": "matched", "compared_at": "2026-09-15T00:00:00Z",
    }
    receipt["comparison_fingerprint"] = canonical_fingerprint(receipt)
    return receipt


class ReelBenchContractTests(unittest.TestCase):
    def test_evidence_requires_exact_fingerprint_and_validate_gate_set(self) -> None:
        receipt = evidence()
        validate_reelbench_evidence(receipt)
        duplicate = copy.deepcopy(receipt)
        duplicate["gates"].append({"name": "timeline", "status": "FAIL", "evidence": "duplicate"})
        duplicate["evidence_fingerprint"] = canonical_fingerprint({key: value for key, value in duplicate.items() if key != "evidence_fingerprint"})
        with self.assertRaises(ValueError):
            validate_reelbench_evidence(duplicate)
        missing = evidence(); missing["gates"].pop(); missing["evidence_fingerprint"] = canonical_fingerprint({key: value for key, value in missing.items() if key != "evidence_fingerprint"})
        with self.assertRaises(ValueError):
            validate_reelbench_evidence(missing)

    def test_evidence_rejects_noncanonical_timestamp_path_and_fingerprint(self) -> None:
        receipt = evidence("seed")
        validate_reelbench_evidence(receipt)
        for key, value, error in (
            ("created_at", "2026-09-15", "timestamp"),
            ("evidence_fingerprint", "0" * 64, "fingerprint"),
        ):
            candidate = copy.deepcopy(receipt); candidate[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_reelbench_evidence(candidate)
        path_escape = copy.deepcopy(receipt); path_escape["artifacts"][0]["path"] = "../report.md"; path_escape["evidence_fingerprint"] = canonical_fingerprint({key: value for key, value in path_escape.items() if key != "evidence_fingerprint"})
        with self.assertRaises(ValueError):
            validate_reelbench_evidence(path_escape)

    def test_comparison_forces_overall_to_match_domain_verdicts_and_fingerprint(self) -> None:
        receipt = comparison()
        validate_reelbench_comparison(receipt)
        contradiction = copy.deepcopy(receipt); contradiction["domains"]["motion"]["verdict"] = "manual_review"; contradiction["comparison_fingerprint"] = canonical_fingerprint({key: value for key, value in contradiction.items() if key != "comparison_fingerprint"})
        with self.assertRaisesRegex(ValueError, "overall"):
            validate_reelbench_comparison(contradiction)
        contradiction["overall"] = "manual_review"; contradiction["comparison_fingerprint"] = canonical_fingerprint({key: value for key, value in contradiction.items() if key != "comparison_fingerprint"})
        validate_reelbench_comparison(contradiction)
