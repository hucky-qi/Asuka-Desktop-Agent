"""Isolated transport mocks; never touches production usage, keys or network."""
import json,os,sys,tempfile,time,threading,subprocess,unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import backend.providers.web_usage as u, backend.providers.bubble_web as w
class Response:
 def __enter__(self):return self
 def __exit__(self,*a):pass
 def read(self,*a):return b'{"references":[{"title":"public","url":"https://example.org/","snippet":"evidence"}]}'
class Budget30Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path('acceptance/baidu-budget30'))
  self.root=Path(self.tmp.name).resolve()
  self.patches=[patch.object(u,'PATH',self.root/'usage.json'),patch.object(u,'LOCK_PATH',self.root/'usage.lock'),patch.dict(os.environ,{'BAIDU_LOCAL_DAILY_LIMIT':'30','BAIDU_TEST_MODE':'false','BUBBLE_WEB_ENABLED':'true','BAIDU_FREE_RESOURCES_CONFIRMED':'true','BAIDU_APPBUILDER_API_KEY':'mock-only'})]
  for p in self.patches:p.start()
  self.addCleanup(self.tmp.cleanup)
  for p in self.patches:self.addCleanup(p.stop)
 def seed(self,n):
  self.old=time.time()-90000
  u.PATH.write_text(json.dumps({'attempts':[self.old]+[time.time()]*n,'test_attempts':4}))
 def test_boundary_history_and_failure(self):
  self.seed(29);calls=[]
  def fail(*a,**k):calls.append(True);raise TimeoutError()
  with self.assertRaises(RuntimeError):w.search('公开问题',threading.Event(),fail)
  with self.assertRaisesRegex(ValueError,'上限'):w.search('公开问题',threading.Event(),fail)
  self.assertEqual(len(calls),1);self.assertEqual(u.status()['used'],30)
  self.assertIn(self.old,json.loads(u.PATH.read_text())['attempts'])
 def test_concurrent_atomic_reservation(self):
  self.seed(28);calls=[]
  def transport(*a,**kw):calls.append(True);return Response()
  def attempt(_):
   try:w.search('公开问题',threading.Event(),transport);return True
   except ValueError:return False
  with ThreadPoolExecutor(max_workers=8) as pool:out=list(pool.map(attempt,range(8)))
  self.assertEqual(sum(out),2);self.assertEqual(len(calls),2);self.assertEqual(u.status()['used'],30)
 def test_restart_persistence(self):
  self.seed(29);w.search('公开问题',threading.Event(),lambda *a,**k:Response())
  code=f"import web_usage as u;from pathlib import Path;u.PATH=Path({str(u.PATH)!r});u.LOCK_PATH=Path({str(u.LOCK_PATH)!r});print(u.status()['used'],u.status()['limit'])"
  self.assertEqual(subprocess.check_output([sys.executable,'-c',code],text=True).strip(),'30 30')
 def test_cancellation_before_and_after_dispatch(self):
  self.seed(0);c=threading.Event();c.set();calls=[]
  with self.assertRaises(InterruptedError):w.search('公开问题',c,lambda *a,**k:calls.append(True))
  self.assertEqual(calls,[]);self.assertEqual(u.status()['used'],0)
  c.clear()
  def sent(*a,**k):c.set();return Response()
  with self.assertRaises(InterruptedError):w.search('公开问题',c,sent)
  self.assertEqual(u.status()['used'],1)
 def test_old_environment_cannot_raise_safety_ceiling(self):
  os.environ['BAIDU_LOCAL_DAILY_LIMIT']='50';self.assertEqual(u.limit(),30)
 def test_payload_standard_no_cloud_generation(self):
  calls=[]
  def send(req,**kw):calls.append(req);return Response()
  w.search('悉尼歌剧院开放年份',threading.Event(),send)
  payload=json.loads(calls[0].data)
  self.assertEqual(payload['edition'],'standard');self.assertNotIn('model',payload)
  self.assertEqual(payload['messages'],[{'role':'user','content':'悉尼歌剧院开放年份'}])
if __name__=='__main__':unittest.main()
