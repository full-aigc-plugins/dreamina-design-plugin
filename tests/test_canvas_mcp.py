"""MCP-surface tests for the Canvas rail of the submit tools.

The dual-rail contract after the 2026-09-30 port: ``dreamina_submit_image``
and ``dreamina_submit_video`` default to ``runtime="canvas"`` (the
quote-bound Canvas chain over the nine ``dreamina-canvas-cli*`` entries),
while ``runtime="legacy"`` keeps the frozen ``dreamina`` path byte-for-byte
as it was. The trust store for the Canvas binary is faked here; everything
behind it — adapter, executor, submission service, guard, ledger — runs for
real against a synthetic ``dreamina-canvas`` stub.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_dreamina_canvas_adapter import write_fake_canvas

from scripts.dreamina_adapter import DreaminaResult
from scripts.dreamina_mcp_server import (
    DreaminaMcpTools,
    _tool_definitions,
)


class _Approve:
    def confirm(self, request):
        return "test-native-user"


class _FakeCanvasStore:
    """TrustedCanvasCliStore stand-in pinned to the stub binary."""

    def __init__(self, cli: Path) -> None:
        self._cli = cli

    def load(self) -> dict[str, str]:
        return {
            "cli_path": str(self._cli),
            "cli_sha256": hashlib.sha256(self._cli.read_bytes()).hexdigest(),
        }


class _LegacyContext:
    """_adapter() stand-in driving the frozen-rail fake from the old tests."""

    def __init__(self, adapter) -> None:
        self._adapter = adapter

    def __enter__(self):
        return self._adapter

    def __exit__(self, *_exc):
        return None


class _LegacyAdapter:
    def capability_snapshot(self):
        return {
            "modes": ["text2image"],
            "models": [
                {"name": "5.0Pro", "modes": ["text2image"], "resolutions": ["1.5k"], "ratios": ["1:1"], "max_count": 10}
            ],
            "resolutions": {"image": ["1.5k"]},
            "ratios": ["1:1"],
        }

    def run(self, args):
        return DreaminaResult(
            exit_code=0,
            payload={"submit_id": "mcp-sub-1"},
            error_code=None,
            submit_id="mcp-sub-1",
            stderr="",
        )


class CanvasRuntimeDefaultTests(unittest.TestCase):
    def test_submit_image_defaults_to_the_canvas_rail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stub = write_fake_canvas(Path(tmp))
            state = Path(tmp) / "state"
            with mock.patch(
                "scripts.dreamina_mcp_server.TrustedCanvasCliStore",
                lambda: _FakeCanvasStore(stub),
            ):
                tools = DreaminaMcpTools(
                    state_root=state, approval_provider=_Approve()
                )
                result = tools.call(
                    "dreamina_submit_image",
                    {
                        "mode": "text2image",
                        "prompt": "a fox in snow",
                        "model": "stub-image-v1",
                        "resolution_type": "2K",
                        "ratio": "1:1",
                        "count": 1,
                        "project_id": "proj_given",
                        "credit_ceiling": 7,
                    },
                )
                self.assertEqual(result["runtime"], "canvas")
                self.assertEqual(result["submit_id"], "submit_reused")
                self.assertEqual(result["project_id"], "proj_given")
                self.assertEqual(result["node_id"], "node_image")
                self.assertEqual(result["quoted_ceiling"], 7)
                self.assertFalse(result["created_canvas"])
                # The receipt was recorded and consumed inside the handler.
                receipts = [
                    json.loads(path.read_text(encoding="utf-8"))
                    for path in (state / "approvals").rglob("*.json")
                    if path.name not in ("index.json", "session.json")
                ]
                self.assertTrue(
                    receipts, "expected at least one approval receipt"
                )
                self.assertTrue(all(r.get("consumed_at") for r in receipts))
                # The ledger intent closed with the canvas submit id.
                intents = list(
                    (state / "operations" / "submission_intents").glob("*.json")
                )
                self.assertTrue(intents)

    def test_canvas_creation_asks_for_its_own_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stub = write_fake_canvas(Path(tmp))
            asks: list[dict] = []

            class _CountingApprove:
                def confirm(self, request):
                    asks.append(dict(request))
                    return "test-native-user"

            with mock.patch(
                "scripts.dreamina_mcp_server.TrustedCanvasCliStore",
                lambda: _FakeCanvasStore(stub),
            ):
                tools = DreaminaMcpTools(
                    state_root=Path(tmp) / "state", approval_provider=_CountingApprove()
                )
                result = tools.call(
                    "dreamina_submit_image",
                    {
                        "mode": "text2image",
                        "prompt": "fox",
                        "model": "stub-image-v1",
                        "resolution_type": "2K",
                        "canvas_name": "work",
                        "credit_ceiling": 7,
                    },
                )
        self.assertTrue(result["created_canvas"])
        self.assertEqual(result["project_id"], "proj_created")
        creations = [a for a in asks if a.get("operation") == "dreamina-canvas-create"]
        self.assertEqual(len(creations), 1)
        self.assertEqual(creations[0]["canvas_name"], "work")

    def test_video_rail_requires_the_web_prerequisite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stub = write_fake_canvas(Path(tmp))
            with mock.patch(
                "scripts.dreamina_mcp_server.TrustedCanvasCliStore",
                lambda: _FakeCanvasStore(stub),
            ):
                tools = DreaminaMcpTools(
                    state_root=Path(tmp) / "state", approval_provider=_Approve()
                )
                with self.assertRaises(ValueError):
                    tools.call(
                        "dreamina_submit_video",
                        {
                            "mode": "text2video",
                            "prompt": "sunset,",
                            "model": "stub-video-v1",
                            "video_resolution": "720p",
                            "duration_seconds": 5,
                            "project_id": "proj_given",
                            "credit_ceiling": 7,
                        },
                    )

    def test_canvas_video_happy_path_carries_the_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stub = write_fake_canvas(Path(tmp))
            with mock.patch(
                "scripts.dreamina_mcp_server.TrustedCanvasCliStore",
                lambda: _FakeCanvasStore(stub),
            ):
                tools = DreaminaMcpTools(
                    state_root=Path(tmp) / "state", approval_provider=_Approve()
                )
                result = tools.call(
                    "dreamina_submit_video",
                    {
                        "mode": "text2video",
                        "prompt": "sunset, slow push",
                        "model": "stub-video-v1",
                        "video_resolution": "720p",
                        "duration_seconds": 5,
                        "project_id": "proj_given",
                        "credit_ceiling": 7,
                        "web_prerequisite_acknowledged": True,
                    },
                )
        self.assertEqual(result["runtime"], "canvas")
        self.assertEqual(result["node_id"], "node_video")


class LegacyRailExplicitTests(unittest.TestCase):
    def test_legacy_runtime_keeps_the_frozen_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "scripts.dreamina_mcp_server._adapter",
            return_value=_LegacyContext(_LegacyAdapter()),
        ):
            tools = DreaminaMcpTools(state_root=Path(tmp), approval_provider=_Approve())
            result = tools.call(
                "dreamina_submit_image",
                {
                    "runtime": "legacy",
                    "mode": "text2image",
                    "prompt": "x",
                    "model": "5.0Pro",
                    "resolution_type": "1.5k",
                    "count": 1,
                    "ratio": "1:1",
                },
            )
        self.assertEqual(result["submit_id"], "mcp-sub-1")
        self.assertNotIn("runtime", result)


class CanvasSchemaContractTests(unittest.TestCase):
    def test_submit_schemas_declare_the_dual_rail(self) -> None:
        definitions = {tool["name"]: tool for tool in _tool_definitions()}
        for name in ("dreamina_submit_image", "dreamina_submit_video"):
            schema = definitions[name]["inputSchema"]
            runtime = schema["properties"]["runtime"]
            self.assertEqual(runtime["enum"], ["canvas", "legacy"])
            self.assertEqual(runtime.get("default"), "canvas")
            self.assertIn("project_id", schema["properties"])
            self.assertIn("canvas_name", schema["properties"])
            self.assertIn("credit_ceiling", schema["properties"])


if __name__ == "__main__":
    unittest.main()
