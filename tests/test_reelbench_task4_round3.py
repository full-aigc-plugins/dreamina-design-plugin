"""Regressions for Task 4's third review round."""
import copy
import json
import os
import unittest
from unittest.mock import patch

from tests import test_reelbench_composite as fixtures
from scripts.json_contracts import canonical_fingerprint
from scripts.reelbench_binding_service import CompositeBindingIndeterminateError, ReelBenchBindingService
from scripts.reelbench_contracts import validate_reelbench_binding, validate_reelbench_comparison
from scripts.video_redesign_service import VideoRedesignService


class RoundThreeTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.CompositeTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.store, self.project_id = self.case.store, self.case.project_id

    def test_pending_redesign_blocks_other_candidate_until_exact_recovery(self):
        publish = self.store.publish_reserved_version
        def fail(project_id, reservation, **kwargs):
            if reservation['family'] == 'redesign':
                raise OSError('crashed with exact v001 reserved')
            return publish(project_id, reservation, **kwargs)
        with patch.object(self.store, 'publish_reserved_version', side_effect=fail):
            with self.assertRaises(CompositeBindingIndeterminateError) as caught:
                self.case.invoke('redesign')
        designer = VideoRedesignService(self.store)
        payload = copy.deepcopy(self.case.candidate['payload'])
        payload['concept'] = 'A distinct second design'
        other = designer.prepare_candidate(self.project_id, 'v001', payload)
        with self.assertRaises(Exception) as blocked:
            designer.commit_version(other, None, comparison_version='v001')
        self.assertEqual(type(blocked.exception).__name__, 'PendingVersionRecoveryRequired')
        self.assertEqual((blocked.exception.project_id, blocked.exception.family, blocked.exception.version),
                         (self.project_id, 'redesign', 'v001'))
        self.assertEqual(len(blocked.exception.operation_id), 64)
        with self.assertRaises(type(blocked.exception)):
            designer.commit_version(other, None)
        with self.assertRaises(type(blocked.exception)):
            self.store.write_version(self.project_id, 'redesign',
                {**other, 'rights_receipt_id': None, 'committed_at': '2026-09-15T00:00:00Z'},
                schema_name='video_redesign.schema.json', parent_version_field='parent_version', version='v002')
        root = self.store.project_root(self.project_id)
        self.assertFalse((root / 'redesign/v002.json').exists())
        first = self.case.invoke('redesign', caught.exception)
        second = designer.commit_version(other, None, comparison_version='v001')
        self.assertEqual((first['version'], second['version'], second['parent_version']), ('v001', 'v002', 'v001'))

    def test_resigned_comparison_requires_symmetric_domains_reasons_and_mismatches(self):
        matched = copy.deepcopy(self.case.comparison)
        valid_review = copy.deepcopy(matched)
        valid_review['overall'] = 'manual_review'
        valid_review['domains']['motion'] = {'verdict': 'manual_review', 'reasons': ['mismatch_count=1; codes=track_coverage']}
        valid_review['mismatches'] = [{'domain': 'motion', 'shot_id': 'S01', 'code': 'track_coverage',
                                     'expected': 'complete samples', 'observed': 'missing samples'}]
        valid_review['comparison_fingerprint'] = canonical_fingerprint({k: v for k, v in valid_review.items() if k != 'comparison_fingerprint'})
        validate_reelbench_comparison(valid_review)
        for kind in ('matched_reasons', 'manual_empty_mismatches', 'manual_empty_reasons', 'wrong_count', 'wrong_code', 'orphan_manual_domain'):
            with self.subTest(kind=kind):
                receipt = copy.deepcopy(matched if kind == 'matched_reasons' else valid_review)
                if kind == 'matched_reasons':
                    receipt['domains']['motion']['reasons'] = ['unexpected mismatch']
                elif kind == 'manual_empty_mismatches':
                    receipt['mismatches'] = []
                elif kind == 'manual_empty_reasons':
                    receipt['domains']['motion']['reasons'] = []
                elif kind == 'wrong_count':
                    receipt['domains']['motion']['reasons'] = ['mismatch_count=9; codes=track_coverage']
                elif kind == 'wrong_code':
                    receipt['domains']['motion']['reasons'] = ['mismatch_count=1; codes=motion_median']
                else:
                    receipt['domains']['duration'] = {'verdict': 'manual_review', 'reasons': ['mismatch_count=1; codes=duration']}
                receipt['comparison_fingerprint'] = canonical_fingerprint({k: v for k, v in receipt.items() if k != 'comparison_fingerprint'})
                with self.assertRaises(ValueError):
                    validate_reelbench_comparison(receipt)

    def standalone(self):
        annotation = self.store.write_version(self.project_id, 'annotation',
            {**self.case.annotation, 'analysis_version': 'v001'}, schema_name='shot_annotation.schema.json')
        return ReelBenchBindingService(self.store).bind(self.project_id, subject_family='annotation',
            subject_version=annotation['version'], comparison_version='v001')

    def test_standalone_binding_cleans_reservations_and_stays_idempotent(self):
        for _ in range(5):
            receipt = self.standalone()
        root = self.store.project_root(self.project_id)
        self.assertEqual(list((root / '.reservations').glob('*.json')), [])
        again = ReelBenchBindingService(self.store).bind(self.project_id, subject_family='annotation',
            subject_version=receipt['subject_version'], comparison_version='v001')
        self.assertEqual(receipt, again)
        self.assertEqual(list((root / '.reservations').glob('*.json')), [])

    def test_binding_rejects_resigned_invalid_calendar_and_timezone(self):
        receipt = self.standalone()
        for timestamp in ('2026-02-30T12:00:00Z', '2026-09-15T25:00:00Z',
                          '2026-09-15T12:00:00+25:00', '2026-13-15T12:00:00Z',
                          '2026-09-15T12:00:00+00:60', '2026-09-15T12:00:00-01:99'):
            with self.subTest(timestamp=timestamp):
                bad = {**receipt, 'bound_at': timestamp}
                bad['binding_fingerprint'] = canonical_fingerprint({k: v for k, v in bad.items() if k != 'binding_fingerprint'})
                with self.assertRaises(ValueError):
                    validate_reelbench_binding(bad)

    def test_standalone_cleanup_failures_keep_exact_recovery_and_no_pending_growth(self):
        from scripts.video_project_store import VersionCommitIndeterminateError
        for phase in ('before_unlink', 'after_unlink', 'after_unlink_fsync'):
            with self.subTest(phase=phase):
                self.setUp()
                unlink, fsync = os.unlink, os.fsync
                removed = [False]
                root = self.store.project_root(self.project_id)
                def deleting(name, **kwargs):
                    pending = str(name).startswith('.reservations/') and str(name).endswith('.json')
                    if pending and phase == 'before_unlink':
                        raise OSError('reservation cleanup failed')
                    result = unlink(name, **kwargs)
                    if pending:
                        removed[0] = True
                        if phase == 'after_unlink':
                            raise OSError('reservation cleanup uncertain')
                    return result
                def syncing(fd):
                    if removed[0] and phase == 'after_unlink_fsync' and os.fstat(fd).st_ino == (root / '.reservations').stat().st_ino:
                        raise OSError('reservation cleanup not durable')
                    return fsync(fd)
                with patch('os.unlink', side_effect=deleting), patch('os.fsync', side_effect=syncing):
                    with self.assertRaises(VersionCommitIndeterminateError) as caught:
                        self.standalone()
                error = caught.exception
                self.assertEqual(error.version, 'v001')
                recovered = ReelBenchBindingService(self.store).bind(self.project_id, subject_family='annotation',
                    subject_version='v001', comparison_version='v001', indeterminate_commit=error)
                self.assertEqual(recovered['version'], 'v001')
                self.assertEqual(list((root / '.reservations').glob('*.json')), [])
                self.assertFalse((root / 'reelbench_binding/v002.json').exists())
