#!/usr/bin/env python3
"""Black-box public HTTP regression. No imports of production business modules.
Quick: isolated unchanged source snapshot + HTTP dependencies. --real: local models.
"""
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from concurrent.futures import ThreadPoolExecutor
import argparse,datetime,hashlib,json,os,shutil,socket,subprocess,sys,threading,time,uuid,base64
P=Path(__file__).resolve().parents[2];R=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'acceptance/test-safety'))
from safety import launch

def request(base,path,body=None,timeout=150):
 req=Request(base+path,data=None if body is None else json.dumps(body,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
 try:
  with urlopen(req,timeout=timeout) as r:return json.load(r)
 except HTTPError as e:return {'http_error':e.code,'body':e.read().decode()[:1000]}

class Stream:
 def __init__(self,base,path,body):
  self.base=base;self.path=path;self.body=dict(body,request_id=uuid.uuid4().hex);self.events=[];self.error=None;self.started=time.monotonic();self.done=threading.Event();self.thread=threading.Thread(target=self.run,daemon=True)
 def start(self):self.thread.start();return self
 def run(self):
  try:
   req=Request(self.base+self.path,data=json.dumps(self.body,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
   with urlopen(req,timeout=160) as r:
    for line in r:
     if line.strip():self.events.append((round(time.monotonic()-self.started,3),json.loads(line)))
  except Exception as e:self.error=type(e).__name__+': '+str(e)
  finally:self.ended=time.monotonic();self.done.set()
 def wait(self,timeout=150):
  if not self.done.wait(timeout):raise AssertionError('public stream did not finish within harness timeout')
  if self.error:raise AssertionError(self.error)
  return self
 def session(self):
  for _,e in self.events:
   if e.get('session_id'):return e['session_id']
  return self.body.get('session_id')
 def answer(self):
  for _,e in reversed(self.events):
   if e.get('type')=='done':return e.get('answer','')
  return ''
 def evidence(self):
  return {'seconds':round(getattr(self,'ended',time.monotonic())-self.started,3),'events':[{'at':t,'type':e.get('type'),'state':e.get('state'),'phase':e.get('phase')} for t,e in self.events], 'first_cards_seconds':next((t for t,e in self.events if e.get('events') or e.get('results') or e.get('type') in ('tool','tool_result')),None),'done_seconds':next((t for t,e in self.events if e.get('type')=='done'),None),'error':self.error,'public_schema':[{'type':e.get('type'),'keys':list(e)} for _,e in self.events if e.get('type') not in ('delta','session')],'tool_metadata':[metadata(e) for _,e in self.events if e.get('type')=='tool']}

def wait_for(predicate,timeout=10):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  try:value=predicate()
  except OSError:value=False
  if value:return value
  time.sleep(.05)
 raise AssertionError('observable condition not reached')

class Dependencies(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.0';calls=[];lock=threading.Lock();web_mode='ok'
 def log_message(self,*args):pass
 def do_GET(self):
  if self.path=='/harness':
   self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write((R/'ui.html').read_bytes());return
  if self.path=='/observations':return self.reply({'calls':type(self).calls})
  if self.path.startswith('/app/'):
   return self.proxy()
  self.reply({'models':[{'name':'qwen3.5:9b'},{'name':'qwen3-embedding:0.6b'}]})
 def proxy(self):
  body=self.rfile.read(int(self.headers.get('Content-Length',0))) if self.command=='POST' else None
  path=self.path[len('/app'):] if self.path.startswith('/app/') else self.path
  with type(self).lock:type(self).calls.append({'path':'UI:'+path,'body':json.loads(body) if body else None,'at':time.monotonic()})
  req=Request(type(self).app_url+path,data=body,headers={'Content-Type':'application/json'},method=self.command)
  try:r=urlopen(req,timeout=120)
  except HTTPError as e:r=e
  self.send_response(r.status);self.send_header('Content-Type',r.headers.get('Content-Type','application/json'));self.end_headers()
  try:
   if 'text/html' in r.headers.get('Content-Type',''):
    page=r.read().decode().replace('/api/','/app/api/').replace('/input-guard.js','/app/input-guard.js');self.wfile.write(page.encode())
   else:
    while block:=r.read1(65536):self.wfile.write(block);self.wfile.flush()
  except (BrokenPipeError,ConnectionResetError):pass
 def reply(self,data,status=200):
  self.send_response(status);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(data,ensure_ascii=False).encode())
 def do_POST(self):
  if self.path.startswith('/app/'):return self.proxy()
  data=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))))
  if self.path=='/ui-report':
   type(self).ui_report=data;return self.reply({'ok':True})
  with type(self).lock:type(self).calls.append({'path':self.path,'data':data,'at':time.monotonic()})
  if self.path=='/open':return self.reply({'ok':True})
  if self.path=='/web_search':
   if type(self).web_mode=='fail':return self.reply({'error':{'code':503,'message':'isolated simulated outage'},'request_id':'blackbox-mock-outage'},503)
   time.sleep(1.0 if type(self).web_mode=='slow' else .25)
   return self.reply({'request_id':'blackbox-mock-only','references':[{'id':1,'title':'公开测试资料','content':'公开摘要：测试花园于1988年开放。','url':'https://example.org/garden'}]})
  if self.path=='/api/embed':
   values=data.get('input',[]);values=[values] if isinstance(values,str) else values
   return self.reply({'model':'qwen3-embedding:0.6b','embeddings':[[1.0,0.0,0.0] for _ in values]})
  if self.path=='/api/chat':
   messages=data.get('messages',[]);question=next((m.get('content','') for m in reversed(messages) if m.get('role')=='user'),'');history='\n'.join(m.get('content','') for m in messages)
   if '只输出JSON' in history:
    answer=json.dumps({'kind':'clock' if '几点' in question else 'calendar' if '安排' in question else 'none','day_offset':1 if '明天' in question else 0,'period':'morning' if '上午' in question else 'day','operation':'read'})
   elif '安排' in question:answer='日历中没有已有安排。'
   elif '测试昵称是' in question:answer='已收到你的测试昵称。'
   elif '叫什么' in question:answer='你的昵称是黑盒小岚。' if any('测试昵称是黑盒小岚' in m.get('content','') for m in messages[:-1]) else '我不知道你的昵称。'
   elif '例子' in question:answer='结合刚才RAG的检索与生成，用图书资料回答读者问题。' if 'RAG' in history else '请提供上文。'
   elif '几点' in question:answer=next((m['content'] for m in messages if '"now"' in m.get('content','')),'系统时间资料')
   elif '公开测试' in history:answer='据百度搜索摘要，测试花园于1988年开放。'
   else:answer='RAG结合检索与生成。这是隔离HTTP模型模拟回答。'
   if not data.get('stream'):return self.reply({'model':'qwen3.5:9b','message':{'role':'assistant','content':answer},'done':True})
   self.send_response(200);self.send_header('Content-Type','application/x-ndjson');self.end_headers()
   try:
    for piece in [answer[:len(answer)//2],answer[len(answer)//2:]]:
     if '慢速' in question:time.sleep(1.2)
     else:time.sleep(.12)
     self.wfile.write((json.dumps({'model':'qwen3.5:9b','message':{'role':'assistant','content':piece},'done':False},ensure_ascii=False)+'\n').encode());self.wfile.flush()
    self.wfile.write((json.dumps({'model':'qwen3.5:9b','message':{'role':'assistant','content':''},'done':True})+'\n').encode())
   except (BrokenPipeError,ConnectionResetError):pass
   return
  self.reply({'error':'unsupported mock dependency path'},404)

class CalendarDependency:
 """Simulates only external native bridge replies inside the isolated deployment."""
 def __init__(self,app):
  self.root=app/'.runtime/calendar-ipc';self.root.mkdir(parents=True,exist_ok=True);self.status='ok';self.calls=[];self.stop=threading.Event()
  self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
 def run(self):
  while not self.stop.wait(.03):
   ready=self.root/'ready.json';tmp=ready.with_suffix('.tmp');tmp.write_text(json.dumps({'bundle':'local.filechat.asuka.launcher','pid':os.getpid(),'version':1}));tmp.replace(ready)
   for req in self.root.glob('*.request.json'):
    try:
     data=json.loads(req.read_text());output=self.root/(data['id']+'.response.json')
     if output.exists():continue
     self.calls.append(data);result={'id':data['id'],'status':self.status,'events':[],'partial':False,'timezone':str(Path('/etc/localtime').resolve()).split('/zoneinfo/')[-1]}
     tmp=output.with_suffix('.tmp');tmp.write_text(json.dumps(result));tmp.replace(output)
    except (OSError,ValueError):pass

class Suite:
 def __init__(self,base,out,real=False,fail_fast=False):self.base=base;self.out=out;self.rows=[];self.real=real;self.fail_fast=fail_fast
 def test(self,name,fn):
  if getattr(self,'selected',None) and name not in self.selected:return
  start=time.monotonic()
  try:detail=fn() or {};row={'case':name,'status':'PASS','seconds':round(time.monotonic()-start,3),'evidence':detail}
  except Exception as e:row={'case':name,'status':'FAIL','seconds':round(time.monotonic()-start,3),'error':str(e)}
  self.rows.append(row);self.save();print(name,row['status'],row.get('error',''),flush=True)
  if self.fail_fast and row['status']!='PASS':raise RuntimeError('Blackbox fail-stop; first failure preserved in results')
 def save(self):self.out.write_text(json.dumps({'mode':'real_local_models' if self.real else 'isolated_http_dependencies','expectations_sha256':hashlib.sha256((R/'EXPECTATIONS.json').read_bytes()).hexdigest(),'results':self.rows},ensure_ascii=False,indent=2))
 def chat(self,q,session=None):return Stream(self.base,'/api/bubble/chat',{'question':q,'session_id':session,'web_enabled':False}).start()
 def files(self,q,session=None,scope='workspace'):return Stream(self.base,'/api/chat',{'question':q,'session_id':session,'scope':scope,'stream':True}).start()
 def run(self):
  def health():
   d=request(self.base,'/api/health');assert d.get('service')=='local-file-chat';return {'service':d['service']}
  self.test('health',health)
  def memory():
   a=self.chat('我的测试昵称是黑盒小岚，请只回复确认。').wait();b=self.chat('我叫什么？',a.session()).wait();c=self.chat('我叫什么？').wait()
   assert '黑盒小岚' in b.answer(),b.answer();assert '黑盒小岚' not in c.answer(),'nickname leaked to another session'
   d=self.chat('解释一下RAG',a.session()).wait();e=self.chat('结合刚才的解释举个例子',a.session()).wait();assert e.answer();
   reset=request(self.base,'/api/bubble/reset',{'session_id':a.session()});f=self.chat('我叫什么？',reset['session_id']).wait();assert '黑盒小岚' not in f.answer(),'reset retained nickname'
   return {'recall':b.evidence(),'followup':e.evidence(),'different_session_no_leak':True,'reset_no_recall':True}
  self.test('chat_memory_isolation_followup_reset',memory)
  def concurrent(cancel=False,reset=False):
   a=self.chat('慢速详细解释RAG，列出步骤。' if not self.real else '请详细解释RAG的检索与生成流程，举一个图书馆例子。')
   sid=wait_for(a.session,30);b=self.files('找一下野餐清单' if not self.real else '找 4418',scope='workspace' if not self.real else 'all')
   snapshots=[]
   for _ in range(6):
    snap=request(self.base,'/api/tasks/status');snapshots.append(snap)
    if snap.get('active_count',0)>=2:break
    time.sleep(.08)
   if cancel or reset:
    path='/api/bubble/reset' if reset else '/api/bubble/cancel';request(self.base,path,{'session_id':sid,'request_id':a.body['request_id']})
   b.wait()
   remaining=request(self.base,'/api/tasks/status');snapshots.append(remaining)
   a.wait()
   # A fast filename search may finish between status polls. Public stream
   # intervals still prove overlap; also require the slower task remain active.
   assert b.events and a.events and b.started < a.ended and a.started < b.ended,'public request intervals did not overlap'
   if not (cancel or reset):
    assert any(t.get('request_id')==a.body['request_id'] for t in remaining.get('tasks',[])), 'file completion cleared active chat task'
   else:
    assert any(t.get('request_id')==a.body['request_id'] for snap in snapshots[:-1] for t in snap.get('tasks',[])), 'chat never observed active before cancel/reset'
   assert b.answer(),'file search did not produce terminal answer'
   if cancel or reset:assert not a.answer(),'cancelled task still committed full answer'
   else:assert a.answer(),'chat lost answer'
   assert '黑盒小岚' not in b.answer(),'chat history leaked into files'
   return {'chat':a.evidence(),'files':b.evidence(),'aggregate_snapshots':snapshots}
  self.test('concurrent_chat_filename',lambda:concurrent())
  self.test('cancel_chat_preserves_file_task',lambda:concurrent(cancel=True))
  if not self.real:self.test('reset_chat_preserves_file_task',lambda:concurrent(reset=True))
  def constraints():
   data=request(self.base,'/api/files/search',{'scope':'workspace','keyword':'野餐清单','extensions':['pdf']});events=data.get('events',[]);assert events,'no public file event'
   rows=[r for e in events for r in e.get('results',[])];assert rows,'known fixture was lost';assert all(Path(r['path']).suffix.lower()=='.pdf' for r in rows),'hard type leaked'
   assert all('/fixtures/' in r['path'] for r in rows),'scope leaked'
   e=events[-1];counts=e.get('counts',{});assert counts.get('displayed')==len(rows),'display count inconsistent';assert counts.get('verified',-1)>=len(rows),'verified count inconsistent'
   sid=data['session_id'];filtered=request(self.base,'/api/files/search',{'session_id':sid,'scope':'workspace','keyword':'野餐清单','sort_by':'modified_desc','extensions':['pdf'],'reuse_current':True});assert filtered.get('session_id')==sid
   ordered=[r['path'] for e in filtered.get('events',[]) for r in e.get('results',[])];assert ordered==sorted(ordered,key=lambda p:Path(p).stat().st_mtime,reverse=True),'modified sort order wrong'
   other=self.chat('慢速解释RAG');wait_for(other.session);follow=suite_follow(self,sid);other.wait();assert other.answer(),'independent chat lost during file selection';assert follow['second_file']==ordered[1],'second-file selection changed identity'
   no=request(self.base,'/api/files/search',{'scope':'workspace','keyword':'blackbox_no_such_6dd9','extensions':['pdf']});assert not [r for e in no.get('events',[]) for r in e.get('results',[])],'no-match fabricated candidates'
   identifier=request(self.base,'/api/files/search',{'scope':'workspace','keyword':'ZX8427','extensions':['pdf']});rs=[r for e in identifier.get('events',[]) for r in e.get('results',[])];assert rs and all('ZX8427' in r['path'] for r in rs),'identifier dropped or broadened'
   return {'paths':[Path(r['path']).name for r in rows],'counts':counts,'identity_before':[r['path'] for r in rows],'identity_after':[r['path'] for e in filtered.get('events',[]) for r in e.get('results',[])]}
  if not self.real:self.test('type_directory_identifier_counts_snapshot',constraints)
  def config():
   c=request(self.base,'/api/bubble/config');u=c.get('usage',{});assert u.get('limit')==30;assert u.get('test_limit_reached') is True;return {'web_configured':c.get('web_configured'),'usage':u}
  self.test('rolling30_and_exhausted_developer_lock_public_config',config)

def suite_follow(suite,sid):
 # Natural-language operations through the same public file session.
 pdf=suite.files('只看PDF',sid).wait();ordered=suite.files('按最近修改排序',sid).wait()
 rows=[r for _,e in ordered.events if e.get('type')=='tool' for r in e.get('event',{}).get('results',[])]
 assert rows and all(Path(r['path']).suffix=='.pdf' for r in rows),'multi-turn type lost'
 expected=sorted([r['path'] for r in rows],key=lambda p:Path(p).stat().st_mtime,reverse=True);assert [r['path'] for r in rows]==expected,'multi-turn modified sort wrong'
 opened=suite.files('打开第二个',sid).wait()
 actions=[e.get('event',{}) for _,e in opened.events if e.get('type')=='tool']
 target=next((e.get('file',{}).get('path') for e in actions if e.get('tool')=='open_file'),None)
 assert target==expected[1],f'ordinal selection mismatch: public tool metadata {[(e.get("tool"),e.get("path")) for e in actions]}'
 return {'second_file':target,'multi_turn_sort':True}

def metadata(e):
 result={}
 for key,value in e.items():
  if key in ('retrieval','counts','tool','scope','total','partial','truncated','path'):result[key]=value
  elif key in ('event','data','result','file') and isinstance(value,dict):result[key]=metadata(value)
  elif key=='results' and isinstance(value,list):result['names']=[Path(r.get('path','')).name for r in value if isinstance(r,dict)]
 return result

def calendar_cases(suite,bridge):
 base=suite.base;observed=[];before=len([x for x in Dependencies.calls if x['path']=='/web_search'])
 for status,word in [('denied','拒绝'),('ok','没有'),('read_error','失败')]:
  bridge.status=status;a=suite.chat('明天上午有什么安排？').wait();assert word in a.answer(),f'calendar {status} feedback missing: '+str([(e.get('type'),e.get('error'),e.get('answer')) for _,e in a.events])
  observed.append({'simulated_native_status':status,'answer_completed':bool(a.answer())})
 assert len(bridge.calls)==3 and all(r['day_offset']==1 and r['period']=='morning' for r in bridge.calls),'native range intent incorrect'
 assert len([x for x in Dependencies.calls if x['path']=='/web_search'])==before,'calendar leaked to external web'
 return {'scenarios':observed,'public_ipc_requests':3,'range_intent_verified':True,'actual_EventKit_ranges':'prior production range tests and user confirmation; not simulated as real authorization'}

def extra_files(suite):
 base=suite.base
 def paths(d):return [r['path'] for e in d.get('events',[]) for r in e.get('results',[])]
 media={}
 for query,extensions in [('动漫图片',['png','jpg','jpeg']),('动漫视频',['mp4','mkv','mov'])]:
  natural=suite.files('找一下'+query.replace('图片','有关的图片').replace('视频','有关的视频')).wait();natural_rows=[r['path'] for _,e in natural.events if e.get('type')=='tool' for r in e.get('event',{}).get('results',[])];assert natural_rows and all(Path(p).suffix[1:] in extensions for p in natural_rows),'natural-language media hard-type failed';d=request(base,'/api/files/search',{'scope':'workspace','keyword':'动漫','extensions':extensions});rs=paths(d);assert rs,'known media name lost';assert all(Path(p).suffix[1:] in extensions for p in rs),'media hard-type failed';assert all('confirmation' not in p and not p.endswith('.swift') for p in rs),'unrelated topic/type leaked';media[query]=[Path(p).name for p in rs]
 d=request(base,'/api/files/search',{'scope':'desktop','keyword':'野餐清单'});rs=paths(d);assert rs and all('/fixtures/desktop/' in p for p in rs),'explicit directory boundary failed'
 a=suite.chat('慢速解释RAG');wait_for(a.session)
 b=suite.files('慢速详细解释文件里的RAG步骤');sid=wait_for(b.session)
 request(base,'/api/chat/clear',{'session_id':sid,'request_id':b.body['request_id']})
 b.wait();a.wait();assert a.answer(),'file clear cancelled chat';assert not b.answer(),'cleared file task committed answer'
 return {'media_metadata_only':media,'directory_boundary':True,'file_clear_did_not_cancel_chat':True}

def web_budget(suite):
 base=suite.base
 def count():return len([c for c in Dependencies.calls if c['path']=='/web_search'])
 def web(q):return Stream(base,'/api/bubble/chat',{'question':q,'web_enabled':True}).start()
 initial=request(base,'/api/bubble/config')['usage'];assert count()==0,'unchecked local requests leaked external searches';assert initial['used']==1 and initial['test_used']==7
 # One real application request to a loopback simulated external endpoint.
 before=count();a=web('搜索公开测试花园开放年份').wait();assert count()==before+1,'not exactly one outbound transport'
 assert '1988' in a.answer(),'summary not based on simulated snippet'
 assert any('example.org/garden' in json.dumps(e) for _,e in a.events),'source URL missing'
 Dependencies.web_mode='fail';before=count();failure=web('搜索公开测试故障').wait();assert count()==before+1,'failure retried';assert not failure.answer(),'failure invented an answer'
 Dependencies.web_mode='slow';before=count();remaining=suite.chat('慢速解释RAG');wait_for(remaining.session);cancelled=web('搜索公开测试取消')
 wait_for(lambda:count()==before+1);wifi=request(base,'/api/tasks/status');assert wifi['waiting']=='wifi';sid=wait_for(cancelled.session);request(base,'/api/bubble/cancel',{'session_id':sid,'request_id':cancelled.body['request_id']});cancelled.wait();assert not cancelled.answer();dots=request(base,'/api/tasks/status');assert dots['waiting']=='dots' and dots['active_count']>=1;remaining.wait();assert remaining.answer()
 Dependencies.web_mode='ok'
 used=request(base,'/api/bubble/config')['usage']['used'];assert used==4,'failure/cancel not counted'
 # Fill only isolated simulator budget via actual public requests, no ledger seeding.
 for i in range(25):
  result=web('搜索公开测试预算'+str(i)).wait();assert result.answer(),'mock allowance unexpectedly blocked'
 assert request(base,'/api/bubble/config')['usage']['used']==29
 before=count();tasks=[web('搜索公开测试最后额度'+str(i)) for i in range(2)]
 for task in tasks:task.wait()
 assert count()==before+1,'last-slot reservation not atomic'
 assert sum(bool(t.answer()) for t in tasks)==1,'limit should allow only one answer'
 final=request(base,'/api/bubble/config')['usage'];assert final['used']==30 and final['test_used']==7
 before=count();blocked=web('搜索公开测试已达上限').wait();assert count()==before and not blocked.answer(),'blocked request dispatched'
 return {'simulated_external_requests':count(),'real_baidu_requests':0,'initial':initial,'final':final,'failed_request_counted':True,'cancelled_after_send_counted':True,'automatic_retries':0,'developer_lock_preserved':True,'mock_wifi_dots_transition':{'wifi':wifi,'remaining_dots':dots},'cache_hit':'No query cache exposed by this search path; not claimed as covered'}

def port():
 with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def prepare(runroot,appport,depport):
 app=runroot/'app';app.mkdir(parents=True);fixtures=app/'fixtures';fixtures.mkdir()
 for f in P.glob('*.py'):shutil.copy2(f,app/f.name)
 for f in ['bubble.html','input-guard.js']:shutil.copy2(P/f,app/f)
 # Isolated deployment configuration only; no algorithm or internal state mutation.
 with (app/'settings.py').open('a') as f:f.write(f'\n# Black-box isolated deployment configuration\nPORT={appport}\nWORKSPACE=PROJECT/"fixtures"\nSEARCH_ROOTS={{"workspace":WORKSPACE,"desktop":WORKSPACE/"desktop"}}\nSEMANTIC_DB=PROJECT/".semantic_index.sqlite3"\n')
 for name,text in {'野餐清单.txt':'野餐需要水、苹果和餐垫。','ZX8427野餐清单.txt':'项目ZX8427户外用品。','ZX8428野餐清单.txt':'另一个项目编号。','无关账单.txt':'五月电费账单。'}.items():(fixtures/name).write_text(text)
 from reportlab.pdfgen import canvas
 for name in ('野餐清单.pdf','ZX8427野餐清单.pdf','ZX8428野餐清单.pdf'):
  c=canvas.Canvas(str(fixtures/name));c.drawString(72,720,'Picnic checklist: water, apples, a mat.');c.save()
  stamp=1700000010+10*('野餐清单.pdf','ZX8427野餐清单.pdf','ZX8428野餐清单.pdf').index(name);os.utime(fixtures/name,(stamp,stamp))
 (fixtures/'动漫海报.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j0XcAAAAASUVORK5CYII='))
 (fixtures/'anime_clip.mp4').write_bytes(b'Black-box metadata-only video fixture; not a playable video')
 (fixtures/'confirmation.png').write_bytes(b'metadata-only unrelated image fixture')
 (fixtures/'animation.swift').write_text('// unrelated source code')
 (fixtures/'desktop').mkdir();(fixtures/'desktop'/'野餐清单.txt').write_text('桌面目录野餐清单。')
 # Route just the external web transport to a loopback HTTP simulator.
 f=app/'bubble_web.py';s=f.read_text();endpoint='https://qianfan.baidubce.com/v2/ai_search/web_search';assert endpoint in s;s=s.replace(endpoint,f'http://127.0.0.1:{depport}/web_search');f.write_text(s)
 # Preserve real history in an isolated copy; never alter the formal ledger.
 (app/'.bubble-web-usage.json').write_text(json.dumps({'attempts':[time.time()],'test_attempts':7}))
 for localfile in app.glob('*.py'):
  code=localfile.read_text();code=code.replace("host='http://127.0.0.1:11434'",f"host='http://127.0.0.1:{depport}'");localfile.write_text(code)
 env={k:v for k,v in os.environ.items() if not k.startswith(('BAIDU_','DEEPSEEK_','BUBBLE_WEB_','OLLAMA_'))}
 env.update(OLLAMA_HOST=f'http://127.0.0.1:{depport}',BUBBLE_WEB_ENABLED='true',BAIDU_FREE_RESOURCES_CONFIRMED='true',BAIDU_API_KEY='blackbox-nonsecret-placeholder',BAIDU_LOCAL_DAILY_LIMIT='30',BAIDU_TEST_MODE='true',BAIDU_TEST_LIMIT='7',MODEL_PROVIDER='ollama',DEEPSEEK_ENABLED='false')
 return app,env

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--real',action='store_true');parser.add_argument('--serve-ui',action='store_true');parser.add_argument('--ui',action='store_true');parser.add_argument('--only',nargs='+');args=parser.parse_args()
 if args.real:raise SystemExit('Legacy --real is disabled: use the explicit policy/query/call gated runner; current real budgets are zero')
 stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');out=R/('real-'+stamp if args.real else 'baseline-'+stamp);out.mkdir()
 freeze=json.loads((R/'freeze.json').read_text());assert hashlib.sha256((R/'EXPECTATIONS.json').read_bytes()).hexdigest()==freeze['expectations_sha256'],'Frozen expectations changed'
 if args.real:
  real_suite(out);sys.exit(1 if any(r['status']=='FAIL' for r in json.loads((out/'results.json').read_text())['results']) else 0)
 appport,depport=port(),port();server=ThreadingHTTPServer(('127.0.0.1',depport),Dependencies);Dependencies.app_url=f'http://127.0.0.1:{appport}';threading.Thread(target=server.serve_forever,daemon=True).start()
 app,env=prepare(out,appport,depport);log=(out/'server.log').open('w');proc=launch(app,env,depport,appport,log,mock_open=True)
 try:
  wait_for(lambda:request(Dependencies.app_url,'/api/health') if proc.poll() is None else False,10)
  calendar=CalendarDependency(app)
  wait_for(lambda:(app/'.runtime/calendar-ipc/ready.json').exists())
  suite=Suite(Dependencies.app_url,out/'results.json',fail_fast=True);suite.selected=args.only;suite.run()
  suite.test('external_calendar_ipc_denied_empty_failure_range',lambda:calendar_cases(suite,calendar))
  suite.test('file_media_directory_and_cancel_file_not_chat',lambda:extra_files(suite))
  if args.serve_ui or args.ui:
   (R/'current-ui.json').write_text(json.dumps({'url':f'http://127.0.0.1:{depport}/harness','app_url':Dependencies.app_url,'run':str(out)}));print('UI harness:',f'http://127.0.0.1:{depport}/harness',flush=True)
   if args.ui:subprocess.run(['/usr/bin/open','-g',f'http://127.0.0.1:{depport}/harness'],check=True)
   wait_for(lambda:hasattr(Dependencies,'ui_report'),90)
   (out/'ui-results.json').write_text(json.dumps(Dependencies.ui_report,ensure_ascii=False,indent=2))
  suite.test('simulated_web_daily_eligibility_failure_cancel_and_atomic30',lambda:web_budget(suite))
  used_before=request(Dependencies.app_url,'/api/bubble/config')['usage']
  proc.terminate();proc.wait(timeout=8);proc=launch(app,env,depport,appport,log,mock_open=True)
  wait_for(lambda:request(Dependencies.app_url,'/api/health'),10)
  def persistent():
   after=request(Dependencies.app_url,'/api/bubble/config')['usage'];assert after['used']==used_before['used'] and after['test_used']==7;return {'before':used_before,'after':after}
  suite.test('rolling_budget_survives_process_restart',persistent)
 finally:
  if 'calendar' in locals():calendar.stop.set()
  proc.terminate();proc.wait(timeout=8);server.shutdown();log.close();(out/'dependency-calls.json').write_text(json.dumps(Dependencies.calls,ensure_ascii=False,indent=2));print('Evidence:',out,flush=True)
 if any(r['status']=='FAIL' for r in suite.rows) or any(r['status']=='FAIL' for r in getattr(Dependencies,'ui_report',{}).get('results',[])):sys.exit(1)
if __name__=='__main__':main()
