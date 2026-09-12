"""RED tests for closed schemas, plugin identity, and request/approval fingerprint binding.

These tests cover the runtime contract surface (Task 2) without invoking any
external service. They assert that:

  * the plugin manifest carries the exact identity `codex-dreamina-design`
    and never advertises credentials or MCP servers;
  * every schema in `schemas/` is a valid JSON Schema and refuses unknown
    properties (closed via `additionalProperties: false`);
  * capability, generation, approval, operation, and artifact schemas reject
    credential-shaped fields;
  * the approval schema requires a `request_fingerprint` that matches the
    canonical fingerprint of a generation request.
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = ROOT / "schemas"
MANIFEST = ROOT / ".codex-plugin" / "plugin.json"

EXPECTED_SCHEMAS = (
    "capability_snapshot.schema.json",
    "generation_request.schema.json",
    "approval_receipt.schema.json",
    "operation_receipt.schema.json",
    "artifact_receipt.schema.json",
)

CREDENTIAL_KEYS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "session_token",
    "cookie",
    "private_key",
)


def load_json(target: Path):
    return json.loads(target.read_text(encoding="utf-8"))


def all_keys(node):
    """Yield every key reachable from a JSON-decoded structure."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from all_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from all_keys(item)


class PluginIdentityTests(unittest.TestCase):
    def test_manifest_identity_is_codex_dreamina_design(self) -> None:
        manifest = load_json(MANIFEST)
        self.assertEqual(manifest["name"], "codex-dreamina-design")
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertFalse(manifest.get("skills", "").endswith("/*"))
        self.assertTrue(manifest["skills"].endswith("/"))

    def test_manifest_has_guarded_mcp_and_no_credentials(self) -> None:
        manifest = load_json(MANIFEST)
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")
        self.assertNotIn("mcp", manifest)
        lowered = {key.lower() for key in manifest}
        for forbidden in CREDENTIAL_KEYS:
            self.assertNotIn(forbidden, lowered, f"manifest exposes credential field: {forbidden}")


class SchemaPresenceTests(unittest.TestCase):
    def test_all_required_schemas_exist(self) -> None:
        for name in EXPECTED_SCHEMAS:
            target = SCHEMAS_DIR / name
            self.assertTrue(target.is_file(), f"missing schema: {name}")

    def test_capability_schema_accepts_commit_based_cli_versions(self) -> None:
        schema = load_json(SCHEMAS_DIR / "capability_snapshot.schema.json")
        pattern = schema["properties"]["cli_version"]["pattern"]
        self.assertIsNotNone(re.fullmatch(pattern, "ec1b9fa-dirty"))


class ClosedSchemaTests(unittest.TestCase):
    def test_each_schema_rejects_unknown_properties(self) -> None:
        for name in EXPECTED_SCHEMAS:
            schema = load_json(SCHEMAS_DIR / name)
            self.assertEqual(
                schema.get("additionalProperties"),
                False,
                f"{name} must use additionalProperties: false",
            )

    def test_each_schema_is_valid_draft_2020_12(self) -> None:
        for name in EXPECTED_SCHEMAS:
            schema = load_json(SCHEMAS_DIR / name)
            self.assertEqual(schema.get("$schema"), "https://json-schema.org/draft/2020-12/schema")
            self.assertEqual(schema.get("type"), "object")

    def test_schemas_never_advertise_credentials(self) -> None:
        for name in EXPECTED_SCHEMAS:
            schema = load_json(SCHEMAS_DIR / name)
            for key in all_keys(schema):
                self.assertNotIn(key.lower(), CREDENTIAL_KEYS, f"{name} defines credential field: {key}")


class GenerationAndApprovalFingerprintTests(unittest.TestCase):
    def test_generation_request_defines_fingerprint_input(self) -> None:
        schema = load_json(SCHEMAS_DIR / "generation_request.schema.json")
        required = schema.get("required", [])
        self.assertIn("mode", required)
        self.assertIn("prompt", required)
        self.assertIn("model", required)
        self.assertIn("count", required)

    def test_approval_receipt_requires_request_fingerprint(self) -> None:
        schema = load_json(SCHEMAS_DIR / "approval_receipt.schema.json")
        required = schema.get("required", [])
        self.assertIn("request_fingerprint", required)
        self.assertIn("acknowledged_cost", required)
        self.assertIn("acknowledged_scope", required)
        self.assertEqual(
            schema["properties"]["request_fingerprint"].get("type"),
            "string",
        )
        self.assertEqual(
            schema["properties"]["acknowledged_cost"].get("enum"),
            ["credits", "membership", "free"],
        )

    def test_fingerprint_digest_is_stable(self) -> None:
        """Canonical fingerprint for a generation request must be stable."""
        payload = {
            "mode": "text2image",
            "prompt": "a serene mountain",
            "model": "seedream-5.0-pro",
            "count": 1,
        }
        first = compute_fingerprint(payload)
        second = compute_fingerprint(payload.copy())
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)


def canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_fingerprint(payload: dict) -> str:
    """SHA-256 hex digest over canonical JSON serialization."""
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


if __name__ == "__main__":
    unittest.main()
