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
        path = self.root / f"{shot_id}.mp4"; path.write_bytes(payload); os.chmod(path, 0o600)
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
        plan = self.build(); argv = self.service.build_ffmpeg_argv(plan)
        expected = json.loads((Path(__file__).parent / "fixtures/reference_video/expected-ffmpeg-argv.json").read_text())
        expected = [value.replace("{staging_dir}", str(plan.staging_dir)).replace("{output}", str(self.root / "final.mp4")) for value in expected]
        self.assertEqual(argv, expected)

    def test_raw_filter_codec_and_extra_argv_fields_are_rejected(self):
        for forbidden in ("filter_complex", "codec", "extra_args"):
            with self.subTest(forbidden=forbidden), self.assertRaises(CompositionPlanError): self.build(**{forbidden: "unsafe"})

    def test_synchronized_review_receipt_is_rejected_by_real_composition_consumer(self):
        review = self.clip("S01")
        review["artifact_role"] = "synchronized_review"
        with self.assertRaisesRegex(CompositionPlanError, "review evidence"):
            self.build(clips=[review, self.clip("S02")])

    def test_dimensions_fps_transition_and_duration_are_closed(self):
        for change in ({"width": 1279}, {"height": 200}, {"fps": 60}, {"transitions": [{"kind":"wipe","duration_seconds":.2}]},
                       {"target_duration_seconds": 4}):
            with self.subTest(change=change), self.assertRaises(CompositionPlanError): self.build(**change)

    def test_compose_rechecks_staged_digest_audio_and_publishes_no_overwrite(self):
        audio_plan = {"audio_policy": "silent"}; plan = self.build(audio_plan=audio_plan)
        output = self.service.compose(plan)
        self.assertEqual(output.read_bytes(), b"mp4"); self.assertEqual(self.audio.calls, 2)
        with self.assertRaises(CompositionPlanError): self.service.compose(plan)
        self.assertEqual(len(self.adapter.calls), 1)

    def test_mutated_input_blocks_before_ffmpeg(self):
        plan = self.build(); Path(plan.clips[0].source_path).write_bytes(b"changed")
        with self.assertRaises(CompositionPlanError): self.service.compose(plan)
        self.assertFalse(self.adapter.calls)

    def test_subtitle_must_be_the_verified_audio_plan_receipt(self):
        subtitle = self.clip("subtitle", b"1\n", 1)
        subtitle = {"artifact_role":"subtitle_srt", **{k: subtitle[k] for k in ("path","sha256","size_bytes")}}
        audio_plan = {"audio_policy":"silent", "subtitles":[subtitle]}
        with self.assertRaises(CompositionPlanError):
            self.build(audio_plan=audio_plan, subtitle={**subtitle, "sha256":"0"*64}, subtitle_mode="mux")

    def test_xfade_is_strictly_shorter_than_both_adjacent_clips(self):
        with self.assertRaises(CompositionPlanError):
            self.build(transitions=[{"kind":"crossfade","duration_seconds":2}], target_duration_seconds=2)

    def test_layout_and_cards_are_closed_and_included_in_duration(self):
        plan = self.build(layout_mode="scale_crop", title_card={"text":"A: title", "duration_seconds":1, "style":"dark"},
                          end_card={"text":"End", "duration_seconds":1, "style":"light"}, target_duration_seconds=5.75)
        argv = self.service.build_ffmpeg_argv(plan)
        self.assertIn("force_original_aspect_ratio=increase", argv[argv.index("-filter_complex") + 1])
        self.assertIn("drawtext=textfile=", argv[argv.index("-filter_complex") + 1])
        with self.assertRaises(CompositionPlanError): self.build(layout_mode="raw")

    def test_staging_is_unique_and_removed_after_success(self):
        first = self.build(output_path=self.root/"one.mp4")
        second = self.build(output_path=self.root/"two.mp4")
        self.assertNotEqual(first.staging_dir, second.staging_dir)
        first_dir = first.staging_dir
        self.service.compose(first)
        self.assertFalse(first_dir.exists())

    def test_mux_maps_audio_and_subtitle_independently(self):
        media = self.root/"caption.srt"; media.write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n"); os.chmod(media,0o600)
        subtitle = {"artifact_role":"subtitle_srt", "path":str(media), "sha256":hashlib.sha256(media.read_bytes()).hexdigest(), "size_bytes":media.stat().st_size}
        audio_plan = {"audio_policy":"full_redesign", "subtitles":[subtitle]}
        plan = self.build(audio_plan=audio_plan, subtitle=subtitle, subtitle_mode="mux")
        argv = self.service.build_ffmpeg_argv(plan)
        self.assertIn("-c:s", argv)
        self.assertIn("mov_text", argv)

    def test_four_audio_policies_have_closed_literal_graphs(self):
        def artifact(role, name, **extra):
            path=self.root/name; path.write_bytes(role.encode()); os.chmod(path,0o600)
            return {"artifact_role":role,"path":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"size_bytes":path.stat().st_size,**extra}
        narration=artifact("new_narration","n.aiff")
        music=artifact("music","m.wav",intent={"loop":True,"trim_to_seconds":1,"use_full_track":False})
        effect=artifact("effect","e.wav",at_seconds=.5,duration_seconds=1)
        for policy, expected in {
            "full_redesign":("sidechaincompress=threshold=0.05:ratio=8:attack=20:release=250","-c:a"),
            "preserve_authorized_audio":("aloop=loop=-1:size=48000","-c:a"),
            "subtitles_only":("amix=inputs=3:duration=longest:normalize=0","-c:a"),
            "silent":("", "-an"),
        }.items():
            plan_value={"audio_policy":policy,"subtitles":[],"narration":None if policy=="silent" else narration,
                        "music":None if policy=="silent" else music,"effects":[] if policy=="silent" else [effect],"ambience":[]}
            plan=self.build(audio_plan=plan_value,output_path=self.root/f"{policy}.mp4")
            argv=self.service.build_ffmpeg_argv(plan); graph=argv[argv.index("-filter_complex")+1]
            self.assertIn(expected[0],graph); self.assertIn(expected[1],argv)


if __name__ == "__main__": unittest.main()
