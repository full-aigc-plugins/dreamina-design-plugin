import unittest
from scripts.auth_service import AuthService
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
    def test_headless_uses_fixed_argv_and_redacts_device_code(self):
        a=Adapter(); result=AuthService(a,Account(),Provider()).execute('login_headless')
        self.assertEqual(a.calls,[['login','--headless']]); self.assertNotIn('secret',str(result))
    def test_check_login_uses_bounded_poll(self):
        a=Adapter(); AuthService(a,Account(),Provider()).execute('check_login',device_code='abc',poll_seconds=30)
        self.assertEqual(a.calls,[['login','checklogin','--device_code','abc','--poll','30']])
    def test_logout_denial_never_invokes_cli(self):
        a=Adapter()
        with self.assertRaises(ApprovalDeniedError): AuthService(a,Account(),Provider(True)).execute('logout')
        self.assertEqual(a.calls,[])
