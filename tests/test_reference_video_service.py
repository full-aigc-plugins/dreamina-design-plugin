from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.media_adapter import MediaResult
from scripts.reference_video_service import ReferenceVideoService
from scripts.video_project_store import VideoProjectStore

EXPECTED = json.loads(
    (Path(__file__).parent / "fixtures" / "reference_video" / "expected-analysis.json").read_text(
        encoding="utf-8"
    )
)


class RecordingMediaAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def probe_json(self, path: Path) -> dict[str, object]:
        self.calls.append(("ffprobe", [str(path)]))
        return {
            "format": {"duration": "8.0", "format_name": "mov,mp4"},
            "streams": [{"codec_type": "video", "codec_name": "h264"}],
        }

    def run(self, kind: str, argv, *, timeout_seconds: int) -> MediaResult:
        values = list(argv)
        self.calls.append((kind, values))
        output = Path(values[-1]) if values and values[-1] != "-" else None
        if output is not None and kind == "ffmpeg":
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes((output.name + "\n").encode())
        if any("select=" in item for item in values):
            return MediaResult(0, "", "pts_time:6.00 scene_score=0.72\npts_time:4.00 scene_score=0.51\n")
        if any("signalstats" in item for item in values):
            rows = "\n".join(
                f"frame:{index} pts_time:{index / 5:.2f} lavfi.signalstats.YAVG={value}"
                for index, value in enumerate([0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100,
                                               110, 120, 130, 140, 150, 160, 170, 180, 190, 200,
                                               210, 220, 230, 240, 250, 240, 230, 220, 210, 200,
                                               190, 180, 170, 160, 150, 140, 130, 120, 110, 100])
            )
            return MediaResult(0, "", rows)
        return MediaResult(0, "", "")


class ReferenceVideoServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = VideoProjectStore(self.root / "projects")
        project = self.store.create(
            title="reference", creative_mode="original_redesign", audio_policy="silent"
        )
        self.project_id = project["project_id"]
        source = self.store.project_root(self.project_id) / "source" / "source.mp4"
        source.parent.mkdir(mode=0o700)
        source.write_bytes(b"deterministic source")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        self.receipt = self.store.write_version(
            self.project_id,
            "source_receipt",
            {
                "schema_version": "1.0", "project_id": self.project_id,
                "source_sha256": digest, "size_bytes": source.stat().st_size,
                "mime_type": "video/mp4", "video_codec": "h264", "width": 640,
                "height": 360, "fps": 25.0, "duration_seconds": 8.0,
                "audio_streams": [], "approved_roots_digest": "a" * 64,
                "staged_path": str(source), "intake_at": "2026-09-14T00:00:00Z",
            },
            schema_name="source_receipt.schema.json",
        )
        self.adapter = RecordingMediaAdapter()
        self.service = ReferenceVideoService(self.store, self.adapter)

    def seed(self):
        return self.service.seed(
            self.project_id, scene_threshold=0.30, min_shot_seconds=0.30, track_hz=5
        )

    def test_seed_uses_probe_scene_scores_and_contiguous_timeline(self) -> None:
        analysis = self.seed()
        self.assertEqual([shot["id"] for shot in analysis["shots"]], EXPECTED["shot_ids"])
        self.assertEqual(analysis["cuts"], EXPECTED["cuts"])
        self.assertEqual(analysis["shots"][0]["measured"]["start_seconds"], 0.0)
        self.assertEqual(analysis["shots"][-1]["measured"]["end_seconds"], 8.0)
        self.assertEqual(
            analysis["shots"][0]["measured"]["end_seconds"],
            analysis["shots"][1]["measured"]["start_seconds"],
        )

    def test_motion_track_is_separate_and_per_shot_median_excludes_edges(self) -> None:
        analysis = self.seed()
        self.assertNotIn("track_values", analysis)
        track = json.loads(Path(analysis["track_path"]).read_text(encoding="utf-8"))
        self.assertEqual(track["hz"], EXPECTED["track_hz"])
        self.assertEqual(analysis["shots"][0]["measured"]["motion_median"], 100.0)

    def test_keyframes_are_taken_at_15_and_85_percent(self) -> None:
        analysis = self.seed()
        result = self.service.extract_frames(analysis["analysis_id"])
        self.assertEqual(result["S01"]["a"]["at_seconds"], 0.60)
        self.assertEqual(result["S01"]["b"]["at_seconds"], 3.40)
        self.assertTrue(Path(result["S01"]["a"]["path"]).is_file())

    def test_contact_sheet_order_is_row_major_and_pages_at_25_shots(self) -> None:
        analysis = self.seed()
        result = self.service.build_contact_sheets(analysis["analysis_id"], cols=5, rows=5)
        self.assertEqual(result[0]["shot_ids"], ["S01", "S02", "S03"])
        self.assertEqual(result[0]["layout"], {"cols": 5, "rows": 5})

    def test_recut_recomputes_ids_duration_motion_and_boundary_provenance(self) -> None:
        analysis = self.seed()
        result = self.service.recut(analysis["analysis_id"], splits=[1.25], merges=[4.0])
        self.assertEqual([shot["id"] for shot in result["shots"]], ["S01", "S02", "S03"])
        self.assertEqual(result["cuts"], [0.0, 1.25, 6.0, 8.0])
        self.assertIn(1.25, result["manual_cuts"])
        self.assertEqual(result["shots"][1]["measured"]["boundary_source"], "manual_split")
        self.assertEqual(result["shots"][1]["measured"]["duration_seconds"], 4.75)

    def test_semantic_fields_are_never_carried_across_changed_boundaries(self) -> None:
        analysis = self.seed()
        analysis["shots"][0]["semantic"] = {"description": "legacy"}
        analysis["shots"][2]["semantic"] = {"description": "unchanged"}
        annotated = {key: value for key, value in analysis.items() if key != "version"}
        self.store.write_version(
            self.project_id, "analysis", annotated, schema_name="shot_analysis.schema.json"
        )
        result = self.service.recut(analysis["analysis_id"], splits=[0.5], merges=[])
        self.assertIsNone(result["shots"][0]["semantic"])
        self.assertIsNone(result["shots"][1]["semantic"])
        self.assertEqual(result["shots"][3]["semantic"], {"description": "unchanged"})

    def test_parameters_and_typed_recut_operations_are_bounded(self) -> None:
        for kwargs in (
            {"scene_threshold": 0.04, "min_shot_seconds": 0.3, "track_hz": 5},
            {"scene_threshold": 0.3, "min_shot_seconds": 5.01, "track_hz": 5},
            {"scene_threshold": 0.3, "min_shot_seconds": 0.3, "track_hz": 11},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.service.seed(self.project_id, **kwargs)
        analysis = self.seed()
        with self.assertRaises(ValueError):
            self.service.recut(analysis["analysis_id"], splits=[0.5] * 201, merges=[])
        with self.assertRaises(TypeError):
            self.service.recut(analysis["analysis_id"], splits=["0.5"], merges=[])
        with self.assertRaises(ValueError):
            self.service.extract_frames(analysis["analysis_id"], frame_width=239)
        with self.assertRaises(ValueError):
            self.service.build_contact_sheets(analysis["analysis_id"], cols=6, rows=5)

    def test_machine_fingerprint_changes_when_frames_or_cuts_change(self) -> None:
        analysis = self.seed()
        original = analysis["machine_fingerprint"]
        self.service.extract_frames(analysis["analysis_id"], frame_width=320)
        framed = self.service.get_analysis(analysis["analysis_id"])
        self.assertNotEqual(framed["machine_fingerprint"], original)
        recut = self.service.recut(analysis["analysis_id"], splits=[0.5], merges=[])
        self.assertNotEqual(recut["machine_fingerprint"], framed["machine_fingerprint"])


if __name__ == "__main__":
    unittest.main()
