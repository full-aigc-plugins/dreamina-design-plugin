"""Query-only Dreamina task automation."""
from __future__ import annotations
import re
import hashlib
import time
from pathlib import Path
from collections.abc import Mapping
from scripts.output_redactor import redact_value

IDENTIFIER=re.compile(r'^[A-Za-z0-9_-]{1,128}$')
FILTERS={'gen_status','aigc_type','session'}
class TaskService:
    def __init__(self,adapter,*,sleeper=time.sleep): self.adapter=adapter; self.sleeper=sleeper
    def query(self,submit_id:str,*,poll_seconds:int=0,download_dir:str|None=None)->dict[str,object]:
        if not IDENTIFIER.fullmatch(str(submit_id)): raise ValueError('unsafe submit_id')
        if not 0<=poll_seconds<=300: raise ValueError('poll_seconds must be between 0 and 300')
        before=set(Path(download_dir).iterdir()) if download_dir else set()
        result=None
        for attempt in range(poll_seconds + 1):
            result=self.adapter.run(['query_result','--submit_id',submit_id])
            payload=result.payload if isinstance(result.payload,Mapping) else {}
            if str(payload.get('gen_status','')).lower() not in {'querying','submitted','running','processing','pending'}:
                break
            if attempt < poll_seconds: self.sleeper(1)
        if download_dir and isinstance(result.payload,Mapping) and str(result.payload.get('gen_status','')).lower() == 'success':
            result=self.adapter.run(['query_result','--submit_id',submit_id,'--download_dir',download_dir])
        payload=redact_value(result.payload if isinstance(result.payload,Mapping) else {})
        artifacts=[]
        if download_dir:
            for path in sorted(set(Path(download_dir).iterdir())-before):
                if path.is_symlink() or not path.is_file(): raise ValueError('downloaded artifact must be a regular non-symlink file')
                data=path.read_bytes()
                if not data or len(data)>512*1024*1024: raise ValueError('downloaded artifact size is invalid')
                mime='image/png' if data.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if data.startswith(b'\xff\xd8\xff') else 'video/mp4' if len(data)>=12 and data[4:8]==b'ftyp' else None
                if mime is None: raise ValueError('downloaded artifact type is unsupported')
                artifacts.append({'path':str(path.resolve()),'mime_type':mime,'size_bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'provenance':'externally-queried'})
        return {'submit_id':submit_id,'provenance':'externally-queried','exit_code':result.exit_code,'result':payload,'artifacts':artifacts}
    def list_tasks(self,filters:Mapping[str,object],*,limit:int=20)->dict[str,object]:
        if not 1<=limit<=100: raise ValueError('limit must be between 1 and 100')
        unknown=set(filters)-FILTERS
        if unknown: raise ValueError(f'unsupported task filters: {sorted(unknown)}')
        argv=['list_task']
        for key in ('gen_status','aigc_type','session'):
            if key in filters: argv += [f'--{key}',str(filters[key])]
        argv += ['--limit',str(limit)]
        result=self.adapter.run(argv)
        return {'exit_code':result.exit_code,'result':redact_value(result.payload if isinstance(result.payload,Mapping) else {})}
