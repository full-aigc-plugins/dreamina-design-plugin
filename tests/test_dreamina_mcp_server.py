from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from scripts.dreamina_adapter import DreaminaResult
from scripts.dreamina_mcp_server import DreaminaMcpTools, _tool_definitions


ROOT = Path(__file__).resolve().parents[1]


class McpConfigurationTests(unittest.TestCase):
    def test_paid_tools_require_codex_product_prompt(self) -> None:
        config = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        server = config["mcpServers"]["dreamina_design"]
        self.assertEqual(server["default_tools_approval_mode"], "prompt")
        self.assertEqual(server["tools"]["dreamina_capability_snapshot"]["approval_mode"], "approve")
        self.assertEqual(server["tools"]["dreamina_submit_image"]["approval_mode"], "prompt")
        self.assertEqual(server["tools"]["dreamina_submit_video"]["approval_mode"], "prompt")
        manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")

    def test_paid_tool_annotations_are_destructive_and_non_idempotent(self) -> None:
        tools = {tool["name"]: tool for tool in _tool_definitions()}
        for name in ("dreamina_submit_image", "dreamina_submit_video"):
            annotations = tools[name]["annotations"]
            self.assertFalse(annotations["readOnlyHint"])
            self.assertTrue(annotations["destructiveHint"])
            self.assertFalse(annotations["idempotentHint"])


class McpStdioTests(unittest.TestCase):
    def test_initialize_and_tools_list_json_rpc(self) -> None:
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        result = subprocess.run(
            [sys.executable, "-m", "scripts.dreamina_mcp_server"],
            cwd=ROOT,
            input="".join(json.dumps(message) + "\n" for message in messages),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-06-18")
        names = {tool["name"] for tool in responses[1]["result"]["tools"]}
        self.assertEqual(names, {"dreamina_capability_snapshot", "dreamina_submit_image", "dreamina_submit_video"})


class PaidToolHandlerTests(unittest.TestCase):
    def test_capability_tool_defaults_to_compact_summary(self) -> None:
        class _Adapter:
            def capability_snapshot(self):
                return {"cli_version": "1.2.3", "cli_commit": "abc", "captured_at": "now", "modes": ["text2image"], "models": [{"name": "m"}], "large": "x" * 10000}

        class _Context:
            def __enter__(self): return _Adapter()
            def __exit__(self, *_): return None

        with mock.patch("scripts.dreamina_mcp_server._adapter", return_value=_Context()):
            result = DreaminaMcpTools().call("dreamina_capability_snapshot", {"cli_path": "/x", "cli_sha256": "a" * 64})
        self.assertEqual(result["model_count"], 1)
        self.assertNotIn("large", result)

    def test_image_tool_derives_and_consumes_approval_inside_handler(self) -> None:
        snapshot = {
            "modes": ["text2image"],
            "models": [{"name": "5.0Pro", "modes": ["text2image"], "resolutions": ["1.5k"], "ratios": ["1:1"], "max_count": 10}],
            "resolutions": {"image": ["1.5k"]},
            "ratios": ["1:1"],
        }

        class _Adapter:
            def capability_snapshot(self): return snapshot
            def run(self, args):
                return DreaminaResult(exit_code=0, payload={"submit_id": "mcp-sub-1"}, error_code=None, submit_id="mcp-sub-1", stderr="")

        class _Context:
            def __enter__(self): return _Adapter()
            def __exit__(self, *_): return None

        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "scripts.dreamina_mcp_server._adapter", return_value=_Context()
        ):
            result = DreaminaMcpTools(state_root=Path(tmp)).call(
                "dreamina_submit_image",
                {"cli_path": "/trusted/dreamina", "cli_sha256": "a" * 64, "mode": "text2image", "prompt": "x", "model": "5.0Pro", "resolution_type": "1.5k", "count": 1, "ratio": "1:1"},
            )
            self.assertEqual(result["submit_id"], "mcp-sub-1")
            receipt_files = [path for path in (Path(tmp) / "approvals").rglob("*.json") if path.parent.name == "approvals"]
            receipts = [json.loads(path.read_text()) for path in receipt_files]
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0]["approver"], "codex-product-approved-mcp-tool")
            self.assertIsNotNone(receipts[0]["consumed_at"])


if __name__ == "__main__":
    unittest.main()
