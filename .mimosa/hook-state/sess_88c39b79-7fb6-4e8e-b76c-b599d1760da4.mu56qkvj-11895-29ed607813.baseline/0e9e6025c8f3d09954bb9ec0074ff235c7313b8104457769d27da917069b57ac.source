"""Project final media never takes caller paths or caller acceptance flags."""
import tempfile
import hashlib
import json
import os
import unittest
from pathlib import Path
from scripts.video_project_store import VideoProjectStore
from scripts.json_contracts import canonical_fingerprint

class GeneratedArtifactBoundaryTests(unittest.TestCase):
    def test_review_copy_and_removed_role_cannot_be_opaque_generated_receipts(self):
        from scripts.project_generated_artifacts import ProjectGeneratedArtifacts
        with tempfile.TemporaryDirectory() as temporary:
            store=VideoProjectStore(Path(temporary)/'projects')
            project=store.create(title='test',creative_mode='original_redesign',audio_policy='silent')
            resolver=ProjectGeneratedArtifacts(store)
            for value in ({'path':'/tmp/review-copy.mp4','accepted':True}, '/tmp/review-copy.mp4', 'v001'):
                with self.assertRaises(ValueError): resolver.resolve(project['project_id'],'v001',value)

    def test_real_mcp_routes_project_final_calls_to_protected_service(self):
        from scripts.dreamina_mcp_server import DreaminaMcpTools
        class Pipeline:
            def compose_project(self,**kwargs): return {'protected':True,'project_id':kwargs['project_id']}
        with tempfile.TemporaryDirectory() as temporary:
            tools=DreaminaMcpTools(state_root=Path(temporary),project_media_service=Pipeline())
            result=tools.call('dreamina_compose_video',{'project_id':'vp_'+'a'*24,'batch_version':'v001','composition':{}})
            self.assertTrue(result['protected'])

