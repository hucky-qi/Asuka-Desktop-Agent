"""Hybrid qualification, cancellation and cache isolation; no real model/index."""
import json,threading,time,unittest
from pathlib import Path
from array import array
from types import SimpleNamespace as NS
from unittest.mock import patch
import app,settings,search_retrieval,semantic_search,retrieval_cache,concept_review,file_tools,diagnostics
import test_accuracy_constraints as fixtures

class BalanceTests(unittest.TestCase):
 setUp=fixtures.HardConstraintTests.setUp
 def test_many_filename_matches_never_skip_concepts(self):
  items=[file_tools.file_info(self.desktop/'anime.png')]
  names={'results':items,'total':100,'limit':10,'scanned_files':100}
  meaning={'results':[],'total':0,'indexed_scope_files':10,'needs_index':False}
  with patch.object(file_tools,'find_files',return_value=names),patch.object(semantic_search,'search_semantic',return_value=meaning) as vectors:
   data=search_retrieval.find_files(keyword='unseen concept',scope='desktop',extensions=['txt'],modified_after='2025-01-01',min_size=2)
  vectors.assert_called_once();self.assertEqual(vectors.call_args.kwargs['hard_filters'],{'modified_after':'2025-01-01','min_size':2})
  self.assertEqual(data['retrieval']['mode'],'hybrid')
 def test_cancel_embedding_does_not_cache_late_result(self):
  cancel=threading.Event();release=threading.Event();entered=threading.Event();errors=[]
  def slow(q):entered.set();release.wait(3);return [array('f',[1,0])]
  def request():
   file_tools._current_search.cancel_event=cancel
   try:retrieval_cache.query_vector('unique cancel fixture',slow)
   except Exception as e:errors.append(e)
   finally:file_tools._current_search.cancel_event=None
  t=threading.Thread(target=request);t.start();self.assertTrue(entered.wait(1));cancel.set();t.join(1)
  self.assertFalse(t.is_alive());self.assertIsInstance(errors[0],file_tools.SearchCancelled)
  release.set();self.assertFalse(any(k[-1]=='unique cancel fixture' for k in retrieval_cache.queries))
 def test_review_cache_tracks_scope_evidence_and_revision(self):
  def answer(**kwargs):
   evidence=json.loads(kwargs['messages'][1]['content'])['candidates'][0]['evidence']
   return NS(message=NS(content=json.dumps({'relevant_ids':[0]})),load_duration=0,prompt_eval_duration=0,eval_duration=0)
  a=[{'path':str(self.desktop/'anime.png'),'name':'first','text':'full evidence'}]
  b=[{'path':str(self.downloads/'anime_other.webm'),'name':'second','text':'different evidence'}]
  with patch.object(concept_review.client,'chat',side_effect=answer) as model:
   first=concept_review.review('concept',a);concept_review.review('concept',a);other=concept_review.review('concept',b)
  self.assertEqual(model.call_count,2);self.assertNotEqual(first,other)
 def test_fuzzy_shape_not_subject_but_typo_preserved(self):
  self.assertEqual(file_tools.relevance('vacation',Path('location.png')),0)
  self.assertEqual(file_tools.relevance('animation',Path('information.png')),0)
  self.assertGreater(file_tools.relevance('vacation',Path('vacaton.png')),0)
 def seed(self,text):
  p=self.desktop/'opaque.md';p.write_text('fixture');st=p.stat()
  with semantic_search.connect() as db:
   db.execute('INSERT INTO documents(path,mtime_ns,size,model,indexed_at) VALUES(?,?,?,?,?)',(str(p),st.st_mtime_ns,st.st_size,settings.EMBED_MODEL,1))
   db.execute('INSERT INTO chunks(path,position,text,vector,model) VALUES(?,?,?,?,?)',(str(p),0,text,array('f',[.6,.8]).tobytes(),settings.EMBED_MODEL));db.commit()
  return p
 def test_review_receives_full_evidence_not_card_excerpt(self):
  text='Intro. '*180+'deeper concept evidence at the end';p=self.seed(text)
  captured=[]
  def verify(q,rows):captured.extend(rows);return {str(p)}
  with patch.object(semantic_search,'embedding_vectors',return_value=[array('f',[1,0])]),patch.object(concept_review,'review',side_effect=verify):
   data=semantic_search.search_semantic('paraphrased subject','desktop',['md'])
  self.assertEqual(captured[0]['text'],text);self.assertNotIn('at the end',data['results'][0]['excerpt'])
 def test_review_rejects_nearest_unrelated_candidate(self):
  self.seed('Unrelated content')
  with patch.object(semantic_search,'embedding_vectors',return_value=[array('f',[1,0])]),patch.object(concept_review,'review',return_value=set()):
   self.assertEqual(semantic_search.search_semantic('unknown theme','desktop')['total'],0)
 def test_review_failure_is_partial_not_fake_complete(self):
  self.seed('Unrelated content')
  with patch.object(semantic_search,'embedding_vectors',return_value=[array('f',[1,0])]),patch.object(concept_review,'review',side_effect=RuntimeError('timeout')):
   data=semantic_search.search_semantic('unknown theme','desktop')
  self.assertEqual(data['total'],0);self.assertTrue(data['partial']);self.assertIn('未完成',data['note'])
 def test_one_snapshot_filter_sort_and_selection_identity(self):
  for name in ['ZX81.pdf','ZX81.txt']:(self.desktop/name).write_text('fixture')
  state=app.Session();app.answer('找ZX81',state,'desktop',fast=True)
  app.answer('只看PDF',state,'desktop',fast=True);app.answer('按最近修改排序',state,'desktop',fast=True)
  with patch.object(file_tools,'open_file',return_value={'opened':True}) as opened:
   app.answer('打开第一个',state,'desktop',fast=True)
  self.assertEqual(str(opened.call_args.args[0]),str(self.desktop/'ZX81.pdf'))
 def test_progress_is_not_a_result_or_completion(self):
  events=[];trace=diagnostics.Trace('id',events.append)
  trace.emit('progress',state='searching',elapsed_seconds=16,message='working')
  self.assertIsNone(trace.first_cards);self.assertNotIn('results',events[0]);self.assertEqual(events[0]['request_id'],'id')
 def test_compact_intent_does_not_generate_discarded_final_reply(self):
  p=self.seed('A complete concept discussion')
  response=NS(message=NS(content=json.dumps({'method':'semantic_search_files','query':'concept discussion','scope':'all','extensions':['md'],'directory':None}),tool_calls=[]))
  with patch.object(app,'chat',return_value=response) as chat,patch.object(semantic_search,'embedding_vectors',return_value=[array('f',[.6,.8])]):
   reply,events=app.answer('请按含义查找概念讨论的MD资料',app.Session(),'desktop',fast=True)
  self.assertEqual(chat.call_count,1);self.assertEqual(events[0]['results'][0]['path'],str(p));self.assertTrue(reply)
 def test_review_cannot_invent_a_result_identity(self):
  row=[{'path':str(self.desktop/'opaque.md'),'name':'opaque','text':'Actual source content without that claim.'}]
  response=NS(message=NS(content=json.dumps({'relevant_ids':[4]})),load_duration=0,prompt_eval_duration=0,eval_duration=0)
  with patch.object(concept_review.client,'chat',return_value=response):
   with self.assertRaises(RuntimeError):concept_review.review('novel test concept',row)
 def test_query_instruction_does_not_change_document_vectors(self):
  captured=[]
  def embed(inputs):captured.extend(inputs);return [array('f',[1,0])]
  retrieval_cache.query_vector('unique retrieval topic',embed)
  self.assertTrue(captured[0].startswith('Instruct: '));self.assertIn('Query: unique retrieval topic',captured[0])
  self.assertFalse(settings.SEMANTIC_DB.exists())
