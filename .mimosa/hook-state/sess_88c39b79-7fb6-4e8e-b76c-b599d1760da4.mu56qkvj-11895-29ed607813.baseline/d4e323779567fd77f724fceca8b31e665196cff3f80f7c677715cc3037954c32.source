from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def project_id() -> str: return "vp_0123456789abcdef01234567"
def source_receipt() -> dict[str, Any]: return {"schema_version": "1.0", "source_sha256": "a" * 64, "duration_seconds": 12.0}
def capability_snapshot() -> dict[str, Any]: return {"cli_version": "test", "modes": ["text2video", "image2video", "frames2video", "multiframe2video", "multimodal2video"], "models": []}
def cost_basis() -> dict[str, Any]: return {"kind": "operator_ceiling", "currency": "credits", "recorded_at": "2026-09-14T00:00:00Z"}
def build_annotations(**overrides: Any) -> dict[str, Any]: return {"schema_version": "1.0", "machine_fingerprint": "b" * 64, "shots": [], **overrides}
def build_redesign(**overrides: Any) -> dict[str, Any]: return {"schema_version": "1.0", "creative_mode": "original_redesign", "machine_fingerprint": "b" * 64, "shots": [], **overrides}
def build_design_shot(**overrides: Any) -> dict[str, Any]: return {"id": "S01", "kind": "subject", "duration_seconds": 4, "references": [], **overrides}
def build_clip(shot_id: str) -> dict[str, Any]: return {"shot_id": shot_id, "path": f"/{shot_id}.mp4", "sha256": "c" * 64, "duration_seconds": 4.0}
def build_composition_plan(**overrides: Any) -> dict[str, Any]: return {"clips": [build_clip("S01")], "transitions": [], "width": 1280, "height": 720, "fps": 24, **overrides}
def fake_acceptance_dependencies() -> dict[str, Any]: return {"network": False, "native_approval": False, "paid": False, "publish": False}


def run_mcp_initialize() -> dict[str, Any]:
    message = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    result = subprocess.run(
        [sys.executable, "-m", "scripts.dreamina_mcp_server"],
        cwd=ROOT,
        input=json.dumps(message) + "\n",
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"MCP initialize failed with exit code {result.returncode}: {result.stderr}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"MCP initialize returned invalid JSON: {result.stderr}") from exc
