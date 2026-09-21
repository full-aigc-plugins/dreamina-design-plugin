"""Regression tests for the visual quality loop MCP surface.

These deliberately go through the production dispatch entry (the real tool
table plus ``DreaminaMcpTools.call``).  The reason this loop was unreachable is
that every pre-existing caller was a test constructing the domain service
directly, so a service-only test would repeat the exact blind spot.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
import unittest
from pathlib import Path

from scripts.dreamina_mcp_server import DreaminaMcpTools, _tool_definitions
from scripts.native_approval import ApprovalDeniedError
from scripts.visual_loop_runtime import (
    VISUAL_LOOP_ACTIONS,
    VISUAL_LOOP_TOOL_NAMES,
    VisualLoopRuntime,
    visual_loop_tool_definitions,
)

ROOT = Path(__file__).resolve().parents[1]
TOOL = VISUAL_LOOP_TOOL_NAMES[0]

JUDGE = {
    "provider": "zcode",
    "model": "vision-test",
    "evaluated_at": "2026-09-21T10:00:00Z",
    "rubric_version": "vision_judge/1",
    "score": 5,
    "gates": {"media": "ok"},
    "blocking_gaps": ["lighting: key light is 30 degrees off"],
}


class _FakeGeneration:
    """Stand-in for the approved paid handler; counts paid submissions."""

    def __init__(self, artifact: object) -> None:
        self.artifact = artifact
        self.calls = 0

    def generate(self, request: dict) -> dict:
        self.calls += 1
        if isinstance(self.artifact, Exception):
            raise self.artifact
        return dict(self.artifact)  # type: ignore[arg-type]


def _write_image(root: Path, name: str = "target.png") -> Path:
    path = root / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"visual-loop-fixture")
    return path


def _artifact_for(path: Path, submit_id: str = "submit-1") -> dict:
    data = path.read_bytes()
    return {
        "submit_id": submit_id,
        "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "mime_type": "image/png",
    }


class VisualLoopToolSurfaceTests(unittest.TestCase):
    def test_tool_is_registered_in_the_production_tool_table(self) -> None:
        names = {tool["name"] for tool in _tool_definitions()}
        self.assertIn(TOOL, names)

    def test_tool_definition_is_closed_and_enum_driven(self) -> None:
        definitions = {tool["name"]: tool for tool in visual_loop_tool_definitions()}
        schema = definitions[TOOL]["inputSchema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["required"], ["action"])
        self.assertEqual(set(schema["properties"]["action"]["enum"]), set(VISUAL_LOOP_ACTIONS))

    def test_unknown_action_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tools = DreaminaMcpTools(state_root=Path(tmp))
            with self.assertRaises(ValueError):
                tools.call(TOOL, {"action": "definitely-not-an-action"})

    def test_missing_action_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tools = DreaminaMcpTools(state_root=Path(tmp))
            with self.assertRaises(ValueError):
                tools.call(TOOL, {})


class VisualLoopRoundTests(unittest.TestCase):
    def _locked_loop(self, tmp: str, media_kind: str = "image", **kwargs):
        root = Path(tmp)
        tools = DreaminaMcpTools(state_root=root, **kwargs)
        created = tools.call(TOOL, {"action": "create", "media_kind": media_kind})
        loop_id = created["loop"]["loop_id"]
        image = _write_image(root)
        locked = tools.call(
            TOOL,
            {
                "action": "lock_target",
                "loop_id": loop_id,
                "target": {
                    "source_path": str(image),
                    "approved_roots": [str(root)],
                    "mime_type": "image/png",
                    "width": 1024,
                    "height": 1024,
                    "source": {"kind": "user_supplied"},
                },
            },
        )
        return tools, loop_id, locked

    def test_generation_without_an_approved_port_fails_closed(self) -> None:
        # The runtime itself refuses to guess a paid path when no approved
        # generation port is configured.
        with tempfile.TemporaryDirectory() as tmp:
            runtime = VisualLoopRuntime(state_root=Path(tmp))
            loop_id = runtime.visual_loop({"action": "create", "media_kind": "image"})["loop"]["loop_id"]
            image = _write_image(Path(tmp))
            runtime.visual_loop({
                "action": "lock_target",
                "loop_id": loop_id,
                "target": {
                    "source_path": str(image),
                    "approved_roots": [str(tmp)],
                    "mime_type": "image/png",
                    "width": 1024,
                    "height": 1024,
                    "source": {"kind": "user_supplied"},
                },
            })
            with self.assertRaises(RuntimeError):
                runtime.visual_loop({
                    "action": "run_first_round",
                    "loop_id": loop_id,
                    "request": {"prompt": "x", "credit_ceiling": 1},
                })

    def test_dcc_preview_round_is_refused_on_the_paid_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = _write_image(root, "candidate.png")
            fake = _FakeGeneration(_artifact_for(output))
            tools, loop_id, _ = self._locked_loop(
                tmp, media_kind="dcc_preview", visual_loop_generation_factory=lambda request: fake
            )
            with self.assertRaises(Exception):
                tools.call(
                    TOOL,
                    {"action": "run_first_round", "loop_id": loop_id, "request": {"prompt": "x", "credit_ceiling": 1}},
                )
            self.assertEqual(fake.calls, 0)

    def test_generation_step_returns_evidence_and_awaits_host_judgement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = _write_image(root, "candidate.png")
            fake = _FakeGeneration(_artifact_for(output))
            tools, loop_id, _ = self._locked_loop(
                tmp, visual_loop_generation_factory=lambda request: fake
            )
            result = tools.call(
                TOOL,
                {"action": "run_first_round", "loop_id": loop_id, "request": {"prompt": "x", "credit_ceiling": 1}},
            )
            self.assertEqual(fake.calls, 1)
            self.assertEqual(result["loop"]["state"], "submitting")
            self.assertEqual(result["artifact"]["submit_id"], "submit-1")
            self.assertIn("target", result["judge_evidence"])

    def test_failed_first_round_never_submits_twice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = _write_image(root, "candidate.png")
            fake = _FakeGeneration(_artifact_for(output))
            tools, loop_id, _ = self._locked_loop(
                tmp, visual_loop_generation_factory=lambda request: fake
            )
            round_result = tools.call(
                TOOL,
                {"action": "run_first_round", "loop_id": loop_id, "request": {"prompt": "x", "credit_ceiling": 1}},
            )
            judged = tools.call(
                TOOL,
                {
                    "action": "record_judgement",
                    "loop_id": loop_id,
                    "artifact": round_result["artifact"],
                    "judge_result": JUDGE,
                },
            )
            self.assertEqual(judged["loop"]["state"], "awaiting_approval")
            self.assertEqual(judged["loop"]["rounds"][-1]["next_action"], "request_retry_approval")
            self.assertEqual(fake.calls, 1)

    def test_tampered_artifact_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = _write_image(root, "candidate.png")
            fake = _FakeGeneration(_artifact_for(output))
            tools, loop_id, _ = self._locked_loop(
                tmp, visual_loop_generation_factory=lambda request: fake
            )
            round_result = tools.call(
                TOOL,
                {"action": "run_first_round", "loop_id": loop_id, "request": {"prompt": "x", "credit_ceiling": 1}},
            )
            output.write_bytes(b"\x89PNG\r\n\x1a\n" + b"tampered-after-generation")
            with self.assertRaises(Exception):
                tools.call(
                    TOOL,
                    {
                        "action": "record_judgement",
                        "loop_id": loop_id,
                        "artifact": round_result["artifact"],
                        "judge_result": JUDGE,
                    },
                )

    def test_out_of_range_judge_score_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = _write_image(root, "candidate.png")
            fake = _FakeGeneration(_artifact_for(output))
            tools, loop_id, _ = self._locked_loop(
                tmp, visual_loop_generation_factory=lambda request: fake
            )
            round_result = tools.call(
                TOOL,
                {"action": "run_first_round", "loop_id": loop_id, "request": {"prompt": "x", "credit_ceiling": 1}},
            )
            bad = dict(JUDGE, score=99)
            with self.assertRaises(Exception):
                tools.call(
                    TOOL,
                    {
                        "action": "record_judgement",
                        "loop_id": loop_id,
                        "artifact": round_result["artifact"],
                        "judge_result": bad,
                    },
                )

    def test_stop_blocks_resubmission_without_claiming_remote_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tools, loop_id, _ = self._locked_loop(tmp)
            stopped = tools.call(TOOL, {"action": "stop", "loop_id": loop_id, "reason": "user stopped"})
            self.assertEqual(stopped["loop"]["state"], "stopped")
            self.assertFalse(stopped["loop"]["remote_cancelled"])


class VisualLoopRetryAllowanceTests(unittest.TestCase):
    def test_retry_requires_an_allowance_matching_the_exact_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = _write_image(root, "candidate.png")
            fake = _FakeGeneration(_artifact_for(output))
            tools = DreaminaMcpTools(
                state_root=root, visual_loop_generation_factory=lambda request: fake
            )
            loop_id = tools.call(TOOL, {"action": "create", "media_kind": "image"})["loop"]["loop_id"]
            image = _write_image(root, "target.png")
            tools.call(
                TOOL,
                {
                    "action": "lock_target",
                    "loop_id": loop_id,
                    "target": {
                        "source_path": str(image),
                        "approved_roots": [str(root)],
                        "mime_type": "image/png",
                        "width": 1024,
                        "height": 1024,
                    "source": {"kind": "user_supplied"},
                    },
                },
            )
            round_result = tools.call(
                TOOL,
                {"action": "run_first_round", "loop_id": loop_id, "request": {"prompt": "x", "credit_ceiling": 1}},
            )
            tools.call(
                TOOL,
                {
                    "action": "record_judgement",
                    "loop_id": loop_id,
                    "artifact": round_result["artifact"],
                    "judge_result": JUDGE,
                },
            )
            with self.assertRaises(Exception):
                tools.call(
                    TOOL,
                    {
                        "action": "run_retry",
                        "loop_id": loop_id,
                        "request": {"prompt": "y", "credit_ceiling": 1},
                    },
                )
            self.assertEqual(fake.calls, 1)

    def test_denied_retry_approval_activates_no_allowance(self) -> None:
        class _Deny:
            def confirm(self, request):
                raise ApprovalDeniedError("user declined the retry")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = _write_image(root, "candidate.png")
            fake = _FakeGeneration(_artifact_for(output))
            tools = DreaminaMcpTools(
                state_root=root,
                approval_provider=_Deny(),
                visual_loop_generation_factory=lambda request: fake,
            )
            loop_id = tools.call(TOOL, {"action": "create", "media_kind": "image"})["loop"]["loop_id"]
            image = _write_image(root, "target.png")
            tools.call(TOOL, {
                "action": "lock_target",
                "loop_id": loop_id,
                "target": {
                    "source_path": str(image),
                    "approved_roots": [str(root)],
                    "mime_type": "image/png",
                    "width": 1024,
                    "height": 1024,
                    "source": {"kind": "user_supplied"},
                },
            })
            round_result = tools.call(TOOL, {
                "action": "run_first_round",
                "loop_id": loop_id,
                "request": {"prompt": "x", "credit_ceiling": 1},
            })
            judged = tools.call(TOOL, {
                "action": "record_judgement",
                "loop_id": loop_id,
                "artifact": round_result["artifact"],
                "judge_result": JUDGE,
            })
            fingerprint = judged["loop"]["rounds"][-1]["request_fingerprint"]
            tools.call(TOOL, {
                "action": "propose_retry",
                "loop_id": loop_id,
                "request": {"prompt": "y", "credit_ceiling": 1},
            })
            with self.assertRaises(ApprovalDeniedError):
                tools.call(TOOL, {
                    "action": "approve_retry",
                    "loop_id": loop_id,
                    "request_fingerprint": fingerprint,
                    "credit_ceiling": 1,
                })
            final = tools.call(TOOL, {"action": "status", "loop_id": loop_id})["loop"]
            self.assertIsNone(final.get("retry_allowance"))
            self.assertEqual(fake.calls, 1)


GENERATED_JUDGE_SOURCE = {"kind": "generated", "provider": "dreamina", "submit_id": "target-submit-1"}


class VisualTargetGenerationTests(unittest.TestCase):
    """A generated target is a first-class target: same locking strength,
    auditable provenance, and no auto-generation path in the tool surface."""

    def _tools(self, tmp: str) -> DreaminaMcpTools:
        root = Path(tmp)
        output = _write_image(root, "candidate.png")
        fake = _FakeGeneration(_artifact_for(output))
        return DreaminaMcpTools(
            state_root=root, visual_loop_generation_factory=lambda request: fake
        )

    def _lock_generated_target(self, tools: DreaminaMcpTools, tmp: str) -> dict:
        root = Path(tmp)
        generated = _write_image(root, "dream-target.png")
        loop_id = tools.call(TOOL, {"action": "create", "media_kind": "image"})["loop"]["loop_id"]
        return tools.call(TOOL, {
            "action": "lock_target",
            "loop_id": loop_id,
            "target": {
                "source_path": str(generated),
                "approved_roots": [str(root)],
                "mime_type": "image/png",
                "width": 1024,
                "height": 1024,
                "source": GENERATED_JUDGE_SOURCE,
            },
        })

    def test_generated_target_locks_and_drives_a_full_round(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tools = self._tools(tmp)
            locked = self._lock_generated_target(tools, tmp)
            self.assertEqual(locked["loop"]["state"], "target_locked")
            self.assertEqual(locked["loop"]["target"]["source"]["kind"], "generated")
            round_result = tools.call(TOOL, {
                "action": "run_first_round",
                "loop_id": locked["loop"]["loop_id"],
                "request": {"prompt": "x", "credit_ceiling": 1},
            })
            judged = tools.call(TOOL, {
                "action": "record_judgement",
                "loop_id": locked["loop"]["loop_id"],
                "artifact": round_result["artifact"],
                "judge_result": JUDGE,
            })
            self.assertEqual(judged["loop"]["rounds"][-1]["target_sha256"],
                             locked["loop"]["target"]["sha256"])

    def test_generated_target_gets_the_same_path_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tools = self._tools(tmp)
            root = Path(tmp)
            outside = Path(tempfile.gettempdir()) / "outside-visual-target.png"
            outside.write_bytes(b"\x89PNG\r\n\x1a\n" + b"outside")
            self.addCleanup(outside.unlink, missing_ok=True)
            loop_id = tools.call(TOOL, {"action": "create", "media_kind": "image"})["loop"]["loop_id"]
            with self.assertRaises(Exception):
                tools.call(TOOL, {
                    "action": "lock_target",
                    "loop_id": loop_id,
                    "target": {
                        "source_path": str(outside),
                        "approved_roots": [str(root)],
                        "mime_type": "image/png",
                        "width": 1024,
                        "height": 1024,
                        "source": GENERATED_JUDGE_SOURCE,
                    },
                })

    def test_tool_surface_has_no_auto_target_generation_action(self) -> None:
        # Target generation is a separately approved paid submission; the loop
        # tool must not grow an action that spends without its own approval.
        for action in VISUAL_LOOP_ACTIONS:
            self.assertNotIn("generate_target", action)
            self.assertNotIn("auto_target", action)

    def test_generated_source_provenance_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tools = self._tools(tmp)
            locked = self._lock_generated_target(tools, tmp)
            target = locked["loop"]["target"]
            self.assertEqual(target["source"]["kind"], "generated")
            self.assertEqual(target["source"]["provider"], "dreamina")
            self.assertEqual(target["source"]["submit_id"], "target-submit-1")
            self.assertTrue(target["receipt_fingerprint"])



class VisualLoopDiscoverabilityTests(unittest.TestCase):
    """Guard the drift that made this loop invisible in the first place."""

    def _read(self, *parts: str) -> str:
        return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")

    def test_harness_declares_the_real_skill_count(self) -> None:
        harness = self._read("skills", "dreamina-design-harness", "SKILL.md")
        declared = re.search(r"(\d+) 个技能已随插件分发", harness)
        self.assertIsNotNone(declared, "harness no longer declares a skill count")
        actual = len([p for p in (ROOT / "skills").iterdir() if p.is_dir()])
        self.assertEqual(int(declared.group(1)), actual)

    def test_harness_names_the_previously_unnamed_skills(self) -> None:
        harness = self._read("skills", "dreamina-design-harness", "SKILL.md")
        for name in ("dreamina-vision-judge", "dreamina-auto-seedance", "dreamina-seedance-resume"):
            self.assertIn(name, harness)

    def test_loop_surface_is_reachable_from_plugin_local_paths(self) -> None:
        # The router (dreamina-design-use) is upstream-locked in skills.lock.json,
        # so local edits to it fail skill_vendor check. Discovery for a
        # plugin-local capability must therefore live on plugin-local surfaces.
        for parts in (
            ("skills", "dreamina-design-harness", "SKILL.md"),
            ("commands", "dreamina-visual-loop.md"),
            ("docs", "visual-quality-loop.zh_CN.md"),
        ):
            text = self._read(*parts)
            self.assertIn("dreamina_visual_loop", text, f"{parts[-1]} does not reach the loop")

    def test_harness_reaches_the_independent_judge(self) -> None:
        harness = self._read("skills", "dreamina-design-harness", "SKILL.md")
        self.assertIn("dreamina-vision-judge", harness)

    def test_harness_reaches_the_host_judge_adapter_recipe(self) -> None:
        # Judging is a HOST responsibility (a stdio server cannot spawn the
        # fresh-context subagent); the recipe must stay discoverable.
        harness = self._read("skills", "dreamina-design-harness", "SKILL.md")
        self.assertIn("references/judge-port-adapter.md", harness)
        recipe = self._read("skills", "dreamina-design-harness", "references", "judge-port-adapter.md")
        for token in ("record_judgement", "fresh_context", "vision_judge/1"):
            self.assertIn(token, recipe)
        self.assertTrue((ROOT / "docs" / "judge-port-adapter-contract.md").is_file())

    def test_upstream_locked_skills_are_not_part_of_this_surface(self) -> None:
        # Guard the constraint above: if a future edit needs the router, the
        # routing text must be contributed upstream, not patched locally.
        import json

        lock = json.loads(self._read("skills.lock.json"))
        pinned = set(lock["sources"][0]["skills"])
        self.assertIn("dreamina-design-use", pinned)
        self.assertNotIn("dreamina-design-harness", pinned)
        self.assertNotIn("dreamina-vision-judge", pinned)


if __name__ == "__main__":
    unittest.main()
