"""Opt-in chat -> incremental candidates -> reviewed memory. Public HTTP, isolated storage."""
from pathlib import Path
import sys,json,threading,copy
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parent))
import periodic_distillation as old
f=old.f
class Model(old.Model):
 gen_entered=threading.Event();gen_release=threading.Event();ordinary=[];mode='ok';hold=False;entered=threading.Event();release=threading.Event();proposals={}
 def do_POST(self):
  data=json.loads(self.rfile.read(int(self.headers['Content-Length'])));is_distill=any('MEMORY_DISTILLATION_V1' in m.get('content','') for m in data['messages'])
  if self.path=='/api/chat':
   if not data.get('stream'):return self.reply({'model':'synthetic-local','message':{'role':'assistant','content':json.dumps({'route':'chat','file':None,'reply':''})},'done':True})
   self.send_response(200);self.send_header('Content-Type','application/x-ndjson');self.end_headers()
   try:
    self.wfile.write((json.dumps({'model':'synthetic-local','message':{'role':'assistant','content':'合成未完成片段'},'done':False},ensure_ascii=False)+'\n').encode());self.wfile.flush();self.gen_entered.set();self.gen_release.wait(8)
    if self.mode=='stream_error':self.wfile.write(b'not-json\n');self.wfile.flush();return
    self.wfile.write((json.dumps({'model':'synthetic-local','message':{'role':'assistant','content':'结束'},'done':True},ensure_ascii=False)+'\n').encode());self.wfile.flush()
   except (BrokenPipeError,ConnectionResetError):pass
   return
  if is_distill:
   type(self).calls.append(data)
   if self.hold:self.entered.set();self.release.wait(10)
   cs=[]
   for row in json.loads(data['messages'][-1]['content'])['records']:
    for needle,proposal in self.proposals.items():
     if needle in row['text']:
      c=copy.deepcopy(proposal);c['source_id']=row['id'];cs.append(c)
   answer={'candidates':cs}
  else:
   type(self).ordinary.append(data);answer={'route':'chat','file':None,'reply':'这是合成助手回答，不是用户事实依据。','calendar_action':None}
   if self.mode=='long_answer':answer['reply']='Public example detail. '*160
   if self.mode=='offer':answer['calendar_action']='offer'
   if self.mode=='memory_read':answer=json.loads('{"route":"memory_read","file":null,"reply":"你之前说过样品徽章是椭圆形的。","expression":null,"preference":null,"journal":null,"behavior":null,"calendar_action":null}')
   if self.mode=='tool':answer={'route':'clarify','file':None,'reply':'合成工具澄清，不属于普通聊天来源。','calendar_action':None}
  if self.mode=='invalid' and not is_distill:return self.reply({'id':'bad','choices':[{'message':{'content':'{'},'finish_reason':'stop'}],'usage':{'prompt_tokens':1,'completion_tokens':1}})
  self.reply({'id':'synthetic-chat-source','model':'deepseek-flash','choices':[{'message':{'role':'assistant','content':json.dumps(answer,ensure_ascii=False)},'finish_reason':'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':80}})
def main(out):
 out.mkdir(parents=True);ap,dp=f.bb.port(),f.bb.port();app,env=f.prepare(out,ap,dp);clock=app/'.source-clock';clock.write_text('2033-01-01T10:00:00+00:00')
 # Only clock externality replaced; production store, guards, provider and dispatcher remain.
 with (app/'backend/memory/chat_sources.py').open('a') as h:h.write("\ndef now():return dt.datetime.fromisoformat((mem.PATH.parent/'.source-clock').read_text())\n")
 with (app/'backend/memory/bubble_memory.py').open('a') as h:h.write("\n_source_finish=_finish\ndef _finish(raw,guard):\n if (PATH.parent/'.source-fault').exists() and json.loads(raw).get('learning',{}).get('chat_sources',{}).get('turns'):raise OSError('isolated source commit interruption')\n return _source_finish(raw,guard)\n")
 srv=ThreadingHTTPServer(('127.0.0.1',dp),Model);threading.Thread(target=srv.serve_forever,daemon=True).start();log=(out/'server.log').open('w');proc=f.bb.launch(app,env,dp,ap,log);base=f'http://127.0.0.1:{ap}';sid=None;results=[]
 def raw(action,entry='bubble',session=None,**kw):
  d=f.bb.request(base,'/api/'+entry+'/memory',dict(session_id=session or sid,action=action,**kw));return json.loads(d['body']) if d.get('http_error') else d
 def view():return raw('list')
 def op(action,**kw):return raw(action,revision=view()['revision'],**kw)
 def cfg(save=True,cloud=False,days=30):return op('chat_source_config',save=save,cloud=cloud,days=days)
 def chat(q,session=None,entry='bubble'):
  x=f.bb.Stream(base,'/api/'+entry+'/chat',{'session_id':session or sid,'question':q,'web_enabled':False}).start().wait();return next((v for _,v in reversed(x.events) if v['type'] in ('done','error')),{})
 def run():
  rid=(view()['distillation']['run'] or {}).get('id');raw('distillation_run');return f.bb.wait_for(lambda:(d if (d:=view())['distillation'].get('run') and d['distillation']['run']['id']!=rid and d['distillation']['run']['state'] in ('completed','failed','unknown','no_new') else None))
 def check(name,fn):
  try:fn();r={'case':name,'pass':True}
  except Exception as e:r={'case':name,'pass':False,'error':str(e)[:650]}
  results.append(r);(out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(name,'PASS' if r['pass'] else 'FAIL',flush=True)
 try:
  f.bb.wait_for(lambda:f.bb.request(base,'/api/health'));sid=f.bb.request(base,'/api/bubble/session',{})['session_id']
  if '--serve' in sys.argv:
   Model.proposals={'我的合成徽章采用菱形。':old.proposal('我的合成徽章采用菱形。')}
   (out/'URL.json').write_text(json.dumps({'base':base,'pid':proc.pid}));print(base,flush=True);threading.Event().wait(1200);return True
  def off():
   chat('合成闲聊：云是什么？');assert not (app/'.bubble-memory.json').exists();assert not view()['chat_sources']['policy']['save'] and view()['chat_sources']['turns']==[]
  check('default_off_no_new_history_store',off)
  def boundary():
   cfg();chat('我的合成徽章采用菱形。');d=view()['chat_sources'];assert len(d['turns'])==1 and d['turns'][0]['user']=='我的合成徽章采用菱形。' and d['turns'][0]['status']=='completed';assert not d['policy']['cloud'];assert view()['distillation']['chat_eligible']==0
   cfg(cloud=True);chat('我的合成展板是橙色。');d=view()['chat_sources'];assert len(d['turns'])==2;assert d['turns'][0]['cloud_allowed'] is False;assert view()['distillation']['chat_eligible']>=1
  check('save_and_cloud_distinct_after_opt_in_not_retroactive',boundary)
  def scope():
   ws=f.bb.request(base,'/api/assistant/session',{})['session_id'];chat('仅网页的未开启合成闲聊。',ws,'assistant');assert not raw('list',entry='assistant',session=ws)['chat_sources']['turns'];assert len(view()['chat_sources']['turns'])==2
  check('dual_entry_opt_in_and_sources_isolated',scope)
  def tool():
   Model.mode='tool';chat('合成任务需要进一步说明。');Model.mode='ok';assert len(view()['chat_sources']['turns'])==2
  check('nonordinary_result_not_saved',tool)
  def incremental():
   text='我的合成展板是橙色。';Model.proposals={text:old.proposal(text)};op('distillation_config',enabled=True,days=7,cloud=True,confirm=True,sources=['records','chats']);d=run();cs=d['distillation']['candidates'];assert any(c['text']==text for c in cs);sent=Model.calls[-1]['messages'][-1]['content'];assert '菱形' not in sent and '合成助手回答' in sent
   c=next(c for c in cs if c['text']==text);assert c['sources'][0]['kind']=='chat';assert not any(r.get('distillation_id') for r in d['items']);before=len(Model.calls);run();assert len(Model.calls)==before
  check('chat_distillation_roles_evidence_and_incremental_no_repeat',incremental)
  def assistant_only():
   # Model falsely quotes assistant output as evidence; application must reject, not trust ownership=self.
   chat('请解释一下合成图形。');Model.proposals={'请解释一下合成图形。':old.proposal('这是合成助手回答，不是用户事实依据。',subject='合成助手')};d=run();assert d['distillation']['run']['state']=='failed';assert not any(c['text'].startswith('这是合成助手') for c in d['distillation']['candidates']);op('distillation_abandon',confirm=True);Model.proposals={}
  check('assistant_claim_cannot_prove_user_fact',assistant_only)
  def adopt_expire():
   nonlocal sid
   chat('我的合成书夹是弧形。');Model.proposals={'我的合成书夹是弧形。':old.proposal('我的合成书夹是弧形。')};d=run();c=next(c for c in d['distillation']['candidates'] if c['text']=='我的合成书夹是弧形。');d=op('distillation_adopt',id=c['id'],candidate_version=c['version'],text=c['text']);assert not d.get('error'),d;mid=next(r['id'] for r in d['items'] if r.get('distillation_id')==c['id']);assert any(r['id']==mid for r in raw('recall',query='书夹')['items']);assert not any(r['id']==mid for r in raw('recall',query='书夹',destination='cloud')['items'])
   f.bb.request(base,'/api/bubble/reset',{'session_id':sid});sid=f.bb.request(base,'/api/bubble/session',{})['session_id'];assert len(view()['chat_sources']['turns'])>=3
   clock.write_text('2033-03-04T10:00:00+00:00');op('chat_source_cleanup');assert view()['chat_sources']['turns']==[];assert any(r['id']==mid for r in raw('recall',query='书夹')['items'])
  check('reset_retains_source_retention_keeps_adopted_memory',adopt_expire)
  def stop_and_delete():
   cfg(cloud=True);chat('留出：我的合成路牌是圆形。');cfg(False);d=view()['chat_sources'];assert len(d['turns'])==1;chat('停止后不能保存这条。');assert len(view()['chat_sources']['turns'])==1
   t=d['turns'][0];op('chat_source_delete',ids=[t['id']],forget_linked=False);assert not view()['chat_sources']['turns']
  check('holdout_stop_save_not_delete_explicit_delete_separate',stop_and_delete)
  def late_revoke():
   cfg(cloud=True);q='我的合成支架为三角形。';chat(q);Model.proposals={q:old.proposal(q)};Model.entered.clear();Model.release.clear();Model.hold=True;raw('distillation_run');assert Model.entered.wait(4);cfg(cloud=False);Model.release.set();f.bb.wait_for(lambda:view()['distillation']['run']['state'] in ('failed','unknown'));assert not any(c['text']==q and c['status']=='candidate' for c in view()['distillation']['candidates']);Model.hold=False;op('distillation_abandon',confirm=True)
  check('revoke_cloud_blocks_late_extraction_commit',late_revoke)
  def restart():
   nonlocal proc,sid
   cfg(cloud=True);chat('留出：I completed the synthetic checklist.');count=len(view()['chat_sources']['turns']);proc.terminate();proc.wait(8);proc=f.bb.launch(app,env,dp,ap,log);f.bb.wait_for(lambda:f.bb.request(base,'/api/health'));sid=f.bb.request(base,'/api/bubble/session',{})['session_id'];assert len(view()['chat_sources']['turns'])==count
  check('holdout_restart_persists_sources_not_runtime_permissions',restart)
  def reference_correction():
   nonlocal sid
   sid=f.bb.request(base,'/api/bubble/session',{})['session_id'];cfg(cloud=True)
   chat('我的合成托盘采用红色。');chat('更正一下，它现在是蓝色。')
   Model.proposals={'更正一下，它现在是蓝色。':dict(old.proposal('我的合成托盘现在是蓝色。',subject='合成托盘',relation='correction'),quote='更正一下，它现在是蓝色。')};d=run();c=next(c for c in d['distillation']['candidates'] if c['text']=='我的合成托盘现在是蓝色。');assert c['status']=='conflict';payload=json.loads(Model.calls[-1]['messages'][-1]['content']);unit=next(r for r in payload['records'] if '更正一下' in r['text']);assert [m['role'] for m in unit['messages']]==['user','assistant','user','assistant'];assert c['sources'][0]['quote']=='更正一下，它现在是蓝色。'
  check('whole_turn_reference_and_correction_not_sentence_split',reference_correction)
  def delete_linked():
   nonlocal sid
   sid=f.bb.request(base,'/api/bubble/session',{})['session_id'];q='我的合成指南针是紫色。';chat(q);Model.proposals={q:old.proposal(q)};d=run();c=next(c for c in d['distillation']['candidates'] if c['text']==q);d=op('distillation_adopt',id=c['id'],candidate_version=c['version'],text=q);assert not d.get('error'),d;row=next(r for r in d['items'] if r.get('distillation_id')==c['id']);t=next(t for t in view()['chat_sources']['turns'] if t['user']==q);assert any(x['memory_id']==row['id'] for x in view()['chat_sources']['links']);op('chat_source_delete',ids=[t['id']],forget_linked=True);assert not any(r['id']==row['id'] for r in raw('recall',query='指南针')['items'])
  check('delete_source_explicit_linked_forget_tombstone',delete_linked)
  def partial_cancel():
   nonlocal sid
   sid=f.bb.request(base,'/api/bubble/session',{})['session_id'];f.bb.request(base,'/api/bubble/model',{'session_id':sid,'action':'select','provider':'ollama'});Model.gen_entered.clear();Model.gen_release.clear();q='请解释合成齿轮的转动。';stream=f.bb.Stream(base,'/api/bubble/chat',{'session_id':sid,'question':q,'web_enabled':False}).start();assert Model.gen_entered.wait(4);f.bb.wait_for(lambda:any(v.get('type')=='delta' for _,v in stream.events));f.bb.request(base,'/api/bubble/cancel',{'session_id':sid,'request_id':stream.body['request_id']});Model.gen_release.set();stream.wait();t=f.bb.wait_for(lambda:next((t for t in view()['chat_sources']['turns'] if t['user']==q),None));assert t['status']=='cancelled' and t['assistant']=='合成未完成片段',t
   assert not any(q in r['text'] for r in json.loads(Model.calls[-1]['messages'][-1]['content'])['records']);f.bb.request(base,'/api/bubble/model',{'session_id':sid,'action':'select','provider':'deepseek'})
  check('cancelled_classified_chat_partial_is_not_completed_or_distilled',partial_cancel)
  def independent():
   nonlocal sid
   cfg(cloud=True);q='我的合成收纳盒是六边形。';Model.proposals={q:old.proposal(q)};turns=[]
   for _ in range(2):
    sid=f.bb.request(base,'/api/bubble/session',{})['session_id'];chat(q);turns.append(next(t for t in view()['chat_sources']['turns'] if t['user']==q and t['session']==next(t['session'] for t in view()['chat_sources']['turns'][::-1] if t['user']==q)))
   record=op('record_save',text=q,explicit=True,date='2033-03-04',cloud_allowed=True,audience='entry');record=next(r for r in record['items'] if r.get('journal') and r['text']==q)
   for _ in range(4):
    d=run()
    if d['distillation']['pending_sources']==0:break
   cs=[c for c in view()['distillation']['candidates'] if c['text']==q and c['status'] in ('candidate','conflict')];assert len(cs)==1 and len(cs[0]['sources'])==3,cs;c=cs[0];d=op('distillation_adopt',id=c['id'],candidate_version=c['version'],text=q);assert not d.get('error'),d;mid=next(r['id'] for r in d['items'] if r.get('distillation_id')==c['id'])
   op('chat_source_delete',ids=[turns[0]['id']],forget_linked=True);assert any(r['id']==mid for r in raw('recall',query='收纳盒')['items'])
   op('record_forget',id=record['id']);assert any(r['id']==mid for r in raw('recall',query='收纳盒')['items'])
   op('chat_source_delete',ids=[turns[1]['id']],forget_linked=True);assert not any(r['id']==mid for r in raw('recall',query='收纳盒')['items'])
  check('holdout_cross_source_dedup_and_independent_basis_survives_one_deletion',independent)
  def delete_late():
   nonlocal sid
   sid=f.bb.request(base,'/api/bubble/session',{})['session_id'];q='留出：我的合成钥匙环是方形。';chat(q);t=next(t for t in view()['chat_sources']['turns'] if t['user']==q);Model.proposals={q:old.proposal(q)};Model.entered.clear();Model.release.clear();Model.hold=True;raw('distillation_run');assert Model.entered.wait(4);op('chat_source_delete',ids=[t['id']],forget_linked=False);Model.release.set();f.bb.wait_for(lambda:view()['distillation']['run']['state'] in ('failed','unknown'));assert not any(c['text']==q and c['status']=='candidate' for c in view()['distillation']['candidates']);Model.hold=False;op('distillation_abandon',confirm=True)
  check('delete_source_during_outbound_blocks_late_candidate',delete_late)
  def interrupted():
   cfg(cloud=True);marker=app/'.source-fault';marker.touch();before=len(Model.ordinary);d=chat('留出：今天完成合成复核表。');assert d['type']=='done' and d.get('source_notice') and d['answer'];recovery=raw('recovery_status');assert recovery['recoverable'];marker.unlink();r=raw('recover',confirm=True,recovery_id=recovery['recovery_id']);assert not r.get('error'),r;assert any(t['user']=='留出：今天完成合成复核表。' for t in view()['chat_sources']['turns']);assert len(Model.ordinary)==before+1
  check('holdout_interrupted_source_commit_recovery_no_model_replay',interrupted)
  def concurrent_config():
   from concurrent.futures import ThreadPoolExecutor
   admin=f.bb.request(base,'/api/bubble/session',{})['session_id'];revision=view()['revision'];args=dict(revision=revision,save=True,cloud=True,days=30)
   with ThreadPoolExecutor(max_workers=2) as pool:answers=list(pool.map(lambda session:raw('chat_source_config',session=session,**args),[sid,admin]))
   assert sum(bool(d.get('error')) for d in answers)==1;assert view()['chat_sources']['policy']['save'] is True
  check('concurrent_policy_cas_no_silent_overwrite',concurrent_config)
  def memory_read_state():
   text='My sample badge has an oval shape';op('save',text=text,cloud_allowed=True);before=len(view()['chat_sources']['turns']);Model.mode='memory_read';d=chat('样品徽章是什么形状？没有依据请说明不知道。');Model.mode='ok';assert d['assistant_result']['kind']=='text' and d['assistant_result']['status']=='completed',d;assert len(view()['chat_sources']['turns'])==before;assert '椭圆形' in d['answer']
  check('approved_memory_read_completion_not_waiting_or_source',memory_read_state)
  def failed_partial():
   nonlocal sid
   sid=f.bb.request(base,'/api/bubble/session',{})['session_id'];f.bb.request(base,'/api/bubble/model',{'session_id':sid,'action':'select','provider':'ollama'});Model.mode='stream_error';Model.gen_entered.clear();Model.gen_release.clear();q='请讲解合成滑轮的受力。';stream=f.bb.Stream(base,'/api/bubble/chat',{'session_id':sid,'question':q,'web_enabled':False}).start();assert Model.gen_entered.wait(4);f.bb.wait_for(lambda:any(v.get('type')=='delta' for _,v in stream.events));Model.gen_release.set();stream.wait();assert any(v.get('type')=='error' for _,v in stream.events);t=next(t for t in view()['chat_sources']['turns'] if t['user']==q);assert t['status']=='failed' and t['assistant']=='合成未完成片段';assert not t['cloud_allowed'];Model.mode='ok'
  check('known_plain_generation_failure_keeps_true_partial_status',failed_partial)
  def offer_excluded():
   nonlocal sid
   sid=f.bb.request(base,'/api/bubble/session',{})['session_id'];before=len(view()['chat_sources']['turns']);Model.mode='offer';chat('合成安排尚待用户决定。');Model.mode='ok';assert len(view()['chat_sources']['turns'])==before
  check('coarse_chat_calendar_offer_promotion_never_saved',offer_excluded)
  def reset_boundary():
   nonlocal sid
   sid=f.bb.request(base,'/api/bubble/session',{})['session_id'];cfg(cloud=True);Model.proposals={};a='我的合成摆件叫砂岩。';b='它在重置后没有明确对象。';chat(a);ta=next(t for t in view()['chat_sources']['turns'] if t['user']==a);f.bb.request(base,'/api/bubble/reset',{'session_id':sid});chat(b);tb=next(t for t in view()['chat_sources']['turns'] if t['user']==b);assert ta['segment']!=tb['segment'];before=len(Model.calls)
   for _ in range(4):
    d=run()
    if d['distillation']['pending_sources']==0:break
   units=[r for call in Model.calls[before:] for r in json.loads(call['messages'][-1]['content'])['records'] if b in r['text']];assert units and all(a not in r['text'] for r in units)
  check('reset_keeps_saved_turns_but_not_cross_reset_reference_context',reset_boundary)

  def bounded_input():
   nonlocal sid
   sid=f.bb.request(base,'/api/bubble/session',{})['session_id'];cfg(cloud=True);op('distillation_config',enabled=True,days=7,cloud=True,confirm=True,sources=['chats'],max_microcny=100000);Model.proposals={}
   for _ in range(4):
    if not view()['distillation']['pending_sources']:break
    run()
   for row in view()['items']:
    if not row.get('journal') and row['cloud_allowed']:op('update',id=row['id'],text=row['text'],cloud_allowed=False)
   Model.mode='long_answer'
   for i in range(4):assert chat('请解释公共合成示例'+str(i)+'。')['type']=='done'
   Model.mode='ok';before=len(Model.calls);d=run();assert d['distillation']['run']['state']=='failed' and d['distillation']['run']['error_code']=='input_limit',d['distillation'];assert len(Model.calls)==before;assert d['distillation']['pending_sources']>0;assert len([t for t in view()['chat_sources']['turns'] if t['user'].startswith('请解释公共合成示例')])==4
  check('complete_context_oversize_no_truncate_or_outbound_or_watermark',bounded_input)






 finally:
  Model.gen_release.set();Model.release.set();proc.terminate();proc.wait(8);srv.shutdown();log.close()
 return all(r['pass'] for r in results)
if __name__=='__main__':raise SystemExit(0 if main(Path(sys.argv[1]).resolve()) else 1)
