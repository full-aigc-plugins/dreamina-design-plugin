"""Opaque generated-shot references backed by accepted project batch evaluations."""
from __future__ import annotations
import json
import os
import re
import stat
from pathlib import Path
from scripts import reelbench_workspace as ws
from scripts.json_contracts import canonical_fingerprint,validate_contract
from scripts.reelbench_project_service import ReelBenchProjectService
from scripts.video_generation_planner import validate_batch_quote

class ProjectGeneratedArtifactError(ValueError): pass

class ProjectGeneratedArtifacts(ReelBenchProjectService):
    def __init__(self,store): super().__init__(store,None)

    @staticmethod
    def receipt_id(evaluation):
        return 'gs_'+canonical_fingerprint({'binding':evaluation['binding'],
            'evaluation_id':evaluation['evaluation_id'],'evaluation_fingerprint':evaluation['evaluation_fingerprint']})

    def _accepted(self,fd,project_id,batch_version):
        self._version(batch_version)
        quote=self._json(ws.read(fd,f'video_batch_quote/{batch_version}.json'))
        wrapper=quote.pop('version',batch_version)
        if wrapper!=batch_version or quote.get('quote_version')!=batch_version or quote.get('project_id')!=project_id:
            raise ProjectGeneratedArtifactError('batch quote identity differs')
        validate_batch_quote(quote)
        state=self._json(ws.read(fd,f'video_batch_execution/batches/{project_id}-{batch_version}.json'))
        if state.get('project_id')!=project_id or state.get('batch_version')!=batch_version:
            raise ProjectGeneratedArtifactError('batch state identity differs')
        result=[]
        for item in quote['items']:
            tasks=[t for t in state['tasks'] if t.get('shot_id')==item['shot_id'] and t.get('state')=='accepted']
            if len(tasks)!=1: raise ProjectGeneratedArtifactError('each quoted shot requires one accepted task')
            task=tasks[0]; evaluation=task.get('evaluation_receipt')
            validate_contract(evaluation,'shot_evaluation.schema.json')
            if canonical_fingerprint({k:v for k,v in evaluation.items() if k!='evaluation_fingerprint'})!=evaluation['evaluation_fingerprint']:
                raise ProjectGeneratedArtifactError('evaluation fingerprint differs')
            if evaluation['decision']!={'action':'accepted'} or task.get('evaluation_decision')!={'action':'accepted'} or evaluation['failed_gates'] or any(g['status']!='passed' for group in ('measured_gates','semantic_gates') for g in evaluation[group].values()):
                raise ProjectGeneratedArtifactError('generated shot evaluation is not accepted')
            binding=evaluation['binding']
            expected={'project_id':project_id,'batch_version':batch_version,'shot_id':item['shot_id'],
                'attempt':task['attempt'],'submit_id':task['submit_id'],'allowance_id':state['allowance_id'],
                'design_version':quote['design_version'],'design_fingerprint':quote['design_fingerprint'],
                'quote_fingerprint':quote['quote_fingerprint']}
            if any(binding[k]!=v for k,v in expected.items()): raise ProjectGeneratedArtifactError('accepted task/evaluation binding differs')
            planned=[a for a in item['attempts'] if a['attempt_number']==task['attempt'] and a['request_fingerprint']==task['request_fingerprint']]
            if len(planned)!=1: raise ProjectGeneratedArtifactError('accepted task is not a quoted attempt')
            artifact=evaluation['artifact_evidence']
            matches=[a for a in task['artifacts'] if all(a.get(k)==artifact[k] for k in ('path','mime_type','size_bytes','sha256','provenance'))]
            if len(matches)!=1 or artifact['provenance']!='externally-queried' or artifact['sha256']!=binding['artifact_sha256']:
                raise ProjectGeneratedArtifactError('accepted generated artifact binding differs')
            if abs(artifact['probe']['duration_seconds']-planned[0]['request']['duration_seconds'])>.1:
                raise ProjectGeneratedArtifactError('accepted artifact duration differs')
            result.append((self.receipt_id(evaluation),task,evaluation))
        return quote,result

    def references(self,project_id,batch_version):
        with self._locked_project(project_id) as (_,fd,_,guard):
            _,accepted=self._accepted(fd,project_id,batch_version)
            guard()
            return [{'receipt_id':identifier,'receipt_version':batch_version,'shot_id':task['shot_id']} for identifier,task,_ in accepted]

    def resolve(self,project_id,receipt_version,receipt_id):
        if not isinstance(receipt_id,str) or re.fullmatch(r'gs_[a-f0-9]{64}',receipt_id) is None:
            raise ProjectGeneratedArtifactError('opaque generated-shot receipt id required')
        self._version(receipt_version)
        with self._locked_project(project_id) as (_,fd,root,guard):
            _,accepted=self._accepted(fd,project_id,receipt_version)
            found=[row for row in accepted if row[0]==receipt_id]
            if len(found)!=1: raise ProjectGeneratedArtifactError('generated-shot receipt is not accepted in this batch')
            _,task,evaluation=found[0]
            artifact=evaluation['artifact_evidence']; digest=artifact['sha256']
            relative=Path(artifact['path']).relative_to(root).as_posix()
            if not relative.startswith('video_batch_execution/downloads/'):
                raise ProjectGeneratedArtifactError('generated shot is outside project provider downloads')
            # Review bytes retain their excluded role even if copied or renamed.
            try:
                with ws.directory(fd,'reelbench_sync') as family:
                    for name in os.listdir(family):
                        if not re.fullmatch(r'v[0-9]{3,}\.json',name): continue
                        review=self._json(ws.read(family,name))
                        if review.get('artifact_role')=='synchronized_review' and any(a.get('sha256')==digest for a in review.get('artifacts',[])):
                            raise ProjectGeneratedArtifactError('synchronized-review bytes cannot resolve as generated_shot')
            except FileNotFoundError: pass
            blob='generated_shots/blobs/'+digest+'.mp4'
            manifest='generated_shots/receipts/'+receipt_id+'.json'
            ws.mkdir(fd,'generated_shots/blobs'); ws.mkdir(fd,'generated_shots/receipts')
            expected={'sha256':digest,'size_bytes':artifact['size_bytes']}
            if self._digest(fd,relative,2*1024**3)!=expected: raise ProjectGeneratedArtifactError('accepted provider bytes changed')
            try:
                record=self._json(ws.read(fd,manifest))
            except FileNotFoundError:
                try: copied,info=ws.copy(fd,relative,fd,blob,maximum=2*1024**3,expected=expected,mode=0o400)
                except FileExistsError:
                    if self._digest(fd,blob,2*1024**3)!=expected: raise ProjectGeneratedArtifactError('content-addressed generated blob differs')
                    with ws.file_at(fd,blob) as opened: info=os.fstat(opened)
                record={'schema_version':'1.0','project_id':project_id,'receipt_id':receipt_id,
                    'receipt_version':receipt_version,'shot_id':task['shot_id'],'artifact_role':'generated_shot',
                    'evaluation_fingerprint':evaluation['evaluation_fingerprint'],'path':blob,**expected,
                    'device':info.st_dev,'inode':info.st_ino,'duration_seconds':artifact['probe']['duration_seconds']}
                record['fingerprint']=canonical_fingerprint(record)
                guard()
                ws.write(fd,manifest,json.dumps(record,sort_keys=True).encode())
            required={'schema_version','project_id','receipt_id','receipt_version','shot_id','artifact_role','evaluation_fingerprint','path','sha256','size_bytes','device','inode','duration_seconds','fingerprint'}
            if set(record)!=required or canonical_fingerprint({k:v for k,v in record.items() if k!='fingerprint'})!=record['fingerprint'] or record['artifact_role']!='generated_shot' or record['receipt_id']!=receipt_id or record['receipt_version']!=receipt_version or record['evaluation_fingerprint']!=evaluation['evaluation_fingerprint'] or record['path']!=blob or record['sha256']!=digest:
                raise ProjectGeneratedArtifactError('generated receipt identity/role differs')
            if record['project_id']!=project_id or record['shot_id']!=task['shot_id'] or record['size_bytes']!=artifact['size_bytes'] or record['duration_seconds']!=artifact['probe']['duration_seconds']:
                raise ProjectGeneratedArtifactError('generated receipt fields differ from accepted evidence')
            with ws.file_at(fd,blob) as opened:
                info=os.fstat(opened)
                if (info.st_dev,info.st_ino)!=(record['device'],record['inode']) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o400:
                    raise ProjectGeneratedArtifactError('generated blob inode changed')
            if self._digest(fd,blob,2*1024**3)!=expected: raise ProjectGeneratedArtifactError('generated blob bytes changed')
            guard()
            return {**record,'absolute_path':str(root/blob)}

    def batch_quote(self,project_id,batch_version):
        with self._locked_project(project_id) as (_,fd,_,guard):
            quote,_=self._accepted(fd,project_id,batch_version)
            guard()
            return quote
