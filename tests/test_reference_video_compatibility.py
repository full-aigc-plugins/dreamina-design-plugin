from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.dreamina_mcp_server import _tool_definitions


ROOT = Path(__file__).resolve().parents[1]
LEGACY_TOOL_CONTRACTS = json.loads(
    (ROOT / "tests" / "fixtures" / "legacy_mcp_tools_0_3_0.json").read_text(encoding="utf-8")
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


SUBMIT_TOOLS = {"dreamina_submit_image", "dreamina_submit_video"}


class ReferenceVideoCompatibilityTests(unittest.TestCase):
    def test_non_submit_legacy_tool_contracts_are_byte_identical(self) -> None:
        """Tools untouched by the Canvas port must match the 0.3.0 fixture."""
        current = {tool["name"]: tool["inputSchema"] for tool in _tool_definitions()}
        self.assertLessEqual(LEGACY_TOOLS, current.keys())
        untouched = LEGACY_TOOLS - SUBMIT_TOOLS
        self.assertEqual(
            {name: LEGACY_TOOL_CONTRACTS[name] for name in untouched},
            {name: current[name] for name in untouched},
        )

    def test_submit_tool_contracts_grow_additively_only(self) -> None:
        """The Canvas port adds optional fields; nothing legacy is removed.

        Legacy callers keep sending the same arguments and keep hitting the
        same required set; the additions are opt-in (runtime/project_id/
        canvas_name/credit_ceiling), with runtime defaulting to the Canvas
        rail as recorded in the 0.7.0 release notes.
        """
        current = {tool["name"]: tool["inputSchema"] for tool in _tool_definitions()}
        for name in SUBMIT_TOOLS:
            legacy = LEGACY_TOOL_CONTRACTS[name]
            modern = current[name]
            self.assertEqual(
                legacy["required"], modern["required"],
                f"{name}: required set must not change",
            )
            self.assertLessEqual(
                set(legacy["properties"]), set(modern["properties"]),
                f"{name}: legacy properties must survive",
            )
            for prop, spec in legacy["properties"].items():
                self.assertEqual(
                    spec, modern["properties"][prop],
                    f"{name}.{prop}: legacy spec must be byte-identical",
                )


if __name__ == "__main__":
    unittest.main()
