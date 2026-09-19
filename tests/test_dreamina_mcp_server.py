from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from scripts.dreamina_adapter import DreaminaAdapterError, DreaminaResult
from scripts.dreamina_mcp_server import DreaminaMcpTools, _handle, _tool_definitions
from scripts.native_approval import ApprovalDeniedError
from scripts.trusted_cli import TrustedCliError
from tests.video_project_fixtures import run_mcp_initialize


ROOT = Path(__file__).resolve().parents[1]


class McpConfigurationTests(unittest.TestCase):
    def test_paid_tools_require_dreamina_product_prompt(self) -> None:
        config = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        server = config["mcpServers"]["dreamina_design"]
        self.assertEqual(server["default_tools_approval_mode"], "prompt")
        self.assertEqual(server["tools"]["dreamina_capability_snapshot"]["approval_mode"], "approve")
        self.assertEqual(server["tools"]["dreamina_submit_image"]["approval_mode"], "prompt")
        self.assertEqual(server["tools"]["dreamina_submit_video"]["approval_mode"], "prompt")
        self.assertEqual(len(server["tools"]), 21)
        # The ten additive project tools are registered with the same
        # contract: only the read-only quote is pre-approved.
        self.assertEqual(server["tools"]["dreamina_quote_video_batch"]["approval_mode"], "approve")
        for name in (
            "dreamina_video_project",
            "dreamina_analyze_reference_video",
            "dreamina_validate_shot_analysis",
            "dreamina_create_redesign",
            "dreamina_approve_video_batch",
            "dreamina_execute_video_batch",
            "dreamina_evaluate_video_batch",
            "dreamina_compose_video",
            "dreamina_export_video_project",
        ):
            self.assertEqual(server["tools"][name]["approval_mode"], "prompt", name)
        manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")

    def test_paid_tool_annotations_are_destructive_and_non_idempotent(self) -> None:
        tools = {tool["name"]: tool for tool in _tool_definitions()}
        for tool in tools.values():
            properties = tool["inputSchema"].get("properties", {})
            self.assertNotIn("cli_path", properties)
            self.assertNotIn("cli_sha256", properties)
        for name in ("dreamina_submit_image", "dreamina_submit_video"):
            annotations = tools[name]["annotations"]
            self.assertFalse(annotations["readOnlyHint"])
            self.assertTrue(annotations["destructiveHint"])
            self.assertFalse(annotations["idempotentHint"])


    def test_handlers_reject_unknown_properties(self) -> None:
        with self.assertRaises(ValueError):
            DreaminaMcpTools().call("dreamina_account", {"shell": "echo unsafe"})

    def test_auth_schema_exposes_flow_id_not_device_code(self) -> None:
        tool = {item["name"]: item for item in _tool_definitions()}["dreamina_auth"]
        self.assertIn("flow_id", tool["inputSchema"]["properties"])
        self.assertNotIn("device_code", tool["inputSchema"]["properties"])


