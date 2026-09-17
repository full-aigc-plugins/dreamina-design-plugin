import unittest
from scripts.auth_service import AuthFlowStore, AuthService
from scripts.native_approval import ApprovalDeniedError

class Adapter:
    def __init__(self): self.calls=[]
    def run_text(self,args): self.calls.append(args); return type('R',(),{'exit_code':0,'stdout':'verification_uri=https://x\ndevice_code=secret','stderr':''})()
class Account:
    def user_credit(self): return {'ready':True}
class Provider:
    def __init__(self,deny=False): self.deny=deny
    def confirm(self,request):
        if self.deny: raise ApprovalDeniedError('denied')
        return 'ok'

class AuthServiceTests(unittest.TestCase):
    def test_headless_returns_flow_id_not_device_code(self):
        a=Adapter(); result=AuthService(a,Account(),Provider(),AuthFlowStore()).execute('login_headless')
        self.assertEqual(a.calls,[['login','--headless']]); self.assertIn('flow_id',result); self.assertNotIn('secret',str(result)); self.assertNotIn('device_code',result)
    def test_check_login_consumes_flow_once(self):
        a=Adapter(); store=AuthFlowStore(); flow=store.issue('abc')
        service=AuthService(a,Account(),Provider(),store); service.execute('check_login',flow_id=flow,poll_seconds=30)
        self.assertEqual(a.calls,[['login','checklogin','--device_code','abc','--poll','30']])
        with self.assertRaises(ValueError): service.execute('check_login',flow_id=flow,poll_seconds=0)
    def test_logout_denial_never_invokes_cli(self):
        a=Adapter()
        with self.assertRaises(ApprovalDeniedError): AuthService(a,Account(),Provider(True),AuthFlowStore()).execute('logout')
        self.assertEqual(a.calls,[])
    def test_interactive_login_extends_adapter_timeout(self):
        a=Adapter(); a.timeout_seconds=30
        AuthService(a,Account(),Provider(),AuthFlowStore()).execute('login')
        self.assertGreaterEqual(a.timeout_seconds,300)
