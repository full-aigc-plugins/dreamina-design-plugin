import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.media_adapter import MediaResult
from scripts.video_composition_service import CompositionPlanError, VideoCompositionService


class FakeAudioService:
    def __init__(self): self.calls = 0
    def verify_for_use(self, plan): self.calls += 1; return dict(plan)


class FakeAdapter:
    def __init__(self): self.calls = []
    def run(self, kind, argv, *, timeout_seconds):
        self.calls.append((kind, list(argv), timeout_seconds))
        Path(argv[-1]).write_bytes(b"mp4")
        return MediaResult(0, "", "")


class VideoCompositionServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name); os.chmod(self.root, 0o700)
        self.render = self.root / "render"; self.render.mkdir(mode=0o700)
        self.audio = FakeAudioService(); self.adapter = FakeAdapter()
        self.service = VideoCompositionService(self.adapter, self.audio, self.render)

    def tearDown(self): self.temp.cleanup()

    def clip(self, shot_id, payload=b"clip", duration=2):
        path = self.root / f"{shot_id}.mp4"; path.write_bytes(payload)
        return {"shot_id": shot_id, "path": str(path), "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload), "duration_seconds": duration, "accepted": True}

    def build(self, **changes):
        values = dict(required_shots=["S01", "S02"], clips=[self.clip("S01"), self.clip("S02")],
                      transitions=[{"kind": "crossfade", "duration_seconds": .25}], width=1280, height=720,
                      fps=25, target_duration_seconds=3.75, audio_plan=None, subtitle=None,
                      subtitle_mode="none", output_path=self.root / "final.mp4")
        values.update(changes); return self.service.build_plan(**values)

    def test_timeline_requires_each_required_shot_exactly_once_in_order(self):
        with self.assertRaises(CompositionPlanError):
            self.build(clips=[self.clip("S02"), self.clip("S02")])

    def test_filter_graph_is_derived_only_from_closed_options(self):
        argv = self.service.build_ffmpeg_argv(self.build())
        expected = json.loads((Path(__file__).parent / "fixtures/reference_video/expected-ffmpeg-argv.json").read_text())
        expected = [value.replace("{render_root}", str(self.render)).replace("{output}", str(self.root / "final.mp4")) for value in expected]
        self.assertEqual(argv, expected)

    def test_raw_filter_codec_and_extra_argv_fields_are_rejected(self):
        for forbidden in ("filter_complex", "codec", "extra_args"):
            with self.subTest(forbidden=forbidden), self.assertRaises(CompositionPlanError): self.build(**{forbidden: "unsafe"})

    def test_dimensions_fps_transition_and_duration_are_closed(self):
        for change in ({"width": 1279}, {"height": 200}, {"fps": 60}, {"transitions": [{"kind":"wipe","duration_seconds":.2}]},
                       {"target_duration_seconds": 4}):
            with self.subTest(change=change), self.assertRaises(CompositionPlanError): self.build(**change)

    def test_compose_rechecks_staged_digest_audio_and_publishes_no_overwrite(self):
        audio_plan = {"audio_policy": "silent"}; plan = self.build(audio_plan=audio_plan)
        output = self.service.compose(plan)
        self.assertEqual(output.read_bytes(), b"mp4"); self.assertEqual(self.audio.calls, 1)
        with self.assertRaises(CompositionPlanError): self.service.compose(plan)
        self.assertEqual(len(self.adapter.calls), 1)

    def test_mutated_input_blocks_before_ffmpeg(self):
        plan = self.build(); Path(plan.clips[0].source_path).write_bytes(b"changed")
        with self.assertRaises(CompositionPlanError): self.service.compose(plan)
        self.assertFalse(self.adapter.calls)


if __name__ == "__main__": unittest.main()
