"""Public HTTP journeys, isolated source memory and mock outbound, controlled UTC clock."""
from pathlib import Path
import sys,json,copy,threading,time,uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parent))
import relative_time as f
class Model(f.bb.Dependencies):
 calls=[];hold=False;entered=threading.Event();release=threading.Event();mode='ok';proposals={}
 def do_POST(self):
  if self.path!='/chat/completions':return self.reply({'error':'mock only, no fallback'},501)
  payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])));records=json.loads(payload['messages'][-1]['content'])['records'];type(self).calls.append(payload)
  if self.hold:self.entered.set();self.release.wait(12)
  if self.mode=='error':return self.reply({'error':{'message':'isolated failure'}},503)
  proposals=[]
  for r in records:
   if r['text'] in self.proposals:
    c=copy.deepcopy(self.proposals[r['text']]);c['source_id']=r['id'];proposals.append(c)
  text='{' if self.mode=='invalid' else json.dumps({'candidates':proposals},ensure_ascii=False)
  self.reply({'id':'isolated-distillation','model':'deepseek-flash','choices':[{'message':{'role':'assistant','content':text},'finish_reason':'stop'}],'usage':{'prompt_tokens':300,'completion_tokens':80,'total_tokens':380}})
def proposal(text,kind='fact',ownership='self',subject=None,applicability='unknown',relation='new'):
 return dict(kind=kind,ownership=ownership,text=text,subject=subject or text[:5],quote=text,source_id='',applicability=applicability,relation=relation)
