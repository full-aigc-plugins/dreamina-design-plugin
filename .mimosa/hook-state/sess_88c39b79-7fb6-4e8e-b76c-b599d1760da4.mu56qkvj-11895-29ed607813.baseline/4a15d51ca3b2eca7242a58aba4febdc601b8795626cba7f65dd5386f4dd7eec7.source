"""Production project-only composition/export, separate from standalone file APIs."""
from __future__ import annotations
import os
import secrets
from pathlib import Path
from types import SimpleNamespace
from scripts import reelbench_workspace as ws
from scripts.json_contracts import canonical_fingerprint
from scripts.project_generated_artifacts import ProjectGeneratedArtifacts,ProjectGeneratedArtifactError
from scripts.reelbench_project_service import ReelBenchProjectService
from scripts.narration_service import AudioPlanService

def load_audio_plan(store,project_id,quote,version,mode,audio_service):
    resolver=ProjectGeneratedArtifacts(store)
    if type(audio_service) is not AudioPlanService: raise ProjectGeneratedArtifactError('trusted AudioPlanService required')
    with resolver._locked_project(project_id) as (_,fd,_,guard):
        if version is None:
            try:
                with ws.directory(fd,'audio_plan') as family:
                    versions=[name[:-5] for name in os.listdir(family) if name.endswith('.json')]
                for candidate in versions: resolver._version(candidate)
                version=max(versions,key=lambda v:int(v[1:]),default=None)
            except FileNotFoundError: pass
        if version is None:
            if quote['audio_policy']=='silent' and mode=='none': return None
            raise ProjectGeneratedArtifactError('PROJECT_AUDIO_PLAN_UNAVAILABLE: no persisted audio plan')
        resolver._version(version)
        try:
            with ws.file_at(fd,'audio_plan/'+version+'.json'): pass
            plan=store.read_version(project_id,'audio_plan',version,'audio_plan.schema.json')
        except FileNotFoundError as exc: raise ProjectGeneratedArtifactError('PROJECT_AUDIO_PLAN_UNAVAILABLE: requested audio plan missing') from exc
        guard()
    plan=audio_service.verify_for_use(plan)
    if plan['project_id']!=project_id or plan['design_fingerprint']!=quote['design_fingerprint'] or plan['batch_fingerprint']!=quote['quote_fingerprint'] or plan['audio_policy']!=quote['audio_policy']:
        raise ProjectGeneratedArtifactError('audio plan differs from exact batch/design')
    return plan

def select_subtitle(plan,mode):
    if mode=='none': return None
    preferred='subtitle_ass' if mode=='burned_in' else 'subtitle_srt'
    matches=[entry for entry in plan['subtitles'] if entry['artifact_role']==preferred] if plan else []
    if len(matches)!=1: raise ProjectGeneratedArtifactError('exact signed subtitle receipt is unavailable')
    return matches[0]

class ProjectMediaService:
    def __init__(self,store,media_adapter,approval_provider,audio_plan_service=None):
        self.store=store; self.adapter=media_adapter; self.approval=approval_provider
        self.audio=audio_plan_service or AudioPlanService(project_store=store)

    def compose_project(self,*,project_id,batch_version,composition,audio_policy=None,subtitle_mode=None):
        from scripts.video_composition_service import VideoCompositionService
        resolver=ProjectGeneratedArtifacts(self.store)
        # Reject caller authority before resolving any media or constructing a process.
        allowed={'transitions','layout_mode','title_card','end_card','audio_plan_version'}
        if not isinstance(composition,dict) or set(composition)-allowed:
            raise ProjectGeneratedArtifactError('project composition accepts options, never caller clip paths or roles')
        quote=resolver.batch_quote(project_id,batch_version)
        selected_policy=audio_policy or quote['audio_policy']
        if selected_policy!=quote['audio_policy']: raise ProjectGeneratedArtifactError('composition audio policy differs from quote')
        mode=subtitle_mode or 'none'
        if mode not in {'none','sidecar','muxed','burned_in'}: raise ProjectGeneratedArtifactError('invalid project subtitle mode')
        load_audio_plan(self.store,project_id,quote,composition.get('audio_plan_version'),mode,self.audio)
        root=self.store.project_root(project_id)
        with resolver._locked_project(project_id) as (_,fd,_,guard):
            ws.mkdir(fd,'project_composition_media')
            name='render-'+secrets.token_hex(16)
            ws.mkdir(fd,'project_composition_media/'+name)
            guard()
        render=root/'project_composition_media'/name
        service=VideoCompositionService(self.adapter,self.audio,render)
        return service.compose_project(store=self.store,project_id=project_id,batch_version=batch_version,
            composition=composition,subtitle_mode=mode)

    def export_project(self,*,project_id,composition_version,destination,approved_roots,include_report=True,subtitle_mode=None):
        from scripts.final_media_service import FinalMediaService
        if not approved_roots: raise ProjectGeneratedArtifactError('approved export roots required')
        self.approval.confirm({'operation':'project-final-export','project_id':project_id,'composition_version':composition_version,'destination':destination})
        return FinalMediaService(self.adapter).export_project(store=self.store,project_id=project_id,
            composition_version=composition_version,destination=Path(destination),approved_roots=approved_roots,
            audio_plan_service=self.audio,subtitle_mode=subtitle_mode)

