"""Dependency-free stdio MCP server for approved Dreamina workflows."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping

from scripts.approval_guard import ApprovalGuard
from scripts.dreamina_adapter import DreaminaAdapter
from scripts.image_service import ImageService, build_request_fingerprint
from scripts.native_approval import NativeApprovalProvider
from scripts.reference_policy import ReferencePolicy
from scripts.trusted_cli import TrustedCliStore
from scripts.video_service import VideoService, build_video_request_fingerprint


PROTOCOL_VERSION = "2025-06-18"


def _tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "dreamina_capability_snapshot",
            "description": "Read the verified Dreamina CLI capability snapshot without generation.",
            "inputSchema": {"type": "object", "properties": {"detail": {"type": "string", "enum": ["summary", "full"], "default": "summary"}}, "additionalProperties": False},
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        },
        {
            "name": "dreamina_submit_image",
            "description": "Submit one explicitly approved paid Dreamina image request and persist recovery state.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["text2image", "image2image"]},
                    "prompt": {"type": "string", "minLength": 1, "maxLength": 4000},
                    "model": {"type": "string"},
                    "resolution_type": {"type": "string"},
                    "count": {"type": "integer", "minimum": 1, "maximum": 10},
                    "ratio": {"type": "string"},
                    "references": {"type": "array", "items": {"type": "object"}, "maxItems": 10},
                    "approved_roots": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["mode", "prompt", "model", "resolution_type"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
        },
        {
            "name": "dreamina_submit_video",
            "description": "Submit one explicitly approved paid Dreamina video request and persist recovery state.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["text2video", "image2video", "frames2video", "multimodal2video"]},
                    "prompt": {"type": "string", "maxLength": 4000},
                    "model": {"type": "string"},
                    "video_resolution": {"type": "string"},
                    "ratio": {"type": "string"},
                    "duration_seconds": {"type": "integer", "minimum": 1, "maximum": 30},
                    "references": {"type": "array", "items": {"type": "object"}, "maxItems": 50},
                    "approved_roots": {"type": "array", "items": {"type": "string"}},
                    "web_prerequisite_acknowledged": {"type": "boolean"},
                },
                "required": ["mode", "prompt", "model", "video_resolution"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
        },
    ]


class DreaminaMcpTools:
    """Tool handlers; paid handlers are protected by Codex approval_mode=prompt."""

    def __init__(self, *, state_root: Path | None = None, approval_provider: Any | None = None) -> None:
        self.state_root = state_root or (Path.home() / ".local" / "share" / "codex-dreamina-design")
        self.approval_provider = approval_provider or NativeApprovalProvider()

    def call(self, name: str, args: Mapping[str, Any]) -> dict[str, Any]:
        if name == "dreamina_capability_snapshot":
            with _adapter(args) as adapter:
                snapshot = adapter.capability_snapshot()
                if args.get("detail", "summary") == "full":
                    return snapshot
                return {
                    "cli_version": snapshot["cli_version"],
                    "cli_commit": snapshot.get("cli_commit"),
                    "modes": snapshot.get("modes", []),
                    "model_count": len(snapshot.get("models", [])),
                    "captured_at": snapshot["captured_at"],
                }
        if name == "dreamina_submit_image":
            return self._submit_image(args)
        if name == "dreamina_submit_video":
            return self._submit_video(args)
        raise ValueError(f"unknown tool: {name}")

    def _submit_image(self, args: Mapping[str, Any]) -> dict[str, Any]:
        with _adapter(args) as adapter:
            snapshot = adapter.capability_snapshot()
            policy = _reference_policy(args)
            session_id, guard = self._approval_context("image")
            service = ImageService(snapshot=snapshot, ledger_dir=self.state_root / "operations", reference_policy=policy)
            request = service.build_request(
                mode=str(args["mode"]), prompt=str(args["prompt"]), model=str(args["model"]),
                resolution_type=str(args["resolution_type"]), count=int(args.get("count", 1)),
                ratio=args.get("ratio"), references=list(args.get("references", [])),
            )
            scope = _scope(request)
            approver = self.approval_provider.confirm(request)
            approval_id = guard.record_approval(session_id, request=request, receipt=_approved_receipt(build_request_fingerprint(request), scope, approver))
            return service.submit(request, adapter=adapter, approval_guard=guard, session_id=session_id, approval_id=approval_id)

    def _submit_video(self, args: Mapping[str, Any]) -> dict[str, Any]:
        with _adapter(args) as adapter:
            snapshot = adapter.capability_snapshot()
            policy = _reference_policy(args)
            session_id, guard = self._approval_context("video")
            service = VideoService(snapshot=snapshot, ledger_dir=self.state_root / "operations", reference_policy=policy)
            if args.get("web_prerequisite_acknowledged") is True:
                service.record_web_prerequisite_acknowledgement()
            request = service.build_request(
                mode=str(args["mode"]), prompt=str(args.get("prompt", "")), model=str(args["model"]),
                video_resolution=str(args["video_resolution"]), ratio=args.get("ratio"),
                duration_seconds=int(args["duration_seconds"]) if args.get("duration_seconds") is not None else None,
                references=list(args.get("references", [])),
            )
            scope = _scope(request)
            approver = self.approval_provider.confirm(request)
            approval_id = guard.record_approval(session_id, request=request, receipt=_approved_receipt(build_video_request_fingerprint(request), scope, approver))
            return service.submit(request, adapter=adapter, approval_guard=guard, session_id=session_id, approval_id=approval_id, web_prerequisite_cleared=bool(args.get("web_prerequisite_acknowledged")))

    def _approval_context(self, kind: str) -> tuple[str, ApprovalGuard]:
        guard = ApprovalGuard(root=self.state_root / "approvals")
        session_id = guard.create_session(label=f"mcp-{kind}-{uuid.uuid4().hex[:12]}")
        return session_id, guard


class _AdapterContext:
    def __init__(self, adapter: DreaminaAdapter) -> None: self.adapter = adapter
    def __enter__(self) -> DreaminaAdapter: return self.adapter
    def __exit__(self, *_): self.adapter.close()


def _adapter(args: Mapping[str, Any]) -> _AdapterContext:
    trusted = TrustedCliStore().load()
    return _AdapterContext(DreaminaAdapter(cli_command=trusted["cli_path"], trusted_binary_sha256=trusted["cli_sha256"]))


def _reference_policy(args: Mapping[str, Any]) -> ReferencePolicy | None:
    references = list(args.get("references", []))
    if not references:
        return None
    roots = [Path(value) for value in args.get("approved_roots", [])]
    if not roots:
        raise ValueError("approved_roots is required when references are uploaded")
    return ReferencePolicy(approved_roots=roots)


def _scope(request: Mapping[str, Any]) -> dict[str, Any]:
    values = {
        "count": int(request.get("count", 1)), "model": request.get("model"),
        "resolution": request.get("resolution_type", request.get("video_resolution")),
        "ratio": request.get("ratio"), "duration_seconds": request.get("duration_seconds"),
    }
    return {key: value for key, value in values.items() if value is not None}


def _approved_receipt(fingerprint: str, scope: Mapping[str, Any], approver: str) -> dict[str, Any]:
    return {"request_fingerprint": fingerprint, "acknowledged_cost": "credits", "acknowledged_scope": dict(scope), "approver": approver}


def _response(request_id: Any, result: Any = None, error: dict | None = None) -> dict:
    payload = {"jsonrpc": "2.0", "id": request_id}
    payload["error" if error else "result"] = error or result
    return payload


def _handle(message: Mapping[str, Any], tools: DreaminaMcpTools) -> dict | None:
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        return _response(request_id, {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}}, "serverInfo": {"name": "dreamina-design", "version": "0.2.0"}})
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return _response(request_id, {"tools": _tool_definitions()})
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            structured = tools.call(str(params.get("name", "")), params.get("arguments") or {})
            return _response(request_id, {"content": [{"type": "text", "text": json.dumps(structured, ensure_ascii=False)}], "structuredContent": structured, "isError": False})
        except Exception as exc:
            return _response(request_id, {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True})
    if request_id is not None:
        return _response(request_id, error={"code": -32601, "message": f"method not found: {method}"})
    return None


def main() -> int:
    tools = DreaminaMcpTools()
    for line in sys.stdin:
        try:
            message = json.loads(line)
            response = _handle(message, tools)
        except Exception as exc:
            response = _response(None, error={"code": -32700, "message": str(exc)})
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
