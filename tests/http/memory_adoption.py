"""Candidate review and actual recall requests; public endpoints and isolated guarded storage."""
from pathlib import Path
import sys,json,threading,copy
from http.server import ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parent))
import periodic_distillation as old
f=old.f
class Model(old.Model):
 requests=[]
 def do_POST(self):
  raw=self.rfile.read(int(self.headers['Content-Length']));data=json.loads(raw)
  if any('MEMORY_DISTILLATION_V1' in m.get('content','') for m in data['messages']):
   records=json.loads(data['messages'][-1]['content'])['records'];type(self).calls.append(data);cs=[]
   if self.hold:self.entered.set();self.release.wait(8)
   for r in records:
    if r['text'] in self.proposals:c=copy.deepcopy(self.proposals[r['text']]);c['source_id']=r['id'];cs.append(c)
   answer={'candidates':cs}
  else:
   type(self).requests.append(data);answer={'route':'chat','file':None,'reply':'合成回答；依据仅为本次允许的记忆。','calendar_action':None}
  self.reply({'id':'isolated-adoption','model':'deepseek-flash','choices':[{'message':{'role':'assistant','content':json.dumps(answer,ensure_ascii=False)},'finish_reason':'stop'}],'usage':{'prompt_tokens':300,'completion_tokens':80}})