class AcceptedGeneratedArtifactTests(unittest.TestCase):
    def setUp(self):
        from tests.test_video_batch_allowance import make_quote
        from scripts.project_generated_artifacts import ProjectGeneratedArtifacts
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.store=VideoProjectStore(Path(temporary.name)/'projects')
        self.project=self.store.create(title='generated',creative_mode='original_redesign',audio_policy='silent')['project_id']
        self.root=self.store.project_root(self.project)
        for path in ('video_batch_quote','video_batch_execution','video_batch_execution/batches','video_batch_execution/downloads'):
            (self.root/path).mkdir(mode=0o700,exist_ok=True)
        self.artifact=self.root/'video_batch_execution/downloads/clip.mp4'
        self.artifact.write_bytes(b'\0\0\0\x18ftypisom'+b'x'*20); self.artifact.chmod(0o600)
        quote=make_quote(); quote['project_id']=self.project
        quote['quote_fingerprint']=canonical_fingerprint({k:v for k,v in quote.items() if k!='quote_fingerprint'})
        self.store._atomic_write(self.root/'video_batch_quote/v001.json',quote)
        evaluation=json.loads((Path(__file__).parent/'fixtures/reference_video/valid-evaluation.json').read_text())
        digest=hashlib.sha256(self.artifact.read_bytes()).hexdigest()
        evaluation['binding'].update(project_id=self.project,artifact_sha256=digest,
            quote_fingerprint=quote['quote_fingerprint'],design_fingerprint=quote['design_fingerprint'])
        evaluation['artifact_evidence'].update(path=str(self.artifact),sha256=digest,size_bytes=self.artifact.stat().st_size)
        evaluation['evaluation_fingerprint']=canonical_fingerprint({k:v for k,v in evaluation.items() if k!='evaluation_fingerprint'})
        self.task={'shot_id':'S01','state':'accepted','attempt':1,'submit_id':'submit_1',
            'request_fingerprint':quote['items'][0]['attempts'][0]['request_fingerprint'],
            'evaluation_receipt':evaluation,'evaluation_decision':{'action':'accepted'},
            'artifacts':[{k:evaluation['artifact_evidence'][k] for k in ('path','mime_type','sha256','size_bytes','provenance')}]}
        self.state={'project_id':self.project,'batch_version':'v001','allowance_id':evaluation['binding']['allowance_id'],
            'tasks':[self.task],'state':'completed'}
        self.state_path=self.root/'video_batch_execution/batches'/f'{self.project}-v001.json'
        self.store._atomic_write(self.state_path,self.state)
        self.resolver=ProjectGeneratedArtifacts(self.store)
        self.ref=self.resolver.references(self.project,'v001')[0]

    def test_accepted_receipt_resolves_content_addressed_role_and_exact_inode(self):
        receipt=self.resolver.resolve(self.project,'v001',self.ref['receipt_id'])
        self.assertEqual(receipt['artifact_role'],'generated_shot')
        self.assertTrue(receipt['path'].endswith(receipt['sha256']+'.mp4'))
        target=Path(receipt['absolute_path'])
        self.assertEqual(target.stat().st_ino,receipt['inode'])
        self.assertEqual(target.read_bytes(),self.artifact.read_bytes())
        self.assertEqual(self.resolver.resolve(self.project,'v001',self.ref['receipt_id']),receipt)

    def test_provider_alias_and_byte_replacement_are_rejected(self):
        copy=self.root/'review-copy.mp4'; copy.write_bytes(self.artifact.read_bytes()); copy.chmod(0o600)
        self.artifact.unlink(); self.artifact.symlink_to(copy)
        with self.assertRaises((ValueError,OSError)): self.resolver.resolve(self.project,'v001',self.ref['receipt_id'])
        self.artifact.unlink(); self.artifact.write_bytes(b'review bytes'); self.artifact.chmod(0o600)
        with self.assertRaises(ValueError): self.resolver.resolve(self.project,'v001',self.ref['receipt_id'])

    def test_review_bytes_are_rejected_even_with_removed_role_and_valid_evaluation(self):
        (self.root/'reelbench_sync').mkdir(mode=0o700)
        digest=self.task['evaluation_receipt']['binding']['artifact_sha256']
        self.store._atomic_write(self.root/'reelbench_sync/v001.json',{'artifact_role':'synchronized_review','artifacts':[{'sha256':digest}]})
        with self.assertRaisesRegex(ValueError,'review bytes'): self.resolver.resolve(self.project,'v001',self.ref['receipt_id'])

    def test_mcp_composes_from_batch_state_and_rejects_caller_path(self):
        from scripts.project_media_service import ProjectMediaService
        from scripts.dreamina_mcp_server import DreaminaMcpTools
        from tests.test_video_composition_service import FakeAdapter
        class Approver:
            def confirm(self,*_): return 'test-approved'
        pipeline=ProjectMediaService(self.store,FakeAdapter(),Approver())
        tools=DreaminaMcpTools(state_root=self.root,project_media_service=pipeline)
        with self.assertRaisesRegex(ValueError,'caller clip'):
            tools.call('dreamina_compose_video',{'project_id':self.project,'batch_version':'v001',
                'composition':{'clips':[{'path':str(self.artifact),'accepted':True}]}})
        receipt=tools.call('dreamina_compose_video',{'project_id':self.project,'batch_version':'v001','composition':{}})
        self.assertEqual(receipt['artifact_role'],'project_composition')
        self.assertEqual(receipt['generated_receipts'],[self.ref])
        output=self.root/receipt['path']; output.write_bytes(b'copied review'); output.chmod(0o600)
        with self.assertRaisesRegex(ValueError,'composition bytes changed'):
            tools.call('dreamina_export_video_project',{'project_id':self.project,'composition_version':receipt['version'],
                'destination':str(self.root/'delivery.mp4'),'approved_roots':[str(self.root)]})

    def change_to_narrated_project(self):
        project=self.store.get(self.project); project['audio_policy']='full_redesign'
        self.store._atomic_write(self.root/'project.json',project)
        quote=json.loads((self.root/'video_batch_quote/v001.json').read_text())
        quote.update(audio_policy='full_redesign',creative_mode='original_redesign')
        quote.pop('rights_receipt_id'); quote.pop('rights_receipt_fingerprint')
        quote['quote_fingerprint']=canonical_fingerprint({k:v for k,v in quote.items() if k!='quote_fingerprint'})
        self.store._atomic_write(self.root/'video_batch_quote/v001.json',quote)
        evaluation=self.task['evaluation_receipt']; evaluation['binding']['quote_fingerprint']=quote['quote_fingerprint']
        evaluation['evaluation_fingerprint']=canonical_fingerprint({k:v for k,v in evaluation.items() if k!='evaluation_fingerprint'})
        self.store._atomic_write(self.state_path,self.state)
        return quote

    def test_non_silent_without_persisted_audio_plan_is_typed_missing(self):
        from scripts.project_media_service import ProjectMediaService
        self.change_to_narrated_project()
        with self.assertRaisesRegex(ValueError,'PROJECT_AUDIO_PLAN_UNAVAILABLE'):
            ProjectMediaService(self.store,None,None).compose_project(project_id=self.project,batch_version='v001',composition={})

    def test_persisted_signed_audio_plan_is_loaded_and_used_by_composition(self):
        from scripts.narration_service import AudioPlanService,MacOSSayProvider
        from scripts.project_media_service import ProjectMediaService
        from tests.test_narration_service import FixedKeyStore,SyntheticNarrationAdapter
        from tests.test_video_composition_service import FakeAdapter
        quote=self.change_to_narrated_project(); keys=FixedKeyStore()
        audio=AudioPlanService(key_store=keys,project_store=self.store)
        narration=MacOSSayProvider(SyntheticNarrationAdapter(),self.root,voices={'Samantha'},key_store=keys).synthesize(
            [{'start':0,'end':4,'text':'new narration'}],voice='Samantha',output_path=self.root/'narration.aiff')
        plan=audio.create_plan(project_id=self.project,design_fingerprint=quote['design_fingerprint'],batch_fingerprint=quote['quote_fingerprint'],
            creative_mode='original_redesign',audio_policy='full_redesign',source_rights=None,transcript=None,
            rewritten_script=[{'start':0,'end':4,'text':'new narration'}],narration=narration,music=None,effects=[],subtitles=[],target_duration_seconds=4)
        persisted=audio.commit_plan(plan); media=FakeAdapter()
        receipt=ProjectMediaService(self.store,media,None,audio_plan_service=audio).compose_project(project_id=self.project,batch_version='v001',
            composition={'audio_plan_version':persisted['version']})
        self.assertEqual(receipt['audio_plan_fingerprint'],persisted['plan_fingerprint'])
        self.assertIn('-c:a',media.calls[-1][1])
        self.assertNotIn('-an',media.calls[-1][1])
