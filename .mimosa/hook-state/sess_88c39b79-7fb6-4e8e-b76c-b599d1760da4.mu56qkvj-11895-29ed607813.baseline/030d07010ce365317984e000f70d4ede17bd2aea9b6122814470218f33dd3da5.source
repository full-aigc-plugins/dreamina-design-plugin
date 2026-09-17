"""End-to-end failures must resume one exact subject and corroboration receipt."""
import copy
import json
import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from tests import test_reelbench_project_service as project_fixtures
from tests import test_video_redesign_service as redesign_fixtures
from scripts.json_contracts import canonical_fingerprint
from scripts.reelbench_binding_service import CompositeBindingIndeterminateError, ReelBenchBindingService
from scripts.shot_analysis_service import ShotAnalysisService
from scripts.video_redesign_service import VideoRedesignService


class CompositeTests(unittest.TestCase):
    def setUp(self):
        fixture = project_fixtures.ReelBenchProjectServiceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.store, self.project_id = fixture.store, fixture.project_id
        self.service = fixture.service
        if hasattr(self, "candidate"):
            del self.candidate
        evidence = fixture._validated_reelbench()
        analysis = fixture._write_native_analysis([
            {"start_seconds": 0.0, "end_seconds": 8.0, "motion_median": 0.0}])
        # Supply actual immutable frame metadata so all native annotation gates run.
        analysis["frame_checksums"] = {f"S01:{label}": {
            "sha256": "e" * 64, "path": "/private/frame.png", "at_seconds": at,
            "frame_width": 480, "boundary_fingerprint": "f" * 64,
        } for label, at in (("a", .5), ("b", 7.5))}
        analysis["machine_fingerprint"] = canonical_fingerprint({
            "source": analysis["source"], "parameters": analysis["parameters"],
            "cuts": analysis["cuts"], "measured_shots": [s["measured"] for s in analysis["shots"]],
            "frame_checksums": analysis["frame_checksums"],
        })
        # Fixture setup replaces before the first comparison or consumer exists.
        target = self.store.project_root(self.project_id) / "analysis/v001.json"
        target.write_text(json.dumps(analysis))
        self.comparison = fixture.service.compare_native(self.project_id, "v001", evidence["version"])
        self.annotation = {
            "schema_version": "1.0", "machine_fingerprint": analysis["machine_fingerprint"],
            "shots": [{"id": "S01", "shot_size": "wide", "category": "subject",
                "category_evidence": "subject presenter", "camera": "static",
                "frame_description": "A clearly described presenter inside a bright modern studio.",
                "rhythm_role": None, "rhythm_evidence": None, "subjects": [],
                "on_screen_text": [], "dialogue": [], "narration": [], "music": [],
                "sound": [], "confidence": .9, "review_note": ""}], "transcript": None,
        }
        self.analysis = analysis

    def invoke(self, family, retry=None):
        if family == "annotation":
            return ShotAnalysisService(self.store).validate_and_persist(
                self.project_id, "v001", self.annotation, comparison_version="v001",
                binding_indeterminate_commit=retry)
        if not hasattr(self, "candidate"):
            self.store.write_version(self.project_id, "annotation",
                {**self.annotation, "analysis_version": "v001"}, schema_name="shot_annotation.schema.json")
            payload = redesign_fixtures.VideoRedesignServiceTests.payload(self,
                machine_fingerprint=self.analysis["machine_fingerprint"], target_duration_seconds=8.0)
            self.candidate = VideoRedesignService(self.store).prepare_candidate(self.project_id, "v001", payload)
        return VideoRedesignService(self.store).commit_version(self.candidate, None,
            comparison_version="v001", binding_indeterminate_commit=retry)

    @property
    def fingerprint(self):
        return self.analysis["machine_fingerprint"]

    def test_public_flows_recover_binding_failure_without_second_subject(self):
        for family in ("annotation", "redesign"):
            with self.subTest(family=family):
                # A new fixture per flow keeps both subject counters at v001.
                if family == "redesign":
                    self.setUp()
                real = self.store.write_version
                def fail(*args, **kwargs):
                    if args[1] == "reelbench_binding":
                        raise OSError("binding disk failure before visibility")
                    return real(*args, **kwargs)
                with patch.object(self.store, "write_version", side_effect=fail):
                    with self.assertRaises(CompositeBindingIndeterminateError) as caught:
                        self.invoke(family)
                error = caught.exception
                self.assertEqual(error.subject_version, "v001")
                self.assertEqual(error.comparison_version, "v001")
                self.assertEqual(error.binding_version, "v001")
                self.assertTrue(error.journal_path.exists())
                self.invoke(family, error)
                root = self.store.project_root(self.project_id)
                self.assertEqual([p.name for p in (root / family).glob('v*.json')], ['v001.json'])
                self.assertEqual([p.name for p in (root / 'reelbench_binding').glob('v*.json')], ['v001.json'])

    def test_empty_motion_slice_is_missing_coverage(self):
        self.assertEqual(self.service._upstream_median_motion({"hz": 1, "values": [0]}, 0, .2), (None, False))

    def test_timeline_mismatch_identifies_actual_shot(self):
        native = copy.deepcopy(self.analysis)
        native['cuts'] = [0, 4, 8]
        native['shots'] = [copy.deepcopy(native['shots'][0]), copy.deepcopy(native['shots'][0])]
        native['shots'][0]['measured'].update(end_seconds=4, duration_seconds=4)
        native['shots'][1]['id'] = 'S02'
        native['shots'][1]['measured'].update(start_seconds=5, end_seconds=8, duration_seconds=3)
        reel = {'shots': {'meta': {'durationSeconds': 8}, 'shots': [
            {'id': 'S01', 'start': 0, 'end': 4, 'seconds': 4},
            {'id': 'S02', 'start': 4, 'end': 8, 'seconds': 4}]},
            'motions': [{'motion': 0, 'covered': True}, {'motion': 0, 'covered': True}]}
        _, mismatches = self.service._compare(native, {'source_sha256': native['source']['source_sha256'],
            'source_receipt_version': 'v001'}, reel)
        rows = [m for m in mismatches if m['domain'] == 'timeline_continuity']
        self.assertEqual([m['shot_id'] for m in rows], ['S02'])

    def test_legacy_writer_skips_pending_subject_reservations(self):
        document = {**self.annotation, 'analysis_version': 'v001'}
        reserved = self.store.reserve_version(self.project_id, 'annotation', document,
            schema_name='shot_annotation.schema.json', operation_id='a' * 64)
        written = self.store.write_version(self.project_id, 'annotation', document,
            schema_name='shot_annotation.schema.json')
        self.assertEqual((reserved['version'], written['version']), ('v001', 'v002'))
        self.assertEqual(self.store.publish_reserved_version(self.project_id, reserved)['version'], 'v001')

    def test_reservations_are_distinct_and_idempotent_before_and_after_publication(self):
        document = {**self.annotation, 'analysis_version': 'v001'}
        def reserve(key):
            return self.store.reserve_version(self.project_id, 'annotation', document,
                schema_name='shot_annotation.schema.json', operation_id=key * 64)
        first, second = reserve('a'), reserve('b')
        self.assertEqual((first['version'], second['version']), ('v001', 'v002'))
        self.assertEqual(reserve('a'), first)
        self.store.publish_reserved_version(self.project_id, first)
        self.assertEqual(reserve('a'), first)

    def test_forged_reservation_cannot_choose_a_different_version(self):
        document = {**self.annotation, 'analysis_version': 'v001'}
        reservation = self.store.reserve_version(self.project_id, 'annotation', document,
            schema_name='shot_annotation.schema.json', operation_id='a' * 64)
        reservation['version'] = reservation['payload']['version'] = 'v099'
        reservation['payload_fingerprint'] = canonical_fingerprint(reservation['payload'])
        path = self.store.project_root(self.project_id) / '.reservations' / ('a' * 64 + '.json')
        path.write_text(json.dumps(reservation))
        with self.assertRaises(ValueError):
            self.store.publish_reserved_version(self.project_id, reservation)

    def test_failure_matrix_returns_exact_composite_and_recovers_both_flows(self):
        from scripts.reelbench_composite_journal import CompositeJournal
        for family in ('annotation', 'redesign'):
            for point in ('subject_before', 'subject_after', 'binding_before', 'binding_after',
                          'subject_phase', 'binding_phase', 'complete_phase', 'cleanup_before', 'cleanup_after'):
                with self.subTest(family=family, point=point):
                    self.setUp()
                    write, save, finish = self.store.write_version, CompositeJournal.save, CompositeJournal.finish
                    def write_fault(*args, **kwargs):
                        label = 'binding' if args[1] == 'reelbench_binding' else 'subject' if args[1] == family else 'other'
                        if point == label + '_before':
                            raise OSError(point)
                        result = write(*args, **kwargs)
                        if point == label + '_after':
                            raise OSError(point)
                        return result
                    def save_fault(journal, record, guard):
                        save(journal, record, guard)
                        if point == record['phase'] + '_phase':
                            raise OSError(point)
                    def finish_fault(journal, record, guard):
                        if point == 'cleanup_before':
                            raise OSError(point)
                        finish(journal, record, guard)
                        if point == 'cleanup_after':
                            raise OSError(point)
                    with patch.object(self.store, 'write_version', side_effect=write_fault), \
                         patch.object(CompositeJournal, 'save', save_fault), patch.object(CompositeJournal, 'finish', finish_fault):
                        with self.assertRaises(CompositeBindingIndeterminateError) as caught:
                            self.invoke(family)
                    error = caught.exception
                    self.assertEqual((error.subject_version, error.binding_version), ('v001', 'v001'))
                    self.invoke(family, error)
                    # The same receipt remains usable after cleanup and later wall-clock time.
                    self.invoke(family, error)
                    root = self.store.project_root(self.project_id)
                    self.assertEqual(sorted(p.name for p in (root / family).glob('v*.json')), ['v001.json'])
                    self.assertEqual(sorted(p.name for p in (root / 'reelbench_binding').glob('v*.json')), ['v001.json'])
                    self.assertFalse(error.journal_path.exists())
                    self.assertTrue(error.completed_journal_path.exists())

    def test_same_and_distinct_bindings_are_serialized_without_duplicates(self):
        subject = self.store.write_version(self.project_id, 'annotation',
            {**self.annotation, 'analysis_version': 'v001'}, schema_name='shot_annotation.schema.json')
        other = self.store.write_version(self.project_id, 'annotation',
            {**self.annotation, 'analysis_version': 'v001'}, schema_name='shot_annotation.schema.json')
        def bind(version):
            return ReelBenchBindingService(self.store).bind(self.project_id, subject_family='annotation',
                subject_version=version, comparison_version='v001')
        with ThreadPoolExecutor(max_workers=4) as pool:
            receipts = list(pool.map(bind, [subject['version'], subject['version'], other['version'], other['version']]))
        self.assertEqual(receipts[0], receipts[1])
        self.assertEqual(receipts[2], receipts[3])
        self.assertNotEqual(receipts[0]['version'], receipts[2]['version'])
        self.assertEqual(bind('v001'), receipts[0])

    def test_same_composite_concurrent_calls_share_exact_subject(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.invoke('annotation'), range(4)))
        self.assertTrue(all(r == results[0] for r in results))
        self.assertEqual(results[0]['annotation_version'], 'v001')

    def test_process_death_resumes_exact_versions_without_exception_token(self):
        child = r'''
import json, os, sys
from unittest.mock import patch
from scripts.video_project_store import VideoProjectStore
from scripts.shot_analysis_service import ShotAnalysisService
from scripts.video_redesign_service import VideoRedesignService
from scripts.reelbench_composite_journal import CompositeJournal
request = json.loads(sys.stdin.read())
store = VideoProjectStore(__import__('pathlib').Path(request['root']))
point, family = request['point'], request['family']
reserve, write, save, finish = store.reserve_version, store.write_version, CompositeJournal.save, CompositeJournal.finish
link, replace, unlink = os.link, os.replace, os.unlink
def linking(src, dst, **kwargs):
    result = link(src, dst, **kwargs)
    if point == 'reserve_visible' and str(dst).startswith('.reservations/'):
        os._exit(73)
    for target in (family, 'reelbench_binding'):
        path = store._root / request['project_id'] / target
        if point == target + '_visible' and dst == 'v001.json' and path.exists() and os.fstat(kwargs['dst_dir_fd']).st_ino == path.stat().st_ino:
            os._exit(73)
    return result
def replacing(src, dst, **kwargs):
    result = replace(src, dst, **kwargs)
    if point == 'journal_visible' and str(dst).startswith('.reelbench-composites/'):
        os._exit(73)
    if point == 'cleanup_visible' and str(dst).startswith('.reelbench-completed/'):
        os._exit(73)
    return result
def reserving(*args, **kwargs):
    result = reserve(*args, **kwargs)
    if point == 'reservation' and args[1] == family:
        os._exit(73)
    return result
def writing(*args, **kwargs):
    result = write(*args, **kwargs)
    if (point == 'subject' and args[1] == family) or (point == 'binding' and args[1] == 'reelbench_binding'):
        os._exit(73)
    return result
def saving(journal, record, guard):
    save(journal, record, guard)
    if point == 'journal' and record['phase'] == 'reserved':
        os._exit(73)
def finishing(journal, record, guard):
    finish(journal, record, guard)
    if point == 'cleanup':
        os._exit(73)
with patch.object(store, 'reserve_version', side_effect=reserving), patch.object(store, 'write_version', side_effect=writing), patch.object(CompositeJournal, 'save', saving), patch.object(CompositeJournal, 'finish', finishing), patch('os.link', side_effect=linking), patch('os.replace', side_effect=replacing):
    if family == 'annotation':
        ShotAnalysisService(store).validate_and_persist(request['project_id'], 'v001', request['annotation'], comparison_version='v001')
    else:
        VideoRedesignService(store).commit_version(request['candidate'], None, comparison_version='v001')
sys.exit(74)
'''
        for family in ('annotation', 'redesign'):
            for point in ('reservation', 'reserve_visible', 'journal', 'journal_visible',
                          'subject', family + '_visible', 'binding', 'reelbench_binding_visible',
                          'cleanup', 'cleanup_visible'):
                with self.subTest(family=family, point=point):
                    self.setUp()
                    if family == 'redesign':
                        self.store.write_version(self.project_id, 'annotation',
                            {**self.annotation, 'analysis_version': 'v001'}, schema_name='shot_annotation.schema.json')
                        payload = redesign_fixtures.VideoRedesignServiceTests.payload(self, target_duration_seconds=8.0)
                        self.candidate = VideoRedesignService(self.store).prepare_candidate(self.project_id, 'v001', payload)
                    request = {'root': str(self.store._root), 'project_id': self.project_id, 'family': family,
                        'point': point, 'annotation': self.annotation, 'candidate': getattr(self, 'candidate', None)}
                    result = subprocess.run([sys.executable, '-c', child], input=json.dumps(request),
                        capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 73, result.stderr)
                    self.invoke(family)
                    root = self.store.project_root(self.project_id)
                    self.assertEqual([p.name for p in (root / family).glob('v*.json')], ['v001.json'])
                    self.assertEqual([p.name for p in (root / 'reelbench_binding').glob('v*.json')], ['v001.json'])

    def test_fsync_faults_at_subject_binding_and_cleanup_are_recoverable(self):
        for family in ('annotation', 'redesign'):
            for target in (family, family + '/v001.json', 'reelbench_binding',
                           'reelbench_binding/v001.json', '.reelbench-completed', '.reservations'):
                with self.subTest(family=family, target=target):
                    self.setUp()
                    root = self.store.project_root(self.project_id)
                    fsync = os.fsync
                    def failing(fd):
                        visible = (root / family / 'v001.json').exists()
                        bound = (root / 'reelbench_binding/v001.json').exists()
                        folder = root / target
                        if visible and (target != '.reservations' or bound) and folder.exists() and os.fstat(fd).st_ino == folder.stat().st_ino:
                            raise OSError('directory fsync failure')
                        return fsync(fd)
                    with patch('os.fsync', side_effect=failing):
                        with self.assertRaises(CompositeBindingIndeterminateError) as caught:
                            self.invoke(family)
                    self.invoke(family, caught.exception)
                    self.assertFalse((root / family / 'v002.json').exists())

    def interrupted(self):
        real = self.store.write_version
        def fail(*args, **kwargs):
            if args[1] == 'reelbench_binding':
                raise OSError('pause before sidecar')
            return real(*args, **kwargs)
        with patch.object(self.store, 'write_version', side_effect=fail):
            with self.assertRaises(CompositeBindingIndeterminateError) as caught:
                self.invoke('annotation')
        return caught.exception

    def test_forged_or_wrong_retry_identity_never_allocates(self):
        error = self.interrupted()
        for field, value in (('subject_version', 'v002'), ('subject_fingerprint', '0' * 64),
                             ('comparison_version', 'v002'), ('binding_version', 'v002'),
                             ('binding_fingerprint', '0' * 64), ('journal_id', '0' * 64)):
            with self.subTest(field=field):
                original = getattr(error, field)
                setattr(error, field, value)
                with self.assertRaises(ValueError):
                    self.invoke('annotation', error)
                setattr(error, field, original)
        self.assertFalse((self.store.project_root(self.project_id) / 'annotation/v002.json').exists())
        self.invoke('annotation', error)

    def test_forged_deleted_or_nonprivate_journal_blocks_recovery(self):
        for change in ('forged', 'deleted', 'mode', 'symlink'):
            with self.subTest(change=change):
                self.setUp()
                error = self.interrupted()
                path = error.journal_path
                if change == 'forged':
                    data = json.loads(path.read_text())
                    data['phase'] = 'complete'
                    path.write_text(json.dumps(data))
                elif change == 'deleted':
                    path.unlink()
                elif change == 'mode':
                    path.chmod(0o644)
                else:
                    other = path.with_suffix('.copy')
                    path.rename(other)
                    path.symlink_to(other)
                with self.assertRaises((ValueError, OSError)):
                    self.invoke('annotation', error)
                self.assertFalse((self.store.project_root(self.project_id) / 'annotation/v002.json').exists())

    def test_tampered_subject_and_sidecar_are_not_replaced(self):
        for family in ('annotation', 'reelbench_binding'):
            with self.subTest(family=family):
                self.setUp()
                self.invoke('annotation')
                root = self.store.project_root(self.project_id)
                path = root / family / 'v001.json'
                data = json.loads(path.read_text())
                if family == 'annotation':
                    data['shots'][0]['review_note'] = 'forged'
                else:
                    data['comparison_version'] = 'v099'
                    data['binding_fingerprint'] = canonical_fingerprint({k: v for k, v in data.items() if k != 'binding_fingerprint'})
                path.write_text(json.dumps(data))
                before = path.read_bytes()
                with self.assertRaises(CompositeBindingIndeterminateError):
                    self.invoke('annotation')
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse((root / family / 'v002.json').exists())

    def test_atomic_writer_does_not_close_a_reused_descriptor_after_failure(self):
        from scripts.video_project_store import VersionCommitIndeterminateError
        root = self.store.project_root(self.project_id)
        directory = root / 'annotation'
        directory.mkdir(mode=0o700)
        real_close, real_fsync = os.close, os.fsync
        unrelated = []
        def closing(fd):
            is_target = os.fstat(fd).st_ino == directory.stat().st_ino
            real_close(fd)
            if is_target and not unrelated:
                # Reuse exactly the descriptor just closed by the writer.
                unrelated.append(os.open(root / 'project.json', os.O_RDONLY))
        def syncing(fd):
            if os.fstat(fd).st_ino == directory.stat().st_ino:
                raise OSError('directory durability failed')
            return real_fsync(fd)
        try:
            with patch('os.close', side_effect=closing), patch('os.fsync', side_effect=syncing):
                with self.assertRaises(VersionCommitIndeterminateError):
                    self.store.write_version(self.project_id, 'annotation',
                        {**self.annotation, 'analysis_version': 'v001'}, schema_name='shot_annotation.schema.json')
            self.assertEqual(len(unrelated), 1)
            os.fstat(unrelated[0])
        finally:
            for fd in unrelated:
                try:
                    real_close(fd)
                except OSError:
                    pass

    def test_prevalidation_rejects_manual_review_and_forged_matched_before_subject(self):
        for forge in (False, True):
            with self.subTest(forge=forge):
                self.setUp()
                native = copy.deepcopy(self.analysis)
                native['shots'][0]['measured']['motion_median'] = 3.0
                native['machine_fingerprint'] = canonical_fingerprint({
                    'source': native['source'], 'parameters': native['parameters'], 'cuts': native['cuts'],
                    'measured_shots': [s['measured'] for s in native['shots']], 'frame_checksums': native['frame_checksums']})
                root = self.store.project_root(self.project_id)
                (root / 'analysis/v001.json').write_text(json.dumps(native))
                comparison = self.service.compare_native(self.project_id, 'v001', self.comparison['reelbench_evidence_version'])
                self.assertEqual(comparison['overall'], 'manual_review')
                comparison['version'] = 'v001'
                if forge:
                    comparison['overall'] = 'matched'
                    comparison['mismatches'] = []
                    comparison['domains'] = {k: {'verdict': 'matched', 'reasons': []} for k in comparison['domains']}
                comparison['comparison_fingerprint'] = canonical_fingerprint({k: v for k, v in comparison.items() if k != 'comparison_fingerprint'})
                (root / 'reelbench_comparison/v001.json').write_text(json.dumps(comparison))
                self.annotation['machine_fingerprint'] = native['machine_fingerprint']
                # Native camera claims now agree with the new native measurements.
                self.annotation['shots'][0]['camera'] = 'unknown'
                with self.assertRaises(ValueError):
                    self.invoke('annotation')
                self.assertFalse((root / 'annotation/v001.json').exists())

    def test_recovery_fails_when_project_inode_or_request_changes(self):
        error = self.interrupted()
        self.annotation['shots'][0]['review_note'] = 'different request'
        with self.assertRaises(ValueError):
            self.invoke('annotation', error)
        self.annotation['shots'][0]['review_note'] = ''
        root = self.store.project_root(self.project_id)
        import shutil
        old = root.with_name(root.name + '-old')
        root.rename(old)
        shutil.copytree(old, root)
        with self.assertRaises(ValueError):
            self.invoke('annotation', error)
        self.assertFalse((root / 'annotation/v002.json').exists())

    def test_forged_design_fingerprint_is_rejected_by_direct_binding(self):
        self.invoke('redesign')
        path = self.store.project_root(self.project_id) / 'redesign/v001.json'
        design = json.loads(path.read_text())
        design['payload']['concept'] = 'changed after fingerprinting'
        path.write_text(json.dumps(design))
        with self.assertRaises(ValueError):
            ReelBenchBindingService(self.store).bind(self.project_id, subject_family='video_design',
                subject_version='v001', comparison_version='v001')

    def test_repeated_composite_failures_release_every_descriptor(self):
        import fcntl
        def descriptors():
            result = set()
            for fd in range(256):
                try:
                    fcntl.fcntl(fd, fcntl.F_GETFD)
                    result.add(fd)
                except OSError:
                    pass
            return result
        before = descriptors()
        error = self.interrupted()
        for _ in range(5):
            with patch.object(self.store, 'publish_reserved_version', side_effect=OSError('retry failed')):
                with self.assertRaises(CompositeBindingIndeterminateError):
                    self.invoke('annotation', error)
            self.assertEqual(descriptors(), before)
        self.invoke('annotation', error)
        self.assertEqual(descriptors(), before)

    def test_annotation_state_transition_failure_keeps_composite_identity(self):
        self.store.transition(self.project_id, expected='created', next_state='analyzing', evidence={})
        with patch.object(self.store, 'transition', side_effect=OSError('project state write failed')):
            with self.assertRaises(CompositeBindingIndeterminateError) as caught:
                self.invoke('annotation')
        result = self.invoke('annotation', caught.exception)
        self.assertEqual(result['annotation_version'], 'v001')
        self.assertEqual(self.store.get(self.project_id)['state'], 'analysis_review')

    def test_reservation_failure_before_subject_is_safe_to_retry_without_new_version(self):
        for family in ('annotation', 'redesign'):
            for after in (False, True):
                with self.subTest(family=family, after=after):
                    self.setUp()
                    reserve = self.store.reserve_version
                    def fault(*args, **kwargs):
                        if args[1] == family and not after:
                            raise OSError('reservation unavailable')
                        result = reserve(*args, **kwargs)
                        if args[1] == family and after:
                            raise OSError('reservation durability uncertain')
                        return result
                    with patch.object(self.store, 'reserve_version', side_effect=fault):
                        with self.assertRaises(OSError):
                            self.invoke(family)
                    root = self.store.project_root(self.project_id)
                    self.assertFalse((root / family / 'v001.json').exists())
                    self.invoke(family)
                    self.assertFalse((root / family / 'v002.json').exists())
