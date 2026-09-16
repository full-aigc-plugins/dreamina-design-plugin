"""Contract tests for the typed Dreamina Design MCP client."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "seedance_pipeline"))
sys.path.insert(0, str(ROOT / "tests" / "seedance_pipeline" / "fakes"))

from fake_mcp_tool_invoker import FakeMcpToolInvoker
from mcp_design_client import McpDesignClient, McpToolError, UserActionRequired


def _ok(payload):
    return {"structuredContent": payload, "isError": False}


class McpDesignClientTests(unittest.TestCase):
    def test_status_and_account_use_only_authoritative_tools(self):
        invoker = FakeMcpToolInvoker({
            "dreamina_cli_status": [_ok({"installed": True, "trusted": True})],
            "dreamina_account": [_ok({"credits": 200})],
        })
        client = McpDesignClient(invoker)
        self.assertEqual(client.invoke("status", {}), {"ready": True, "installed": True, "trusted": True})
        self.assertEqual(client.invoke("account", {}), {"ready": True, "credits": 200})
        self.assertEqual([name for name, _ in invoker.calls], ["dreamina_cli_status", "dreamina_account"])

    def test_quote_is_explicitly_unavailable_without_calling_a_tool(self):
        invoker = FakeMcpToolInvoker({})
        self.assertEqual(McpDesignClient(invoker).invoke("quote", {"model": "seedance2.5"}), {"quote_available": False})
        self.assertEqual(invoker.calls, [])

    def test_submit_maps_preview_to_multimodal_reference(self):
        invoker = FakeMcpToolInvoker({"dreamina_submit_video": [_ok({"submit_id": "sub-123"})]})
        client = McpDesignClient(invoker)
        result = client.invoke("submit", {
            "prompt": "orbit",
            "model": "seedance2.5",
            "resolution": "720p",
            "ratio": "16:9",
            "duration_seconds": 4,
            "preview": {"path": "/approved/preview.mp4", "sha256": "a" * 64},
            "approved_roots": ["/approved"],
        })
        self.assertEqual(result, {"submit_id": "sub-123"})
        name, arguments = invoker.calls[0]
        self.assertEqual(name, "dreamina_submit_video")
        self.assertEqual(arguments["mode"], "multimodal2video")
        self.assertEqual(arguments["video_resolution"], "720p")
        self.assertEqual(arguments["references"], [{"path": "/approved/preview.mp4", "role": "reference", "sha256": "a" * 64}])
        self.assertNotIn("resolution", arguments)
        self.assertNotIn("preview", arguments)

    def test_query_normalizes_success_and_single_downloaded_artifact(self):
        artifact = {"path": "/approved/final.mp4", "sha256": "b" * 64, "size_bytes": 12}
        invoker = FakeMcpToolInvoker({
            "dreamina_query_task": [_ok({
                "submit_id": "sub-123",
                "exit_code": 0,
                "result": {"status": "success"},
                "artifacts": [artifact],
            })]
        })
        result = McpDesignClient(invoker).invoke("query", {
            "submit_id": "sub-123", "download_dir": "/approved/out", "approved_roots": ["/approved"]
        })
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["artifact"], {"path": artifact["path"], "sha256": artifact["sha256"], "bytes": 12})

    def test_user_action_error_is_typed_and_never_flattened_to_ready(self):
        invoker = FakeMcpToolInvoker({
            "dreamina_cli_status": [{
                "structuredContent": {"error_type": "permission_error", "message": "enrol CLI", "requires_user_action": True},
                "isError": True,
            }]
        })
        with self.assertRaises(UserActionRequired):
            McpDesignClient(invoker).invoke("status", {})

    def test_non_user_error_is_typed(self):
        invoker = FakeMcpToolInvoker({
            "dreamina_account": [{"structuredContent": {"error_type": "runtime_error", "message": "failed"}, "isError": True}]
        })
        with self.assertRaises(McpToolError):
            McpDesignClient(invoker).invoke("account", {})

    def test_rejects_unrecognized_semantic_action(self):
        with self.assertRaises(ValueError):
            McpDesignClient(FakeMcpToolInvoker({})).invoke("capabilities", {})

    def test_public_typed_methods_are_exposed(self):
        client = McpDesignClient(FakeMcpToolInvoker({}))
        for method in ("status", "account", "submit_video", "query_task"):
            self.assertTrue(callable(getattr(client, method, None)), method)

    def test_malformed_envelope_and_unknown_submit_fields_fail_closed(self):
        client = McpDesignClient(FakeMcpToolInvoker({"dreamina_cli_status": [{"isError": False}]}))
        with self.assertRaises(McpToolError):
            client.status()
        with self.assertRaises(ValueError):
            McpDesignClient(FakeMcpToolInvoker({})).submit_video({"unknown": True})

    def test_blend_scene_is_never_accepted_as_the_preview_reference(self):
        request = {
            "prompt": "orbit", "model": "seedance2.5", "resolution": "720p",
            "ratio": "16:9", "duration_seconds": 4,
            "preview": {"path": "/approved/scene.blend", "sha256": "a" * 64},
            "approved_roots": ["/approved"],
        }
        with self.assertRaises(ValueError):
            McpDesignClient(FakeMcpToolInvoker({})).submit_video(request)


if __name__ == "__main__":
    unittest.main()
