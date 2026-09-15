from __future__ import annotations

import hashlib
import copy
import json
import os
import subprocess
import tempfile
import unittest
import fcntl
import shutil
from pathlib import Path
from unittest.mock import patch

from scripts.bounded_process import BoundedProcessResult
from scripts.trusted_media_tools import TrustedExecutable, TrustedMediaTool
from scripts.json_contracts import canonical_fingerprint
from scripts.video_project_store import (
    VersionCommitIndeterminateError,
    VersionReconciliationError,
    VideoProjectStore,
)


def fixture_path(value):
    """Resolve a synthetic descriptor path only inside the non-process test double."""
    value = str(value)
    if not value.startswith("/dev/fd/"):
        return Path(value)
    number, tail = value[8:].split("/", 1)
    if os.sys.platform == "darwin":
        base = fcntl.fcntl(int(number), 50, bytes(1024)).split(b"\0", 1)[0].decode()
    else:
        base = os.readlink(f"/proc/self/fd/{number}")
    return Path(base) / tail


class FakeTrustedStore:
    """A trust-store test double; adapters still stage, hash, and launch identically."""
    def __init__(self, base):
        self.root = Path(tempfile.mkdtemp(prefix='fake-trusted-', dir=Path(base).resolve()))
        self.tools = {}
        for kind in ('node', 'ffmpeg', 'ffprobe'):
            path = self.root / kind
            path.write_bytes(kind.encode())
            path.chmod(0o500)
            info = path.stat()
            self.tools[kind] = TrustedExecutable(kind, str(path), info.st_uid, 0o500,
                info.st_dev, info.st_ino, info.st_size, hashlib.sha256(path.read_bytes()).hexdigest())

    def load_required(self, kinds):
        result = {}
        for kind in kinds:
            root = Path(tempfile.mkdtemp(prefix='stage-', dir=self.root))
            path = root / kind
            shutil.copyfile(self.root / kind, path)
            path.chmod(0o500)
            result[kind] = TrustedMediaTool(kind, str(self.root / kind), self.tools[kind].sha256, str(path))
        return result

    def release(self, tool):
        shutil.rmtree(Path(tool.staged_path).parent)


def fixture_argv(argv):
    assert argv[:3] == ['/usr/bin/python3', '-I', '-c']
    fd = argv[4]
    return [str(fixture_path(f"/dev/fd/{fd}/" + arg)) if arg.startswith(('source/', 'inputs/', 'output/')) else arg
            for arg in argv[6:]]


class ReelBenchProjectServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = VideoProjectStore(Path(self.temp.name) / "projects")
        project = self.store.create(title="reference", creative_mode="original_redesign", audio_policy="silent")
        self.project_id = project["project_id"]
        self.source = self.store.project_root(self.project_id) / "source" / "source.mp4"
        self.source.parent.mkdir(mode=0o700)
        self.source.write_bytes(b"private source bytes")
        self.source.chmod(0o400)
        self.source_receipt = self.store.write_version(
            self.project_id,
            "source_receipt",
            {
                "schema_version": "1.0", "project_id": self.project_id,
                "source_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
                "size_bytes": self.source.stat().st_size, "mime_type": "video/mp4",
                "video_codec": "h264", "width": 640, "height": 360, "fps": 25.0,
                "duration_seconds": 8.0, "audio_streams": [], "approved_roots_digest": "a" * 64,
                "staged_path": str(self.source), "intake_at": "2026-09-15T00:00:00Z",
            },
            schema_name="source_receipt.schema.json",
        )
        self.service = self._service()

    def _service(self):
        from scripts.reelbench_adapter import ReelBenchAdapter
        from scripts.reelbench_project_service import ReelBenchProjectService

        script = Path(__file__).resolve().parents[1] / "skills" / "dreamina-video-shots" / "scripts" / "video-shots.mjs"
        trust = FakeTrustedStore(self.temp.name)

        def runner(argv, **_kwargs):
            _kwargs['monitor']()
            argv = fixture_argv(argv)
            if argv[2] == "seed":
                track = Path(argv[argv.index("--track") + 1])
                track.write_text('{"hz": 5, "values": [' + ', '.join(['0'] * 50) + ']}\n', encoding="utf-8")
                return BoundedProcessResult(0, '{"meta":{"durationSeconds":8},"shots":[{"id":"S01","start":0,"end":8,"seconds":8,"motion":0}]}\n', "")
            if argv[2] == "frames":
                target = Path(argv[argv.index("--dir") + 1])
                target.mkdir()
                (target / "S01a.jpg").write_bytes(b"frame-a")
                (target / "S01b.jpg").write_bytes(b"frame-b")
                return BoundedProcessResult(0, "", "")
            if argv[2] == "sheet":
                target = Path(argv[argv.index("--out") + 1])
                target.mkdir(exist_ok=True)
                pick = argv[argv.index("--pick") + 1]
                (target / f"sheet-{pick}01.jpg").write_bytes(b"sheet")
                return BoundedProcessResult(0, "", "")
            if argv[2] == "validate":
                marks = ["✅"] * 15
                marks[11] = "⊘"
                labels = [
                    "Timeline is continuous", "Durations add up", "Shot numbering", "Shot size vocabulary",
                    "Category vocabulary", "Camera vocabulary", "Transition vocabulary", "Frame description is checkable",
                    "No duplicate descriptions", "Subjects reconcile with cast", "Categories carry evidence",
                    "Camera vs. measured motion", "Boundaries come from detection", "Keyframes present",
                    "Rhythm annotation is checkable",
                ]
                return BoundedProcessResult(0, "\n".join(f"{mark} {label}" for mark, label in zip(marks, labels)), "")
            if argv[2] == "render":
                return BoundedProcessResult(0, "# bounded report\n", "")
            return BoundedProcessResult(0, "", "")

        adapter = ReelBenchAdapter(
            project_root=self.store.project_root(self.project_id), shots_script=script,
            tools=trust.tools, tool_store=trust, runner=runner,
        )
        return ReelBenchProjectService(self.store, adapter)

    def _validated_reelbench(self):
        seed = self.service.run(
            self.project_id, action="seed", expected_parent=None,
            source_receipt_version=self.source_receipt["version"], threshold=0.3, title="Reference",
        )
        evidence = self.service.run(
            self.project_id, action="evidence", expected_parent=seed["version"],
            source_receipt_version=self.source_receipt["version"],
        )
        return self.service.run(
            self.project_id, action="validate", expected_parent=evidence["version"],
            source_receipt_version=self.source_receipt["version"],
        )

    def _write_native_analysis(self, shots, *, source_sha256=None):
        source = dict(self.source_receipt)
        if source_sha256 is not None:
            source["source_sha256"] = source_sha256
        payload = {
            "schema_version": "1.0", "analysis_id": "an_" + "a" * 24,
            "project_id": self.project_id, "source": source,
            "parameters": {"scene_threshold": .3, "min_shot_seconds": .3, "track_hz": 5},
            "cuts": [shots[0]["start_seconds"], *(shot["end_seconds"] for shot in shots)],
            "manual_cuts": [],
            "shots": [
                {"id": f"S{index:02d}", "measured": {
                    "start_seconds": shot["start_seconds"], "end_seconds": shot["end_seconds"],
                    "duration_seconds": shot["end_seconds"] - shot["start_seconds"],
                    "motion_median": shot["motion_median"],
                    "boundary_source": "source_start" if index == 1 else "scene",
                }, "semantic": None}
                for index, shot in enumerate(shots, 1)
            ],
            "track_path": "/private/track.json", "frame_checksums": {},
        }
        payload["machine_fingerprint"] = canonical_fingerprint({
            "source": payload["source"], "parameters": payload["parameters"],
            "cuts": payload["cuts"],
            "measured_shots": [shot["measured"] for shot in payload["shots"]],
            "frame_checksums": payload["frame_checksums"],
        })
        return self.store.write_version(
            self.project_id, "analysis", payload, schema_name="shot_analysis.schema.json",
        )

    def test_repeated_action_creates_new_version_without_overwriting(self) -> None:
        first = self.service.run(
            self.project_id, action="seed", expected_parent=None,
            source_receipt_version=self.source_receipt["version"], threshold=0.3, title="Reference",
        )
        second = self.service.run(
            self.project_id, action="evidence", expected_parent=first["version"],
            source_receipt_version=self.source_receipt["version"],
        )

        self.assertEqual((first["version"], second["version"]), ("v001", "v002"))
        self.assertEqual(first["parent_version"], None)
        self.assertEqual(second["parent_version"], "v001")
        self.assertNotEqual(first["evidence_fingerprint"], second["evidence_fingerprint"])

    def test_expected_parent_and_source_identity_are_strict(self) -> None:
        with self.assertRaises(ValueError):
            self.service.run(
                self.project_id, action="seed", expected_parent="v001",
                source_receipt_version=self.source_receipt["version"], threshold=0.3, title="Reference",
            )
        self.source.chmod(0o600)
        self.source.write_bytes(b"source replacement")
        self.source.chmod(0o400)
        with self.assertRaises(ValueError):
            self.service.run(
                self.project_id, action="seed", expected_parent=None,
                source_receipt_version=self.source_receipt["version"], threshold=0.3, title="Reference",
            )

    def test_same_bytes_source_replacement_during_execution_fails_closed(self) -> None:
        def runner(argv, **_kwargs):
            argv = fixture_argv(argv)
            track = Path(argv[argv.index("--track") + 1])
            track.write_text('{"hz": 5, "values": []}\n', encoding="utf-8")
            replacement = self.source.with_name("source-replacement.mp4")
            replacement.write_bytes(self.source.read_bytes())
            replacement.chmod(0o400)
            os.replace(replacement, self.source)
            return BoundedProcessResult(0, '{"meta":{"durationSeconds":8},"shots":[{"id":"S01","start":0,"end":8,"seconds":8}]}\n', "")

        self.service._adapter._runner = runner
        with self.assertRaisesRegex(ValueError, "identity changed"):
            self.service.run(
                self.project_id, action="seed", expected_parent=None,
                source_receipt_version=self.source_receipt["version"], threshold=0.3, title="Reference",
            )

    def test_indeterminate_commit_reconciles_only_the_exact_published_receipt(self) -> None:
        real_write = self.store.write_version

        def publish_then_report_indeterminate(*args, **kwargs):
            document = real_write(*args, **kwargs)
            raise VersionCommitIndeterminateError(
                project_id=self.project_id, family="reelbench_evidence", version=document["version"],
                path=self.store.project_root(self.project_id) / "reelbench_evidence" / f"{document['version']}.json",
                payload_fingerprint=canonical_fingerprint(document),
            )

        with patch.object(self.store, "write_version", side_effect=publish_then_report_indeterminate):
            with self.assertRaises(VersionCommitIndeterminateError) as caught:
                self.service.run(
                    self.project_id, action="seed", expected_parent=None,
                    source_receipt_version=self.source_receipt["version"], threshold=0.3, title="Reference",
                )
        recovered = self.service.reconcile_indeterminate(caught.exception)

        self.assertEqual(recovered["version"], "v001")
        self.assertEqual(recovered["evidence_fingerprint"], canonical_fingerprint({key: value for key, value in recovered.items() if key != "evidence_fingerprint"}))

    def test_every_action_publishes_an_immutable_version_and_keeps_skipped_gate(self) -> None:
        first = self.service.run(
            self.project_id, action="seed", expected_parent=None,
            source_receipt_version=self.source_receipt["version"], threshold=0.3, title="Reference",
        )
        second = self.service.run(
            self.project_id, action="evidence", expected_parent=first["version"],
            source_receipt_version=self.source_receipt["version"],
        )
        third = self.service.run(
            self.project_id, action="validate", expected_parent=second["version"],
            source_receipt_version=self.source_receipt["version"],
        )
        fourth = self.service.run(
            self.project_id, action="render", expected_parent=third["version"],
            source_receipt_version=self.source_receipt["version"], mode="md",
        )

        self.assertEqual((second["version"], third["version"], fourth["version"]), ("v002", "v003", "v004"))
        self.assertEqual(third["gates"][11]["status"], "SKIPPED")
        self.assertEqual(fourth["artifacts"][0]["path"], "reelbench/v004/report.md")

    def test_render_requires_immediate_validated_parent(self) -> None:
        seed = self.service.run(self.project_id, action="seed", expected_parent=None,
                                source_receipt_version=self.source_receipt["version"], threshold=0.3, title="Reference")
        evidence = self.service.run(self.project_id, action="evidence", expected_parent=seed["version"],
                                    source_receipt_version=self.source_receipt["version"])
        with self.assertRaisesRegex(ValueError, "validated"):
            self.service.run(self.project_id, action="render", expected_parent=evidence["version"],
                             source_receipt_version=self.source_receipt["version"], mode="md")

    def test_matching_timelines_produce_corroborating_receipt(self) -> None:
        reelbench = self._validated_reelbench()
        analysis = self._write_native_analysis([
            {"start_seconds": 0.0, "end_seconds": 8.0, "motion_median": 0.0},
        ])

        result = self.service.compare_native(self.project_id, analysis["version"], reelbench["version"])

        self.assertEqual(result["overall"], "matched")
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(
            self.store.read_version(
                self.project_id, "reelbench_comparison", result["version"],
                "reelbench_comparison.schema.json",
            ),
            result,
        )
        self.assertEqual(
            result["comparison_fingerprint"],
            canonical_fingerprint({key: value for key, value in result.items() if key != "comparison_fingerprint"}),
        )

    def test_source_or_boundary_mismatch_requires_manual_review_without_native_mutation(self) -> None:
        reelbench = self._validated_reelbench()
        analysis = self._write_native_analysis([
            {"start_seconds": 0.0, "end_seconds": 7.0, "motion_median": 0.0},
        ])
        before = self.store.read_version(
            self.project_id, "analysis", analysis["version"], "shot_analysis.schema.json",
        )

        result = self.service.compare_native(self.project_id, analysis["version"], reelbench["version"])

        self.assertEqual(result["overall"], "manual_review")
        self.assertEqual(result["domains"]["source_identity"]["verdict"], "matched")
        self.assertEqual(result["domains"]["boundaries"]["verdict"], "manual_review")
        self.assertEqual(
            [entry["code"] for entry in result["mismatches"]],
            ["native_timeline", "boundary_end"],
        )
        self.assertEqual(
            self.store.read_version(
                self.project_id, "analysis", analysis["version"], "shot_analysis.schema.json",
            ),
            before,
        )

    def test_comparison_rejects_a_forged_native_machine_fingerprint(self) -> None:
        reelbench = self._validated_reelbench()
        analysis = self._write_native_analysis([
            {"start_seconds": 0.0, "end_seconds": 8.0, "motion_median": 0.0},
        ])
        path = self.store.project_root(self.project_id) / "analysis" / f"{analysis['version']}.json"
        forged = copy.deepcopy(analysis)
        forged["machine_fingerprint"] = "0" * 64
        path.write_text(json.dumps(forged, sort_keys=True), encoding="utf-8")
        path.chmod(0o600)

        with self.assertRaisesRegex(ValueError, "fingerprint"):
            self.service.compare_native(self.project_id, analysis["version"], reelbench["version"])

    def test_annotation_binding_persists_a_durable_comparison_sidecar(self) -> None:
        from scripts.reelbench_binding_service import ReelBenchBindingService

        reelbench = self._validated_reelbench()
        analysis = self._write_native_analysis([
            {"start_seconds": 0.0, "end_seconds": 8.0, "motion_median": 0.0},
        ])
        comparison = self.service.compare_native(
            self.project_id, analysis["version"], reelbench["version"]
        )
        annotation = {
            "schema_version": "1.0", "analysis_version": analysis["version"],
            "machine_fingerprint": analysis["machine_fingerprint"],
            "shots": [{
                "id": "S01", "shot_size": "wide", "category": "subject",
                "category_evidence": "subject presenter", "camera": "static",
                "frame_description": "A clearly described presenter inside a bright modern studio.",
                "rhythm_role": None, "rhythm_evidence": None, "subjects": [],
                "on_screen_text": [], "dialogue": [], "narration": [], "music": [],
                "sound": [], "confidence": .9, "review_note": "",
            }], "transcript": None,
        }
        annotation = self.store.write_version(
            self.project_id, "annotation", annotation, schema_name="shot_annotation.schema.json",
        )

        binding = ReelBenchBindingService(self.store).bind(
            self.project_id, subject_family="annotation", subject_version=annotation["version"],
            comparison_version=comparison["version"],
        )

        self.assertEqual(binding["subject_family"], "annotation")
        self.assertEqual(binding["comparison_fingerprint"], comparison["comparison_fingerprint"])
        self.assertEqual(
            self.store.read_version(
                self.project_id, "reelbench_binding", binding["version"], "reelbench_binding.schema.json",
            ), binding,
        )

    def test_redesign_commit_optionally_persists_a_durable_comparison_sidecar(self) -> None:
        from scripts.video_redesign_service import VideoRedesignService

        reelbench = self._validated_reelbench()
        analysis = self._write_native_analysis([
            {"start_seconds": 0.0, "end_seconds": 8.0, "motion_median": 0.0},
        ])
        comparison = self.service.compare_native(
            self.project_id, analysis["version"], reelbench["version"]
        )
        annotation = {
            "schema_version": "1.0", "analysis_version": analysis["version"],
            "machine_fingerprint": analysis["machine_fingerprint"],
            "shots": [{
                "id": "S01", "shot_size": "wide", "category": "subject",
                "category_evidence": "subject presenter", "camera": "static",
                "frame_description": "A clearly described presenter inside a bright modern studio.",
                "rhythm_role": None, "rhythm_evidence": None, "subjects": [],
                "on_screen_text": [], "dialogue": [], "narration": [], "music": [],
                "sound": [], "confidence": .9, "review_note": "",
            }], "transcript": None,
        }
        self.store.write_version(self.project_id, "annotation", annotation, schema_name="shot_annotation.schema.json")
        payload = {
            "schema_version": "1.0", "creative_mode": "original_redesign",
            "machine_fingerprint": analysis["machine_fingerprint"],
            "preserve": ["timing", "shot_sizes", "camera_moves", "rhythm", "transitions", "audio_beats"],
            "replacements": {key: f"new {key}" for key in (
                "likeness", "voice", "dialogue", "music", "brand", "artwork", "settings", "costume", "distinctive_props",
            )}, "required_media": ["video"], "purpose": "public campaign", "audience": "public",
            "territory": "worldwide", "format": "short_video", "target_duration_seconds": 8.0,
            "aspect_ratio": "16:9", "platform": "web", "concept": "new original launch story",
            "cast": ["new presenter"], "settings": ["new studio"], "palette": ["blue"],
            "visual_style": "clean editorial", "dialogue": [], "narration": [], "music": "new score",
            "sound_intent": "new sound design", "shots": [{"id": "S01", "prompt": "new presenter"}],
            "continuity": ["new presenter remains consistent"], "author": "user",
        }
        redesign = VideoRedesignService(self.store)
        design = redesign.commit_version(
            redesign.prepare_candidate(self.project_id, analysis["version"], payload), None,
            comparison_version=comparison["version"],
        )
        binding = self.store.find_version_by_field(
            self.project_id, "reelbench_binding", field="subject_version", value=design["version"],
            schema_name="reelbench_binding.schema.json",
        )
        self.assertEqual(binding["subject_family"], "video_design")

    def test_binding_indeterminate_recovery_requires_the_exact_published_sidecar(self) -> None:
        from scripts.reelbench_binding_service import ReelBenchBindingService

        reelbench = self._validated_reelbench()
        analysis = self._write_native_analysis([
            {"start_seconds": 0.0, "end_seconds": 8.0, "motion_median": 0.0},
        ])
        comparison = self.service.compare_native(self.project_id, analysis["version"], reelbench["version"])
        annotation = {
            "schema_version": "1.0", "analysis_version": analysis["version"],
            "machine_fingerprint": analysis["machine_fingerprint"], "shots": [{
                "id": "S01", "shot_size": "wide", "category": "subject", "category_evidence": "subject presenter",
                "camera": "static", "frame_description": "A clearly described presenter inside a bright modern studio.",
                "rhythm_role": None, "rhythm_evidence": None, "subjects": [], "on_screen_text": [],
                "dialogue": [], "narration": [], "music": [], "sound": [], "confidence": .9, "review_note": "",
            }], "transcript": None,
        }
        annotation = self.store.write_version(self.project_id, "annotation", annotation, schema_name="shot_annotation.schema.json")
        binder, write = ReelBenchBindingService(self.store), self.store.write_version

        def publish_then_report(*args, **kwargs):
            receipt = write(*args, **kwargs)
            if args[1] == "reelbench_binding":
                raise VersionCommitIndeterminateError(
                    project_id=self.project_id, family="reelbench_binding", version=receipt["version"],
                    path=self.store.project_root(self.project_id) / "reelbench_binding" / f"{receipt['version']}.json",
                    payload_fingerprint=canonical_fingerprint(receipt),
                )
            return receipt

        with patch.object(self.store, "write_version", side_effect=publish_then_report):
            with self.assertRaises(VersionCommitIndeterminateError) as caught:
                binder.bind(self.project_id, subject_family="annotation", subject_version=annotation["version"], comparison_version=comparison["version"])
        recovered = binder.bind(
            self.project_id, subject_family="annotation", subject_version=annotation["version"],
            comparison_version=comparison["version"], indeterminate_commit=caught.exception,
        )
        self.assertEqual(recovered["version"], "v001")

    def test_track_motion_must_match_the_pinned_shot_measurement_algorithm(self) -> None:
        original = self.service._adapter._runner

        def forged_motion(argv, **kwargs):
            result = original(argv, **kwargs)
            argv = fixture_argv(argv)
            if argv[2] == "seed":
                return BoundedProcessResult(result.returncode, result.stdout.replace('"motion":0', '"motion":1'), result.stderr)
            return result

        self.service._adapter._runner = forged_motion
        reelbench = self._validated_reelbench()
        analysis = self._write_native_analysis([
            {"start_seconds": 0.0, "end_seconds": 8.0, "motion_median": 0.0},
        ])

        with self.assertRaisesRegex(ValueError, "motion does not match"):
            self.service.compare_native(self.project_id, analysis["version"], reelbench["version"])

    def test_python_motion_recomputation_matches_pinned_upstream_helper(self) -> None:
        script = Path(__file__).resolve().parents[1] / "skills" / "dreamina-video-shots" / "scripts" / "video-shots.mjs"
        track = {"hz": 5, "values": [float(value % 7) for value in range(80)]}
        cases = [(0.0, 8.0), (1.2, 2.1), (4.0, 4.25)]
        program = (
            f"import {{ medianMotion }} from {script.as_uri()!r}; "
            f"const track={json.dumps(track)}; "
            f"console.log(JSON.stringify({json.dumps(cases)}.map(([start,end]) => medianMotion(track,start,end))));"
        )
        upstream = json.loads(subprocess.run(
            ["node", "--input-type=module", "-e", program], check=True,
            capture_output=True, text=True,
        ).stdout)

        observed = [self.service._upstream_median_motion(track, start, end)[0] for start, end in cases]
        self.assertEqual(observed, upstream)

    def test_missing_track_coverage_requires_motion_manual_review(self) -> None:
        original = self.service._adapter._runner

        def truncated_track(argv, **kwargs):
            result = original(argv, **kwargs)
            argv = fixture_argv(argv)
            if argv[2] == "seed":
                Path(argv[argv.index("--track") + 1]).write_text('{"hz":5,"values":[0]}\n', encoding="utf-8")
            return result

        self.service._adapter._runner = truncated_track
        reelbench = self._validated_reelbench()
        analysis = self._write_native_analysis([
            {"start_seconds": 0.0, "end_seconds": 8.0, "motion_median": 0.0},
        ])

        result = self.service.compare_native(self.project_id, analysis["version"], reelbench["version"])

        self.assertEqual(result["overall"], "manual_review")
        self.assertEqual(result["domains"]["motion"]["verdict"], "manual_review")
        self.assertEqual(result["mismatches"][-1]["code"], "track_coverage")

    def test_structured_mismatches_keep_more_than_the_120_shot_worst_case_stably(self) -> None:
        analysis = {
            "source": {"source_sha256": "a" * 64, "version": "v001", "duration_seconds": 120.0},
            "shots": [{"id": f"S{index:02d}", "measured": {
                "start_seconds": float(index - 1), "end_seconds": float(index), "motion_median": 0.0,
            }} for index in range(1, 121)],
        }
        evidence = {"source_sha256": "a" * 64, "source_receipt_version": "v001"}
        reelbench = {"shots": {"meta": {"durationSeconds": 120.0}, "shots": [
            {"id": f"S{index:02d}", "start": float(index) - .75, "end": float(index) + .25, "motion": 1.0}
            for index in range(1, 121)
        ]}, "motions": [{"motion": 1.0, "covered": True} for _ in range(120)]}

        _domains, first = self.service._compare(analysis, evidence, reelbench)
        _domains, second = self.service._compare(analysis, evidence, reelbench)

        self.assertGreaterEqual(len(first), 360)
        self.assertLessEqual(len(first), 2048)
        self.assertEqual(first, second)
        self.assertEqual(
            canonical_fingerprint({"mismatches": first}),
            canonical_fingerprint({"mismatches": second}),
        )

    def test_comparison_requires_exact_existing_versions_and_validated_reelbench_evidence(self) -> None:
        seed = self.service.run(
            self.project_id, action="seed", expected_parent=None,
            source_receipt_version=self.source_receipt["version"], threshold=0.3, title="Reference",
        )
        analysis = self._write_native_analysis([
            {"start_seconds": 0.0, "end_seconds": 8.0, "motion_median": 0.0},
        ])

        with self.assertRaises(VersionReconciliationError):
            self.service.compare_native(self.project_id, "v999", seed["version"])
        with self.assertRaisesRegex(ValueError, "validated"):
            self.service.compare_native(self.project_id, analysis["version"], seed["version"])

    def test_comparison_recovery_reconciles_only_the_exact_visible_receipt(self) -> None:
        reelbench = self._validated_reelbench()
        analysis = self._write_native_analysis([
            {"start_seconds": 0.0, "end_seconds": 8.0, "motion_median": 0.0},
        ])
        real_write = self.store.write_version

        def publish_then_report_indeterminate(*args, **kwargs):
            document = real_write(*args, **kwargs)
            if args[1] != "reelbench_comparison":
                return document
            raise VersionCommitIndeterminateError(
                project_id=self.project_id, family="reelbench_comparison", version=document["version"],
                path=self.store.project_root(self.project_id) / "reelbench_comparison" / f"{document['version']}.json",
                payload_fingerprint=canonical_fingerprint(document),
            )

        with patch.object(self.store, "write_version", side_effect=publish_then_report_indeterminate):
            with self.assertRaises(VersionCommitIndeterminateError) as caught:
                self.service.compare_native(self.project_id, analysis["version"], reelbench["version"])

        recovered = self.service.reconcile_indeterminate(caught.exception)
        self.assertEqual(recovered["version"], "v001")
        self.assertEqual(recovered["overall"], "matched")

    def test_reordered_or_missing_shots_are_listed_as_manual_review_and_tolerance_is_inclusive(self) -> None:
        analysis = {
            "source": {"source_sha256": "a" * 64, "version": "v001", "duration_seconds": 8.0},
            "shots": [
                {"id": "S01", "measured": {"start_seconds": 0.0, "end_seconds": 4.0, "motion_median": 0.0}},
                {"id": "S02", "measured": {"start_seconds": 4.0, "end_seconds": 8.0, "motion_median": 0.0}},
            ],
        }
        evidence = {"source_sha256": "a" * 64, "source_receipt_version": "v001"}
        within_tolerance = {"shots": {"meta": {"durationSeconds": 8.0}, "shots": [
            {"id": "S01", "start": 0.0, "end": 4.1, "motion": 0.0},
            {"id": "S02", "start": 4.1, "end": 8.0, "motion": 0.0},
        ]}, "motions": [{"motion": 0.0, "covered": True}, {"motion": 0.0, "covered": True}]}
        mismatched = {"shots": {"meta": {"durationSeconds": 8.0}, "shots": [
            {"id": "S02", "start": 0.0, "end": 4.0, "motion": 0.0},
        ]}, "motions": [{"motion": 0.0, "covered": True}]}

        accepted = self.service._compare_domains(analysis, evidence, within_tolerance)
        rejected = self.service._compare_domains(analysis, evidence, mismatched)

        self.assertEqual(accepted["boundaries"]["verdict"], "matched")
        self.assertEqual(rejected["shot_count"]["verdict"], "manual_review")
        self.assertEqual(rejected["boundaries"]["verdict"], "manual_review")


if __name__ == "__main__":
    unittest.main()
