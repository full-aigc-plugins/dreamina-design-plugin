#!/usr/bin/env python3
"""Fake Dreamina Design adapter for offline tests.

Mirrors the public codex-dreamina-design receipt interface: discover capability,
quote, approve, submit, query, download. Behaviour is controlled by the
``force_error`` field of the JSON request:

  "force_error": "missing_capability" | "web_prereq" | "quote_mismatch" |
                 "approval_rejected" | "approval_expired" | "duplicate" |
                 "unknown" | "timeout" | "artifact_mismatch"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

STATE_DIR_NAME = ".fake_design_state"


def _state_dir(root: Path) -> Path:
    d = root / STATE_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_file(root: Path, key: str) -> Path:
    return _state_dir(root) / f"{key}.json"


def _write_state(root: Path, key: str, payload: dict) -> None:
    _state_file(root, key).write_text(json.dumps(payload))


def _read_state(root: Path, key: str) -> dict | None:
    path = _state_file(root, key)
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def cmd_capabilities(args) -> int:
    request = json.loads(Path(args.request).read_text())
    force = request.get("force_error")
    if force == "missing_capability":
        Path(args.receipt).write_text(json.dumps({"capabilities": []}))
        return 0
    if force == "web_prereq":
        Path(args.receipt).write_text(json.dumps({"error": "web_login_required"}))
        return 2
    Path(args.receipt).write_text(json.dumps({
        "capabilities": [
            {"model": "seedance-2.5", "max_resolution": "1920x1080", "max_duration_seconds": 12.0}
        ]
    }))
    return 0


def cmd_quote(args) -> int:
    request = json.loads(Path(args.request).read_text())
    force = request.get("force_error")
    inputs = request.get("quote_inputs", {})
    quote_key = request.get("quote_key", "default")
    state_root = Path(args.state_dir)

    existing = _read_state(state_root, f"quote:{quote_key}")
    if existing is not None and force != "quote_mismatch":
        Path(args.receipt).write_text(json.dumps(existing))
        return 0
    if force == "quote_mismatch":
        Path(args.receipt).write_text(json.dumps({
            "price_usd": 99.0,
            "model": inputs.get("model"),
            "resolution": inputs.get("resolution"),
            "ratio": inputs.get("ratio"),
            "duration_seconds": inputs.get("duration_seconds"),
            "mismatch_marker": "price_inflated",
        }))
        return 0
    quote = {
        "price_usd": 1.0,
        "model": inputs.get("model"),
        "resolution": inputs.get("resolution"),
        "ratio": inputs.get("ratio"),
        "duration_seconds": inputs.get("duration_seconds"),
    }
    _write_state(state_root, f"quote:{quote_key}", quote)
    Path(args.receipt).write_text(json.dumps(quote))
    return 0


def cmd_approve(args) -> int:
    request = json.loads(Path(args.request).read_text())
    force = request.get("force_error")
    if force == "approval_rejected":
        Path(args.receipt).write_text(json.dumps({"status": "rejected"}))
        return 3
    if force == "approval_expired":
        Path(args.receipt).write_text(json.dumps({"status": "expired"}))
        return 3
    Path(args.receipt).write_text(json.dumps({"status": "approved", "approval_id": request.get("approval_id", "apr_1")}))
    return 0


def cmd_submit(args) -> int:
    request = json.loads(Path(args.request).read_text())
    force = request.get("force_error")
    state_root = Path(args.state_dir)
    job_key = request.get("job_key", "default")
    prior = _read_state(state_root, f"submit:{job_key}")
    if prior is not None and force != "duplicate":
        Path(args.receipt).write_text(json.dumps(prior))
        return 0
    if force == "duplicate":
        Path(args.receipt).write_text(json.dumps({
            "status": "duplicate",
            "existing_submit_id": prior["design_submit_id"] if prior else "ds_prior",
        }))
        return 3
    if force == "unknown":
        Path(args.receipt).write_text(json.dumps({"status": "unknown"}))
        return 2
    payload = {
        "design_submit_id": f"ds_{job_key}_{int(time.time()*1000)}",
        "status": "submitted",
    }
    _write_state(state_root, f"submit:{job_key}", payload)
    Path(args.receipt).write_text(json.dumps(payload))
    return 0


def cmd_query(args) -> int:
    request = json.loads(Path(args.request).read_text())
    force = request.get("force_error")
    if force == "timeout":
        time.sleep(5)
    if force == "artifact_mismatch":
        # Write a real file at the declared path so the orchestrator can
        # independently re-hash it; declare a different sha256 to force a
        # mismatch detection.
        mismatch_path = Path(args.output) if args.output else Path("/tmp/fake_artifact_mismatch.mp4")
        mismatch_path.parent.mkdir(parents=True, exist_ok=True)
        mismatch_path.write_bytes(b"MISMATCH")
        Path(args.receipt).write_text(json.dumps({
            "status": "succeeded",
            "artifact": {"sha256": "f" * 64, "path": str(mismatch_path)},
        }))
        return 0
    if force == "missing_artifact":
        Path(args.receipt).write_text(json.dumps({"status": "succeeded"}))
        return 0
    if force == "missing_file":
        Path(args.receipt).write_text(json.dumps({
            "status": "succeeded",
            "artifact": {"sha256": "a" * 64, "path": str(Path(args.state_dir) / "missing.mp4")},
        }))
        return 0
    if force == "unknown":
        Path(args.receipt).write_text(json.dumps({"status": "unknown"}))
        return 2
    output = Path(args.output) if args.output else Path(args.state_dir) / "fake_output.mp4"
    output.write_bytes(b"FAKE_MEDIA_BYTES")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    Path(args.receipt).write_text(json.dumps({
        "status": "succeeded",
        "artifact": {"sha256": digest, "path": str(output)},
    }))
    return 0


def cmd_download(args) -> int:
    request = json.loads(Path(args.request).read_text())
    force = request.get("force_error")
    if force == "artifact_mismatch":
        Path(args.output).write_bytes(b"MISMATCH")
        return 0
    Path(args.output).write_bytes(b"FAKE_MEDIA_BYTES")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["capabilities", "quote", "approve", "submit", "query", "download"])
    parser.add_argument("--request", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--output", required=False, default="")
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args()

    dispatch = {
        "capabilities": cmd_capabilities,
        "quote": cmd_quote,
        "approve": cmd_approve,
        "submit": cmd_submit,
        "query": cmd_query,
        "download": cmd_download,
    }
    try:
        return dispatch[args.mode](args)
    except Exception as exc:  # noqa: BLE001
        print(f"fake design adapter crashed: {exc}", file=sys.stderr)
        return 99


if __name__ == "__main__":
    raise SystemExit(main())
