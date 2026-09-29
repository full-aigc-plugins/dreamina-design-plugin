"""Tests for the Canvas execution seam: adapter + execution layer.

These tests use a synthetic ``dreamina-canvas`` script (the same pattern
as ``tests/test_dreamina_adapter.py`` for the legacy binary). The stub
answers argv shapes, never touches a network, and lets each test pin exit
codes and payloads so the paid chain can be driven deterministically.

Nothing here approves credits: the executor must refuse to run without a
ceiling the caller bound to the live quote.
"""

from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.approval_guard import (
    ApprovalConsumedError,
    ApprovalGuard,
    ApprovalReplayMismatchError,
)
from scripts.canvas_execution import (
    CanvasAuthorizationRequired,
    CanvasExecutionRequest,
    CanvasExecutor,
    CanvasModeUnsupported,
    CanvasReferenceError,
)
from scripts.canvas_submission_service import (
    CanvasQuoteContext,
    CanvasSubmissionService,
    build_canvas_approval_fingerprint,
)
from scripts.dreamina_adapter import (
    CLINotFoundError,
    PermissionDeniedError,
    UpgradeRequiredError,
)
from scripts.dreamina_canvas_adapter import (
    CanvasCommandError,
    DreaminaCanvasAdapter,
)

STUB = r'''
#!/usr/bin/env python3
"""Synthetic dreamina-canvas: answers argv shapes with canvas envelopes."""
import json, os, sys

argv = sys.argv[1:]
assert argv[0] == "--format" and argv[1] == "json", f"argv must lead with --format json: {argv}"
args = argv[2:]

def emit(code, data=None, error=None):
    payload = {"schemaVersion": "1", "ok": code == 0}
    if code == 0:
        payload["data"] = data if data is not None else {}
    else:
        payload["error"] = error or {"code": "cli.unknown", "requiredAction": "none"}
    print(json.dumps(payload))
    sys.exit(code)

def forced():
    return int(os.environ.get("STUB_EXIT", "0"))

def forced_error():
    return os.environ.get("STUB_ERROR_CODE", "cli.unknown")

if forced():
    emit(forced(), error={"code": forced_error(), "requiredAction": "resume"})

cmd = args[0] if args else ""

if cmd == "version":
    emit(0, {"commit": "09307bb", "edition": "cn", "distribution": "cn"})
elif cmd == "schema":
    emit(0, {"command": args[1] if len(args) > 1 else "", "flags": []})
elif cmd == "auth":
    emit(0, {"account": {"id": "acct_stub", "loggedIn": True}})
elif cmd == "model":
    emit(0, {"items": [{"model": "stub-image-v1"}, {"model": "stub-video-v1"}]})
elif cmd == "canvas":
    if args[1] == "create":
        emit(0, {"projectId": "proj_created", "webUrl": "https://example.invalid/canvas/proj_created"})
    emit(0, {"items": [{"projectId": "proj_listed", "webUrl": "https://example.invalid/c/1"}]})
elif cmd == "resource":
    if args[1] == "upload":
        emit(0, {"resource": {"resourceId": "res_uploaded", "sha256": "ab" * 32}})
    emit(0, {"resource": {"resourceId": args[2], "state": "ready"}})
elif cmd == "node":
    action = args[1]
    if action == "create":
        kind = args[2]
        if kind == "image":
            emit(0, {"node": {"nodeId": "node_image", "nodeType": "image"}})
        emit(0, {"node": {"nodeId": "node_video", "nodeType": "video"}})
    if action == "quote":
        emit(0, {"node": {"nodeId": args[3]}, "totalMaxCredits": 7, "confirmable": True})
    if action == "confirm":
        emit(0, {"nodeId": args[3], "confirmationRequired": False})
    if action == "run":
        emit(0, {"nodeId": args[3], "submitId": "submit_reused"})
    if action == "upscale":
        emit(0, {"node": {"nodeId": "node_upscaled"}, "totalMaxCredits": 4, "confirmable": True})
    if action == "show":
        emit(0, {"node": {"nodeId": args[3]}})
    emit(0, {"items": []})
elif cmd == "operation":
    emit(0, {"state": "completed", "submitId": "submit_reused"})
else:
    emit(2, error={"code": "cli.unknown_command", "requiredAction": "none"})
'''