def compose_project(service,*,store,project_id,batch_version,composition,subtitle_mode='none'):
    resolver=ProjectGeneratedArtifacts(store)
    if not isinstance(composition,dict) or set(composition)-{'transitions','layout_mode','title_card','end_card','audio_plan_version'}:
        raise ProjectGeneratedArtifactError('project composition forbids caller clip authority')
    quote=resolver.batch_quote(project_id,batch_version)
    references=resolver.references(project_id,batch_version)
    # Re-resolve opaque authority immediately before the legacy implementation
    # stages private bytes. Caller-supplied mappings never reach build_plan.
    records=[resolver.resolve(project_id,ref['receipt_version'],ref['receipt_id']) for ref in references]
    clips=[{'shot_id':r['shot_id'],'path':r['absolute_path'],'sha256':r['sha256'],
        'size_bytes':r['size_bytes'],'duration_seconds':r['duration_seconds'],'accepted':True} for r in records]
    transitions=composition.get('transitions',[{'kind':'cut','duration_seconds':0} for _ in clips[1:]])
    target=sum(r['duration_seconds'] for r in records)-sum(t['duration_seconds'] for t in transitions)
    target+=sum(composition[k]['duration_seconds'] for k in ('title_card','end_card') if composition.get(k))
    profile=quote['output_profile']
    audio=load_audio_plan(store,project_id,quote,composition.get('audio_plan_version'),subtitle_mode,service._audio)
    if audio is not None and abs(audio['target_duration_seconds']-target)>1/profile.get('fps',25): raise ProjectGeneratedArtifactError('audio plan duration differs from composition')
    subtitle=select_subtitle(audio,subtitle_mode)
    media_mode={'none':'none','sidecar':'none','muxed':'mux','burned_in':'burn'}[subtitle_mode]
    plan=service.build_plan(required_shots=[r['shot_id'] for r in records],clips=clips,transitions=transitions,
        width=profile['width'],height=profile['height'],fps=int(profile.get('fps',25)),target_duration_seconds=target,
        audio_plan=audio,subtitle=subtitle if media_mode!='none' else None,subtitle_mode=media_mode,output_path=service._root/'final.mp4',
        layout_mode=composition.get('layout_mode','scale_pad'),title_card=composition.get('title_card'),end_card=composition.get('end_card'))
    output=service.compose(plan)
    with resolver._locked_project(project_id) as (_,fd,root,guard):
        relative=output.relative_to(root).as_posix()
        digest=resolver._digest(fd,relative,2*1024**3)
        with ws.file_at(fd,relative) as opened: info=os.fstat(opened)
        version=resolver._next_version(fd,'project_composition')
        receipt={'schema_version':'1.0','project_id':project_id,'version':version,'artifact_role':'project_composition',
            'batch_version':batch_version,'quote_fingerprint':quote['quote_fingerprint'],'generated_receipts':references,
            'generated_fingerprints':[r['fingerprint'] for r in records],'path':relative,**digest,
            'device':info.st_dev,'inode':info.st_ino,'target_duration_seconds':target,'audio_policy':quote['audio_policy'],
            'audio_plan_version':audio['version'] if audio else None,'audio_plan_fingerprint':audio['plan_fingerprint'] if audio else None,
            'subtitle_mode':subtitle_mode,
            'width':profile['width'],'height':profile['height'],'fps':int(profile.get('fps',25))}
        receipt['composition_fingerprint']=canonical_fingerprint(receipt)
        return store.write_version(project_id,'project_composition',receipt,version=version,project_fd=fd,publication_guard=guard)

