"""Public HTTP replay of synthetic historical failures and batch recovery; no real outbound."""
from pathlib import Path
import sys,json,threading,copy
from http.server import ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parent))
import chat_sources as c
f=c.f
LEGACY_JOURNAL={'route':'journal','file':None,'reply':'','journal':{'action':'save','content':'Finished the sample diagram.','date_from':None,'date_to':None,'new_date':None,'topic':None,'target':None,'resume':False,'use_selected':False,'summarize':False,'self_report':True,'evidence':'I finished the sample diagram today.'}}
BROKEN='{"route":"chat","file":null,"reply":""éllipse" is the precise term — an oval defined by two focal points. If you want plain wording instead: "a stretched circle, wider than it is tall." For a badge spec, "elliptical (oval)" covers both precision and readability."}'
class Model(c.Model):
 mode='ok';calls=[];ordinary=[];reply_override=None;proposals={};hold=False
 def do_POST(self):
  raw=self.rfile.read(int(self.headers['Content-Length']));d=json.loads(raw);is_batch=any('MEMORY_DISTILLATION_V1' in m.get('content','') for m in d['messages'])
  type(self).calls.append(d)
  if is_batch:
   ans={'candidates':[]}
   for row in json.loads(d['messages'][-1]['content'])['records']:
    for q,proposal in self.proposals.items():
     if q in row['text']:ans['candidates'].append(dict(copy.deepcopy(proposal),source_id=row['id']))
   content=json.dumps(ans,ensure_ascii=False)
   if self.mode=='batch_broken':content='{"candidates":['
  else:
   type(self).ordinary.append(d);content=self.reply_override or json.dumps({'route':'chat','file':None,'reply':'Synthetic answer with supported facts only.'})
  self.reply({'id':'isolated-replay','model':'deepseek-flash','choices':[{'message':{'role':'assistant','content':content},'finish_reason':'length' if self.mode=='length' else 'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':50}})
def main(out):
 out.mkdir(parents=True);ap,dp=f.bb.port(),f.bb.port();app,env=f.prepare(out,ap,dp)
 with (app/'backend/memory/chat_sources.py').open('a') as h:h.write("\ndef now():return dt.datetime(2033,1,1,10,tzinfo=dt.timezone.utc)\n")
 srv=ThreadingHTTPServer(('127.0.0.1',dp),Model);threading.Thread(target=srv.serve_forever,daemon=True).start();log=(out/'server.log').open('w');proc=f.bb.launch(app,env,dp,ap,log);base=f'http://127.0.0.1:{ap}';results=[]
 def api(action,**kw):
  d=f.bb.request(base,'/api/bubble/memory',dict(session_id=sid,action=action,**kw));return json.loads(d['body']) if d.get('http_error') else d
 def op(action,**kw):return api(action,revision=api('list')['revision'],**kw)
 def chat(q):
  s=f.bb.Stream(base,'/api/bubble/chat',{'session_id':sid,'question':q,'web_enabled':False}).start().wait();return next(v for _,v in reversed(s.events) if v['type'] in ('done','error'))
 def run():
  prior=(api('list')['distillation']['run'] or {}).get('id');api('distillation_run');return f.bb.wait_for(lambda:(v if (v:=api('list'))['distillation'].get('run') and v['distillation']['run']['id']!=prior and v['distillation']['run']['state'] in ('completed','failed','unknown','no_new') else None))
 def check(name,fn):
  try:fn();r={'case':name,'pass':True}
  except Exception as e:r={'case':name,'pass':False,'error':str(e)[:900]}
  results.append(r);(out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(name,'PASS' if r['pass'] else 'FAIL',flush=True)
 try:
  f.bb.wait_for(lambda:f.bb.request(base,'/api/health'));sid=f.bb.request(base,'/api/bubble/session',{})['session_id'];op('chat_source_config',save=True,cloud=True,days=30)
  def replay_journal():
   Model.reply_override=json.dumps(LEGACY_JOURNAL);before=api('list');d=chat('I finished the sample diagram today. A colleague prefers gold covers; that is not my preference.');assert d.get('assistant_result',{}).get('status')=='failed',d;after=api('list');assert after['items']==before['items'] and after['chat_sources']['turns']==before['chat_sources']['turns'];assert len(Model.calls)==1
  check('original_journal_route_field_rejection_no_source_no_write',replay_journal)
  def replay_json():
   Model.reply_override=BROKEN;n=len(Model.calls);d=chat('My synthetic badge is oval. Can you suggest a precise word for that shape?');assert d['type']=='error';assert len(Model.calls)==n+1 and api('list')['chat_sources']['turns']==[]
  check('original_unescaped_json_rejected_once_no_source_or_memory',replay_json)
  def outputs():
   Model.reply_override=None
   for q in ['我已完成合成零件图。','I have completed the synthetic appendix.','更新一下：the sample poster is ready.']:
    assert chat(q)['type']=='done'
   assert len(api('list')['chat_sources']['turns'])==3
  check('multilingual_plain_contract_source_capture',outputs)
  def prompt_contract():
   sysmsg=' '.join(m['content'] for m in Model.ordinary[-1]['messages'] if m['role']=='system');assert 'Reporting an experience is not requesting storage' in sysmsg;assert 'escaping' in sysmsg.lower() or isinstance(json.loads(Model.ordinary[-1]['messages'][0]['content'].split('EXAMPLE JSON OUTPUT:',1)[1])['reply'],str)
  check('same_language_neutral_intent_and_json_wire_contract_sent',prompt_contract)
  def partial_batch():
   nonlocal sid
   Model.proposals={'我已完成合成零件图。':c.old.proposal('我已完成合成零件图。','progress',subject='合成零件图')};op('distillation_config',enabled=True,days=7,cloud=True,confirm=True,sources=['chats']);a=run();assert a['distillation']['run']['state']=='completed';candidate=next(x for x in a['distillation']['candidates'] if x['text']=='我已完成合成零件图。');before=copy.deepcopy(a['distillation']['candidates']);sid=f.bb.request(base,'/api/bubble/session',{})['session_id'];assert chat('我的合成旗帜是青色。')['type']=='done';Model.mode='batch_broken';bad=run();assert bad['distillation']['run']['state']=='failed';assert bad['distillation']['candidates']==before and bad['distillation']['pending_sources']>0;assert not bad['items'];Model.mode='ok';Model.proposals={'我的合成旗帜是青色。':c.old.proposal('我的合成旗帜是青色。',subject='合成旗帜')};old_id=bad['distillation']['run']['id'];rev=api('list')['revision'];n=len(Model.calls)
   with ThreadPoolExecutor(max_workers=2) as pool:receipts=list(pool.map(lambda _:api('distillation_resume',revision=rev,run_id=old_id,confirm=True),range(2)))
   assert sum(not x.get('error') for x in receipts)==1,receipts
   done=f.bb.wait_for(lambda:(v if (v:=api('list'))['distillation']['run']['id']!=old_id and v['distillation']['run']['state'] in ('completed','failed') else None));assert done['distillation']['run']['state']=='completed' and len(Model.calls)==n+1,done['distillation'];assert op('distillation_resume',run_id=old_id,confirm=True).get('error');assert sum(x['id']==candidate['id'] for x in done['distillation']['candidates'])==1;assert sum(x['text']=='我的合成旗帜是青色。' for x in done['distillation']['candidates'])==1;n=len(Model.calls);assert run()['distillation']['run']['state']=='no_new' and len(Model.calls)==n
  check('successful_batch_retained_failed_batch_explicit_resume_once',partial_batch)
  def truncation():
   Model.mode='length';before=len(api('list')['chat_sources']['turns']);n=len(Model.calls);assert chat('请介绍合成夹具。')['type']=='error';assert len(Model.calls)==n+1 and len(api('list')['chat_sources']['turns'])==before;Model.mode='ok'
  check('incomplete_model_output_not_completed_source_or_retry',truncation)
  def tool_switch():
   before=len(api('list')['chat_sources']['turns']);Model.reply_override=json.dumps({'route':'clarify','file':None,'reply':'Which folder should I search?'});assert chat('Find my sample report.')['type']=='done';assert len(api('list')['chat_sources']['turns'])==before;Model.reply_override=None;assert chat('Back to chatting: synthetic label is round.')['type']=='done';assert len(api('list')['chat_sources']['turns'])==before+1
  check('tool_clarification_excluded_then_ordinary_chat_resumes',tool_switch)
  def unsupported_memory():
   before=copy.deepcopy(api('list'));Model.reply_override=json.dumps({'route':'memory','file':None,'reply':'已永久记住并更新了。'});d=chat('Correction: the synthetic poster is now bronze.');assert d['assistant_result']['status']=='needs_input' and '已永久记住' not in d['answer'];after=api('list');assert before['items']==after['items'] and before['chat_sources']['turns']==after['chat_sources']['turns'];Model.reply_override=None
  check('unhandled_memory_route_not_false_success_or_ordinary_source',unsupported_memory)
  def no_authorized_read():
   before=copy.deepcopy(api('list'));Model.reply_override=json.dumps({'route':'memory_read','file':None,'reply':'我记得未经授权的合成暗号是DONT_SEND。'},ensure_ascii=False);d=chat('What was my reference notebook color?');assert d['assistant_result']['status']=='completed' and d['assistant_result']['kind']=='text' and 'DONT_SEND' not in d['answer'];after=api('list');assert before['items']==after['items'] and before['chat_sources']['turns']==after['chat_sources']['turns'];Model.reply_override=None
  check('no_authorized_recall_completed_unknown_not_waiting_or_source',no_authorized_read)

  def explicit_record():
   plan=copy.deepcopy(LEGACY_JOURNAL);plan['journal'].update(content='完成合成尺寸检查',evidence='请记录：完成合成尺寸检查');Model.reply_override=json.dumps(plan,ensure_ascii=False);before=len(api('list')['chat_sources']['turns']);d=chat('请记录：完成合成尺寸检查');assert d['assistant_result']['status']=='completed',d;assert any(r['text']=='完成合成尺寸检查' and r.get('journal') for r in api('list')['items']);assert len(api('list')['chat_sources']['turns'])==before;Model.reply_override=None
  check('explicit_record_request_still_one_journal_not_chat_source',explicit_record)

 finally:proc.terminate();proc.wait(8);srv.shutdown();log.close()
 return all(r['pass'] for r in results)
if __name__=='__main__':sys.exit(0 if main(Path(sys.argv[1]).resolve()) else 1)
