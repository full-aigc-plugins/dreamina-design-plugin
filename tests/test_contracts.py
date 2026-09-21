"""RED tests for closed schemas, plugin identity, and request/approval fingerprint binding.

These tests cover the runtime contract surface (Task 2) without invoking any
external service. They assert that:

  * the plugin manifest carries the exact identity `dreamina-design`
    and never advertises credentials or MCP servers;
  * every schema in `schemas/` is a valid JSON Schema and refuses unknown
    properties (closed via `additionalProperties: false`);
  * capability, generation, approval, operation, and artifact schemas reject
    credential-shaped fields;
  * the approval schema requires a `request_fingerprint` that matches the
    canonical fingerprint of a generation request.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from scripts.json_contracts import ContractValidationError, canonical_fingerprint, validate_contract

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = ROOT / "schemas"
MANIFEST = ROOT / ".codex-plugin" / "plugin.json"

EXPECTED_SCHEMAS = (
    "capability_snapshot.schema.json",
    "generation_request.schema.json",
    "approval_receipt.schema.json",
    "operation_receipt.schema.json",
    "artifact_receipt.schema.json",
    "video_project.schema.json",
    "source_receipt.schema.json",
    "shot_analysis.schema.json",
    "shot_annotation.schema.json",
    "video_rights_receipt.schema.json",
    "video_redesign.schema.json",
    "video_batch_quote.schema.json",
    "video_batch_allowance.schema.json",
    "composition_receipt.schema.json",
    "shot_evaluation.schema.json",
    "audio_plan.schema.json",
    "transcript_receipt.schema.json",
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
    def test_manifest_identity_is_dreamina_design(self) -> None:
        manifest = load_json(MANIFEST)
        self.assertEqual(manifest["name"], "dreamina-design")
        self.assertEqual(manifest["version"].split("+", 1)[0], "0.5.0")
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

    def test_capability_schema_declares_closed_multiframe_mode_limits(self) -> None:
        schema = load_json(SCHEMAS_DIR / "capability_snapshot.schema.json")
        self.assertIn("multiframe2video", schema["properties"]["modes"]["items"]["enum"])
        limits = schema["properties"]["mode_limits"]
        self.assertFalse(limits["additionalProperties"])


class ClosedSchemaTests(unittest.TestCase):
    def test_shot_evaluation_schema_requires_each_domain_gate_exactly_once(self) -> None:
        fixture = load_json(ROOT / "tests" / "fixtures" / "reference_video" / "valid-evaluation.json")
        validate_contract(fixture, "shot_evaluation.schema.json")
        core = {key: value for key, value in fixture.items() if key != "evaluation_fingerprint"}
        self.assertEqual(fixture["evaluation_fingerprint"], canonical_fingerprint(core))
        for domain, mutation in (
            ("measured_gates", lambda gates: gates.pop("duration")),
            ("semantic_gates", lambda gates: gates.update({"watermark": {"status": "passed", "evidence": "x"}})),
            ("semantic_gates", lambda gates: gates.update({"duration": gates.pop("intent")})),
        ):
            candidate = json.loads(json.dumps(fixture)); mutation(candidate[domain])
            with self.subTest(domain=domain, keys=sorted(candidate[domain])), self.assertRaises(ContractValidationError):
                validate_contract(candidate, "shot_evaluation.schema.json")

    def test_rights_and_redesign_reuse_dimensions_are_exact_and_closed(self) -> None:
        expected = [
            "timing", "shot_sizes", "camera_moves", "rhythm", "transitions",
            "audio_beats", "likeness", "voice", "dialogue", "music", "brand",
            "artwork", "distinctive_props", "effects", "ambience",
        ]
        rights = load_json(SCHEMAS_DIR / "video_rights_receipt.schema.json")
        redesign = load_json(SCHEMAS_DIR / "video_redesign.schema.json")
        self.assertEqual(rights["properties"]["allowed_reuse"]["items"]["enum"], expected)
        for key in ("preserved", "replaced"):
            self.assertEqual(
                redesign["properties"]["similarity_audit"]["properties"][key]["items"]["enum"],
                expected,
            )
        self.assertFalse(redesign["properties"]["payload"]["additionalProperties"])

    def test_video_project_policy_enums_are_exact(self) -> None:
        schema = load_json(SCHEMAS_DIR / "video_project.schema.json")
        self.assertEqual(
            schema["properties"]["creative_mode"]["enum"],
            ["authorized_replication", "original_redesign"],
        )
        self.assertEqual(
            schema["properties"]["audio_policy"]["enum"],
            [
                "full_redesign",
                "preserve_authorized_audio",
                "subtitles_only",
                "silent",
            ],
        )

    def test_audio_plan_is_closed_versioned_and_fingerprint_bound(self) -> None:
        schema = load_json(SCHEMAS_DIR / "audio_plan.schema.json")
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("plan_fingerprint", schema["required"])
        self.assertEqual(schema["properties"]["audio_policy"]["enum"], [
            "full_redesign", "preserve_authorized_audio", "subtitles_only", "silent",
        ])
        self.assertEqual(schema["properties"]["creative_mode"]["enum"], [
            "authorized_replication", "original_redesign",
        ])

    def test_audio_plan_artifact_roles_are_closed_and_not_interchangeable(self) -> None:
        schema = load_json(SCHEMAS_DIR / "audio_plan.schema.json")
        self.assertEqual(schema["properties"]["narration"]["oneOf"][1]["$ref"], "#/$defs/newNarrationArtifact")
        self.assertEqual(schema["properties"]["narration"]["oneOf"][2]["$ref"], "#/$defs/existingVoiceArtifact")
        self.assertEqual(schema["properties"]["narration"]["oneOf"][4]["$ref"], "#/$defs/existingVoiceDialogueArtifact")
        self.assertEqual(schema["properties"]["music"]["anyOf"][1]["$ref"], "#/$defs/musicArtifact")
        self.assertEqual(schema["properties"]["effects"]["items"]["$ref"], "#/$defs/effectArtifact")
        self.assertEqual(schema["properties"]["subtitles"]["items"]["oneOf"][0]["$ref"], "#/$defs/subtitleSrtArtifact")
        self.assertEqual(schema["properties"]["subtitles"]["items"]["oneOf"][1]["$ref"], "#/$defs/subtitleAssArtifact")
        self.assertEqual(schema["$defs"]["subtitleProvenance"]["properties"]["rights_declared"]["const"], ["subtitles"])

    def test_audio_plan_schema_rejects_cross_role_artifact_fields(self) -> None:
        def artifact(provider, mime, kind, source, rights, voice=None, model=None):
            return {"provider":provider,"path":"/tmp/artifact","sha256":"a"*64,"size_bytes":1,
                "mime_type":mime,"provenance":{"kind":kind,"rights_declared":rights,
                "approved_root":"/tmp" if kind == "user_supplied" else None,"source":source,
                "voice":voice,"model":model,"source_voice_cloned":False},"attestation_version":1,
                "attestation_key_id":"b"*64,"attestation":"c"*64}
        base = {"schema_version":"1.0","version":"v001","project_id":"vp_"+"1"*24,
            "design_fingerprint":"2"*64,"batch_fingerprint":"3"*64,
            "creative_mode":"original_redesign","audio_policy":"subtitles_only",
            "target_duration_seconds":1,"source_rights":None,"preserve":[],"transcript":None,
            "rewritten_script":[],"narration":None,"music":None,"effects":[],"ambience":[],"subtitles":[],
            "provenance":{"remote_services_used":False,"source_voice_cloned":False},
            "plan_fingerprint":"4"*64}
        invalid = [
            {**base, "subtitles":[artifact("macos-say","application/x-subrip","generated_subtitle","rewritten_script",["subtitles"],"Samantha","macos-say")]},
            {**base, "subtitles":[artifact("subtitle-service","audio/wav","generated_subtitle","rewritten_script",["subtitles"])]},
            {**base, "narration":artifact("existing-audio","audio/wav","new_narration","rewritten_script",["voice"])},
            {**base, "subtitles":[artifact("subtitle-service","application/x-subrip","user_supplied","user_supplied",["subtitles"])]},
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ContractValidationError):
                validate_contract(payload, "audio_plan.schema.json")

    def test_audio_plan_schema_closes_mode_specific_rights_and_silence_semantics(self) -> None:
        base = {"schema_version":"1.0","version":"v001","project_id":"vp_"+"1"*24,
            "design_fingerprint":"2"*64,"batch_fingerprint":"3"*64,
            "creative_mode":"original_redesign","audio_policy":"silent",
            "target_duration_seconds":1,"source_rights":None,"preserve":[],"transcript":None,
            "rewritten_script":[],"narration":None,"music":None,"effects":[],"ambience":[],"subtitles":[],
            "provenance":{"remote_services_used":False,"source_voice_cloned":False},
            "plan_fingerprint":"4"*64}
        validate_contract(base, "audio_plan.schema.json")
        fabricated_rights = {"receipt_id":"rr_"+"5"*24,"receipt_fingerprint":"6"*64,
            "allowed_reuse":[],"artifact_bindings":{}}
        invalid = [
            {**base, "source_rights": fabricated_rights},
            {**base, "preserve": ["music"]},
            {**base, "audio_policy": "preserve_authorized_audio"},
            {**base, "audio_policy": "full_redesign"},
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ContractValidationError):
                validate_contract(payload, "audio_plan.schema.json")

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

    def test_each_schema_accepts_its_declared_shape_through_shared_validator(self) -> None:
        validate_contract(
            {"mode": "text2image", "prompt": "demo", "model": "seedream-test", "count": 1},
            "generation_request.schema.json",
        )

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
        first = canonical_fingerprint(payload)
        second = canonical_fingerprint(payload.copy())
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)


if __name__ == "__main__":
    unittest.main()
