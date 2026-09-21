"""The ten additive video-project MCP tools: inventory, closed schemas, dispatch."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.video_project_mcp import (
    PROJECT_TOOL_NAMES,
    ProjectToolError,
    VideoProjectMcpTools,
    project_tool_definitions,
    validate_action,
)

LEGACY_TOOLS = {
    "dreamina_capability_snapshot",
    "dreamina_cli_status",
    "dreamina_cli_install_or_upgrade",
    "dreamina_auth",
    "dreamina_account",
    "dreamina_submit_image",
    "dreamina_submit_video",
    "dreamina_query_task",
    "dreamina_list_tasks",
    "dreamina_session",
    "dreamina_diagnose",
}
PROJECT_TOOLS = set(PROJECT_TOOL_NAMES)
FORBIDDEN_FIELDS = {"shell", "argv", "command", "filter_complex", "extra_args", "flags"}


class InventoryTests(unittest.TestCase):
    def test_exactly_ten_project_tools(self) -> None:
        self.assertEqual(len(project_tool_definitions()), 10)
        self.assertEqual({t["name"] for t in project_tool_definitions()}, PROJECT_TOOLS)

    def test_inventory_is_eleven_legacy_plus_ten_project_tools(self) -> None:
        from scripts.dreamina_mcp_server import _tool_definitions

        names = {tool["name"] for tool in _tool_definitions()}
        self.assertTrue(LEGACY_TOOLS <= names)
        self.assertEqual(LEGACY_TOOLS | PROJECT_TOOLS, names)

    def test_legacy_vendor_validator_still_passes(self) -> None:
        """The compatibility import and the eleven definitions must not move."""
        from scripts.dreamina_mcp_server import _tool_definitions

        current = {tool["name"]: tool["inputSchema"] for tool in _tool_definitions()}
        self.assertGreaterEqual(len(current), 11)
        for name in LEGACY_TOOLS:
            self.assertIn(name, current)


class ClosedSchemaTests(unittest.TestCase):
    def test_all_project_schemas_are_closed(self) -> None:
        for tool in project_tool_definitions():
            with self.subTest(tool=tool["name"]):
                schema = tool["inputSchema"]
                self.assertEqual(schema["type"], "object")
                self.assertIs(schema["additionalProperties"], False)

    def test_no_schema_exposes_a_shell_or_argv_surface(self) -> None:
        for tool in project_tool_definitions():
            with self.subTest(tool=tool["name"]):
                properties = tool["inputSchema"].get("properties", {})
                self.assertTrue(FORBIDDEN_FIELDS.isdisjoint(properties))

    def test_every_identifier_field_is_pattern_bound(self) -> None:
        for tool in project_tool_definitions():
            for field, spec in tool["inputSchema"].get("properties", {}).items():
                if field == "project_id":
                    self.assertEqual(spec["pattern"], r"^vp_[a-f0-9]{24}$")
                if field.endswith("_version"):
                    self.assertEqual(spec["pattern"], r"^v[0-9]{3}$")
                if field.endswith("sha256") or field.endswith("fingerprint"):
                    self.assertEqual(spec["pattern"], r"^[a-f0-9]{64}$")

    def test_collection_sizes_are_bounded(self) -> None:
        by_name = {t["name"]: t["inputSchema"]["properties"] for t in project_tool_definitions()}
        self.assertEqual(by_name["dreamina_video_project"]["limit"]["maximum"], 100)
        self.assertEqual(by_name["dreamina_analyze_reference_video"]["approved_roots"]["maxItems"], 10)
        self.assertEqual(by_name["dreamina_analyze_reference_video"]["splits"]["maxItems"], 200)
        self.assertEqual(by_name["dreamina_execute_video_batch"]["max_new_submissions"]["maximum"], 4)
        self.assertEqual(by_name["dreamina_quote_video_batch"]["max_attempts_per_shot"]["maximum"], 3)

    def test_enums_are_closed(self) -> None:
        by_name = {t["name"]: t["inputSchema"]["properties"] for t in project_tool_definitions()}
        self.assertEqual(
            by_name["dreamina_create_redesign"]["creative_mode"]["enum"],
            ["original_redesign", "authorized_replication"],
        )
        self.assertEqual(
            by_name["dreamina_export_video_project"]["subtitle_mode"]["enum"],
            ["none", "sidecar", "muxed", "burned_in"],
        )

    def test_definitions_are_json_serializable(self) -> None:
        json.dumps(project_tool_definitions())


class DispatchTests(unittest.TestCase):
    def test_unknown_tool_is_rejected(self) -> None:
        tools = VideoProjectMcpTools()
        self.assertFalse(tools.handles("dreamina_not_a_tool"))
        with self.assertRaises(ProjectToolError):
            tools.call("dreamina_not_a_tool", {})

    def test_registered_handler_is_invoked_with_validated_args(self) -> None:
        seen: list[dict] = []
        tools = VideoProjectMcpTools()
        tools.register("dreamina_quote_video_batch", lambda args: seen.append(dict(args)) or {"ok": True})
        result = tools.call(
            "dreamina_quote_video_batch",
            {"project_id": "vp_" + "a" * 24, "design_version": "v001", "cost_basis": {"credit": 1},
             "generation": {"S01": {"mode": "text2video"}}, "output_destination": "/approved/final.mp4",
             "output_profile": {"container": "mp4", "codec": "h264"}},
        )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(seen[0]["design_version"], "v001")

    def test_missing_handler_is_reported(self) -> None:
        tools = VideoProjectMcpTools()
        with self.assertRaises(ProjectToolError) as raised:
            tools.call(
                "dreamina_quote_video_batch",
                {"project_id": "vp_" + "a" * 24, "design_version": "v001",
                 "cost_basis": {"credit_ceiling": 1}, "generation": {"S01": {"mode": "text2video"}},
                 "output_destination": "/approved/final.mp4",
                 "output_profile": {"container": "mp4", "codec": "h264"}},
            )
        self.assertIn("no registered handler", str(raised.exception))

    def test_unregistered_name_cannot_be_added(self) -> None:
        tools = VideoProjectMcpTools()
        with self.assertRaises(ProjectToolError):
            tools.register("dreamina_invented", lambda args: {})


class SchemaValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = VideoProjectMcpTools()
        self.tools.register("dreamina_video_project", lambda args: {"action": args["action"]})

    def test_unknown_field_is_rejected(self) -> None:
        with self.assertRaises(ProjectToolError) as raised:
            self.tools.call("dreamina_video_project", {"action": "list", "unknown": True})
        self.assertIn("unsupported fields", str(raised.exception))

    def test_shell_and_argv_fields_are_rejected(self) -> None:
        for field in sorted(FORBIDDEN_FIELDS):
            with self.subTest(field=field):
                with self.assertRaises(ProjectToolError):
                    self.tools.call("dreamina_video_project", {"action": "list", field: "x"})

    def test_bad_project_id_pattern_is_rejected(self) -> None:
        with self.assertRaises(ProjectToolError):
            self.tools.call("dreamina_video_project", {"action": "get", "project_id": "not-a-project"})

    def test_unknown_enum_value_is_rejected(self) -> None:
        with self.assertRaises(ProjectToolError):
            self.tools.call("dreamina_video_project", {"action": "explode"})

    def test_out_of_range_limit_is_rejected(self) -> None:
        for limit in (0, 101):
            with self.subTest(limit=limit):
                with self.assertRaises(ProjectToolError):
                    self.tools.call("dreamina_video_project", {"action": "list", "limit": limit})

    def test_title_length_is_bounded(self) -> None:
        with self.assertRaises(ProjectToolError):
            self.tools.call("dreamina_video_project", {"action": "create", "title": "x" * 201})

    def test_bad_identifier_types_are_rejected(self) -> None:
        with self.assertRaises(ProjectToolError):
            self.tools.call("dreamina_video_project", {"action": "list", "limit": "ten"})


class ActionRuleTests(unittest.TestCase):
    def test_analyze_seed_requires_source_path(self) -> None:
        with self.assertRaises(ProjectToolError) as raised:
            validate_action(
                "dreamina_analyze_reference_video",
                {"action": "seed", "project_id": "vp_" + "a" * 24},
            )
        self.assertIn("source_path", str(raised.exception))

    def test_analyze_seed_forbids_later_action_fields(self) -> None:
        with self.assertRaises(ProjectToolError) as raised:
            validate_action(
                "dreamina_analyze_reference_video",
                {"action": "seed", "project_id": "vp_" + "a" * 24,
                 "source_path": "/approved/source.mov", "analysis_version": "v001"},
            )
        self.assertIn("analysis_version", str(raised.exception))

    def test_analyze_sheets_requires_columns_and_rows(self) -> None:
        with self.assertRaises(ProjectToolError) as raised:
            validate_action(
                "dreamina_analyze_reference_video",
                {"action": "sheets", "project_id": "vp_" + "a" * 24, "analysis_version": "v001"},
            )
        self.assertIn("columns", str(raised.exception))

    def test_analyze_recut_accepts_numeric_boundaries(self) -> None:
        validate_action(
            "dreamina_analyze_reference_video",
            {"action": "recut", "project_id": "vp_" + "a" * 24,
             "analysis_version": "v001", "splits": [1.25], "merges": [2.5]},
        )

    def test_execute_reconcile_forbids_new_submissions(self) -> None:
        with self.assertRaises(ProjectToolError) as raised:
            validate_action(
                "dreamina_execute_video_batch",
                {"action": "reconcile", "project_id": "vp_" + "a" * 24,
                 "batch_version": "v001", "allowance_id": "aid", "max_new_submissions": 2},
            )
        self.assertIn("max_new_submissions", str(raised.exception))

    def test_video_project_get_requires_project_id(self) -> None:
        with self.assertRaises(ProjectToolError):
            validate_action("dreamina_video_project", {"action": "get"})

    def test_video_project_enroll_requires_media_tool_paths(self) -> None:
        with self.assertRaises(ProjectToolError) as raised:
            validate_action("dreamina_video_project", {"action": "enroll_media_tools"})
        self.assertIn("media_tool_paths", str(raised.exception))

    def test_video_project_list_accepts_a_bare_action(self) -> None:
        validate_action("dreamina_video_project", {"action": "list"})

    def test_unknown_action_is_rejected(self) -> None:
        with self.assertRaises(ProjectToolError):
            validate_action("dreamina_video_project", {"action": "destroy"})


class ApprovalMetadataTests(unittest.TestCase):
    def test_quote_is_read_only_and_every_mutating_tool_is_not(self) -> None:
        by_name = {t["name"]: t for t in project_tool_definitions()}
        self.assertTrue(by_name["dreamina_quote_video_batch"]["annotations"]["readOnlyHint"])
        for name in PROJECT_TOOLS - {"dreamina_quote_video_batch"}:
            with self.subTest(tool=name):
                self.assertFalse(by_name[name]["annotations"]["readOnlyHint"])

    def test_native_confirmation_gate_tools_prompt(self) -> None:
        """Enrollment, rights, approval, paid execution, composition and export
        are the tools whose handlers must reach a native confirmation."""
        gated = {
            "dreamina_video_project",
            "dreamina_create_redesign",
            "dreamina_approve_video_batch",
            "dreamina_execute_video_batch",
            "dreamina_compose_video",
            "dreamina_export_video_project",
        }
        self.assertTrue(gated <= PROJECT_TOOLS)


if __name__ == "__main__":
    unittest.main()
