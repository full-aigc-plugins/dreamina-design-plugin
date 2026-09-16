#!/usr/bin/env python3
"""Fake Maya adapter — mirrors the fake Blender adapter but tags receipts as
codex-maya and defaults preview_mode to 'local_video'.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    if args.status:
        status_path = Path(args.receipt).with_suffix(".status.json")
        if status_path.is_file():
            sys.stdout.write(status_path.read_text())
            return 0
        sys.stdout.write(json.dumps({"status": "unknown"}))
        return 0

    request = json.loads(Path(args.request).read_text())
    if args.inspect:
        Path(args.receipt).write_text(json.dumps({"status": "inspect_ok", "scene": request.get("scene")}))
        return 0

    force = request.get("force_error")
    if force == "adapter":
        print("maya adapter exploded", file=sys.stderr)
        return 2
    if force == "timeout":
        time.sleep(5)

    restore_status = request.get("restore_status", "confirmed")
    out_bytes = request.get("output_bytes", 2048)
    payload = bytes(out_bytes)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest()

    receipt = {
        "schema_version": "1.0.0",
        "producer_plugin": "codex-maya",
        "producer_version": "0.1.0",
        "artifact_id": request.get("artifact_id", "maya_fake"),
        "path": str(output_path),
        "sha256": sha,
        "codec": "h264",
        "container": "mp4",
        "dimensions": {"width": 1920, "height": 1080},
        "fps": 30.0,
        "duration_seconds": 3.0,
        "bytes": len(payload),
        "camera": {"name": request.get("camera_name", "perspShape")},
        "frame_range": {"start": 1, "end": 90},
        "preview_mode": "local_video",
        "restoration": {"status": restore_status, "evidence": "fake_playblast_restored"},
    }
    Path(args.receipt).write_text(json.dumps(receipt))

    if force == "stale":
        output_path.write_bytes(payload + b"STALE")
    if force == "unsupported":
        Path(args.receipt).write_text(json.dumps({"error": "unsupported_codec"}))
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())