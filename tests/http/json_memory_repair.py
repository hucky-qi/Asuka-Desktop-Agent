"""Strict wire/response/source behavior over isolated public HTTP; no real services."""
from pathlib import Path
import sys,json,threading,copy
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parent))
import chat_sources as c
import chat_source_repair as old
f=c.f
BROKEN_CLOSURE='{"route":"chat","file":null,"reply":"桦木配琥珀色，这组合挺会挑的——木头的清冷骨架配上暖融融的琥珀底，像深秋午后阳光斜照进木屋的那种感觉。你是想把标签用在什么场合？贴纸、界面配色，还是某个要交给别人的东西上？"}, "preference":null, "expression":null, "journal":null, "behavior":null, "calendar_action":null}'
class Model(c.Model):
 mode='valid';calls=[];ordinary=[];proposals={}
 def do_POST(self):
  raw=self.rfile.read(int(self.headers['Content-Length']));d=json.loads(raw);type(self).calls.append(d)
  batch=any('MEMORY_DISTILLATION_V1' in m.get('content','') for m in d['messages'])
  answer={'route':'chat','file':None,'reply':'合成回答含有 "引号"、反斜杠 \\ 和换行\n结束。'};finish='stop'
  if batch:
   answer={'candidates':[]}
   for row in json.loads(d['messages'][-1]['content'])['records']:
    for q,p in self.proposals.items():
     if q in row['text']:answer['candidates'].append(dict(copy.deepcopy(p),source_id='not-code-owned' if self.mode=='bad_source' else row['id']))
  contents={'unescaped':old.BROKEN,'closure':BROKEN_CLOSURE,'empty':'','trailing':json.dumps(answer)+' done','missing':json.dumps({'route':'chat','file':None}),'type':json.dumps({'route':'chat','file':None,'reply':3}),'nested_missing':json.dumps(dict(answer,preference={'action':'save'})),'enum':json.dumps(dict(answer,route='invented')),'unknown':json.dumps(dict(answer,raw_id='invented')),'duplicate':'{"route":"calendar","route":"chat","file":null,"reply":"ok"}','truncated':'{"route":"chat",','long':json.dumps(dict(answer,reply='Public synthetic paragraph. '*100))}
  content=contents.get(self.mode,json.dumps(answer,ensure_ascii=False))
  if self.mode in ('length','aborted','content_filter','insufficient_system_resource'):finish=self.mode
  obj={'id':'isolated-json','model':'deepseek-flash','choices':[{'message':{'role':'assistant','content':content},'finish_reason':finish}],'usage':{'prompt_tokens':100,'completion_tokens':60}}
  if self.mode=='bad_content':obj['choices'][0]['message']['content']={'not':'text'}
  if self.mode in ('transfer','envelope'):
   body=json.dumps(obj).encode() if self.mode=='transfer' else b'{';self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)+(100 if self.mode=='transfer' else 0)));self.end_headers();self.wfile.write(body);self.wfile.flush();self.close_connection=True;return
  self.reply(obj)
