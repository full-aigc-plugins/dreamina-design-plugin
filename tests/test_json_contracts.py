from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.json_contracts import (
    ContractValidationError,
    canonical_fingerprint,
    load_schema,
    validate_contract,
)


class JsonContractTests(unittest.TestCase):
    def test_canonical_fingerprint_is_order_independent_and_unicode_stable(self) -> None:
        first = {"prompt": "月光", "count": 1, "nested": {"b": False, "a": None}}
        second = {"nested": {"a": None, "b": False}, "count": 1, "prompt": "月光"}
        self.assertEqual(canonical_fingerprint(first), canonical_fingerprint(second))
        self.assertEqual(
            canonical_fingerprint(first),
            "7229abacabba856e43b3828caf8979c16d2d4887ae27d0afb8630167411b5abe",
        )

    def test_load_schema_rejects_paths_outside_schema_root(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "unsafe schema name"):
            load_schema("../README.md")

    def test_canonical_fingerprint_rejects_non_finite_numbers(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaises(ContractValidationError):
                canonical_fingerprint({"value": value})

    def test_contract_rejects_unknown_nested_property(self) -> None:
        with self.assertRaises(ContractValidationError):
            validate_contract(
                {
                    "mode": "text2video",
                    "prompt": "demo",
                    "model": "seedance-test",
                    "count": 1,
                    "unknown": True,
                },
                "generation_request.schema.json",
            )

    def test_contract_enforces_required_enum_pattern_and_numeric_bounds(self) -> None:
        invalid_payloads = (
            {"prompt": "demo", "model": "seedance-test", "count": 1},
            {"mode": "unknown", "prompt": "demo", "model": "seedance-test", "count": 1},
            {"mode": "text2video", "prompt": "demo", "model": "seedance-test", "count": 0},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ContractValidationError):
                validate_contract(payload, "generation_request.schema.json")

        with self.assertRaises(ContractValidationError):
            validate_contract(
                {
                    "approval_id": "x" * 32,
                    "request_fingerprint": "not-a-digest",
                    "acknowledged_cost": "credits",
                    "acknowledged_scope": {"count": 1},
                    "approved_at": "2026-09-14T00:00:00Z",
                    "approver": "operator",
                    "expires_at": "2026-09-14T01:00:00Z",
                    "consumed_at": None,
                },
                "approval_receipt.schema.json",
            )

    def test_contract_enforces_const_arrays_and_local_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "defs.schema.json").write_text(
                json.dumps({"$defs": {"tag": {"type": "string", "pattern": "^[A-Z]+$"}}}),
                encoding="utf-8",
            )
            (root / "main.schema.json").write_text(
                json.dumps(
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["version", "tags", "ratio"],
                        "properties": {
                            "version": {"const": "1.0"},
                            "tags": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 2,
                                "items": {"$ref": "defs.schema.json#/$defs/tag"},
                            },
                            "ratio": {"type": "number", "minimum": 0.1, "maximum": 1.0},
                        },
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch("scripts.json_contracts.SCHEMAS_ROOT", root):
                validate_contract({"version": "1.0", "tags": ["A"], "ratio": 0.5}, "main.schema.json")
                for payload in (
                    {"version": "2.0", "tags": ["A"], "ratio": 0.5},
                    {"version": "1.0", "tags": [], "ratio": 0.5},
                    {"version": "1.0", "tags": ["bad"], "ratio": 0.5},
                    {"version": "1.0", "tags": ["A"], "ratio": 1.1},
                ):
                    with self.subTest(payload=payload), self.assertRaises(ContractValidationError):
                        validate_contract(payload, "main.schema.json")

    def test_contract_pattern_uses_json_schema_search_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "pattern.schema.json").write_text(
                json.dumps({"type": "string", "pattern": "[A-Z]+"}),
                encoding="utf-8",
            )
            with mock.patch("scripts.json_contracts.SCHEMAS_ROOT", root):
                validate_contract("prefix-ABC-suffix", "pattern.schema.json")

    def test_contract_rejects_non_finite_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "number.schema.json").write_text(
                json.dumps({"type": "number"}),
                encoding="utf-8",
            )
            with mock.patch("scripts.json_contracts.SCHEMAS_ROOT", root):
                for value in (float("nan"), float("inf"), float("-inf")):
                    with self.subTest(value=value), self.assertRaises(ContractValidationError):
                        validate_contract(value, "number.schema.json")

    def test_contract_unique_items_rejects_duplicate_json_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "unique.schema.json").write_text(
                json.dumps({"type": "array", "uniqueItems": True}),
                encoding="utf-8",
            )
            duplicate_payloads = (
                ["same", "same"],
                [{"a": 1, "b": [2, 3]}, {"b": [2, 3], "a": 1}],
                [[{"nested": None}], [{"nested": None}]],
            )
            with mock.patch("scripts.json_contracts.SCHEMAS_ROOT", root):
                for payload in duplicate_payloads:
                    with self.subTest(payload=payload), self.assertRaises(
                        ContractValidationError
                    ):
                        validate_contract(payload, "unique.schema.json")

    def test_contract_unique_items_preserves_json_scalar_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "unique.schema.json").write_text(
                json.dumps({"type": "array", "uniqueItems": True}),
                encoding="utf-8",
            )
            with mock.patch("scripts.json_contracts.SCHEMAS_ROOT", root):
                validate_contract([True, 1, False, 0], "unique.schema.json")
                for value in (float("nan"), float("inf"), object()):
                    with self.subTest(value=value), self.assertRaises(
                        ContractValidationError
                    ):
                        validate_contract([value], "unique.schema.json")


if __name__ == "__main__":
    unittest.main()
