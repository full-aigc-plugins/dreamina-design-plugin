import unittest
from scripts.session_service import SessionService

class Adapter:
    def __init__(self): self.calls=[]
    def run_text(self,args): self.calls.append(args); return type('R',(),{'exit_code':0,'stdout':'{}','stderr':''})()
class Provider:
    def confirm(self,request): return 'ok'

class SessionServiceTests(unittest.TestCase):
    def test_rename_exact_argv(self):
        a=Adapter(); SessionService(a,Provider()).execute('rename',session_id='123',name='new name')
        self.assertEqual(a.calls,[['session','rename','123','new name']])
    def test_list_is_read_only(self):
        a=Adapter(); SessionService(a,Provider()).execute('list')
        self.assertEqual(a.calls,[['session','list']])
    def test_default_delete_rejected(self):
        a=Adapter()
        with self.assertRaises(ValueError): SessionService(a,Provider()).execute('delete',session_id='0')
        self.assertEqual(a.calls,[])
