from __future__ import annotations

import copy
import hashlib
from pathlib import Path
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
        "schema_version": "1.1", "version": "v001", "parent_version": None, "parent_fingerprint": None,
        "project_id": "vp_" + "1" * 24, "source_receipt_version": "v001",
        "source_sha256": "2" * 64, "action": action,
        "upstream": {"source": "https://github.com/eternityspring/reelbench-skills.git", "revision": "18f2f63987337df0975a89973d38d50f3231ee31", "lock_fingerprint": "3" * 64},
        "tool_identities": [{"kind": "node", "source_path": "/trusted/node", "owner_uid": 501, "mode": 493, "device": 1, "inode": 2, "size_bytes": 3, "sha256": "4" * 64}],
        "argv_fingerprint": "5" * 64, "gates": gates,
        "artifacts": [{"path": "reelbench/v001/report.md", "size_bytes": 1, "mime_type": "text/markdown", "sha256": "6" * 64}],
        "created_at": "2026-09-15T00:00:00Z", "committed_at": "2026-09-15T00:00:01Z",
    }
    if action != "seed":
        receipt.update(version="v003", parent_version="v002", parent_fingerprint="9" * 64)
    receipt["shots"] = {"sha256": "6" * 64, "ids": ["S01"]}
    scripts = Path(__file__).resolve().parents[1] / "skills/dreamina-video-shots/scripts"
    receipt["script_manifest"] = [{"path": "script/" + name, "size_bytes": (scripts / name).stat().st_size,
                                   "sha256": hashlib.sha256((scripts / name).read_bytes()).hexdigest()}
                                  for name in ("video-shots.mjs", "report.css", "report.js")]
    arguments = {
        "seed": ["source/" + "2" * 64, "--threshold", "0.30", "--min", "0.30", "--track", "output/track.json", "--title", "Reference"],
        "validate": ["inputs/shots.json", "--track", "inputs/track.json", "--frames", "inputs/frames", "--lang", "en"],
    }.get(action, [])
    if action in {"seed", "evidence", "validate", "render"}:
        template = receipt["tool_identities"][0]
        receipt["tool_identities"] = [{**template, "kind": kind, "mode": 0o500,
            "source_path": "tools/node" if kind == "node" else f"tools/bin/{kind}"} for kind in ("node", "ffmpeg", "ffprobe")]
    receipt["commands"] = [{"action": action, "argv": ["tools/node", "script/video-shots.mjs", action, *arguments],
                            "returncode": 0, "tool_identities": receipt["tool_identities"],
                            "script_manifest": receipt["script_manifest"]}]
    if action == "seed":
        receipt["artifacts"] = [{"path": f"reelbench/v001/{name}", "size_bytes": 1,
                                "mime_type": "application/json", "sha256": "6" * 64} for name in ("shots.json", "track.json")]
    if action == "validate":
        receipt["artifacts"] = []
    receipt["consumed_artifacts"] = [{"version": "v001", "receipt_fingerprint": "7" * 64,
        "path": "source/source.mp4", "workspace_path": "source/" + "2" * 64,
        "sha256": "2" * 64, "size_bytes": 1}]
    if action in {"evidence", "validate", "render"}:
        receipt["consumed_artifacts"] += [{"version": "v001", "receipt_fingerprint": "8" * 64,
            "path": f"reelbench/v001/{name}", "workspace_path": "inputs/" + name,
            "sha256": "6" * 64, "size_bytes": 1} for name in ("shots.json", "track.json")]
    if action in {"validate", "render"}:
        receipt["consumed_artifacts"] += [{"version": "v002", "receipt_fingerprint": "9" * 64,
            "path": f"reelbench/v002/frames/S01{pick}.jpg", "workspace_path": f"inputs/frames/S01{pick}.jpg",
            "sha256": "6" * 64, "size_bytes": 1} for pick in ("a", "b")]
    receipt["argv_fingerprint"] = canonical_fingerprint({"commands": receipt["commands"]})
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
    def test_resigned_command_path_tampering_is_rejected(self):
        receipt = evidence("seed")
        receipt["commands"][0]["argv"][3] = "/etc/passwd"
        receipt["argv_fingerprint"] = canonical_fingerprint({"commands": receipt["commands"]})
        with self.assertRaises(ValueError):
            self.validate_signed(receipt)

    def test_resigned_consumed_source_path_must_match_digest(self):
        receipt = evidence("seed")
        receipt["consumed_artifacts"][0]["workspace_path"] = "source/wrong"
        with self.assertRaises(ValueError):
            self.validate_signed(receipt)

    def test_seed_without_track_artifact_is_not_complete(self):
        receipt = evidence("seed")
        receipt["artifacts"] = [a for a in receipt["artifacts"] if not a["path"].endswith("track.json")]
        with self.assertRaises(ValueError):
            self.validate_signed(receipt)

    def test_shots_receipt_missing_ffprobe_identity_is_rejected(self):
        receipt = evidence("seed")
        receipt["tool_identities"] = [item for item in receipt["tool_identities"] if item["kind"] != "ffprobe"]
        receipt["commands"][0]["tool_identities"] = receipt["tool_identities"]
        receipt["argv_fingerprint"] = canonical_fingerprint({"commands": receipt["commands"]})
        with self.assertRaises(ValueError):
            self.validate_signed(receipt)
    @staticmethod
    def sync_receipt():
        receipt = evidence("verify")
        receipt["gates"] = [{"name": name, "status": "PASS", "evidence": "observed"} for name in
                            ("duration", "dimensions", "codec", "audio_policy", "cut_alignment", "sampled_correspondence")]
        return receipt

    @staticmethod
    def validate_signed(receipt):
        receipt["evidence_fingerprint"] = canonical_fingerprint({k: v for k, v in receipt.items() if k != "evidence_fingerprint"})
        validate_reelbench_evidence(receipt)

    def test_sync_exact_six_set_passes(self):
        self.validate_signed(self.sync_receipt())

    def test_sync_missing_gate_rejected(self):
        for index in range(6):
            receipt = self.sync_receipt(); receipt["gates"].pop(index)
            with self.subTest(index=index), self.assertRaises(ValueError): self.validate_signed(receipt)

    def test_sync_extra_gate_rejected(self):
        receipt = self.sync_receipt(); receipt["gates"].append({"name": "frames", "status": "PASS", "evidence": "observed"})
        with self.assertRaises(ValueError): self.validate_signed(receipt)

    def test_sync_duplicate_gate_rejected(self):
        receipt = self.sync_receipt(); receipt["gates"].append(receipt["gates"][0].copy())
        with self.assertRaises(ValueError): self.validate_signed(receipt)

    def test_sync_skipped_each_gate_rejected(self):
        for index in range(6):
            receipt = self.sync_receipt(); receipt["gates"][index]["status"] = "SKIPPED"
            with self.subTest(index=index), self.assertRaises(ValueError): self.validate_signed(receipt)

    def test_sync_old_sampled_gate_alias_rejected(self):
        receipt = self.sync_receipt(); receipt["gates"][-1]["name"] = "sampled_alignment"
        with self.assertRaises(ValueError): self.validate_signed(receipt)

    def test_sync_fail_is_evidence_not_skipped(self):
        receipt = self.sync_receipt(); receipt["gates"][-1]["status"] = "FAIL"
        self.validate_signed(receipt)

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