def main(out):
 out.mkdir(parents=True);ap,dp=f.bb.port(),f.bb.port();app,env=f.prepare(out,ap,dp)
 with (app/'backend/memory/chat_sources.py').open('a') as h:h.write("\ndef now():return dt.datetime(2033,1,1,10,tzinfo=dt.timezone.utc)\n")
 srv=ThreadingHTTPServer(('127.0.0.1',dp),Model);threading.Thread(target=srv.serve_forever,daemon=True).start();log=(out/'server.log').open('w');proc=f.bb.launch(app,env,dp,ap,log);base=f'http://127.0.0.1:{ap}';results=[]
 def api(action,**kw):
  d=f.bb.request(base,'/api/bubble/memory',dict(session_id=sid,action=action,**kw));return json.loads(d['body']) if d.get('http_error') else d
 def op(action,**kw):return api(action,revision=api('list')['revision'],**kw)
 def chat(q='请简短解释合成标识的作用。'):
  s=f.bb.Stream(base,'/api/bubble/chat',{'session_id':sid,'question':q,'web_enabled':False}).start().wait();return next(v for _,v in reversed(s.events) if v['type'] in ('done','error'))
 def run():
  prior=(api('list')['distillation']['run'] or {}).get('id');api('distillation_run');return f.bb.wait_for(lambda:(v if (v:=api('list'))['distillation'].get('run') and v['distillation']['run']['id']!=prior and v['distillation']['run']['state'] in ('completed','failed','unknown','no_new') else None))
 def check(name,fn):
  try:fn();r={'case':name,'pass':True}
  except Exception as e:r={'case':name,'pass':False,'error':str(e)[:1000]}
  results.append(r);(out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(name,'PASS' if r['pass'] else 'FAIL',flush=True)
 try:
  f.bb.wait_for(lambda:f.bb.request(base,'/api/health'));sid=f.bb.request(base,'/api/bubble/session',{})['session_id'];op('chat_source_config',save=True,cloud=True,days=30)
  def reject(mode):
   Model.mode=mode;before=copy.deepcopy(api('list'));n=len(Model.calls);d=chat();assert d['type']=='error',d;assert d['task']['state']=='failed' and len(Model.calls)==n+1;after=api('list');assert before['items']==after['items'] and before['chat_sources']['turns']==after['chat_sources']['turns'];Model.mode='valid'
  for mode in ['unescaped','closure','empty','trailing','missing','type','nested_missing','enum','unknown','duplicate','truncated','length','aborted','content_filter','insufficient_system_resource','transfer','envelope','bad_content']:check('reject_'+mode,lambda m=mode:reject(m))
  def valid():
   for mode in ['valid','long']:
    Model.mode=mode;before=len(api('list')['chat_sources']['turns']);d=chat('只讨论合成标识：'+mode);assert d['type']=='done' and d['assistant_result']['status']=='completed';assert len(api('list')['chat_sources']['turns'])==before+1
   Model.mode='valid';(out/'outbound.json').write_text(json.dumps(Model.calls[-1],ensure_ascii=False,indent=2))
  check('valid_escaped_and_long_response_preserve_exact_content',valid)
  def example():
   request=Model.calls[-1];assert request['response_format']=={'type':'json_object'} and request['max_tokens']==768 and request['thinking']=={'type':'disabled'};system=request['messages'][0]['content'];assert 'EXAMPLE JSON OUTPUT:' in system;assert 'tool_calls' not in request
  check('json_mode_contract_has_schema_consistent_example',example)
  def semantic():
   nonlocal sid
   sid=f.bb.request(base,'/api/bubble/session',{})['session_id'];q='我的合成定位环是紫铜色。';chat(q);Model.proposals={q:c.old.proposal(q,subject='合成定位环')};Model.mode='bad_source';op('distillation_config',enabled=True,days=7,cloud=True,confirm=True,sources=['chats']);d=run();assert d['distillation']['run']['state']=='failed' and d['distillation']['candidate_count']==0 and d['distillation']['pending_sources']>0;assert not d['items'];Model.mode='valid'
  check('valid_json_unknown_provenance_not_candidate_or_progress',semantic)
  def input_budget():
   nonlocal sid
   sid=f.bb.request(base,'/api/bubble/session',{})['session_id'];before=copy.deepcopy(api('list'));n=len(Model.calls);d=f.bb.request(base,'/api/bubble/chat',{'session_id':sid,'question':'隔离长输入'*300,'web_enabled':False});assert d.get('http_error')==400 and len(Model.calls)==n;after=api('list');assert before['chat_sources']['turns']==after['chat_sources']['turns']
  check('message_or_input_protection_before_outbound_no_source',input_budget)
 finally:proc.terminate();proc.wait(8);srv.shutdown();log.close()
 return all(r['pass'] for r in results)
if __name__=='__main__':sys.exit(0 if main(Path(sys.argv[1]).resolve()) else 1)
