import unittest,tempfile,threading,json,os,subprocess,sys
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
import backend.providers.bubble_web as w, backend.providers.web_usage as u, backend.assistant.bubble_chat as b, backend.app as app, backend.settings as settings
class Response:
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def read(self,*args):return json.dumps({'references':[{'title':'公开摘要','url':'https://example.org/source','content':'来源证据'}]}).encode()
class Provider:
 def __init__(self):self.calls=[]
 def chat(self,**kw):self.calls.append(kw);yield NS(message=NS(content='摘要归纳 [1]'))
class BaiduTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path('acceptance/baidu-navigation'));root=Path(self.tmp.name).resolve();self.patches=[patch.object(u,'PATH',root/'usage.json'),patch.object(u,'LOCK_PATH',root/'usage.lock'),patch.dict(os.environ,{'BUBBLE_WEB_ENABLED':'true','BAIDU_APPBUILDER_API_KEY':'mock','BAIDU_FREE_RESOURCES_CONFIRMED':'true','BAIDU_TEST_MODE':'false','BAIDU_LOCAL_DAILY_LIMIT':'50','BAIDU_TEST_LIMIT':'2'})]
  for p in self.patches:p.start()
  b.SESSIONS.clear()
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.tmp.cleanup()
 def test_unchecked_zero_checked_once_and_payload(self):
  calls=[]
  def transport(req,timeout):calls.append(req);return Response()
  def run(enabled,rid):
   job=b.prepare({'question':'公开基础问题','request_id':rid,'web_enabled':enabled});events=[]
   with patch.object(w,'urlopen',side_effect=transport):b.generate(job,events.append,Provider())
   return events
  run(False,'local');self.assertEqual(calls,[])
  events=run(True,'web');self.assertEqual(len(calls),1)
  payload=json.loads(calls[0].data);self.assertEqual(payload['messages'],[{'role':'user','content':'公开基础问题'}]);self.assertNotIn('model',payload)
  self.assertTrue(calls[0].full_url.endswith('/web_search'));self.assertEqual(u.status()['used'],1)
  self.assertTrue(any(e.get('source')=='百度搜索摘要' for e in events))
 def test_limit_and_concurrency_persistence(self):
  os.environ['BAIDU_LOCAL_DAILY_LIMIT']='3'
  def attempt(_):
   try:w.search('公开问题',threading.Event(),lambda *a,**k:Response());return True
   except ValueError:return False
  with ThreadPoolExecutor(max_workers=8) as pool:out=list(pool.map(attempt,range(8)))
  self.assertEqual(sum(out),3);self.assertEqual(u.status()['used'],3)
  code="import web_usage as u;from pathlib import Path;u.PATH=Path("+repr(str(u.PATH))+");u.LOCK_PATH=Path("+repr(str(u.LOCK_PATH))+");print(u.status()['used'])"
  out=subprocess.check_output([sys.executable,'-c',code],text=True);self.assertEqual(out.strip(),'3')
 def test_test_budget_failure_counts_cancel_before_send_does_not(self):
  os.environ['BAIDU_TEST_MODE']='true'
  def fail(*a,**k):raise TimeoutError()
  for _ in range(2):
   with self.assertRaises(RuntimeError):w.search('公开问题',threading.Event(),fail)
  with self.assertRaisesRegex(ValueError,'测试预算'):w.search('公开问题',threading.Event(),fail)
  self.assertEqual(u.status()['used'],2);self.assertEqual(u.status()['test_used'],2)
  os.environ['BAIDU_TEST_LIMIT']='3'
  with self.assertRaises(RuntimeError):w.search('公开问题',threading.Event(),fail)
  self.assertEqual(u.status()['used'],3);self.assertEqual(u.status()['test_used'],3)
  with self.assertRaisesRegex(ValueError,'测试预算'):w.search('公开问题',threading.Event(),fail)
  c=threading.Event();c.set()
  with self.assertRaises(InterruptedError):w.search('公开问题',c,fail)
  self.assertEqual(u.status()['used'],3)
 def test_zero_results_and_cancel_after_send(self):
  class Empty(Response):
   def read(self,*a):return b'{"references":[]}'
  with self.assertRaises(ValueError):w.search('公开问题',threading.Event(),lambda *a,**k:Empty())
  self.assertEqual(u.status()['used'],1)
  c=threading.Event()
  def cancel(*a,**k):c.set();return Response()
  with self.assertRaises(InterruptedError):w.search('公开问题',c,cancel)
  self.assertEqual(u.status()['used'],2)
 def test_unconfigured_and_corrupt_fail_closed(self):
  os.environ['BAIDU_FREE_RESOURCES_CONFIRMED']='false'
  with patch.object(w,'urlopen') as net:
   with self.assertRaises(ValueError):w.search('公开问题',threading.Event())
   net.assert_not_called()
  os.environ['BAIDU_FREE_RESOURCES_CONFIRMED']='true';u.PATH.write_text('broken')
  with patch.object(w,'urlopen') as net:
   with self.assertRaises(ValueError):w.search('公开问题',threading.Event())
   net.assert_not_called()
 def test_structured_search_reuses_file_session(self):
  root=Path(self.tmp.name);(root/'test.pdf').write_text('pdf');(root/'test.txt').write_text('text')
  state=app.Session()
  with patch.object(settings,'SEARCH_ROOTS',{'workspace':root}),patch('backend.preferences.custom_scopes',return_value={}):
   event=app.structured_search({'keyword':'test','scope':'workspace','extensions':['pdf'],'sort_by':'modified_desc'},state)
   self.assertEqual([x['name'] for x in event['results']],['test.pdf']);self.assertEqual(state.last_search_results,event['results']);self.assertTrue(state.last_search_query)
  with self.assertRaises(ValueError):app.structured_search({'min_size':-1},state)
