"""New behavior tests use project-local fixtures and mock cloud transports only."""
import io,json,tempfile,threading,time,unittest,urllib.request
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch,Mock
from array import array
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
  req=urllib.request.Request(url+'/api/chat',data=json.dumps({'question':'复杂表达需模型处理','scope':'workspace','request_id':'stream-test','stream':True}).encode(),headers={'Content-Type':'application/json','Origin':url})
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
  key,state=app.session_for(None);url=f'http://127.0.0.1:{server.server_port}';responses=[]
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
  self.assertIn(key,app.SESSIONS);self.assertFalse(any(x['content']=='old answer' for x in app.SESSIONS[key].history))
 def test_clear_cancels_inflight_and_preserves_empty_context(self):
  key,state=app.session_for(None);cancel=threading.Event()
  with app.CHAT_JOBS_LOCK:app.CHAT_JOBS['clear-inflight']={'state':state,'cancel':cancel,'session_id':key}
  self.addCleanup(app.CHAT_JOBS.pop,'clear-inflight',None)
  fresh=app.clear_chat(key,'clear-inflight')
  self.assertTrue(cancel.is_set());self.assertEqual(app.SESSIONS[fresh].history,[]);self.assertNotIn(key,app.SESSIONS)

class ProviderTests(unittest.TestCase):
 def test_disabled_or_no_key_never_constructs_request(self):
  transport=Mock(side_effect=AssertionError('network forbidden'))
  for p in [model_providers.DeepSeekProvider(transport=transport),model_providers.DeepSeekProvider(enabled=True,transport=transport)]:
   with self.assertRaises(RuntimeError):p.chat(messages=[])
  transport.assert_not_called()
 def test_mock_request_tools_and_tool_ids(self):
  response=io.BytesIO(json.dumps({'choices':[{'message':{'content':None,'tool_calls':[{'id':'c1','function':{'name':'find_files','arguments':'{"keyword":"4418"}'}}]}}]}).encode())
  transport=Mock(return_value=response);p=model_providers.DeepSeekProvider(enabled=True,api_key='mock-only',transport=transport)
  data=p.chat(messages=[{'role':'user','content':'fixture'}],tools=[{'type':'function','function':{'name':'find_files'}}])
  self.assertEqual(data.message.tool_calls[0].function.arguments,{'keyword':'4418'})
  request=transport.call_args.args[0];payload=json.loads(request.data);self.assertEqual(payload['model'],'deepseek-flash')
  msg=p.message(data.message);self.assertEqual(msg['tool_calls'][0]['id'],'c1')
  with self.assertRaises(ValueError):p.chat(messages=[{'role':'tool','tool_name':'find_files','content':'{}'}])
 def test_mock_stream_fragments_cancel_closes(self):
  response=io.BytesIO(b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n')
  transport=Mock(return_value=response);p=model_providers.DeepSeekProvider(enabled=True,api_key='mock-only',transport=transport);cancel=threading.Event()
  stream=p.chat(messages=[],stream=True,cancel=cancel);self.assertEqual(next(stream)['delta']['content'],'hello');cancel.set()
  with self.assertRaises(model_providers.ProviderCancelled):next(stream)
  self.assertTrue(response.closed)
 def test_mock_error_no_auto_fallback(self):
  p=model_providers.DeepSeekProvider(enabled=True,api_key='mock-only',transport=Mock(side_effect=OSError('mock offline')))
  with self.assertRaises(OSError):p.chat(messages=[])
 def test_default_ollama_no_cloud_constructor(self):
  with patch.dict('os.environ',{},clear=True),patch.object(model_providers,'DeepSeekProvider',side_effect=AssertionError('no cloud')):
   self.assertIsInstance(model_providers.configured_provider(),model_providers.OllamaProvider)

 def test_mock_stream_assembles_tool_call_fragments(self):
  chunks=[{'choices':[{'delta':{'tool_calls':[{'index':0,'id':'tool-1','function':{'name':'find_files','arguments':'{"key'}}]}}]},
          {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'word":"4418"}'}}]},'finish_reason':'tool_calls'}]}]
  response=io.BytesIO((''.join('data: '+json.dumps(x)+'\n\n' for x in chunks)+'data: [DONE]\n').encode())
  provider=model_providers.DeepSeekProvider(enabled=True,api_key='mock-only',transport=Mock(return_value=response))
  events=list(provider.chat(messages=[],stream=True));last=events[-1]['message']
  self.assertEqual(last.role,'assistant');self.assertEqual(last.tool_calls[0].function.arguments,{'keyword':'4418'})
 def test_mock_stream_error_and_pre_cancel(self):
  cancel=threading.Event();cancel.set();transport=Mock()
  p=model_providers.DeepSeekProvider(enabled=True,api_key='mock-only',transport=transport)
  with self.assertRaises(model_providers.ProviderCancelled):p.chat(messages=[],cancel=cancel)
  transport.assert_not_called()
  p.transport=Mock(return_value=io.BytesIO(b'data: {"error":{"message":"mock failure"}}\n'))
  with self.assertRaises(RuntimeError):list(p.chat(messages=[],stream=True))