def export_project(service,*,store,project_id,composition_version,destination,approved_roots,audio_plan_service=None,subtitle_mode=None):
    resolver=ProjectGeneratedArtifacts(store)
    with resolver._locked_project(project_id) as (_,fd,root,guard):
        receipt=resolver._read_version(fd,'project_composition',composition_version)
        if receipt.get('artifact_role')!='project_composition' or receipt.get('project_id')!=project_id or canonical_fingerprint({k:v for k,v in receipt.items() if k!='composition_fingerprint'})!=receipt.get('composition_fingerprint'):
            raise ProjectGeneratedArtifactError('project composition receipt identity differs')
    records=[resolver.resolve(project_id,ref['receipt_version'],ref['receipt_id']) for ref in receipt['generated_receipts']]
    if [r['fingerprint'] for r in records]!=receipt['generated_fingerprints']:
        raise ProjectGeneratedArtifactError('project composition generated authority changed')
    if subtitle_mode is not None and subtitle_mode!=receipt['subtitle_mode']: raise ProjectGeneratedArtifactError('export subtitle mode differs from composition')
    quote=resolver.batch_quote(project_id,receipt['batch_version'])
    audio=load_audio_plan(store,project_id,quote,receipt['audio_plan_version'],receipt['subtitle_mode'],audio_plan_service or AudioPlanService(project_store=store))
    if (audio['plan_fingerprint'] if audio else None)!=receipt['audio_plan_fingerprint']: raise ProjectGeneratedArtifactError('composition audio plan binding changed')
    with resolver._locked_project(project_id) as (_,fd,root,guard):
        if resolver._digest(fd,receipt['path'],2*1024**3)!={k:receipt[k] for k in ('sha256','size_bytes')}:
            raise ProjectGeneratedArtifactError('project composition bytes changed')
        with ws.file_at(fd,receipt['path']) as opened:
            info=os.fstat(opened)
            if (info.st_dev,info.st_ino)!=(receipt['device'],receipt['inode']): raise ProjectGeneratedArtifactError('project composition inode changed')
        plan=SimpleNamespace(project_id=project_id,composition_version=composition_version,
            target_duration_seconds=receipt['target_duration_seconds'],subtitle={'cues':[{}]} if receipt['subtitle_mode']=='muxed' else None)
        source=root/receipt['path']
        verified=service.verify_final(source,plan,audio_policy=receipt['audio_policy'],composition_fingerprint=receipt['composition_fingerprint'])
        guard()
        service._assert_all_required_gates_pass(verified)
        service._confirm_exact_destination(destination,approved_roots)
        if not destination.parent.is_dir(): raise ProjectGeneratedArtifactError('approved export directory must exist')
        destination_parent=ws.open_absolute(destination.parent.resolve(strict=True))
        temporary='.project-export-'+secrets.token_hex(16)+'.mp4'
        try:
            expected={k:receipt[k] for k in ('sha256','size_bytes')}
            copied,_=ws.copy(fd,receipt['path'],destination_parent,temporary,
                maximum=2*1024**3,expected=expected,mode=0o600)
            os.link(temporary,destination.name,src_dir_fd=destination_parent,dst_dir_fd=destination_parent,follow_symlinks=False)
            os.unlink(temporary,dir_fd=destination_parent)
            os.fsync(destination_parent)
        finally:
            try: os.unlink(temporary,dir_fd=destination_parent)
            except FileNotFoundError: pass
            finally: ws._close_owned([destination_parent])
        result={'path':str(destination),**copied,'atomic':True}
        guard()
        return {'project_id':project_id,'composition_version':composition_version,'artifact_role':'project_final','export':result,'verification':verified}
