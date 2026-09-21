"""Production composition root for the ten reference-video MCP tools."""

from __future__ import annotations

import copy
import os
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from scripts.final_media_service import FinalMediaService
from scripts.media_adapter import MediaAdapter
from scripts.media_intake_service import MediaIntakeService
from scripts.narration_service import AudioPlanService
from scripts.native_approval import NativeApprovalProvider
from scripts.reference_policy import ReferencePolicy
from scripts.reference_video_service import ReferenceVideoService
from scripts.shot_analysis_service import ShotAnalysisService
from scripts.trusted_capability_provider import TrustedCapabilityProvider
from scripts.trusted_cli import TrustedCliStore
from scripts.trusted_media_tools import TrustedMediaToolStore
from scripts.video_batch_allowance import VideoBatchAllowance
from scripts.video_batch_executor import VideoBatchExecutor
from scripts.video_comparison_report import ComparisonReportService
from scripts.video_composition_service import VideoCompositionService
from scripts.video_evaluation_service import VideoEvaluationService
from scripts.video_generation_planner import (
    TrustedAnchorProvider,
    VideoGenerationPlanner,
)
from scripts.video_project_mcp import PROJECT_TOOL_NAMES, VideoProjectMcpTools
from scripts.video_project_store import VideoProjectStore
from scripts.video_redesign_service import VideoRedesignService
from scripts.video_rights_service import VideoRightsService


