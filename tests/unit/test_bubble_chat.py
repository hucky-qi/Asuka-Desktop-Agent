import unittest,threading
from types import SimpleNamespace as NS
from unittest.mock import patch
import bubble_chat as b

class Provider:
 def __init__(self):self.calls=[]
 def chat(self,**kwargs):
  self.calls.append(kwargs)
  yield NS(message=NS(content='回答'))
  yield NS(message=NS(content='完成'))

class BubbleTests(unittest.TestCase):
 def setUp(self):b.SESSIONS.clear();b.PENDING_CANCEL.clear()
 def job(self,sid=None,rid='r'):return b.prepare({'question':'你好','session_id':sid,'request_id':rid})
 def test_local_only_no_tools_bounded_independent_history(self):
  p=Provider();sid=None
  for i in range(10):
   job=self.job(sid,str(i));sid=job[0];events=[];b.generate(job,events.append,p)
   self.assertEqual(events[-1]['state'],'complete')
  self.assertTrue(sid.startswith('bubble-'));self.assertLessEqual(len(b.SESSIONS[sid].history),16)
  self.assertLessEqual(sum(len(x['content']) for x in b.SESSIONS[sid].history),b.MAX_CONTEXT)
  self.assertTrue(all('tools' not in x and x['think'] is False for x in p.calls))
  self.assertGreater(len(p.calls[-1]['messages']),2)
  with self.assertRaises(ValueError):self.job('file-assistant-session')
 def test_cancel_reset_and_supersede_prevent_commit(self):
  for operation in ['cancel','reset','supersede']:
   b.SESSIONS.clear();job=self.job();events=[]
   def emit(e):
    events.append(e)
    if e['type']=='delta':
     if operation=='supersede':self.job(job[0],'new')
     else:b.stop(job[0],'r',operation=='reset')
   b.generate(job,emit,Provider())
   self.assertEqual(b.SESSIONS[job[0]].history,[])
   self.assertFalse(any(e.get('state')=='complete' for e in events))
   self.assertEqual(sum(e['type']=='delta' for e in events),1)
 def test_cancel_before_session_packet(self):
  job=self.job();b.stop(None,'r');b.generate(job,lambda e:None,Provider());self.assertEqual(job[1].history,[])
  b.stop(None,'not-started')
  with self.assertRaises(ValueError):self.job(rid='not-started')
 def test_busy_and_provider_error_preserve_existing_context(self):
  job=self.job();events=[]
  with patch.object(b.SLOTS,'acquire',return_value=False):b.generate(job,events.append,Provider())
  self.assertEqual(events[-1]['type'],'error');self.assertEqual(job[1].history,[])
  class Broken:
   def chat(self,**kw):raise RuntimeError('offline')
  b.generate(self.job(job[0],'next'),events.append,Broken());self.assertEqual(events[-1]['state'],'idle')
 def test_reset_and_later_request_start_empty(self):
  job=self.job();p=Provider();b.generate(job,lambda e:None,p);b.stop(job[0],reset=True)
  b.generate(self.job(job[0],'next'),lambda e:None,p);self.assertEqual(len(p.calls[-1]['messages']),2)
  recovered=b.stop('expired-after-backend-restart',reset=True)
  self.assertEqual(b.SESSIONS[recovered].history,[])
