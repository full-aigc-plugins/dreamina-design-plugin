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