def main(out):
 out.mkdir(parents=True);ap,dp=f.bb.port(),f.bb.port();app,env=f.prepare(out,ap,dp);clock=app/'.distillation-clock';clock.write_text('2033-04-01T10:00:00+00:00')
 with (app/'backend/settings.py').open('a') as h:h.write('\nAUTO_INDEX_INTERVAL=.1\n')
 with (app/'backend/memory/periodic_distillation.py').open('a') as h:h.write("\ndef now():return dt.datetime.fromisoformat((mem.PATH.parent/'.distillation-clock').read_text())\n")
 # Inject only the external storage completion fault; recovery remains production code.
 with (app/'backend/memory/bubble_memory.py').open('a') as h:h.write("\n_distill_finish=_finish\ndef _finish(raw,guard):\n if (PATH.parent/'.fail-distill-commit').exists() and json.loads(raw).get('learning',{}).get('distillation',{}).get('run',{}).get('state')=='completed':raise OSError('isolated commit interruption')\n return _distill_finish(raw,guard)\n")
 srv=ThreadingHTTPServer(('127.0.0.1',dp),Model);threading.Thread(target=srv.serve_forever,daemon=True).start();log=(out/'server.log').open('w');proc=f.bb.launch(app,env,dp,ap,log);base=f'http://127.0.0.1:{ap}';results=[];sid=None
 def raw(action,entry='bubble',session=None,**kw):
  d=f.bb.request(base,f'/api/{entry}/memory',dict(session_id=session or sid,action=action,**kw));return json.loads(d['body']) if d.get('http_error') else d
 def state():return raw('list')
 def op(action,**kw):return raw(action,revision=state()['revision'],**kw)
 def cfg(enabled=True,**kw):return op('distillation_config',enabled=enabled,days=7,cloud=True,confirm=True,sources=['records'],**kw)
 def add(text,cloud=True,**kw):
  r=op('record_save',text=text,explicit=True,date='2033-04-01',cloud_allowed=cloud,**kw);assert not r.get('error'),r;return next(x for x in r['items'] if x.get('journal') and x['text']==text)
 def run():
  before=state()['distillation'].get('run');rid=before and before.get('id');raw('distillation_run')
  return f.bb.wait_for(lambda:(d if (d:=state()).get('distillation',{}).get('run') and d['distillation']['run']['id']!=rid and d['distillation']['run']['state'] in ('completed','no_new','failed','unknown') else None),timeout=12)
 def clear():
  Model.release.set();Model.hold=False;Model.mode='ok'
  d=state()['distillation'];
  if d.get('run') and d['run']['state'] not in ('completed','no_new','abandoned','cancelled'):op('distillation_abandon',confirm=True)
 def check(name,fn):
  try:fn();results.append({'case':name,'pass':True});print(name,'PASS',flush=True)
  except Exception as e:results.append({'case':name,'pass':False,'error':str(e)[:600]});print(name,'FAIL',flush=True)
  (out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
 try:
  f.bb.wait_for(lambda:f.bb.request(base,'/api/health'));sid=f.bb.request(base,'/api/bubble/session',{})['session_id']
  if '--serve' in sys.argv:
   (out/'URL.json').write_text(json.dumps({'base':base,'pid':proc.pid}));print(base,flush=True)
   threading.Event().wait(1200)
   return True
  def disabled():
   before=len(Model.calls);d=state();assert not d['distillation']['enabled'];raw('distillation_run');assert len(Model.calls)==before;assert not (app/'.bubble-memory.json').exists()
  check('default_disabled_no_source_write_or_call',disabled)
  a='我已完成合成接口校验。';b='解释程序时我喜欢先看小例子。';unallowed='禁止出站的合成口令 ZEBRA';other='同事说他喜欢夜间工作。'
  Model.proposals={a:proposal(a,'progress'),b:proposal(b,'preference',applicability='解释程序时'),other:proposal(other,ownership='other')}
  rows={x:add(x,cloud=x!=unallowed) for x in (a,b,unallowed,other)}
  def consent():
   r=op('distillation_config',enabled=True);assert r.get('error');cfg();d=run();assert d['distillation']['candidate_count']==2,d;assert unallowed not in json.dumps(Model.calls,ensure_ascii=False)
   assert all(not x.get('distillation_id') for x in d['items']);assert raw('recall',task='chat',query='合成接口')['items']==[]
  check('first_batch_evidence_candidates_only_and_permission_exclusion',consent)
  def no_new():
   before=len(Model.calls);d=run();assert d['distillation']['run']['state']=='no_new' and len(Model.calls)==before
  check('no_increment_zero_calls',no_new)
  def update_source():
   text='我已完成合成接口复核。';Model.proposals[text]=proposal(text,'progress');op('record_update',id=rows[a]['id'],text=text);d=run();old=next(x for x in d['distillation']['candidates'] if x['text']==a);assert old['status']=='invalid';assert any(x['text']==text and x['status']=='candidate' for x in d['distillation']['candidates'])
  check('corrected_source_invalidates_previous_candidate',update_source)
  def adopt_forget():
   c=next(x for x in state()['distillation']['candidates'] if x['text']==b);d=op('distillation_adopt',id=c['id']);assert not d.get('error'),d;memory=next(x for x in d['items'] if x.get('distillation_id')==c['id']);assert memory['cloud_allowed'] is False;assert any(x['id']==memory['id'] for x in raw('recall',task='chat',query='程序')['items'])
   op('delete',id=memory['id']);d=state();assert not any(x.get('distillation_id')==c['id'] for x in d['items']);assert next(x for x in d['distillation']['candidates'] if x['id']==c['id'])['status']=='rejected';assert not raw('recall',task='chat',query='程序')['items']
  check('explicit_adoption_local_only_then_forget_no_recall',adopt_forget)
  def duplicate():
   text='留出：我每周整理公共资料。';Model.proposals[text]=proposal(text);add(text);d=run();count=len([x for x in d['distillation']['candidates'] if x['text']==text]);assert count==1;add(text);before=len(Model.calls);run();assert len(Model.calls)==before
  check('holdout_duplicate_submission_and_no_self_reinforcement',duplicate)
  def conflict():
   x='我将合成主题改为蓝色。';y='我将合成主题改为绿色。'
   for text in (x,y):Model.proposals[text]=proposal(text,subject='合成主题');add(text);run()
   d=state();c=next(z for z in d['distillation']['candidates'] if z['text']==y);assert c['status']=='conflict';assert op('distillation_adopt',id=c['id']).get('error')
  check('same_subject_unresolved_conflict_never_overwrites',conflict)
  def quote_temporary():
   x='“我喜欢用编号解释。”';y='明天整理合成资料。';z='这次请只回答一行。'
   Model.proposals[x]=proposal(x[1:-1]);Model.proposals[y]=proposal(y,'short_term');Model.proposals[z]=proposal(z,ownership='temporary_instruction')
   for text in (x,y,z):add(text)
   d=run();assert not any(c['text'] in (x[1:-1],z) for c in d['distillation']['candidates']);c=next(c for c in d['distillation']['candidates'] if c['text']==y);assert op('distillation_adopt',id=c['id']).get('error')
  check('holdout_quoted_and_temporary_not_long_term_short_event_not_adopted',quote_temporary)
  def pause_late():
   x='暂停旅程的合成记录。';Model.proposals[x]=proposal(x);add(x);Model.entered.clear();Model.release.clear();Model.hold=True;raw('distillation_run');assert Model.entered.wait(4);cfg(False);Model.release.set();f.bb.wait_for(lambda:state()['distillation']['run']['state'] in ('unknown','failed'));d=state();assert not any(c['text']==x for c in d['distillation']['candidates']);before=len(Model.calls);raw('distillation_run');assert len(Model.calls)==before;clear();cfg()
  check('disable_while_outbound_blocks_late_commit',pause_late)
  def version_race():
   x='并发前的合成记录。';Model.proposals[x]=proposal(x);add(x);Model.entered.clear();Model.release.clear();Model.hold=True;raw('distillation_run');assert Model.entered.wait(4);row=next(z for z in state()['items'] if z['text']==x);op('record_update',id=row['id'],text='并发后修正记录。');Model.release.set();f.bb.wait_for(lambda:state()['distillation']['run']['state'] in ('unknown','failed'));assert not any(c['text']==x for c in state()['distillation']['candidates']);clear()
  check('source_change_during_generation_prevents_stale_commit',version_race)
  def invalid():
   x='结构错误合成来源。';add(x);Model.mode='invalid';d=run();assert d['distillation']['run']['state']=='failed';before=len(Model.calls);raw('distillation_run');assert len(Model.calls)==before;clear()
  check('malformed_response_known_failure_not_retried_or_checkpointed',invalid)
  def scope():
   ws=f.bb.request(base,'/api/assistant/session',{})['session_id'];text='仅网页入口的留出记录。';Model.proposals[text]=proposal(text);r=raw('record_save',entry='assistant',session=ws,text=text,date='2033-04-01',explicit=True,cloud_allowed=True,audience='entry');assert not r.get('error'),r;d=run();assert text not in json.dumps(Model.calls,ensure_ascii=False)
  check('dual_entry_source_scope_no_cross_entry_export',scope)
  def fee():
   add('预算限制合成记录。');cfg(max_microcny=1000);before=len(Model.calls);d=run();assert len(Model.calls)==before and d['distillation']['run']['state']=='failed';clear();cfg(max_microcny=50000)
  check('conservative_fee_reservation_blocks_before_transport',fee)
  def revoke():
   c=next(z for z in state()['distillation']['candidates'] if z['status']=='candidate');src=next(z for z in state()['items'] if z['id']==c['sources'][0]['id']);op('record_update',id=src['id'],text=src['text'],cloud_allowed=False);d=state();assert next(z for z in d['distillation']['candidates'] if z['id']==c['id'])['status']=='invalid'
  check('source_cloud_permission_revocation_invalidates_candidate',revoke)
  def bad_scope():
   x='讨论算法时我希望分成小步骤。';Model.proposals[x]=proposal(x,'preference',applicability='when discussing algorithms');row=add(x);before=len(Model.calls);d=run();assert d['distillation']['run']['state']=='failed';assert not any(c['text']==x for c in d['distillation']['candidates']);persist=json.loads((app/'.bubble-memory.json').read_text());assert row['id'] not in persist['learning']['distillation']['processed'];assert len(Model.calls)==before+1;clear()
  check('translated_scope_rejected_without_progress_or_permission_default',bad_scope)
  def concurrent():
   x='并发单实例的留出资料。';Model.proposals[x]=proposal(x);add(x);Model.entered.clear();Model.release.clear();Model.hold=True;before=len(Model.calls)
   with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(lambda _:raw('distillation_run'),range(3)))
   assert Model.entered.wait(4);assert len(Model.calls)==before+1;Model.release.set();f.bb.wait_for(lambda:state()['distillation']['run']['state']=='completed');Model.hold=False
  check('parallel_triggers_one_reserved_batch',concurrent)
  def forget_source():
   x='来源遗忘的独立合成事实。';Model.proposals[x]=proposal(x);record=add(x);d=run();c=next(z for z in d['distillation']['candidates'] if z['text']==x);r=op('distillation_adopt',id=c['id']);assert not r.get('error'),r
   op('record_forget',id=record['id']);assert not any(x in z['text'] for z in raw('recall',task='chat',query='来源遗忘')['items']),'adopted derivative survived source forget'
  check('forget_source_invalidates_already_adopted_derivative',forget_source)
  def global_revoke():
   policy=raw('list')['record_cloud'];op('record_cloud_config',enabled=True,policy_revision=policy['revision'])
   x='仅依照记录许可的合成资料。';Model.proposals[x]=proposal(x);r=op('record_save',text=x,date='2033-04-01',explicit=True);assert not r.get('error');d=run();c=next(z for z in d['distillation']['candidates'] if z['text']==x)
   policy=raw('list')['record_cloud'];op('record_cloud_config',enabled=False,policy_revision=policy['revision']);assert next(z for z in state()['distillation']['candidates'] if z['id']==c['id'])['status']=='invalid'
  check('global_record_license_revocation_persistently_invalidates_candidates',global_revoke)
  def interrupted_commit():
   x='写入中断后的合成依据。';Model.proposals[x]=proposal(x);add(x);marker=app/'.fail-distill-commit';marker.touch();before=len(Model.calls);raw('distillation_run')
   recovery=f.bb.wait_for(lambda:(d if (d:=raw('recovery_status')).get('recoverable') else None));assert len(Model.calls)==before+1
   pending=json.loads((app/'.bubble-memory.pending.json').read_text());assert pending['data']['learning']['distillation']['run']['state']=='completed'
   marker.unlink();r=raw('recover',confirm=True,recovery_id=recovery['recovery_id']);assert not r.get('error'),r;assert any(c['text']==x for c in state()['distillation']['candidates']);assert len(Model.calls)==before+1
  check('atomic_candidate_and_watermark_commit_recovered_without_recalling_model',interrupted_commit)
  def unknown_restart():
   nonlocal proc,sid
   x='重启时尚未返回的留出资料。';Model.proposals[x]=proposal(x);add(x);Model.entered.clear();Model.release.clear();Model.hold=True;before=len(Model.calls);raw('distillation_run');assert Model.entered.wait(4);proc.terminate();proc.wait(8);Model.release.set();Model.hold=False
   proc=f.bb.launch(app,env,dp,ap,log);f.bb.wait_for(lambda:f.bb.request(base,'/api/health'));sid=f.bb.request(base,'/api/bubble/session',{})['session_id'];d=state();assert d['distillation']['run']['state']=='unknown';raw('distillation_run');assert len(Model.calls)==before+1;assert not any(c['text']==x for c in d['distillation']['candidates']);clear()
  check('restart_unknown_outbound_not_replayed_source_and_progress_persist',unknown_restart)
  def catchup():
   x='多周期后新增的留出资料。';Model.proposals[x]=proposal(x);add(x);before=len(Model.calls);clock.write_text('2033-06-11T19:00:00+09:00')
   d=f.bb.wait_for(lambda:(d if (d:=state())['distillation']['run']['state']=='completed' and len(Model.calls)>before else None));assert len(Model.calls)==before+1;assert d['distillation']['next_at']=='2033-06-18T10:00:00+00:00'
   clock.write_text('2033-06-11T03:00:00-07:00');r=state();assert r['distillation']['next_at']==d['distillation']['next_at'];assert len(Model.calls)==before+1
  check('missed_many_periods_one_catchup_utc_schedule_timezone_invariant',catchup)
  # Durable progress and unknown results survive process restart, no automatic outstation.
  clear();cfg(False)
 finally:
  Model.release.set();proc.terminate();proc.wait(8);srv.shutdown();log.close()
 return all(r['pass'] for r in results)
if __name__=='__main__':raise SystemExit(0 if main(Path(sys.argv[1]).resolve()) else 1)
