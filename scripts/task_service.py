"""Query-only Dreamina task automation."""
from __future__ import annotations
import re
from collections.abc import Mapping
from scripts.output_redactor import redact_value

IDENTIFIER=re.compile(r'^[A-Za-z0-9_-]{1,128}$')
FILTERS={'gen_status','aigc_type','session'}
class TaskService:
    def __init__(self,adapter): self.adapter=adapter
    def query(self,submit_id:str,*,poll_seconds:int=0,download_dir:str|None=None)->dict[str,object]:
        if not IDENTIFIER.fullmatch(str(submit_id)): raise ValueError('unsafe submit_id')
        if not 0<=poll_seconds<=300: raise ValueError('poll_seconds must be between 0 and 300')
        argv=['query_result','--submit_id',submit_id]
        if poll_seconds: argv += ['--poll',str(poll_seconds)]
        if download_dir: argv += ['--download_dir',download_dir]
        result=self.adapter.run(argv)
        payload=redact_value(result.payload if isinstance(result.payload,Mapping) else {})
        return {'submit_id':submit_id,'provenance':'externally-queried','exit_code':result.exit_code,'result':payload}
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
