from __future__ import annotations

import hashlib
import json
import os
import shutil
import statistics
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.media_adapter import MediaAdapter, MediaOutputError, MediaResult
from scripts.reference_video_service import ReferenceVideoService
from scripts.trusted_media_tools import TrustedMediaToolStore
from scripts.video_project_store import VideoProjectStore

EXPECTED = json.loads(
    (Path(__file__).parent / "fixtures" / "reference_video" / "expected-analysis.json").read_text(
        encoding="utf-8"
    )
)


class ApproveFixtureTools:
    def confirm_media_tool_enrollment(self, **kwargs) -> str:
        return "approved-for-local-test"


class RecordingMediaAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self.motion_offset = 0
        self.encoder_variant = "v1"

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
            logical_argv = [*values[:-1], "<output>"]
            output.write_bytes(
                (json.dumps(logical_argv, separators=(",", ":")) + self.encoder_variant).encode()
            )
        if any("select=" in item for item in values):
            return MediaResult(0, "", "pts_time:6.00 scene_score=0.72\npts_time:4.00 scene_score=0.51\n")
        if any("signalstats" in item for item in values):
            rows = "\n".join(
                f"frame:{index} pts_time:{index / 5:.2f}\nlavfi.signalstats.YAVG={value + self.motion_offset}"
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

    @staticmethod
    def swap_path_after_second_fstat(target: Path, replacement: Path):
        original_fstat = os.fstat
        target_inode = target.lstat().st_ino
        calls = 0

        def racing_fstat(descriptor):
            nonlocal calls
            result = original_fstat(descriptor)
            if result.st_ino == target_inode:
                calls += 1
                if calls == 2:
                    os.replace(replacement, target)
            return result

        return racing_fstat

    def test_synthetic_fixture_has_hard_cut_dissolve_and_no_audio(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if ffmpeg is None or ffprobe is None:
            self.skipTest("ffmpeg and ffprobe are required for the generated media fixture")
        target = self.root / "hard-cut-dissolve-no-audio.mp4"
        subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                "-f", "lavfi", "-i", "color=c=red:s=160x90:r=10:d=1",
                "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=10:d=1",
                "-f", "lavfi", "-i", "color=c=green:s=160x90:r=10:d=1",
                "-filter_complex",
                "[0:v][1:v]concat=n=2:v=1:a=0[hard];"
                "[2:v]settb=AVTB[third];"
                "[hard][third]xfade=transition=fade:duration=0.5:offset=1.5,format=yuv420p[v]",
                "-map", "[v]", "-an", "-y", str(target),
            ],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        probe = subprocess.run(
            [
                ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                "-of", "json", str(target),
            ],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            text=True,
        )
        metadata = json.loads(probe.stdout)
        self.assertTrue(target.is_file())
        self.assertGreater(float(metadata["format"]["duration"]), 2.0)
        self.assertEqual([stream["codec_type"] for stream in metadata["streams"]], ["video"])

        trusted_bin = self.root / "trusted-bin"
        trusted_bin.mkdir(mode=0o700)
        tool_store = TrustedMediaToolStore(
            path=self.root / "trusted-config" / "tools.json",
            staging_root=self.root / "trusted-staging",
        )
        for kind, executable in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
            copied = trusted_bin / kind
            shutil.copy2(Path(executable).resolve(), copied)
            copied.chmod(0o500)
            tool_store.enroll(kind, copied, approval_provider=ApproveFixtureTools())
        adapter = MediaAdapter(tool_store)
        actual_store = VideoProjectStore(self.root / "actual-projects")
        project = actual_store.create(
            title="actual synthetic fixture",
            creative_mode="original_redesign",
            audio_policy="silent",
        )
        actual_probe = adapter.probe_json(target)
        video = next(stream for stream in actual_probe["streams"] if stream["codec_type"] == "video")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        actual_store.write_version(
            project["project_id"],
            "source_receipt",
            {
                "schema_version": "1.0", "project_id": project["project_id"],
                "source_sha256": digest, "size_bytes": target.stat().st_size,
                "mime_type": "video/mp4", "video_codec": video["codec_name"],
                "width": video["width"], "height": video["height"],
                "fps": 10.0, "duration_seconds": float(actual_probe["format"]["duration"]),
                "audio_streams": [], "approved_roots_digest": "b" * 64,
                "staged_path": str(target), "intake_at": "2026-09-14T00:00:00Z",
            },
            schema_name="source_receipt.schema.json",
        )
        service = ReferenceVideoService(actual_store, adapter)
        analysis = service.seed(
            project["project_id"], scene_threshold=0.15, min_shot_seconds=0.20, track_hz=5
        )
        track = json.loads(Path(analysis["track_path"]).read_text(encoding="utf-8"))
        frames = service.extract_frames(analysis["analysis_id"], frame_width=240)
        self.assertEqual(analysis["cuts"][0], 0.0)
        self.assertEqual(analysis["cuts"][-1], round(float(actual_probe["format"]["duration"]), 2))
        self.assertTrue(any(abs(cut - 1.0) <= 0.15 for cut in analysis["cuts"][1:-1]))
        self.assertGreater(len(track["values"]), 5)
        self.assertTrue(Path(frames["S01"]["a"]["path"]).is_file())

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
        self.assertEqual(len(track["values"]), 41)
        interior = [row["value"] for row in track["values"][1:20]]
        self.assertEqual(
            analysis["shots"][0]["measured"]["motion_median"], statistics.median(interior)
        )

    def test_keyframes_are_taken_at_15_and_85_percent(self) -> None:
        analysis = self.seed()
        result = self.service.extract_frames(analysis["analysis_id"])
        self.assertEqual(result["S01"]["a"]["at_seconds"], 0.60)
        self.assertEqual(result["S01"]["b"]["at_seconds"], 3.40)
        self.assertTrue(Path(result["S01"]["a"]["path"]).is_file())

    def test_contact_sheet_order_is_row_major_and_pages_at_25_shots(self) -> None:
        analysis = self.seed()
        template = analysis["shots"][0]
        analysis["shots"] = [
            {
                "id": f"S{index:02d}",
                "measured": {
                    **template["measured"],
                    "start_seconds": 0.0,
                    "end_seconds": 8.0,
                    "duration_seconds": 8.0,
                },
                "semantic": None,
            }
            for index in range(1, 27)
        ]
        updated = {key: value for key, value in analysis.items() if key != "version"}
        self.store.write_version(
            self.project_id, "analysis", updated, schema_name="shot_analysis.schema.json"
        )
        result = self.service.build_contact_sheets(analysis["analysis_id"], cols=5, rows=5)
        self.assertEqual(result[0]["shot_ids"], [f"S{index:02d}" for index in range(1, 26)])
        self.assertEqual(result[1]["shot_ids"], ["S26"])
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

    def test_repeat_seed_and_frame_width_never_mutate_old_artifacts(self) -> None:
        first = self.seed()
        old_track = Path(first["track_path"])
        old_track_bytes = old_track.read_bytes()
        self.adapter.motion_offset = 1
        second = self.seed()
        self.assertNotEqual(second["track_path"], first["track_path"])
        self.assertEqual(old_track.read_bytes(), old_track_bytes)

        first_frames = self.service.extract_frames(first["analysis_id"], frame_width=320)
        old_frame = Path(first_frames["S01"]["a"]["path"])
        old_frame_bytes = old_frame.read_bytes()
        second_frames = self.service.extract_frames(first["analysis_id"], frame_width=640)
        self.assertNotEqual(second_frames["S01"]["a"]["path"], str(old_frame))
        self.assertEqual(old_frame.read_bytes(), old_frame_bytes)

    def test_recut_sheet_regenerates_frames_for_current_boundaries(self) -> None:
        analysis = self.seed()
        old_frames = self.service.extract_frames(analysis["analysis_id"], frame_width=480)
        old_paths = {item["path"] for pair in old_frames.values() for item in pair.values()}
        self.service.recut(analysis["analysis_id"], splits=[1.0], merges=[4.0])
        before = len(self.adapter.calls)
        sheets = self.service.build_contact_sheets(analysis["analysis_id"], cols=3, rows=1)
        new_calls = self.adapter.calls[before:]
        frame_calls = [argv for kind, argv in new_calls if kind == "ffmpeg" and "-ss" in argv]
        self.assertEqual(len(frame_calls), 6)
        self.assertIn("0.15", frame_calls[0])
        sheet_inputs = [argv[index + 1] for kind, argv in new_calls for index, value in enumerate(argv) if kind == "ffmpeg" and value == "-i"]
        self.assertTrue(sheets)
        changed_old_paths = {
            item["path"]
            for shot_id, pair in old_frames.items()
            if shot_id in {"S01", "S02"}
            for item in pair.values()
        }
        self.assertTrue(changed_old_paths.isdisjoint(sheet_inputs))
        self.assertTrue(old_paths.intersection(sheet_inputs))

    def test_changed_encoder_bytes_get_distinct_content_addresses(self) -> None:
        analysis = self.seed()
        first = self.service.extract_frames(analysis["analysis_id"], frame_width=320)
        first_path = Path(first["S01"]["a"]["path"])
        first_bytes = first_path.read_bytes()
        self.adapter.encoder_variant = "v2"
        second = self.service.extract_frames(analysis["analysis_id"], frame_width=320)
        second_path = Path(second["S01"]["a"]["path"])
        self.assertNotEqual(second_path, first_path)
        self.assertEqual(first_path.read_bytes(), first_bytes)
        self.assertEqual(first_path.stem, first["S01"]["a"]["sha256"])
        self.assertEqual(second_path.stem, second["S01"]["a"]["sha256"])
        manifests = list(first_path.parents[2].glob("frame_manifests/*/*.json"))
        self.assertGreaterEqual(len(manifests), 2)

        first_sheet = self.service.build_contact_sheets(
            analysis["analysis_id"], cols=3, rows=1
        )[0]
        first_sheet_path = Path(first_sheet["path"])
        first_sheet_bytes = first_sheet_path.read_bytes()
        self.adapter.encoder_variant = "v3"
        second_sheet = self.service.build_contact_sheets(
            analysis["analysis_id"], cols=3, rows=1
        )[0]
        second_sheet_path = Path(second_sheet["path"])
        self.assertNotEqual(second_sheet_path, first_sheet_path)
        self.assertEqual(first_sheet_path.read_bytes(), first_sheet_bytes)
        self.assertEqual(first_sheet_path.stem, first_sheet["sha256"])
        self.assertEqual(second_sheet_path.stem, second_sheet["sha256"])

    def test_corrupt_frame_and_sheet_are_never_returned_as_reusable(self) -> None:
        analysis = self.seed()
        frames = self.service.extract_frames(analysis["analysis_id"], frame_width=480)
        frame_path = Path(frames["S01"]["a"]["path"])
        frame_path.chmod(0o600)
        frame_path.write_bytes(b"corrupt-frame")
        with self.assertRaises(MediaOutputError):
            self.service.build_contact_sheets(analysis["analysis_id"], cols=3, rows=1)

    def test_symlink_at_content_address_is_never_reused(self) -> None:
        analysis = self.seed()
        frames = self.service.extract_frames(analysis["analysis_id"], frame_width=480)
        frame_path = Path(frames["S01"]["a"]["path"])
        replacement = self.root / "same-frame-bytes.png"
        replacement.write_bytes(frame_path.read_bytes())
        frame_path.unlink()
        frame_path.symlink_to(replacement)
        with self.assertRaises(MediaOutputError):
            self.service.build_contact_sheets(analysis["analysis_id"], cols=3, rows=1)

    def test_verified_manifest_read_is_bounded(self) -> None:
        oversized = self.root / "oversized-manifest.json"
        oversized.write_bytes(b"x" * (1024 * 1024 + 1))
        with self.assertRaisesRegex(MediaOutputError, "size limit"):
            ReferenceVideoService._read_verified_file(oversized)

    def test_manifest_path_swap_after_fd_read_fails_closed(self) -> None:
        analysis = self.seed()
        frames = self.service.extract_frames(analysis["analysis_id"], frame_width=480)
        frame_path = Path(frames["S01"]["a"]["path"])
        manifest = next(frame_path.parents[2].glob("frame_manifests/*/*.json"))
        replacement = self.root / "replacement-manifest.json"
        replacement.write_bytes(manifest.read_bytes())
        manifest_input = json.loads(manifest.read_text(encoding="utf-8"))["input"]
        racing_fstat = self.swap_path_after_second_fstat(manifest, replacement)
        with patch("scripts.reference_video_service.os.fstat", side_effect=racing_fstat):
            with self.assertRaises(MediaOutputError):
                self.service._load_artifact_manifest(
                    frame_path.parents[2] / "frame_manifests", manifest_input
                )

    def test_artifact_path_swap_after_fd_hash_fails_closed(self) -> None:
        analysis = self.seed()
        frames = self.service.extract_frames(analysis["analysis_id"], frame_width=480)
        frame_path = Path(frames["S01"]["a"]["path"])
        replacement = self.root / "replacement-frame.png"
        replacement.write_bytes(frame_path.read_bytes())
        racing_fstat = self.swap_path_after_second_fstat(frame_path, replacement)
        with patch("scripts.reference_video_service.os.fstat", side_effect=racing_fstat):
            with self.assertRaises(MediaOutputError):
                self.service.build_contact_sheets(analysis["analysis_id"], cols=3, rows=1)

        self.adapter.encoder_variant = "fresh"
        frames = self.service.extract_frames(analysis["analysis_id"], frame_width=480)
        sheets = self.service.build_contact_sheets(analysis["analysis_id"], cols=3, rows=1)
        sheet_path = Path(sheets[0]["path"])
        sheet_path.chmod(0o600)
        sheet_path.write_bytes(b"corrupt-sheet")
        with self.assertRaises(MediaOutputError):
            self.service.build_contact_sheets(analysis["analysis_id"], cols=3, rows=1)


if __name__ == "__main__":
    unittest.main()
