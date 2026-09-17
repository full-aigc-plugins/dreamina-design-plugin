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


class ReferenceVideoCompatibilityTests(unittest.TestCase):
    def test_legacy_tool_contracts_are_unchanged(self) -> None:
        current = {tool["name"]: tool["inputSchema"] for tool in _tool_definitions()}
        self.assertLessEqual(LEGACY_TOOLS, current.keys())
        self.assertEqual(LEGACY_TOOL_CONTRACTS, {name: current[name] for name in LEGACY_TOOLS})


if __name__ == "__main__":
    unittest.main()
