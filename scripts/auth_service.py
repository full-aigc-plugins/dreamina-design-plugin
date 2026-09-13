"""Closed Dreamina OAuth command automation."""
from __future__ import annotations
from scripts.output_redactor import redact_text

class AuthService:
    def __init__(self,adapter,account_service,approval_provider): self.adapter=adapter; self.account_service=account_service; self.approval_provider=approval_provider
    def execute(self,action:str,*,device_code:str|None=None,poll_seconds:int=0)->dict[str,object]:
        if not 0 <= poll_seconds <= 300: raise ValueError('poll_seconds must be between 0 and 300')
        if action == 'check_login':
            if not device_code or len(device_code)>512: raise ValueError('device_code is required')
            argv=['login','checklogin','--device_code',device_code,'--poll',str(poll_seconds)]
        else:
            mapping={'login':['login'],'login_headless':['login','--headless'],'relogin':['relogin'],'logout':['logout']}
            if action not in mapping: raise ValueError(f'unsupported auth action: {action}')
            argv=mapping[action]
        if action in {'relogin','logout'}: self.approval_provider.confirm({'operation':'dreamina-auth','action':action})
        result=self.adapter.run_text(argv)
        output=redact_text(result.stdout+result.stderr,max_bytes=128*1024)
        response={'action':action,'exit_code':result.exit_code,'output':output,'success':result.exit_code==0}
        if action in {'login','check_login','relogin'} and result.exit_code==0: response['account']=self.account_service.user_credit()
        if action == 'login_headless': response['requires_user_action']=True
        return response