def main(out):
 out.mkdir(parents=True);ap,dp=f.bb.port(),f.bb.port();app,env=f.prepare(out,ap,dp);srv=ThreadingHTTPServer(('127.0.0.1',dp),Model);threading.Thread(target=srv.serve_forever,daemon=True).start();log=(out/'server.log').open('w');proc=f.bb.launch(app,env,dp,ap,log);base=f'http://127.0.0.1:{ap}';sid=None;results=[]
 def raw(action,entry='bubble',session=None,**kw):
  d=f.bb.request(base,'/api/'+entry+'/memory',dict(session_id=session or sid,action=action,**kw));return json.loads(d['body']) if d.get('http_error') else d
 def view():return raw('list')
 def op(action,**kw):return raw(action,revision=view()['revision'],**kw)
 def add(text,cloud=True,**kw):
  Model.proposals[text]=old.proposal(text,**kw);d=op('record_save',text=text,explicit=True,date='2033-04-02',cloud_allowed=cloud);assert not d.get('error'),d;return next(r for r in d['items'] if r['text']==text)
 def run():
  rid=(view()['distillation']['run'] or {}).get('id');raw('distillation_run');return f.bb.wait_for(lambda:(d if (d:=view())['distillation'].get('run') and d['distillation']['run']['id']!=rid and d['distillation']['run']['state'] in ('completed','no_new','failed','unknown') else None))
 def candidate(text):return next(c for c in view()['distillation']['candidates'] if c['text']==text)
 def check(name,fn):
  try:fn();r={'case':name,'pass':True}
  except Exception as e:r={'case':name,'pass':False,'error':str(e)[:500]}
  results.append(r);(out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(name,'PASS' if r['pass'] else 'FAIL',flush=True)
 try:
  f.bb.wait_for(lambda:f.bb.request(base,'/api/health'));sid=f.bb.request(base,'/api/bubble/session',{})['session_id']
  if '--serve' in sys.argv:
   for text in ['合成观测旗采用黄色。','合成展板采用蓝色。','我完成了合成索引说明。','我完成了合成检查清单。']:Model.proposals[text]=old.proposal(text)
   (out/'URL.json').write_text(json.dumps({'base':base,'pid':proc.pid}));print(base,flush=True);threading.Event().wait(1200);return True
  def counts_empty():
   op('save',text='以后解释时先说结论',kind='preference');d=view()['distillation'];assert d['eligible_sources']==d['pending_sources']==0 and d['unavailable_reason']
  check('style_only_no_records_explained',counts_empty)
  def backlog():
   add('旧记录未许可，合成主题柳树。',False)
   for i in range(5):add('合成模块'+str(i)+'的进展已经完成。',kind='progress')
   d=view()['distillation'];assert d['eligible_sources']==d['pending_sources']==5 and d['unlicensed_sources']==1
   op('distillation_config',enabled=True,days=7,cloud=True,confirm=True,sources=['records']);d=run()['distillation'];assert d['pending_sources']==2 and d['continuation_available'] and len(Model.calls)==1
   d=run()['distillation'];assert d['pending_sources']==0 and len(Model.calls)==2;before=len(Model.calls);run();assert len(Model.calls)==before
  check('permitted_counts_and_bounded_backlog_continue',backlog)
  a='我在合成桌面放了一只蓝色纸鹤。';edited='合成桌面的纸鹤是蓝色。'
  def edit_adopt():
   add(a);run();c=candidate(a);args=dict(id=c['id'],candidate_version=c['version'],text=edited);rev=view()['revision'];d=raw('distillation_adopt',revision=rev,**args);assert not d.get('error'),d
   m=next(r for r in d['items'] if r.get('distillation_id')==c['id']);assert m['text']==edited and not m['cloud_allowed'];repeat=raw('distillation_adopt',revision=rev,**args);assert not repeat.get('error'),repeat;assert len([r for r in repeat['items'] if r.get('distillation_id')==c['id']])==1
   assert any(r['id']==m['id'] for r in raw('recall',query='纸鹤')['items']);assert not any(r['id']==m['id'] for r in raw('recall',query='纸鹤',destination='cloud')['items'])
  check('edited_adoption_idempotent_and_local_only_recall',edit_adopt)
  def actual_cloud():
   m=next(r for r in view()['items'] if r['text']==edited)
   def chat():
    s=f.bb.request(base,'/api/bubble/session',{})['session_id'];x=f.bb.Stream(base,'/api/bubble/chat',{'session_id':s,'question':'合成桌面的纸鹤是什么颜色？','web_enabled':False}).start().wait();assert any(v['type']=='done' for _,v in x.events),x.events;return json.dumps(Model.requests[-1],ensure_ascii=False)
   assert edited not in chat();op('update',id=m['id'],text=edited,cloud_allowed=True);assert edited in chat();op('update',id=m['id'],text=edited,cloud_allowed=False);assert edited not in chat()
  check('actual_cloud_payload_separate_permission_and_revoke',actual_cloud)
  def existing():
   x='合成收藏盒是紫色。';add(x);run();op('save',text=x);c=candidate(x);d=op('distillation_adopt',id=c['id'],candidate_version=c['version'],text=x);assert not d.get('error'),d;assert len([r for r in d['items'] if not r.get('journal') and r['text']==x])==1 and '已存在' in d['message']
  check('same_existing_no_duplicate',existing)
  def correction():
   x='我的合成说明板改成橙色。';op('save',text='我的合成说明板是绿色。');target=next(r for r in view()['items'] if r['text']=='我的合成说明板是绿色。');add(x,relation='correction');run();c=candidate(x)
   assert op('distillation_adopt',id=c['id'],candidate_version=c['version'],text=x).get('error')
   args=dict(id=c['id'],candidate_version=c['version'],text=x,target_id=target['id'],target_version=target['version']);d=op('distillation_adopt',**args);assert not d.get('error'),d;r=next(r for r in d['items'] if r['id']==target['id']);assert r['version']==target['version']+1 and r['text']==x and not r['cloud_allowed']
  check('explicit_target_correction_same_identity',correction)
  def stale_source():
   x='留出：合成沙漏放在西侧。';r=add(x);run();c=candidate(x);op('record_update',id=r['id'],text='留出：合成沙漏放在东侧。');d=op('distillation_adopt',id=c['id'],candidate_version=c['version'],text=x);assert d.get('error');assert not any(z.get('distillation_id')==c['id'] for z in view()['items'])
  check('holdout_source_version_rejects_stale_adoption',stale_source)
  def stale_target():
   x='合成标签改为红色。';target=op('save',text='合成标签为白色。');target=next(r for r in target['items'] if r['text']=='合成标签为白色。');add(x,relation='correction');run();c=candidate(x);op('update',id=target['id'],text='合成标签为黑色。');d=op('distillation_adopt',id=c['id'],candidate_version=c['version'],text=x,target_id=target['id'],target_version=target['version']);assert d.get('error');assert any(r['text']=='合成标签为黑色。' for r in view()['items'])
  check('target_version_conflict_no_overwrite',stale_target)
  def concurrent():
   x='留出：我的合成杯垫是圆形。';add(x);run();c=candidate(x);body=dict(id=c['id'],candidate_version=c['version'],text=x,revision=view()['revision'])
   with ThreadPoolExecutor(max_workers=2) as pool:ds=list(pool.map(lambda _:raw('distillation_adopt',**body),range(2)))
   assert all(not d.get('error') for d in ds),ds;assert len([r for r in view()['items'] if r.get('distillation_id')==c['id']])==1
  check('holdout_concurrent_duplicate_adoption_one_write',concurrent)
  def reject_revoke():
   x='留出：合成笔盒有两层。';row=add(x);run();c=candidate(x);op('distillation_reject',id=c['id']);assert op('distillation_adopt',id=c['id'],candidate_version=c['version'],text=x).get('error');assert not any(r.get('distillation_id')==c['id'] for r in view()['items'])
   m=next(r for r in view()['items'] if r['text']==edited);src=next(r for r in view()['items'] if r.get('journal') and r['text']==a);op('record_forget',id=src['id']);assert not any(r['id']==m['id'] for r in raw('recall',query='纸鹤')['items'])
  check('rejection_and_source_forget_remove_recall',reject_revoke)
  def restart():
   nonlocal proc,sid
   x='合成观测旗使用方格图案。';add(x);run();c=candidate(x);d=op('distillation_adopt',id=c['id'],candidate_version=c['version'],text=x);assert not d.get('error'),d
   f.bb.request(base,'/api/bubble/reset',{'session_id':sid});sid=f.bb.request(base,'/api/bubble/session',{})['session_id'];assert any(r['text']==x for r in raw('recall',query='观测旗')['items'])
   proc.terminate();proc.wait(8);proc=f.bb.launch(app,env,dp,ap,log);f.bb.wait_for(lambda:f.bb.request(base,'/api/health'));sid=f.bb.request(base,'/api/bubble/session',{})['session_id'];assert any(r['text']==x for r in raw('recall',query='观测旗')['items'])
  check('reset_and_restart_keep_adopted_memory',restart)
  def expiry():
   x='下个阶段临时检查合成机架。';add(x,kind='short_term');run();c=candidate(x);assert op('distillation_adopt',id=c['id'],candidate_version=c['version'],text=x).get('error')
   d=op('distillation_adopt',id=c['id'],candidate_version=c['version'],text=x,expires_at='2090-01-01T00:00:00+00:00');assert not d.get('error'),d;m=next(r for r in d['items'] if r.get('distillation_id')==c['id']);op('update',id=m['id'],text=x,expires_at='2001-01-01T00:00:00+00:00');assert not any(r['id']==m['id'] for r in raw('recall',query='机架')['items'])
  check('short_term_explicit_expiry_and_expired_no_recall',expiry)
  def cross_entry():
   ws=f.bb.request(base,'/api/assistant/session',{})['session_id'];x='留出：仅网页的合成支架采用弧形。';Model.proposals[x]=old.proposal(x)
   d=raw('record_save',entry='assistant',session=ws,text=x,explicit=True,date='2033-04-02',cloud_allowed=True,audience='entry');assert not d.get('error'),d
   before=len(Model.calls);run();assert x not in json.dumps(Model.calls[before:],ensure_ascii=False)
   d=view();raw('distillation_config',entry='assistant',session=ws,revision=d['revision'],enabled=True,days=7,cloud=True,confirm=True,sources=['records']);raw('distillation_run',entry='assistant',session=ws)
   d=f.bb.wait_for(lambda:(v if (v:=raw('list',entry='assistant',session=ws))['distillation']['run']['state']=='completed' else None));c=next(z for z in d['distillation']['candidates'] if z['text']==x)
   assert not any(z['id']==c['id'] for z in view()['distillation']['candidates'])
   assert op('distillation_adopt',id=c['id'],candidate_version=c['version'],text=x).get('error')
   d=raw('distillation_adopt',entry='assistant',session=ws,revision=d['revision'],id=c['id'],candidate_version=c['version'],text=x);assert not d.get('error'),d
   assert not any(r['text']==x for r in raw('recall',query='弧形')['items']);assert any(r['text']==x for r in raw('recall',entry='assistant',session=ws,query='弧形')['items'])
  check('holdout_dual_entry_sources_and_adoption_scope',cross_entry)
  def consecutive():
   # Return to the original scope, two distinct new candidates share a batch.
   op('distillation_config',enabled=True,days=7,cloud=True,confirm=True,sources=['records'])
   for x in ('合成书签采用六边形。','合成盒盖采用圆形。'):add(x)
   run()
   for x in ('合成书签采用六边形。','合成盒盖采用圆形。'):
    c=candidate(x);d=op('distillation_adopt',id=c['id'],candidate_version=c['version'],text=x);assert not d.get('error'),d
  check('consecutive_adoptions_do_not_reconfirm_unrelated_targets',consecutive)
  def explicit_separate():
   x='合成装饰改成银色。';add(x,relation='conflict');run();c=candidate(x)
   assert op('distillation_adopt',id=c['id'],candidate_version=c['version'],text=x).get('error')
   before={r['id']:r['text'] for r in view()['items'] if not r.get('journal')};d=op('distillation_adopt',id=c['id'],candidate_version=c['version'],text=x,resolve_as_new=True);assert not d.get('error'),d
   assert all(next(r for r in d['items'] if r['id']==i)['text']==text for i,text in before.items())
  check('conflict_explicit_distinct_item_choice_preserves_others',explicit_separate)
  def lost_receipt():
   import socket
   x='合成路标为三角形。';add(x);run();c=candidate(x);body=dict(session_id=sid,action='distillation_adopt',revision=view()['revision'],id=c['id'],candidate_version=c['version'],text=x)
   payload=json.dumps(body).encode();wire=b'POST /api/bubble/memory HTTP/1.1\r\nHost: 127.0.0.1:'+str(ap).encode()+b'\r\nOrigin: '+base.encode()+b'\r\nContent-Type: application/json\r\nContent-Length: '+str(len(payload)).encode()+b'\r\nConnection: close\r\n\r\n'+payload
   connection=socket.create_connection(('127.0.0.1',ap),timeout=3);connection.sendall(wire);connection.shutdown(socket.SHUT_WR);connection.close()
   f.bb.wait_for(lambda:next((z for z in view()['distillation']['candidates'] if z['id']==c['id'] and z['status']=='adopted'),None))
   d=raw('distillation_adopt',**{k:v for k,v in body.items() if k not in ('session_id','action')});assert not d.get('error'),d;assert len([r for r in d['items'] if r.get('distillation_id')==c['id']])==1
  check('lost_adoption_receipt_retry_returns_same_commit',lost_receipt)



 finally:
  proc.terminate();proc.wait(8);srv.shutdown();log.close()
 return all(r['pass'] for r in results)
if __name__=='__main__':raise SystemExit(0 if main(Path(sys.argv[1]).resolve()) else 1)