def write_fake_canvas(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "dreamina-canvas"
    # The shebang must be the first line or the OS cannot exec the stub.
    target.write_text(textwrap.dedent(STUB).lstrip("\n"))
    target.chmod(target.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return target


def canvas_adapter(cli: Path) -> DreaminaCanvasAdapter:
    """A Canvas adapter pinned to a stub binary by SHA-256."""
    return DreaminaCanvasAdapter(
        cli_command=str(cli),
        trusted_binary_sha256=hashlib.sha256(cli.read_bytes()).hexdigest(),
    )


class CanvasAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cli = write_fake_canvas(Path(self._tmp.name))
        self.adapter = canvas_adapter(self.cli)

    def tearDown(self) -> None:
        self.adapter.close()
        self._tmp.cleanup()

    def test_missing_binary_raises_typed_error(self) -> None:
        adapter = DreaminaCanvasAdapter(cli_command="/nonexistent/dreamina-canvas")
        with self.assertRaises(CLINotFoundError):
            adapter.invoke(["version"])

    def test_argv_is_a_list_never_a_shell_string(self) -> None:
        source = (ROOT / "scripts" / "dreamina_canvas_adapter.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("shell=True", source)
        self.assertIn("run_text", source)

    def test_preflight_returns_version_schema_account(self) -> None:
        preflight = self.adapter.preflight()
        self.assertEqual(preflight["version"]["commit"], "09307bb")
        self.assertTrue(preflight["account"]["account"]["loggedIn"])

    def test_node_create_image_extracts_node_id(self) -> None:
        node_id = self.adapter.node_create_image(
            project_id="proj", prompt="a fox", mode="t2i", model="stub-image-v1",
            ratio="1:1", resolution="2K", count=1,
        )
        self.assertEqual(node_id, "node_image")

    def test_i2i_requires_a_reference(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.node_create_image(project_id="p", prompt="x", mode="i2i")

    def test_video_rejects_i2v_and_multi_modal(self) -> None:
        for mode in ("i2v", "multi_modal"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.adapter.node_create_video(
                    project_id="p", prompt="x", mode=mode, refs=["res:abc"]
                )

    def test_video_duration_defaults_to_five_and_must_be_positive(self) -> None:
        self.assertEqual(
            self.adapter.node_create_video(
                project_id="p", prompt="x", mode="m2v", refs=["res:abc"]
            ),
            "node_video",
        )
        with self.assertRaises(ValueError):
            self.adapter.node_create_video(
                project_id="p", prompt="x", mode="t2v", duration=0
            )

    def test_resource_upload_returns_id_and_rejects_missing_file(self) -> None:
        resource_id = self.adapter.resource_upload(__file__, "self")
        self.assertEqual(resource_id, "res_uploaded")
        with self.assertRaises(FileNotFoundError):
            self.adapter.resource_upload("/nonexistent/material.png")

    def test_quote_is_a_query_not_an_approval(self) -> None:
        quote = self.adapter.node_quote("node_image", "proj")
        self.assertEqual(quote["totalMaxCredits"], 7)
        self.assertNotIn("credit_token", quote)

    def test_download_requires_an_existing_directory(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.adapter.resource_download("res_x", "proj", "/nonexistent/out")

    def test_exit_10_and_20_are_states_not_failures(self) -> None:
        for code in (10, 20):
            with self.subTest(exit_code=code):
                script = (
                    "#!/usr/bin/env python3\n"
                    "import json,sys\n"
                    f"print(json.dumps({{'ok': False, 'error': {{'code': 'cli.x',"
                    f" 'requiredAction': 'confirm'}}}}))\nsys.exit({code})\n"
                )
                with tempfile.TemporaryDirectory() as tmp:
                    stub = Path(tmp) / "dreamina-canvas"
                    stub.write_text(script)
                    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
                    adapter = canvas_adapter(stub)
                    result = adapter.invoke(["node", "run", "--node-id", "n"])
                    self.assertEqual(result.exit_code, code)
                    self.assertFalse(result.recoverable and code == 10)
                    adapter.close()

    def test_fatal_exit_codes_raise_typed_errors(self) -> None:
        cases = {
            11: CanvasCommandError,
            12: PermissionDeniedError,
            13: UpgradeRequiredError,
        }
        for code, expected in cases.items():
            with self.subTest(exit_code=code), tempfile.TemporaryDirectory() as tmp:
                script = (
                    "#!/usr/bin/env python3\n"
                    "import json,sys\n"
                    "print(json.dumps({'ok': False, 'error': {'code': 'cli.x',"
                    " 'requiredAction': 'login'}}))\n"
                    f"sys.exit({code})\n"
                )
                stub = Path(tmp) / "dreamina-canvas"
                stub.write_text(script)
                stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
                adapter = canvas_adapter(stub)
                with self.assertRaises(expected):
                    adapter.invoke(["node", "run", "--node-id", "n"])
                adapter.close()

    def test_capability_snapshot_lists_three_media_types(self) -> None:
        snapshot = self.adapter.capability_snapshot()
        self.assertEqual(set(snapshot["models"]), {"image", "video", "audio"})
        self.assertIn("canvases", snapshot)

    def test_unknown_media_type_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.model_list("subtitle")


class CanvasExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cli = write_fake_canvas(Path(self._tmp.name))
        self.adapter = canvas_adapter(self.cli)
        self._materials = Path(self._tmp.name) / "frame.png"
        self._materials.write_bytes(b"not-a-real-png")

    def tearDown(self) -> None:
        self.adapter.close()
        self._tmp.cleanup()

    def test_supplied_project_id_creates_nothing(self) -> None:
        executor = CanvasExecutor(self.adapter)
        project_id, created = executor.resolve_canvas(
            CanvasExecutionRequest(media="image", mode="text2image", project_id="proj_given")
        )
        self.assertEqual(project_id, "proj_given")
        self.assertFalse(created)

    def test_canvas_creation_requires_explicit_authorization(self) -> None:
        request = CanvasExecutionRequest(
            media="image", mode="text2image", canvas_name="work"
        )
        with self.assertRaises(CanvasAuthorizationRequired):
            CanvasExecutor(self.adapter).resolve_canvas(request)
        with self.assertRaises(CanvasAuthorizationRequired):
            CanvasExecutor(
                self.adapter, authorize_canvas_creation=lambda _name: False
            ).resolve_canvas(request)
        project_id, created = CanvasExecutor(
            self.adapter, authorize_canvas_creation=lambda _name: True
        ).resolve_canvas(request)
        self.assertEqual((project_id, created), ("proj_created", True))

    def test_missing_canvas_and_no_name_is_refused(self) -> None:
        with self.assertRaises(CanvasAuthorizationRequired):
            CanvasExecutor(self.adapter).resolve_canvas(
                CanvasExecutionRequest(media="image", mode="text2image")
            )

    def test_prepare_uploads_material_and_stops_at_the_quote(self) -> None:
        executor = CanvasExecutor(self.adapter)
        preparation = executor.prepare(
            CanvasExecutionRequest(
                media="image",
                mode="image2image",
                prompt="keep the product, change the background",
                model="stub-image-v1",
                project_id="proj_given",
                references=({"path": str(self._materials)},),
            )
        )
        self.assertEqual(preparation.node_id, "node_image")
        self.assertEqual(preparation.project_id, "proj_given")
        self.assertEqual(preparation.quote["totalMaxCredits"], 7)
        self.assertEqual(preparation.resource_ids, ("res_uploaded",))
        # A stable submit id is minted at prepare time and reused later.
        self.assertEqual(len(preparation.submit_id), 36)

    def test_node_references_pass_through_without_upload(self) -> None:
        executor = CanvasExecutor(self.adapter)
        preparation = executor.prepare(
            CanvasExecutionRequest(
                media="video",
                mode="image2video",
                prompt="slow push in",
                model="stub-video-v1",
                project_id="proj_given",
                references=("node:node_abc",),
            )
        )
        self.assertEqual(preparation.node_id, "node_video")
        self.assertEqual(preparation.resource_ids, ())

    def test_frames2video_requires_exactly_two_ordered_frames(self) -> None:
        executor = CanvasExecutor(self.adapter)
        one = CanvasExecutionRequest(
            media="video", mode="frames2video", prompt="blend",
            model="stub-video-v1", project_id="p", references=("node:a",),
        )
        with self.assertRaises(CanvasReferenceError):
            executor.prepare(one)
        two = CanvasExecutionRequest(
            media="video", mode="frames2video", prompt="blend",
            model="stub-video-v1", project_id="p",
            references=("node:a", "node:b"),
        )
        self.assertEqual(executor.prepare(two).node_id, "node_video")

    def test_unknown_model_is_refused_before_draft(self) -> None:
        executor = CanvasExecutor(self.adapter)
        with self.assertRaises(CanvasModeUnsupported):
            executor.prepare(
                CanvasExecutionRequest(
                    media="image", mode="text2image", prompt="x",
                    model="5.0Pro", project_id="p",
                )
            )

    def test_run_requires_a_ceiling_bound_to_the_quote(self) -> None:
        executor = CanvasExecutor(self.adapter)
        preparation = executor.prepare(
            CanvasExecutionRequest(
                media="image", mode="text2image", prompt="x",
                model="stub-image-v1", project_id="p",
            )
        )
        with self.assertRaises(CanvasAuthorizationRequired):
            executor.run(preparation)
        submission = executor.run(preparation, credit_ceiling=7)
        self.assertEqual(submission.submit_id, "submit_reused")
        self.assertEqual(submission.node_id, "node_image")
        self.assertEqual(submission.project_id, "p")

    def test_submit_id_is_reused_by_run_and_wait(self) -> None:
        executor = CanvasExecutor(self.adapter)
        preparation = executor.prepare(
            CanvasExecutionRequest(
                media="video", mode="text2video", prompt="x",
                model="stub-video-v1", project_id="p", duration=5,
            )
        )
        submission = executor.run(preparation, credit_ceiling=7)
        self.assertEqual(submission.submit_id, "submit_reused")
        waited = executor.wait(submission)
        self.assertEqual(waited.data["state"], "completed")

    def test_upscale_needs_an_existing_node(self) -> None:
        executor = CanvasExecutor(self.adapter)
        with self.assertRaises(CanvasModeUnsupported):
            executor.run(
                executor.prepare(
                    CanvasExecutionRequest(
                        media="image", mode="image_upscale", project_id="p",
                    )
                ),
                credit_ceiling=4,
            )

    def test_local_path_reference_must_exist(self) -> None:
        executor = CanvasExecutor(self.adapter)
        with self.assertRaises(CanvasReferenceError):
            executor.prepare(
                CanvasExecutionRequest(
                    media="image", mode="image2image", prompt="x",
                    model="stub-image-v1", project_id="p",
                    references=({"path": "/nonexistent/ref.png"},),
                )
            )

    def test_request_exposes_legacy_only_fields(self) -> None:
        request = CanvasExecutionRequest(media="image", mode="text2image")
        self.assertEqual(request.unsupported_fields(), ())
        with_width = CanvasExecutionRequest(media="image", mode="text2image")
        object.__setattr__(with_width, "resolution_type", "2k")
        object.__setattr__(with_width, "width", 1536)
        self.assertIn("width", with_width.unsupported_fields())


class CanvasSubmissionServiceTests(unittest.TestCase):
    """Quote-bound approval against the real guard + ledger + stub runtime."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.cli = write_fake_canvas(base)
        self.adapter = canvas_adapter(self.cli)
        self.ledger_root = base / "ledger"
        self.guard_root = base / "approvals"
        self.service = CanvasSubmissionService(
            CanvasExecutor(self.adapter), ledger_dir=self.ledger_root
        )
        self.request = CanvasExecutionRequest(
            media="image",
            mode="text2image",
            prompt="a fox in snow",
            model="stub-image-v1",
            ratio="1:1",
            resolution="2K",
            count=1,
            project_id="proj_given",
        )

    def tearDown(self) -> None:
        self.adapter.close()
        self._tmp.cleanup()

    def _quote(self):
        return self.service.quote(self.request)

    def _approve(self, context):
        guard = ApprovalGuard(root=self.guard_root)
        session_id = guard.create_session(label=f"mcp-canvas-{uuid.uuid4().hex[:12]}")
        receipt = {
            "request_fingerprint": context.fingerprint,
            "acknowledged_cost": "credits",
            "acknowledged_scope": dict(context.scope),
            "approver": "test-approver",
        }
        approval_id = guard.record_approval(
            session_id,
            request=context.approval_request,
            receipt=receipt,
            fingerprint_builder=build_canvas_approval_fingerprint,
        )
        return guard, session_id, approval_id

    def test_fingerprint_is_derived_from_the_approval_request(self) -> None:
        context = self._quote()
        self.assertEqual(
            context.fingerprint,
            build_canvas_approval_fingerprint(context.approval_request),
        )

    def test_fingerprint_binds_the_quoted_ceiling(self) -> None:
        base = {
            "media": "image", "mode": "t2i", "prompt": "x", "model": None,
            "ratio": None, "resolution": None, "count": None, "duration": None,
            "references": [], "project_id": "p",
        }
        cheap = build_canvas_approval_fingerprint({**base, "quoted_ceiling": 3})
        pricey = build_canvas_approval_fingerprint({**base, "quoted_ceiling": 9})
        self.assertNotEqual(cheap, pricey)

    def test_quote_binds_scope_and_live_ceiling(self) -> None:
        context = self._quote()
        self.assertEqual(context.ceiling, 7)
        # The receipt scope stays inside the guard allowlist...
        self.assertEqual(
            context.scope,
            {"count": 1, "model": "stub-image-v1", "resolution": "2K", "ratio": "1:1"},
        )
        # ...while the Canvas binding lives in the approval request itself.
        self.assertEqual(context.approval_request["project_id"], "proj_given")
        self.assertEqual(context.approval_request["quoted_ceiling"], 7)
        self.assertEqual(context.preparation.node_id, "node_image")

    def test_submit_consumes_the_bound_approval_once(self) -> None:
        context = self._quote()
        guard, session_id, approval_id = self._approve(context)
        result = self.service.submit(
            context, approval_guard=guard, session_id=session_id,
            approval_id=approval_id,
        )
        self.assertEqual(result["runtime"], "canvas")
        self.assertEqual(result["submit_id"], "submit_reused")
        self.assertEqual(result["project_id"], "proj_given")
        self.assertEqual(result["quoted_ceiling"], 7)
        # The same receipt cannot pay for a second run.
        with self.assertRaises(ApprovalConsumedError):
            self.service.submit(
                context, approval_guard=guard, session_id=session_id,
                approval_id=approval_id,
            )

    def test_submit_without_a_ceiling_is_refused(self) -> None:
        context = self._quote()
        ceiling_free = CanvasQuoteContext(
            preparation=context.preparation,
            request=context.request,
            fingerprint=build_canvas_approval_fingerprint(
                {
                    **context.approval_request,
                    "quoted_ceiling": None,
                }
            ),
            scope=context.scope,
            ceiling=None,
        )
        guard, session_id, approval_id = self._approve(ceiling_free)
        with self.assertRaises(RuntimeError):
            self.service.submit(
                ceiling_free, approval_guard=guard, session_id=session_id,
                approval_id=approval_id,
            )

    def test_submit_records_the_intent_in_the_ledger(self) -> None:
        context = self._quote()
        guard, session_id, approval_id = self._approve(context)
        self.service.submit(
            context, approval_guard=guard, session_id=session_id,
            approval_id=approval_id,
        )
        intents = list((self.ledger_root / "submission_intents").glob("*.json"))
        self.assertTrue(intents)
        payload = json.loads(intents[0].read_text(encoding="utf-8"))
        self.assertEqual(
            payload.get("submit_id") or payload.get("submitId"), "submit_reused"
        )

    def test_tampered_fingerprint_is_rejected_by_the_guard(self) -> None:
        context = self._quote()
        guard = ApprovalGuard(root=self.guard_root)
        session_id = guard.create_session(label=f"mcp-canvas-{uuid.uuid4().hex[:12]}")
        receipt = {
            "request_fingerprint": "0" * 64,
            "acknowledged_cost": "credits",
            "acknowledged_scope": dict(context.scope),
            "approver": "test-approver",
        }
        with self.assertRaises(ApprovalReplayMismatchError):
            guard.record_approval(
                session_id,
                request=context.approval_request,
                receipt=receipt,
                fingerprint_builder=build_canvas_approval_fingerprint,
            )

    def test_ledger_disabled_when_dir_is_none(self) -> None:
        service = CanvasSubmissionService(CanvasExecutor(self.adapter))
        self.assertIsNone(service.ledger_dir)


class CanvasSubprocessSafetyTests(unittest.TestCase):
    def test_stub_is_executed_without_a_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stub = write_fake_canvas(Path(tmp))
            proc = subprocess.run(
                [str(stub), "--format", "json", "version"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.returncode, 0)
            self.assertTrue(json.loads(proc.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