class McpStdioTests(unittest.TestCase):
    def test_initialize_preserves_released_server_version(self) -> None:
        response = run_mcp_initialize()
        self.assertEqual(response["result"]["serverInfo"]["version"], "0.4.0")

    def test_query_adapter_failure_is_marked_retryable(self) -> None:
        class BrokenTools:
            def call(self, name, args):
                raise DreaminaAdapterError("get_history_by_ids failed: ret=1015")
        response = _handle({"id": 1, "method": "tools/call", "params": {"name": "dreamina_query_task", "arguments": {"submit_id": "x"}}}, BrokenTools())
        error = response["result"]["structuredContent"]
        self.assertTrue(error["retryable"])
        self.assertEqual(error["next_action"], "query_same_submit_id")
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
        from scripts.video_project_mcp import PROJECT_TOOL_NAMES

        legacy = {"dreamina_capability_snapshot", "dreamina_cli_status",
                  "dreamina_cli_install_or_upgrade", "dreamina_auth", "dreamina_account",
                  "dreamina_submit_image", "dreamina_submit_video", "dreamina_query_task",
                  "dreamina_list_tasks", "dreamina_session", "dreamina_diagnose"}
        self.assertEqual(names, legacy | set(PROJECT_TOOL_NAMES))

    def test_tool_errors_are_structured_for_automation(self) -> None:
        message={"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"dreamina_account","arguments":{"unknown":True}}}
        result=subprocess.run([sys.executable,"-m","scripts.dreamina_mcp_server"],cwd=ROOT,input=json.dumps(message)+"\n",capture_output=True,text=True,check=False)
        payload=json.loads(result.stdout)["result"]
        self.assertTrue(payload["isError"])
        self.assertEqual(set(payload["structuredContent"]),{"error_type","message","retryable","requires_user_action","next_action"})


class PaidToolHandlerTests(unittest.TestCase):
    class _Approve:
        def confirm(self, request):
            return "test-native-user"

    def test_capability_tool_defaults_to_compact_summary(self) -> None:
        class _Adapter:
            def capability_snapshot(self):
                return {"cli_version": "1.2.3", "cli_commit": "abc", "captured_at": "now", "modes": ["text2image"], "models": [{"name": "m"}], "large": "x" * 10000}

        class _Context:
            def __enter__(self): return _Adapter()
            def __exit__(self, *_): return None

        with mock.patch("scripts.dreamina_mcp_server._adapter", return_value=_Context()):
            result = DreaminaMcpTools().call("dreamina_capability_snapshot", {})
        self.assertEqual(result["model_count"], 1)
        self.assertNotIn("large", result)

    def test_status_returns_actionable_result_when_cli_not_enrolled(self) -> None:
        with mock.patch("scripts.dreamina_mcp_server._adapter", side_effect=TrustedCliError("not enrolled")):
            result=DreaminaMcpTools().call("dreamina_cli_status", {})
        self.assertEqual(result["installed"], False)
        self.assertEqual(result["requires_user_action"], "install_or_enroll")

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
            result = DreaminaMcpTools(state_root=Path(tmp), approval_provider=self._Approve()).call(
                "dreamina_submit_image",
                {"mode": "text2image", "prompt": "x", "model": "5.0Pro", "resolution_type": "1.5k", "count": 1, "ratio": "1:1"},
            )
            self.assertEqual(result["submit_id"], "mcp-sub-1")
            receipt_files = [path for path in (Path(tmp) / "approvals").rglob("*.json") if path.parent.name == "approvals"]
            receipts = [json.loads(path.read_text()) for path in receipt_files]
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0]["approver"], "test-native-user")
            self.assertIsNotNone(receipts[0]["consumed_at"])

    def test_denied_native_approval_never_invokes_generation(self) -> None:
        snapshot = {"modes": ["text2image"], "models": [{"name": "5.0Pro", "modes": ["text2image"], "resolutions": ["1.5k"], "ratios": ["1:1"], "max_count": 1}], "resolutions": {"image": ["1.5k"]}, "ratios": ["1:1"]}

        class _Adapter:
            generation_calls = 0
            def capability_snapshot(self): return snapshot
            def run(self, args):
                self.generation_calls += 1
                raise AssertionError("generation must not run after denial")

        adapter = _Adapter()

        class _Context:
            def __enter__(self): return adapter
            def __exit__(self, *_): return None

        class _Deny:
            def confirm(self, request): raise ApprovalDeniedError("denied")

        with tempfile.TemporaryDirectory() as tmp, mock.patch("scripts.dreamina_mcp_server._adapter", return_value=_Context()):
            with self.assertRaises(ApprovalDeniedError):
                DreaminaMcpTools(state_root=Path(tmp), approval_provider=_Deny()).call(
                    "dreamina_submit_image",
                    {"mode": "text2image", "prompt": "x", "model": "5.0Pro", "resolution_type": "1.5k", "count": 1, "ratio": "1:1"},
                )
        self.assertEqual(adapter.generation_calls, 0)


if __name__ == "__main__":
    unittest.main()
