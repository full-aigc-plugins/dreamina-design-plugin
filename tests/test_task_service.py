import unittest
from scripts.task_service import TaskService

class Adapter:
    def __init__(self): self.calls=[]
    def run(self,args): self.calls.append(args); return type('R',(),{'exit_code':0,'payload':{'gen_status':'success','items':[]}})()

class TaskServiceTests(unittest.TestCase):
    def test_unknown_submit_queries_once_without_resubmit(self):
        a=Adapter(); result=TaskService(a).query('external-1')
        self.assertEqual(a.calls,[['query_result','--submit_id','external-1']]); self.assertEqual(result['provenance'],'externally-queried')
    def test_list_tasks_closed_filters(self):
        a=Adapter(); TaskService(a).list_tasks({'gen_status':'success'},limit=20)
        self.assertEqual(a.calls,[['list_task','--gen_status','success','--limit','20']])
    def test_unknown_filter_rejected(self):
        with self.assertRaises(ValueError): TaskService(Adapter()).list_tasks({'shell':'x'},limit=20)

    def test_query_rejects_nonzero_exit_and_unknown_or_malformed_status(self):
        for exit_code, payload in ((1, {'gen_status':'success'}), (0, {'gen_status':'mystery'}), (0, {})):
            class Bad:
                def run(self, args): return type('R', (), {'exit_code': exit_code, 'payload': payload})()
            with self.subTest(exit_code=exit_code, payload=payload), self.assertRaises(ValueError):
                TaskService(Bad()).query('external-1')

    def test_query_normalizes_real_cli_status_variants(self):
        for raw, expected in (('processing', 'querying'), ('pending', 'querying'), ('succeeded', 'success'), ('failure', 'failed')):
            class Variant:
                def run(self, args): return type('R', (), {'exit_code': 0, 'payload': {'gen_status': raw}})()
            self.assertEqual(TaskService(Variant()).query('external-1')['status'], expected)