class VideoProjectRuntime:
    """Wire project MCP handlers to the production domain services lazily."""

    def __init__(
        self,
        *,
        state_root: Path,
        approval_provider: Any | None = None,
        adapter_factory: Callable[[], AbstractContextManager[Any]] | None = None,
        media_tool_store: TrustedMediaToolStore | None = None,
        trusted_cli_store: TrustedCliStore | None = None,
    ) -> None:
        self.state_root = Path(state_root)
        self.state_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_root, 0o700)
        self.projects = VideoProjectStore(self.state_root / "video-projects")
        self.approval = approval_provider or NativeApprovalProvider()
        self.media_tools = media_tool_store or TrustedMediaToolStore()
        self.trusted_cli = trusted_cli_store or TrustedCliStore()
        self.adapter_factory = adapter_factory

    def registry(self) -> VideoProjectMcpTools:
        handlers = {
            "dreamina_video_project": self.video_project,
            "dreamina_analyze_reference_video": self.analyze_reference_video,
            "dreamina_validate_shot_analysis": self.validate_shot_analysis,
            "dreamina_create_redesign": self.create_redesign,
            "dreamina_quote_video_batch": self.quote_video_batch,
            "dreamina_approve_video_batch": self.approve_video_batch,
            "dreamina_execute_video_batch": self.execute_video_batch,
            "dreamina_evaluate_video_batch": self.evaluate_video_batch,
            "dreamina_compose_video": self.compose_video,
            "dreamina_export_video_project": self.export_video_project,
        }
        if set(handlers) != set(PROJECT_TOOL_NAMES):
            raise RuntimeError("production project tool registry is incomplete")
        return VideoProjectMcpTools(handlers)

    def video_project(self, args: Mapping[str, Any]) -> dict[str, Any]:
        action = str(args["action"])
        if action == "runtime_status":
            return {
                "status": "ready",
                "project_tool_count": len(PROJECT_TOOL_NAMES),
                "project_count": len(self.projects.list_projects(100)),
                "trusted_cli_configured": self.trusted_cli.path.is_file(),
                "trusted_media_tools_configured": self.media_tools.path.is_file(),
            }
        if action == "enroll_media_tools":
            enrolled = {
                kind: self.media_tools.enroll(kind, Path(path), approval_provider=self.approval)
                for kind, path in sorted(dict(args["media_tool_paths"]).items())
            }
            return {"enrolled": enrolled}
        if action == "create":
            return self.projects.create(
                title=str(args["title"]),
                creative_mode=str(args.get("creative_mode", "original_redesign")),
                audio_policy=str(args.get("audio_policy", "silent")),
            )
        if action in {"get", "resume"}:
            project = self.projects.get(str(args["project_id"]))
            return {**project, "resumable": project["state"] != "completed"}
        if action == "list":
            return {"projects": self.projects.list_projects(int(args.get("limit", 20)))}
        raise ValueError(f"unsupported project action: {action}")

    def analyze_reference_video(self, args: Mapping[str, Any]) -> dict[str, Any]:
        project_id = str(args["project_id"])
        action = str(args["action"])
        with self._media_adapter() as media:
            service = ReferenceVideoService(self.projects, media)
            if action == "seed":
                MediaIntakeService(self.projects, media, self.approval).intake(
                    project_id,
                    Path(str(args["source_path"])),
                    [Path(value) for value in args.get("approved_roots", [])],
                )
                self._transition_if(project_id, "created", "analyzing", {"source_intake": True})
                return service.seed(
                    project_id,
                    scene_threshold=float(args.get("threshold", 0.30)),
                    min_shot_seconds=float(args.get("min_shot_seconds", 0.30)),
                    track_hz=int(args.get("track_hz", 5)),
                )
            analysis = self.projects.read_version(
                project_id, "analysis", str(args["analysis_version"]), "shot_analysis.schema.json"
            )
            analysis_id = str(analysis["analysis_id"])
            if action == "frames":
                return {"frames": service.extract_frames(analysis_id)}
            if action == "sheets":
                return {"sheets": service.build_contact_sheets(
                    analysis_id, cols=int(args["columns"]), rows=int(args["rows"])
                )}
            if action == "recut":
                return service.recut(
                    analysis_id,
                    splits=[float(item) for item in args.get("splits", [])],
                    merges=[float(item) for item in args.get("merges", [])],
                )
        raise ValueError(f"unsupported analysis action: {action}")

    def validate_shot_analysis(self, args: Mapping[str, Any]) -> dict[str, Any]:
        annotations = {"schema_version": "1.0", "machine_fingerprint": args["machine_fingerprint"],
                       "shots": copy.deepcopy(args["annotations"]), "transcript": None}
        return ShotAnalysisService(self.projects).validate_and_persist(
            str(args["project_id"]), str(args["analysis_version"]), annotations
        )

    def create_redesign(self, args: Mapping[str, Any]) -> dict[str, Any]:
        project_id = str(args["project_id"])
        self._transition_if(project_id, "analysis_review", "designing", {"design_started": True})
        service = VideoRedesignService(self.projects)
        candidate = service.prepare_candidate(project_id, str(args["analysis_version"]), args["design"])
        receipt_id = None
        if args["creative_mode"] == "authorized_replication":
            assertion = args.get("rights_assertion")
            if not isinstance(assertion, Mapping):
                raise PermissionError("authorized replication requires a complete rights assertion")
            source = self.projects.read_version(
                project_id, "source_receipt", "v001", "source_receipt.schema.json"
            )
            rights = VideoRightsService(self.projects, self.approval)
            receipt_id = rights.record_assertion(project_id, source, candidate, assertion)["receipt_id"]
        else:
            self.approval.confirm({"operation": "commit-video-redesign", "project_id": project_id,
                                   "design_fingerprint": candidate["design_fingerprint"]})
        design = service.commit_version(candidate, receipt_id)
        self._transition_if(project_id, "designing", "design_review", {"design_version": design["version"]})
        return design

    def quote_video_batch(self, args: Mapping[str, Any]) -> dict[str, Any]:
        project_id = str(args["project_id"])
        generation = args.get("generation")
        destination = args.get("output_destination")
        profile = args.get("output_profile")
        if not isinstance(generation, Mapping) or not isinstance(destination, str) or not isinstance(profile, Mapping):
            raise TypeError("generation, output_destination, and output_profile are required for an executable quote")
        generation = copy.deepcopy(dict(generation))
        max_attempts = int(args.get("max_attempts_per_shot", 2))
        for shot in generation.values():
            if not isinstance(shot, dict):
                raise TypeError("each generation entry must be an object")
            shot["max_attempts"] = max_attempts
            if max_attempts == 1:
                shot["repair_directives"] = []
            elif "repair_directives" not in shot:
                shot["repair_directives"] = ["temporal_stability"] * (max_attempts - 1)
        roots = sorted({Path(str(item["path"])).resolve().parent for item in generation.values()
                        if isinstance(item, Mapping) for item in item.get("references", [])
                        if isinstance(item, Mapping) and item.get("path")})
        reference_policy = None
        if roots:
            reference_policy = ReferencePolicy(
                approved_roots=roots,
                durable_root=self.projects.project_root(project_id) / "quote-references",
            )
        media = MediaAdapter(self.media_tools)
        planner = VideoGenerationPlanner(
            reference_policy=reference_policy,
            project_store=self.projects,
            capability_provider_factory=lambda: TrustedCapabilityProvider(self.trusted_cli),
            anchor_provider=TrustedAnchorProvider(media),
        )
        quote = planner.plan(
            project_id,
            str(args["design_version"]),
            args["cost_basis"],
            generation=generation,
            output_destination=destination,
            output_profile=profile,
        )
        persisted = self.projects.write_named_version(
            project_id, "video_batch_quote", quote,
            version_field="quote_version", schema_name="video_batch_quote.schema.json",
        )
        self._transition_if(project_id, "design_review", "quoted", {"quote_version": persisted["quote_version"]})
        return persisted

    def approve_video_batch(self, args: Mapping[str, Any]) -> dict[str, Any]:
        project_id = str(args["project_id"])
        quote = self.projects.read_version(
            project_id, "video_batch_quote", str(args["batch_version"]), "video_batch_quote.schema.json"
        )
        if quote["quote_fingerprint"] != args["quote_fingerprint"]:
            raise PermissionError("quote fingerprint changed before approval")
        allowance = self._allowance()
        try:
            allowance_id = allowance.activate(quote, self.approval)
        finally:
            allowance.close()
        self._transition_if(project_id, "quoted", "awaiting_approval", {"quote_version": quote["quote_version"]})
        self._transition_if(project_id, "awaiting_approval", "generating", {"allowance_id": allowance_id})
        return {"allowance_id": allowance_id, "quote_fingerprint": quote["quote_fingerprint"], "state": "active"}

    def execute_video_batch(self, args: Mapping[str, Any]) -> dict[str, Any]:
        if self.adapter_factory is None:
            raise RuntimeError("Dreamina adapter factory is unavailable")
        allowance = self._allowance()
        try:
            with self.adapter_factory() as adapter:
                executor = VideoBatchExecutor(
                    project_store=self.projects,
                    allowance=allowance,
                    allowance_id=str(args["allowance_id"]),
                    video_service=None,
                    adapter=adapter,
                )
                try:
                    action = str(args["action"])
                    if action == "run_next":
                        return executor.run_next(
                            str(args["project_id"]), str(args["batch_version"]),
                            int(args.get("max_new_submissions", 1)),
                        )
                    if action == "reconcile":
                        return executor.reconcile(str(args["project_id"]), str(args["batch_version"]))
                    if action == "resume":
                        return executor.resume(str(args["project_id"]), str(args["batch_version"]))
                finally:
                    executor.close()
        finally:
            allowance.close()
        raise ValueError("unsupported execution action")

    def evaluate_video_batch(self, args: Mapping[str, Any]) -> dict[str, Any]:
        project_id, batch_version = str(args["project_id"]), str(args["batch_version"])
        quote = self.projects.read_version(
            project_id, "video_batch_quote", batch_version, "video_batch_quote.schema.json"
        )
        allowance = self._allowance()
        try:
            active = allowance.find_by_quote_fingerprint(quote["quote_fingerprint"])
            if active is None:
                raise PermissionError("no active allowance matches this batch")
            adapter_context = self.adapter_factory() if self.adapter_factory is not None else _MissingAdapter()
            with adapter_context as adapter:
                executor = VideoBatchExecutor(
                    project_store=self.projects, allowance=allowance, allowance_id=active,
                    video_service=None, adapter=adapter,
                )
                try:
                    state = executor.load_state(project_id, batch_version)
                    task = next((item for item in state["tasks"]
                                 if item["shot_id"] == args["shot_id"] and item["attempt"] == args["attempt"]), None)
                    if task is None:
                        raise KeyError("shot attempt is not present in the durable batch")
                    artifact = next((item for item in task.get("artifacts", [])
                                     if item.get("sha256") == args["artifact_sha256"]), None)
                    if artifact is None:
                        raise KeyError("artifact is not bound to this shot attempt")
                    item = next(entry for entry in quote["items"] if entry["shot_id"] == args["shot_id"])
                    attempt = next(entry for entry in item["attempts"] if entry["attempt_number"] == args["attempt"])
                    binding = {
                        "project_id": project_id, "batch_version": batch_version,
                        "shot_id": args["shot_id"], "attempt": args["attempt"],
                        "submit_id": task["submit_id"], "artifact_sha256": artifact["sha256"],
                        "design_version": quote["design_version"],
                        "design_fingerprint": quote["design_fingerprint"],
                        "quote_fingerprint": quote["quote_fingerprint"], "allowance_id": active,
                    }
                    profile = quote["output_profile"]
                    design_shot = {**binding, "id": args["shot_id"], "width": profile["width"],
                                   "height": profile["height"], "codec": profile["codec"],
                                   "duration_seconds": attempt["request"].get("duration_seconds"),
                                   "aspect_ratio": attempt["request"].get("ratio")}
                    receipt = VideoEvaluationService(media_adapter=MediaAdapter(self.media_tools)).evaluate(
                        {**binding, **artifact}, design_shot, args["semantic_evaluation"],
                        binding=binding, allowance=allowance.get(active), quote=quote,
                        evaluation_id=f"eval_{args['shot_id']}_{args['attempt']}", evaluator=args.get("evaluator", {}),
                    )
                    return {"receipt": receipt, "batch": executor.apply_evaluation_decision(receipt)}
                finally:
                    executor.close()
        finally:
            allowance.close()

    def compose_video(self, args: Mapping[str, Any]) -> dict[str, Any]:
        project_id = str(args["project_id"])
        composition = copy.deepcopy(dict(args["composition"]))
        render_root = self.projects.project_root(project_id) / "renders"
        render_root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(render_root, 0o700)
        output_path = render_root / f"composition-{args['batch_version']}.mp4"
        composition.pop("output_path", None)
        subtitle_mode = str(args.get("subtitle_mode", "none"))
        subtitle_mode = {"sidecar": "none", "muxed": "mux", "burned_in": "burn"}.get(subtitle_mode, subtitle_mode)
        service = VideoCompositionService(
            MediaAdapter(self.media_tools), AudioPlanService(project_store=self.projects), render_root
        )
        plan = service.build_plan(**composition, subtitle_mode=subtitle_mode, output_path=output_path)
        output = service.compose(plan)
        project = self.projects.get(project_id)
        quote = self.projects.read_version(
            project_id, "video_batch_quote", str(args["batch_version"]), "video_batch_quote.schema.json"
        )
        expected = SimpleNamespace(**plan.__dict__, project_id=project_id, composition_version="v001")
        receipt = FinalMediaService(MediaAdapter(self.media_tools)).verify_final(
            output, expected, audio_policy=str(args.get("audio_policy", project["audio_policy"])),
            source_fingerprint=quote["source_sha256"], design_fingerprint=quote["design_fingerprint"],
            composition_fingerprint=quote["quote_fingerprint"],
        )
        persisted = self.projects.write_named_version(
            project_id, "composition", receipt,
            version_field="composition_version", schema_name="composition_receipt.schema.json",
        )
        self._transition_if(project_id, "evaluating", "composing", {"batch_version": args["batch_version"]})
        self._transition_if(project_id, "composing", "final_review", {"composition_version": persisted["composition_version"]})
        return persisted

    def export_video_project(self, args: Mapping[str, Any]) -> dict[str, Any]:
        project_id = str(args["project_id"])
        receipt = self.projects.read_version(
            project_id, "composition", str(args["composition_version"]), "composition_receipt.schema.json"
        )
        request = {
            "destination": str(args["destination"]), "sha256": receipt["sha256"],
            "composition_version": receipt["composition_version"],
            "rights_basis": self.projects.get(project_id)["creative_mode"],
        }
        if self.approval.confirm_video_export(request) != "native-video-export-confirmed":
            raise PermissionError("video export was not approved")
        exported = FinalMediaService(MediaAdapter(self.media_tools)).export_verified(
            receipt, source=Path(receipt["path"]), destination=Path(str(args["destination"])),
            approved_roots=args.get("approved_roots"),
        )
        result: dict[str, Any] = {"artifact": exported, "receipt": receipt}
        if bool(args.get("include_report", False)):
            report_root = Path(exported["path"]).parent
            report_payload = {"project": {"project_id": project_id,
                                           "composition_version": receipt["composition_version"],
                                           "title": self.projects.get(project_id)["title"]},
                              "final_media": receipt, "generated_at": receipt.get("verified_at")}
            result["report"] = ComparisonReportService().write(
                report_payload, report_root / "comparison.html", report_root / "comparison.json"
            )
        self._transition_if(project_id, "final_review", "completed", {"sha256": exported["sha256"]})
        return result

    def _allowance(self) -> VideoBatchAllowance:
        return VideoBatchAllowance(
            self.state_root / "video-batch-allowances", project_store=self.projects,
            rights_service=VideoRightsService(self.projects, native_confirmer=None),
        )

    def _media_adapter(self):
        adapter = MediaAdapter(self.media_tools)
        return _NoCloseContext(adapter)

    def _transition_if(self, project_id: str, expected: str, next_state: str, evidence: Mapping[str, Any]) -> None:
        if self.projects.get(project_id)["state"] == expected:
            self.projects.transition(project_id, expected=expected, next_state=next_state, evidence=evidence)


class _NoCloseContext:
    def __init__(self, value: Any) -> None:
        self.value = value

    def __enter__(self) -> Any:
        return self.value

    def __exit__(self, *_: object) -> None:
        return None


class _MissingAdapter:
    def __enter__(self) -> Any:
        raise RuntimeError("Dreamina adapter factory is unavailable")

    def __exit__(self, *_: object) -> None:
        return None


__all__ = ["VideoProjectRuntime"]
