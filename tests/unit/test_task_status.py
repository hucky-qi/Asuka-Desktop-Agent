import unittest,tempfile,threading,json,os
from pathlib import Path
from unittest.mock import patch
import backend.assistant.task_status as t, backend.assistant.bubble_chat as b, backend.providers.bubble_web as w, backend.providers.web_usage as u
class TaskStateTests(unittest.TestCase):
 def setUp(self):
  with t.LOCK:t.TASKS.clear();t.CURRENT.clear();t.LAST_COMPLETE=0
 def test_parallel_finish_preserves_other_task(self):
  a=t.begin('a','file');c=t.begin('b','bubble');t.stage(a,'searching');t.stage(c,'external_search')
  self.assertEqual(t.snapshot()['waiting'],'wifi');t.finish(c,'complete')
  self.assertEqual(t.snapshot()['waiting'],'dots');self.assertEqual(t.snapshot()['active_count'],1)
  t.finish(a);self.assertEqual(t.snapshot()['waiting'],'none')
 def test_version_rejects_old_update_or_finish(self):
  old=t.begin('same','bubble');new=t.begin('same','bubble')
  self.assertFalse(t.stage(old,'external_search'));self.assertFalse(t.finish(old))
  self.assertEqual(t.snapshot()['tasks'][0]['version'],new)
 def test_status_contains_only_minimal_activity(self):
  t.begin('random-id','file');s=t.snapshot()
  self.assertEqual(set(s['tasks'][0]),{'request_id','channel','version','phase'})
  self.assertNotIn('history',json.dumps(s))
 def test_disconnected_lease_expires(self):
  token=t.begin('a','file')
  with t.LOCK:t.TASKS[token]['touched']-=211
  self.assertEqual(t.snapshot()['waiting'],'none')
 def test_superseded_bubble_job_cannot_finish_new_task(self):
  old=b.prepare({'question':'old','request_id':'old'});new=b.prepare({'question':'new','request_id':'new','session_id':old[0]})
  b.generate(old,lambda event:None)
  self.assertIn(new[-1],t.TASKS);b.stop(new[0],new[3]);self.assertEqual(t.snapshot()['waiting'],'none')
 def test_mock_network_only_actual_dispatch_uses_wifi(self):
  with tempfile.TemporaryDirectory(dir='acceptance/unified-waiting') as folder,patch.object(u,'PATH',Path(folder)/'u.json'),patch.object(u,'LOCK_PATH',Path(folder)/'u.lock'),patch.dict(os.environ,{'BUBBLE_WEB_ENABLED':'true','BAIDU_FREE_RESOURCES_CONFIRMED':'true','BAIDU_APPBUILDER_API_KEY':'mock','BAIDU_TEST_MODE':'false'}):
   token=t.begin('mock-network','bubble');t.stage(token,'generating');states=[]
   class Response:
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def read(self,*a):states.append(t.snapshot()['waiting']);return b'{"references":[{"title":"public","url":"https://example.org","snippet":"public"}]}'
   def transport(*args,**kw):states.append(t.snapshot()['waiting']);return Response()
   with t.bind(token):w.search('public',threading.Event(),transport)
   self.assertEqual(states,['wifi','wifi']);self.assertEqual(t.snapshot()['waiting'],'dots')
   with t.bind(token):
    with self.assertRaises(RuntimeError):w.search('public',threading.Event(),lambda *a,**k:(_ for _ in ()).throw(TimeoutError()))
   self.assertEqual(t.snapshot()['waiting'],'dots');t.finish(token);self.assertEqual(t.snapshot()['waiting'],'none')
if __name__=='__main__':unittest.main()
