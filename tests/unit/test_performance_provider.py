"""New behavior tests use project-local fixtures and mock cloud transports only."""
import io,json,tempfile,threading,time,unittest,urllib.request
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch,Mock
from array import array
from tests.unit._local_fakes import qwen_web_session
import backend.providers.cloud_access as cloud_access
import backend.app as app, backend.diagnostics as diagnostics, backend.files.file_tools as file_tools, backend.files.semantic_search as semantic_search, backend.settings as settings, backend.files.search_intent as search_intent, backend.files.retrieval_cache as retrieval_cache, backend.providers.model_providers as model_providers

class PerformanceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=settings.PROJECT/'acceptance/performance/tmp');self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);(self.root/'resume 简历.txt').write_text('strategyproofness')
  (self.root/'4418.pdf').write_text('fixture');(self.root/'4418.txt').write_text('fixture')
  self.patches=[patch.object(settings,'SEARCH_ROOTS',{'workspace':self.root}),patch.object(settings,'SEMANTIC_DB',self.root/'index.sqlite3'),patch.object(settings,'PREFERENCES_PATH',self.root/'prefs.json')]
  for p in self.patches:p.start();self.addCleanup(p.stop)
  file_tools._current_search.cancel_event=None;file_tools._current_search.deadline=None
 def test_fast_multiturn_no_model_and_same_numbers(self):
  state=app.Session()
  with patch.object(app,'chat',side_effect=AssertionError('no model')),patch.object(file_tools,'find_files',wraps=file_tools.find_files) as scan:
   _,events=app.answer('找 4418',state,'workspace',fast=True)
   app.answer('只看 PDF',state,'workspace',fast=True);app.answer('按最近修改排序',state,'workspace',fast=True)
   self.assertEqual(scan.call_count,1);self.assertEqual(state.current_filtered_results[0]['name'],'4418.pdf')
   with patch.object(file_tools,'open_file',return_value={'name':'4418.pdf'}) as opened:
    app.answer('打开第一个',state,'workspace',fast=True);self.assertTrue(opened.call_args.args[0].name=='4418.pdf')
 def test_complex_constraints_stay_on_model_path(self):
  for q in ['找最近一周大于10MB的PDF','在“courses”文件夹找4418','找4418然后解释第二个','帮我找为什么没找到']:
   self.assertIsNone(search_intent.fast_plan(q))
 def test_embedding_cache_revision_invalidates(self):
  settings.SEMANTIC_DB.write_bytes(b'first');fn=Mock(return_value=[array('f',[1,0])])
  retrieval_cache.query_vector('q',fn);retrieval_cache.query_vector('q',fn);self.assertEqual(fn.call_count,1)
  settings.SEMANTIC_DB.write_bytes(b'changed revision')
  retrieval_cache.query_vector('q',fn);self.assertEqual(fn.call_count,2)
 def test_vector_snapshot_invalidates_after_incremental_commit(self):
  path=self.root/'resume 简历.txt';stat=path.stat()
  with semantic_search.connect() as db:
   db.execute('INSERT INTO documents(path,mtime_ns,size,model,indexed_at) VALUES(?,?,?,?,?)',(str(path),stat.st_mtime_ns,stat.st_size,settings.EMBED_MODEL,1))
   db.execute('INSERT INTO chunks(path,position,text,vector,model) VALUES(?,?,?,?,?)',(str(path),0,'original',array('f',[1,0]).tobytes(),settings.EMBED_MODEL));db.commit()
   self.assertEqual(retrieval_cache.rows(db)[0][2],'original')
   db.execute('UPDATE chunks SET text=?,vector=?',('updated',array('f',[0,1]).tobytes()));db.commit()
   new=retrieval_cache.rows(db);self.assertEqual(new[0][2],'updated');self.assertEqual(list(new[0][3]),[0,1])
 def test_native_dot_matches_reference(self):
  a=array('f',[.1,.2,.3]);b=array('f',[.2,.3,.4]);self.assertAlmostEqual(retrieval_cache.dot(a,b),sum(x*y for x,y in zip(a,b)),places=6)
 def test_content_reuses_existing_text_without_reparse(self):
  import backend.files.file_reader as file_reader
  path=self.root/'resume 简历.txt';stat=path.stat()
  with semantic_search.connect() as db:
   db.execute('INSERT INTO documents(path,mtime_ns,size,model,indexed_at) VALUES(?,?,?,?,?)',(str(path),stat.st_mtime_ns,stat.st_size,settings.EMBED_MODEL,1))
   db.execute('INSERT INTO chunks(path,position,text,vector,model) VALUES(?,?,?,?,?)',(str(path),0,'strategyproofness',array('f',[1,0]).tobytes(),settings.EMBED_MODEL));db.commit()
  with patch.object(file_reader,'extract_sections',side_effect=AssertionError('not parsed')):
   data=file_reader.search_file_content('strategyproofness','workspace',['txt'],fallback_seconds=0)
  self.assertEqual(data['total'],1);self.assertIn('索引',data['results'][0]['evidence_source']);self.assertTrue(data['partial'])
 def test_stream_cards_arrive_before_final_model(self):
  server=app.ThreadingHTTPServer((settings.HOST,0),app.Handler);old=settings.PORT;settings.PORT=server.server_port
  self.addCleanup(setattr,settings,'PORT',old);self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
  threading.Thread(target=server.serve_forever,daemon=True).start();release=threading.Event();self.addCleanup(release.set)
  calls=[]
  def model(**kwargs):
   calls.append(1)
   if len(calls)==1:return NS(message=NS(content='',tool_calls=[NS(function=NS(name='find_files',arguments={'keyword':'4418','scope':'workspace'}))]))
   release.wait(3);return NS(message=NS(content='done',tool_calls=[]))
  url=f'http://127.0.0.1:{server.server_port}'
  req=urllib.request.Request(url+'/api/chat',data=json.dumps({'question':'复杂表达需模型处理','scope':'workspace','session_id':qwen_web_session(),'request_id':'stream-test','stream':True}).encode(),headers={'Content-Type':'application/json','Origin':url})
  with patch.object(app,'chat',side_effect=model),urllib.request.urlopen(req,timeout=5) as response:
   while True:
    event=json.loads(response.readline())
    if event['type']=='tool':break
   self.assertEqual(event['event']['total'],2);self.assertFalse(release.is_set());release.set()
   rest=[json.loads(line) for line in response];self.assertEqual(rest[-1]['type'],'done')
   states=[e.get('state') for e in rest];self.assertIn('complete',states)

 def test_new_request_supersedes_old_same_session(self):
  server=app.ThreadingHTTPServer((settings.HOST,0),app.Handler);old_port=settings.PORT;settings.PORT=server.server_port
  self.addCleanup(setattr,settings,'PORT',old_port);self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
  threading.Thread(target=server.serve_forever,daemon=True).start();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
  key=qwen_web_session();url=f'http://127.0.0.1:{server.server_port}';responses=[]
  def post(body):
   request=urllib.request.Request(url+'/api/chat',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Origin':url})
   with urllib.request.urlopen(request,timeout=5) as response:return json.load(response)
  def model(**kwargs):
   entered.set();release.wait(3);return NS(message=NS(content='old answer',tool_calls=[]))
  with patch.object(app,'chat',side_effect=model):
   thread=threading.Thread(target=lambda:responses.append(post({'question':'旧复杂请求','scope':'workspace','session_id':key,'request_id':'old-superseded'})),daemon=True);thread.start()
   self.assertTrue(entered.wait(2));new=post({'question':'找 4418','scope':'workspace','session_id':key,'request_id':'new-wins'})
   release.set();thread.join(3)
  self.assertTrue(responses[0]['cancelled']);self.assertEqual(new['events'][0]['total'],2)
  self.assertIn(new['session_id'],app.SESSIONS);self.assertFalse(any(x['content']=='old answer' for x in app.SESSIONS[new['session_id']].history))
 def test_clear_cancels_inflight_and_preserves_empty_context(self):
  key,state=app.session_for(None);cancel=threading.Event()
  with app.CHAT_JOBS_LOCK:app.CHAT_JOBS['clear-inflight']={'state':state,'cancel':cancel,'session_id':key}
  self.addCleanup(app.CHAT_JOBS.pop,'clear-inflight',None)
  fresh=app.clear_chat(key,'clear-inflight')
  self.assertTrue(cancel.is_set());self.assertEqual(app.SESSIONS[fresh].history,[]);self.assertNotIn(key,app.SESSIONS)

class MockPermit(cloud_access.Permit):
 """In-memory grant for wire tests: never touches the real .deepseek-usage.json ledger."""
 def __init__(self,rid):self.rid=rid;self.reserved=[];self.records=[]
 def reserve(self,payload,base_url,api_key):self.reserved.append(payload)
 def record(self,status,usage=None,diagnostic=None):self.records.append(status)
 def check_memory_refs(self):pass

class ProviderTests(unittest.TestCase):
 def test_disabled_no_key_or_unpermitted_never_constructs_request(self):
  transport=Mock(side_effect=AssertionError('network forbidden'))
  for p,permit in [(model_providers.DeepSeekProvider(transport=transport),MockPermit('r')),
                   (model_providers.DeepSeekProvider(enabled=True,transport=transport),MockPermit('r')),
                   (model_providers.DeepSeekProvider(enabled=True,api_key='mock-only',transport=transport),None),
                   (model_providers.DeepSeekProvider(enabled=True,api_key='mock-only',transport=transport),MockPermit('other'))]:
   with self.assertRaises(model_providers.CloudError):p.chat(messages=[],permit=permit,request_id='r')
  transport.assert_not_called()
 def test_mock_request_settles_and_cloud_tool_calls_are_refused(self):
  def reply(body):
   response=io.BytesIO(json.dumps(body).encode());response.status=200;response.headers={};return response
  tool_call={'choices':[{'finish_reason':'tool_calls','message':{'content':None,'tool_calls':[{'id':'c1','function':{'name':'find_files','arguments':'{"keyword":"4418"}'}}]}}]}
  transport=Mock(return_value=reply(tool_call));p=model_providers.DeepSeekProvider(enabled=True,api_key='mock-only',transport=transport);permit=MockPermit('t')
  with self.assertRaises(model_providers.CloudError):p.chat(messages=[{'role':'user','content':'fixture'}],permit=permit,request_id='t')
  self.assertEqual(transport.call_count,1)  # cloud may never trigger local tools; no retry
  transport=Mock(return_value=reply({'choices':[{'finish_reason':'stop','message':{'content':'hi'}}],'usage':{'total_tokens':3}}))
  p=model_providers.DeepSeekProvider(enabled=True,api_key='mock-only',transport=transport);permit=MockPermit('r')
  data=p.chat(messages=[{'role':'user','content':'fixture'}],permit=permit,request_id='r')
  self.assertEqual(data.message.content,'hi');payload=json.loads(transport.call_args.args[0].data);self.assertEqual(payload['model'],'deepseek-flash')
  self.assertEqual(permit.reserved,[payload]);self.assertEqual(permit.records[0],'sent');self.assertEqual(permit.records[-1],'completed')
  with self.assertRaises(ValueError):p.chat(messages=[{'role':'tool','tool_name':'find_files','content':'{}'}],permit=MockPermit('x'),request_id='x')
 def test_stream_requests_are_refused_before_transport(self):
  # Cloud streaming is closed by design (complete answers only); replaces the former stream-fragment tests.
  transport=Mock();p=model_providers.DeepSeekProvider(enabled=True,api_key='mock-only',transport=transport);permit=MockPermit('s')
  with self.assertRaises(model_providers.CloudError):p.chat(messages=[],stream=True,permit=permit,request_id='s')
  transport.assert_not_called();self.assertEqual(permit.reserved,[])
 def test_mock_error_no_auto_fallback(self):
  transport=Mock(side_effect=OSError('mock offline'))
  p=model_providers.DeepSeekProvider(enabled=True,api_key='mock-only',transport=transport);permit=MockPermit('e')
  with self.assertRaises(model_providers.CloudError):p.chat(messages=[],permit=permit,request_id='e')
  self.assertEqual(transport.call_count,1);self.assertEqual(permit.records[-1],'failed')  # recorded, never retried
 def test_default_ollama_no_cloud_constructor(self):
  with patch.dict('os.environ',{},clear=True),patch.object(model_providers,'DeepSeekProvider',side_effect=AssertionError('no cloud')):
   self.assertIsInstance(model_providers.configured_provider(),model_providers.OllamaProvider)
 def test_pre_cancel_never_sends(self):
  cancel=threading.Event();cancel.set();transport=Mock()
  p=model_providers.DeepSeekProvider(enabled=True,api_key='mock-only',transport=transport);permit=MockPermit('c')
  with self.assertRaises(model_providers.ProviderCancelled):p.chat(messages=[],cancel=cancel,permit=permit,request_id='c')
  transport.assert_not_called();self.assertEqual(permit.reserved,[])
