import unittest,tempfile,threading,json,os
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace as NS
import backend.assistant.bubble_chat as b, backend.memory.bubble_memory as m, backend.providers.bubble_web as w, backend.providers.web_usage as u
from tests.unit._local_fakes import LocalProvider, choose_local_qwen, replace_module_provider
def Provider():return LocalProvider('测试回答')
class Extensions(unittest.TestCase):
 def setUp(self):
  choose_local_qwen(self)
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]/'acceptance/b-memory-web');self.patch=patch.object(m,'PATH',Path(self.tmp.name)/'memory.json');self.patch.start();b.SESSIONS.clear();self.usage_patches=[patch.object(u,'PATH',Path(self.tmp.name)/'usage.json'),patch.object(u,'LOCK_PATH',Path(self.tmp.name)/'usage.lock')];[x.start() for x in self.usage_patches]
 def tearDown(self):self.patch.stop();[x.stop() for x in self.usage_patches];self.tmp.cleanup()
 def job(self,q,sid=None,rid='r',web=False):return b.prepare({'question':q,'session_id':sid,'request_id':rid,'web_enabled':web})
 def test_history_pairs_reset_cancel_and_restart_memory(self):
  p=Provider();j=self.job('我的测试昵称是小盛');b.generate(j,lambda x:None,p)
  b.generate(self.job('我叫什么？',j[0],'next'),lambda x:None,p)
  self.assertEqual(p.calls[-1]['messages'][1]['content'],'我的测试昵称是小盛')
  b.stop(j[0],reset=True);b.generate(self.job('解释RAG',j[0],'rag'),lambda x:None,p)
  self.assertEqual(len(p.calls[-1]['messages']),2)
  m.command('记住：我的偏好是简洁')  # explicit save; chat '记住' only proposes and needs confirmation
  b.SESSIONS.clear();self.usage_patches=[patch.object(u,'PATH',Path(self.tmp.name)/'usage.json'),patch.object(u,'LOCK_PATH',Path(self.tmp.name)/'usage.lock')];[x.start() for x in self.usage_patches];b.generate(self.job('我的偏好？'),lambda x:None,p)
  self.assertIn('简洁',p.calls[-1]['messages'][0]['content'])
  self.assertEqual(m.command('查看长期记忆'),'长期记忆：\n1. 我的偏好是简洁')
  m.command('忘记：1');self.assertEqual(m.load(),[])
  m.command('记住：测试');m.command('清空长期记忆');self.assertEqual(m.load(),[])
 def test_no_implicit_save_and_corrupt_store_not_overwritten(self):
  p=Provider();b.generate(self.job('我喜欢红色'),lambda x:None,p);self.assertFalse(m.PATH.exists())
  m.PATH.write_text('invalid');before=m.PATH.read_bytes()
  self.assertIn('损坏',m.command('记住：新事实'))  # reported, never overwritten
  self.assertEqual(m.PATH.read_bytes(),before)
 def test_unconfigured_never_network_and_query_privacy(self):
  with patch.dict(os.environ,{},clear=True),patch('backend.providers.bubble_web.urlopen') as net:
   with self.assertRaises(ValueError):w.search('搜索天气',threading.Event())
   net.assert_not_called()
  for q in ['搜索 /Users/private/a.txt','搜索 api_key=secret']:
   with self.assertRaises(ValueError):w.query(q)
 def test_search_summary_sources_and_no_private_history_transmission(self):
  class Response:
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def read(self,*a):return json.dumps({'references':[{'title':'真实标题','url':'https://example.com/page','content':'<b>摘要</b>'},{'url':'file:///Users/private'}]}).encode()
  captured=[]
  def opener(req,timeout):captured.append((req.full_url,timeout));return Response()
  with patch.dict(os.environ,{'BUBBLE_WEB_ENABLED':'true','BAIDU_APPBUILDER_API_KEY':'mock','BAIDU_FREE_RESOURCES_CONFIRMED':'true'}):
   result=w.search('搜索今日天气',threading.Event(),opener)
   self.assertEqual(len(result),1);self.assertEqual(result[0]['snippet'],'摘要');self.assertEqual(captured[0][1],10)
   events=[];p=Provider();replace_module_provider(self,p)
   with patch.object(w,'search',return_value=result) as search:
    job=self.job('搜索今日天气',web=True);job[1].history=[{'role':'user','content':'秘密记忆'}]
    b.generate(job,events.append,p);self.assertEqual(search.call_args.args[0],'搜索今日天气')
    sent=json.dumps(p.calls[-1]['messages'],ensure_ascii=False);self.assertNotIn('秘密记忆',sent)  # history never sent with search
    self.assertIn('untrusted',p.calls[-1]['messages'][0]['content']);done=[e for e in events if e['type']=='done'][-1]
    self.assertEqual([x['url'] for x in done['sources']],['https://example.com/page'])
 def test_timeout_cancel_reset_during_search_never_commit(self):
  with patch.dict(os.environ,{'BUBBLE_WEB_ENABLED':'true','BAIDU_APPBUILDER_API_KEY':'mock','BAIDU_FREE_RESOURCES_CONFIRMED':'true'}):
   def timeout(*a,**k):raise TimeoutError()
   with self.assertRaises(RuntimeError):w.search('搜索天气',threading.Event(),timeout)
  for operation in ('cancel','reset'):
   job=self.job('搜索天气',web=True);events=[]
   def search(text,cancel):b.stop(job[0],job[3],operation=='reset');return []
   with patch.object(w,'search',side_effect=search):b.generate(job,events.append,Provider())
   self.assertEqual(job[1].history,[]);self.assertFalse(any(e.get('state')=='complete' for e in events))
