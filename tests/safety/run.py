#!/usr/bin/env python3
"""Default offline safety regression, stdlib unittest + loopback recorders only."""
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from concurrent.futures import ProcessPoolExecutor
import unittest,tempfile,threading,json,sys,os,time,subprocess,socket,multiprocessing,hashlib
from urllib.error import URLError,HTTPError
from safety import Ledger,RealGate,MockDependency,SafetyError,BudgetError,post_json,launch
R=Path(__file__).resolve().parent;P=R.parents[1]

def reserve_worker(args):
 path,kind,token,url=args
 ledger=Ledger(path,1,1)
 try:
  ident=ledger.query() if kind=='query' else ledger.call(token)
  post_json(url,{'reservation':ident});return ident
 except BudgetError:return None

class Receiver(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_POST(self):
  self.rfile.read(int(self.headers.get('Content-Length','0')))
  with self.server.lock:self.server.received.append(self.path)
  if self.path=='/redirect':self.send_response(302);self.send_header('Location',self.server.redirect);self.end_headers();return
  self.send_response(500 if self.path=='/failure' else 501 if self.path=='/missing' else 200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{"fixture":true}')

class SafetyTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=OUT);self.root=Path(self.temp.name);self.servers=[]
 def tearDown(self):
  for s in self.servers:s.shutdown();s.server_close()
  self.temp.cleanup()
 def receiver(self):
  s=ThreadingHTTPServer(('127.0.0.1',0),Receiver);s.received=[];s.lock=threading.Lock();self.servers.append(s);threading.Thread(target=s.serve_forever,daemon=True).start();return s,f'http://127.0.0.1:{s.server_port}'
 def ledger(self,q,c):return Ledger(self.root/'budget.json',q,c)
 def fake_gate(self,url,q,c):return RealGate({'enabled':True,'query_limit':q,'call_limit':c,'model_endpoint':url},self.root/'budget.json')
 def guarded_child(self,snippet,port,configuration=True):
  cfg=self.root/'guard.json';cfg.write_text(json.dumps({'connect':[['127.0.0.1',port]],'listen':[]} if configuration else {}))
  code=f'import sys\nsys.path.insert(0,{str(R)!r})\nfrom safety import install_guard\ninstall_guard({str(cfg)!r})\n'+snippet
  return subprocess.run([sys.executable,'-I','-c',code],capture_output=True,text=True,timeout=6)
 def test_01_mock_receiver_only(self):
  s,u=self.receiver();m=MockDependency(u,{'/mock'});self.assertTrue(m.post('/mock',{'fixture':1})['fixture']);self.assertEqual(s.received,['/mock'])
 def test_02_unavailable_no_fallback(self):
  trap,_=self.receiver()
  with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
  m=MockDependency(f'http://127.0.0.1:{port}',{'/mock'})
  with self.assertRaises((URLError,OSError)):m.post('/mock',{})
  self.assertEqual(trap.received,[])
 def test_03_wrong_address_and_unknown_interface(self):
  with self.assertRaises(SafetyError):MockDependency('https://example.invalid:443',{'/mock'})
  s,u=self.receiver();m=MockDependency(u,{'/mock'})
  with self.assertRaises(SafetyError):m.post('/api/embeddings',{})
  self.assertEqual(s.received,[])
 def test_03b_declared_but_unimplemented_dependency(self):
  s,u=self.receiver();m=MockDependency(u,{'/missing'})
  with self.assertRaises(HTTPError):m.post('/missing',{})
  self.assertEqual(s.received,['/missing'])
 def test_04_redirect_no_fallback(self):
  trap,v=self.receiver();s,u=self.receiver();s.redirect=v+'/model'
  with self.assertRaises(SafetyError):MockDependency(u,{'/redirect'}).post('/redirect',{})
  self.assertEqual(s.received,['/redirect']);self.assertEqual(trap.received,[])
 def test_05_nested_ordinary_sdk_blocked(self):
  allowed,_=self.receiver();trap,u=self.receiver()
  code=f'''from ollama import Client
from concurrent.futures import ThreadPoolExecutor
def nested():
 try: Client(host={u!r},timeout=.2).chat(model='fixture',messages=[{{'role':'user','content':'ordinary fixture'}}])
 except Exception: return 'blocked'
 return 'escaped'
with ThreadPoolExecutor() as pool: assert pool.submit(nested).result()=='blocked'
'''
  result=self.guarded_child(code,allowed.server_port);self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(trap.received,[])
 def test_05b_dns_boundary_without_external_resolution(self):
  s,_=self.receiver();result=self.guarded_child('''import socket
try:socket.gethostbyname('127.0.0.2')
except Exception:pass
else:raise AssertionError('unconfigured resolver path')
''',s.server_port);self.assertEqual(result.returncode,0,result.stderr)
 def test_06_guard_missing_config_is_fatal(self):
  s,u=self.receiver();result=self.guarded_child(f'from urllib.request import urlopen\nurlopen({u!r})',s.server_port,False);self.assertNotEqual(result.returncode,0);self.assertEqual(s.received,[])
 def test_07_guard_allowed_dependency_and_no_native_escape(self):
  s,u=self.receiver();result=self.guarded_child(f'''from urllib.request import Request,urlopen
import subprocess,json
r=urlopen(Request({(u+'/mock')!r},data=b'{{}}'));assert json.load(r)['fixture']
try:subprocess.Popen(['/usr/bin/true'])
except Exception:pass
else:raise AssertionError('native escape')
''',s.server_port);self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(s.received,['/mock'])
 def test_08_zero_query_and_call_budgets(self):
  s,u=self.receiver();ledger=self.ledger(0,0)
  with self.assertRaises(BudgetError):token=ledger.query();post_json(u+'/question',{})
  self.assertEqual(s.received,[]);self.assertEqual(ledger.snapshot()['used'],{'queries':0,'calls':0})
  other=Ledger(self.root/'calls0.json',1,0);token=other.query()
  with self.assertRaises(BudgetError):other.call(token);post_json(u+'/model',{})
  self.assertEqual(s.received,[])
 def test_09_no_unbudgeted_or_finished_calls(self):
  s,u=self.receiver();gate=self.fake_gate(u+'/model',1,2)
  with self.assertRaises(BudgetError):gate.model('unregistered',{})
  token=gate.query();gate.finish(token)
  with self.assertRaises(BudgetError):gate.model(token,{})
  self.assertEqual(s.received,[])
 def test_10_query_limit_independent(self):
  s,u=self.receiver();gate=self.fake_gate(u+'/model',1,5);token=gate.query();post_json(u+'/question',{});gate.model(token,{})
  with self.assertRaises(BudgetError):gate.query();post_json(u+'/question',{})
  self.assertEqual(s.received,['/question','/model']);self.assertEqual(gate.ledger.snapshot()['used'],{'queries':1,'calls':1})
 def test_11_call_limit_independent_nested(self):
  s,u=self.receiver();gate=self.fake_gate(u+'/model',3,1);one=gate.query();two=gate.query();gate.model(one,{'phase':'extract'})
  with self.assertRaises(BudgetError):gate.model(one,{'phase':'repair'})
  with self.assertRaises(BudgetError):gate.model(two,{'phase':'ordinary'})
  self.assertEqual(s.received,['/model']);self.assertEqual(gate.ledger.snapshot()['used'],{'queries':2,'calls':1})
 def test_12_failure_counts_without_retry(self):
  s,u=self.receiver();gate=self.fake_gate(u+'/failure',1,1);token=gate.query()
  with self.assertRaises(HTTPError):gate.model(token,{})
  with self.assertRaises(BudgetError):gate.model(token,{})
  self.assertEqual(s.received,['/failure']);self.assertEqual(gate.ledger.snapshot()['used']['calls'],1)
 def race(self,kind):
  s,u=self.receiver();ledger=self.ledger(1,1);token=ledger.query() if kind=='call' else None
  args=[(str(ledger.path),kind,token,u+('/question' if kind=='query' else '/model'))]*12
  with ProcessPoolExecutor(max_workers=3,mp_context=multiprocessing.get_context('spawn')) as pool:values=list(pool.map(reserve_worker,args))
  self.assertEqual(sum(v is not None for v in values),1)
  # Workers send only after the single successful atomic reservation.
  self.assertEqual(len(s.received),1);self.assertEqual(ledger.snapshot()['used']['queries' if kind=='query' else 'calls'],1)
 def test_13_atomic_last_query(self):self.race('query')
 def test_14_atomic_last_call(self):self.race('call')
 def test_15_restart_and_limits_cannot_reset(self):
  ledger=self.ledger(1,1);token=ledger.query();ledger.call(token);again=Ledger(ledger.path,1,1)
  with self.assertRaises(BudgetError):again.query()
  with self.assertRaises(BudgetError):again.call(token)
  with self.assertRaises(BudgetError):Ledger(ledger.path,2,2)
 def test_16_real_disabled_or_incomplete(self):
  policy=json.loads((R/'policy.json').read_text());self.assertFalse(policy['enabled']);self.assertEqual((policy['query_limit'],policy['call_limit']),(0,0))
  with self.assertRaises(BudgetError):RealGate(policy,self.root/'real.json')
  for p in ({},{'enabled':True,'query_limit':1,'call_limit':1}):
   with self.assertRaises(BudgetError):RealGate(p,self.root/'real.json')
 def test_17_overlay_escape_blocked_at_bootstrap(self):
  s,u=self.receiver();trap,v=self.receiver();app=self.root/'app';app.mkdir();(app/'app.py').write_text(f'from urllib.request import Request,urlopen\nurlopen(Request({(v+"/model")!r},data=b"{{}}"))')
  # This unrewritten nested endpoint stands in for an accidentally restored production endpoint.
  with (self.root/'child.log').open('w') as log:
   proc=launch(app,dict(os.environ),s.server_port,trap.server_port,log);self.assertNotEqual(proc.wait(timeout=5),0)
  self.assertEqual(trap.received,[])

class Recording(unittest.TextTestResult):
 def addSuccess(self,test):super().addSuccess(test);ROWS.append({'case':test._testMethodName,'status':'PASS'})
 def addFailure(self,test,err):super().addFailure(test,err);ROWS.append({'case':test._testMethodName,'status':'FAIL','error':self._exc_info_to_string(err,test)})
 def addError(self,test,err):super().addError(test,err);ROWS.append({'case':test._testMethodName,'status':'ERROR','error':self._exc_info_to_string(err,test)})

if __name__=='__main__':
 if len(sys.argv)>1:raise SystemExit('Offline-only command; no --real option. Real validation needs separate explicit policy and runner flags.')
 OUT=R/('offline-'+time.strftime('%Y%m%d-%H%M%S'));OUT.mkdir();ROWS=[]
 # Actual real-validation allowance remains zero; synthetic ledgers live only in each fixture.
 ledger=Ledger(R/'real-budget.json',0,0)
 result=unittest.TextTestRunner(verbosity=2,resultclass=Recording,failfast=True).run(unittest.defaultTestLoader.loadTestsFromTestCase(SafetyTests))
 (OUT/'results.json').write_text(json.dumps({'mode':'offline_loopback_receivers','expectations_sha256':hashlib.sha256((R/'EXPECTATIONS.json').read_bytes()).hexdigest(),'results':ROWS,'real_queries':0,'real_model_calls':0,'real_baidu':0,'private_calendar':0,'real_limits':ledger.snapshot()['limits']},ensure_ascii=False,indent=2));print(OUT);sys.exit(not result.wasSuccessful())
