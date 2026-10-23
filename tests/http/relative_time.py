"""Relative-start user journeys over public HTTP, mock models/calendar, controlled clock."""
from pathlib import Path
import sys,json,datetime as dt,copy,threading,tempfile,subprocess,uuid,time
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parent))
import run as bb
P=Path(__file__).resolve().parents[2]
BASE='2033-01-01T23:50:17+11:00'
def clock(value):
 d=dt.datetime.fromisoformat(value)
 return {'now':value,'date':d.date().isoformat(),'time':d.strftime('%H:%M:%S'),'weekday':['星期一','星期二','星期三','星期四','星期五','星期六','星期日'][d.weekday()],'timezone':'Australia/Sydney'}
def plan(**kw):
 p={'operation':'create','query':None,'create':{'title':'合成整理','date':None,'start':None},'source':None,'changes':None,'resume':False,'reply':''};p.update(kw);return p
class Model(bb.Dependencies):
 routes={};plans={};seen=[];hold=False;entered=threading.Event();release=threading.Event()
 def do_POST(self):
  if self.path not in ('/chat/completions','/api/chat'):return self.reply({'error':'no fallback dependency'},501)
  data=json.loads(self.rfile.read(int(self.headers['Content-Length'])));text=data['messages'][-1]['content'];is_calendar=any('UNIFIED_CALENDAR_V1' in m.get('content','') for m in data['messages']);p=copy.deepcopy(type(self).plans.get(text,plan(operation='none',reply='合成普通回答')))
  answer=p if is_calendar else {'route':'chat' if p['operation']=='none' else 'calendar','file':None,'reply':p.get('reply',''),'calendar_action':p['operation']}
  if not is_calendar and text in type(self).routes:answer=copy.deepcopy(type(self).routes[text])
  if is_calendar and self.hold:self.entered.set();self.release.wait(8)
  type(self).seen.append({'request':data,'input_bytes':len(json.dumps(data,ensure_ascii=False).encode()),'calendar':is_calendar,'raw':copy.deepcopy(answer)})
  if self.path=='/api/chat':return self.reply({'model':'synthetic-local','message':{'role':'assistant','content':json.dumps(answer,ensure_ascii=False)},'done':True})
  self.reply({'id':'synthetic-response','model':'deepseek-flash','choices':[{'message':{'role':'assistant','content':json.dumps(answer,ensure_ascii=False)},'finish_reason':'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':100,'total_tokens':200}})
class Calendar:
 def __init__(self,app):
  self.root=app/'.runtime/calendar-ipc';self.root.mkdir(parents=True);self.mode='ok';self.change_after_create=False;self.catalog=None;self.calls=[];self.writes=[];self.events={};self.stop=threading.Event();self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()
 def loop(self):
  import os
  while not self.stop.wait(.01):
   f=self.root/'ready.json';t=f.with_suffix('.tmp');t.write_text(json.dumps({'bundle':'local.filechat.asuka.launcher','pid':os.getpid(),'version':1}));t.replace(f)
   for f in self.root.glob('*.request.json'):
    try:
     d=json.loads(f.read_text());out=self.root/(d['id']+'.response.json')
     if out.exists():continue
     self.calls.append(d);r={'id':d['id'],'status':self.mode,'events':[],'partial':False,'timezone':'Australia/Sydney'}
     if self.mode=='ok' and (d.get('action',d.get('operation'))=='read' or ('period' in d and 'action' not in d)):r['events']=list(copy.deepcopy(self.events).values());r['total']=len(r['events'])
     if self.mode=='ok' and d.get('action')=='update_check':r.update(event=copy.deepcopy(self.events.get(d['event_id'])),conflicts=[],conflict_version='synthetic-conflict')
     if self.mode=='ok' and d.get('action')=='reminder_check':
      if d['event_id'] in self.events:r['event']=copy.deepcopy(self.events[d['event_id']])
      else:r['status']='not_found'
     if self.mode=='ok' and d.get('action')=='catalog':r['calendars']=self.catalog or [{'id':'synthetic-local','title':'合成日历','default':True,'writable':True}];r['duplicates']=[]
     if self.mode=='ok' and d.get('action')=='create':
      self.writes.append(copy.deepcopy(d));eid='synthetic-event-'+d['operation_id'];self.events[eid]=dict(d['event'],event_id=eid,calendar_id=d['calendar_id'],calendar_title='合成日历',version='v1',reminder_supported=True,recurring=False,shared=False,has_attendees=False);r.update(status='verified',event_id=eid,event=d['event'])
      if self.change_after_create:self.events[eid]['title']='合成外部变更'
     t=out.with_suffix('.tmp');t.write_text(json.dumps(r));t.replace(out)
    except (OSError,ValueError):pass
 def close(self):self.stop.set();self.thread.join(2)
def prepare(out,ap,dp):
 app,env=bb.prepare(out,ap,dp)
 with (app/'backend/settings.py').open('a') as f:f.write('\nUNIFIED_CALENDAR_ENABLED=True\n')
 (app/'.clock.json').write_text(json.dumps(clock(BASE)))
 with (app/'backend/native_context.py').open('a') as f:f.write("\ndef system_clock(now=None):return json.loads((settings.PROJECT/'.clock.json').read_text())\n")
 cfg={'enabled':True,'daily_chat_enabled':True,'explicit_call_limit':None,'explicit_output_limit':None,'calendar_enabled':True,'understanding_enabled':True,'api_key':'synthetic-test-placeholder','base_url':f'http://127.0.0.1:{dp}','model':'deepseek-flash','timeout':15,'call_limit':20,'input_limit':16000,'output_limit':1024}
 with (app/'backend/providers/cloud_access.py').open('a') as f:f.write('\ndef config():return '+repr(cfg)+'\n')
 return app,env

def main(expectations,out):
 out.mkdir(parents=True,exist_ok=False);ap,dp=bb.port(),bb.port();app,env=prepare(out,ap,dp);srv=ThreadingHTTPServer(('127.0.0.1',dp),Model);threading.Thread(target=srv.serve_forever,daemon=True).start();cal=Calendar(app);log=(out/'server.log').open('w');proc=bb.launch(app,env,dp,ap,log);base=f'http://127.0.0.1:{ap}';rows=[]
 def setclock(value):
  t=app/'.clock.tmp';t.write_text(json.dumps(clock(value)));t.replace(app/'.clock.json')
 def post(endpoint,**body):
  d=bb.request(base,endpoint,body);return json.loads(d['body']) if d.get('http_error') else d
 def new(entry='bubble'):return post('/api/'+entry+'/session')['session_id']
 def run(s,q,p,entry='bubble',**options):
  Model.plans[q]=p;x=bb.Stream(base,'/api/'+entry+'/chat',{'session_id':s,'question':q,'web_enabled':False,'received_clock':clock('1999-01-01T00:00:00+11:00'),**options}).start().wait();return next((v for _,v in reversed(x.events) if v['type'] in ('done','error')),{}),x
 def check(name,fn):
  try:fn();r={'case':name,'status':'PASS'}
  except Exception as e:r={'case':name,'status':'FAIL','error':str(e)[:1400]}
  rows.append(r);(out/'results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2));(out/'model-raw.json').write_text(json.dumps(Model.seen,ensure_ascii=False,indent=2));print(name,r['status'],flush=True)
 def preview(d):return d.get('calendar_preview') or d.get('calendar_draft')
 def singles():
  for group in ('dev','holdout'):
   for i,case in enumerate(expectations[group]):
    def sample(c=case):
     setclock(BASE);operation='none' if c.get('route')=='chat' else 'clarify' if c.get('unknown') else 'reminder' if c.get('reminder') else 'create'
     p=plan(operation=operation,create={'title':'合成整理','date':c.get('expression'),'start':None,**({'duration_minutes':c['duration']} if c.get('duration') else {})})
     if c.get('reminder'):p['reminder']={'timing':'explicit','lead_minutes':c['reminder'],'calendar_policy':'allowed'}
     d,_=run(new(),c['q'],p)
     if c.get('reject'):assert not preview(d),d
     else:
      v=preview(d);expected=dt.datetime.fromisoformat(BASE)+dt.timedelta(seconds=c['seconds']);assert v and dt.datetime.fromisoformat(v['start'])==expected,d
      assert (dt.datetime.fromisoformat(v['end'])-expected).total_seconds()==c.get('duration',30)*60,v
      assert v['relative_time']['basis']==BASE and v['relative_time']['target']==v['start'],v
      if c.get('reminder'):assert dt.datetime.fromisoformat(v['assistant_reminder']['due'])==expected-dt.timedelta(minutes=c['reminder']),v
      assert len(cal.writes)==0
    check(group+'_'+str(i),sample)
 try:
  bb.wait_for(lambda:bb.request(base,'/api/health'));singles()
  if '--singles' not in sys.argv:
   def supplement():
    setclock(BASE);sid=new();p=plan(create={'date':'半小时后','start':None,'title':None});d,_=run(sid,'半小时后安排一下',p);assert not preview(d),d
    setclock('2033-01-02T00:00:17+11:00');p=plan(resume=True,create={'title':'合成归档','date':None,'start':None});d,_=run(sid,'事项是合成归档',p);v=preview(d);assert v and v['start']=='2033-01-02T00:20:17+11:00',d
    old=v['id'];setclock('2033-01-02T00:01:17+11:00');p=plan(resume=True,create={'title':None,'date':'一个半小时后','start':None});d,_=run(sid,'不，改成一个半小时后',p);v=preview(d);assert v['start']=='2033-01-02T01:31:17+11:00',d
    assert post('/api/bubble/calendar/confirm',session_id=sid,preview_id=old,confirm=True).get('error');assert not cal.writes
   check('supplement_then_explicit_correction_new_receipt',supplement)
   def permission():
    setclock(BASE);sid=new('assistant');cal.mode='denied';d,_=run(sid,'30min之后安排合成归档',plan(create={'title':'合成归档','date':'30min之后','start':None}),'assistant');draft=d.get('calendar_recovery');assert draft,d
    setclock('2033-01-02T00:05:17+11:00');cal.mode='ok';d=post('/api/assistant/calendar/refresh',session_id=sid,draft=draft);v=preview(d);assert v and v['start']=='2033-01-02T00:20:17+11:00',d;assert not cal.writes
   check('web_permission_wait_same_target_no_write',permission)
   def delay():
    setclock(BASE);sid=new();q='过两小时安排合成整理';Model.plans[q]=plan(create={'title':'合成整理','date':'过两小时','start':None});Model.hold=True;Model.entered.clear();Model.release.clear();x=bb.Stream(base,'/api/bubble/chat',{'session_id':sid,'question':q,'web_enabled':False}).start();assert Model.entered.wait(3);setclock('2033-01-02T00:00:17+11:00');Model.release.set();x.wait();Model.hold=False;d=next(v for _,v in reversed(x.events) if v['type'] in ('done','error'));assert preview(d)['start']=='2033-01-02T01:50:17+11:00',d
   check('received_before_model_wait_midnight',delay)
   def expiry():
    setclock(BASE);sid=new();d,_=run(sid,'半小时后安排合成复盘',plan(create={'title':'合成复盘','date':'半小时后','start':None}));v=preview(d);assert v,d;setclock('2033-01-02T00:21:17+11:00');d=post('/api/bubble/calendar/confirm',session_id=sid,preview_id=v['id'],confirm=True);assert d.get('error') and '过去' in d['error'],d;assert not cal.writes
   check('elapsed_target_confirm_refuses_zero_writes',expiry)
   def reminder_fields():
    setclock(BASE);sid=new();p=plan(operation='reminder',create={'title':'合成复核','date':'过两小时','start':None},reminder={'timing':'explicit','lead_minutes':10,'calendar_policy':'allowed'});d,_=run(sid,'过两小时提醒我合成复核，提前十分钟',p);v=preview(d);assert v,d
    setclock('2033-01-02T00:00:17+11:00');d,_=run(sid,'持续四十五分钟，提前五分钟提醒',plan(operation='reminder',resume=True,create={'duration_minutes':45},reminder={'timing':'explicit','lead_minutes':5,'calendar_policy':'allowed'}));v=preview(d);assert v and v['start']=='2033-01-02T01:50:17+11:00' and v['end']=='2033-01-02T02:35:17+11:00' and v['assistant_reminder']['due']=='2033-01-02T01:45:17+11:00',d
   check('duration_advance_not_new_start',reminder_fields)
   def restart_permission():
    nonlocal proc
    setclock(BASE);sid=new();cal.mode='denied';d,_=run(sid,'过两小时安排合成归档',plan(create={'title':'合成归档','date':'过两小时','start':None}));draft=d['calendar_recovery'];proc.terminate();proc.wait(8);proc=bb.launch(app,env,dp,ap,log);bb.wait_for(lambda:bb.request(base,'/api/health'));sid=post('/api/bubble/session',session_id=sid,recover=True)['session_id'];setclock('2033-01-02T00:10:17+11:00');cal.mode='ok'
    forged=copy.deepcopy(draft);forged['plan']['create']['start']='06:00';assert post('/api/bubble/calendar/refresh',session_id=sid,draft=forged).get('error')
    d=post('/api/bubble/calendar/refresh',session_id=sid,draft=draft);assert preview(d) and preview(d)['start']=='2033-01-02T01:50:17+11:00',d;assert not cal.writes
   check('restart_recovery_anchor_and_tamper_rejection',restart_permission)
   def cancelled_late():
    setclock(BASE);sid=new();q='一个半小时后安排合成归档';Model.plans[q]=plan(create={'title':'合成归档','date':'一个半小时后','start':None});Model.hold=True;Model.entered.clear();Model.release.clear();x=bb.Stream(base,'/api/bubble/chat',{'session_id':sid,'question':q,'web_enabled':False}).start();assert Model.entered.wait(3);post('/api/bubble/reset',session_id=sid);Model.release.set();x.wait();Model.hold=False;assert not any(e.get('calendar_preview') for _,e in x.events);assert not cal.writes
   check('late_model_after_reset_cannot_restore_target',cancelled_late)
   def dst():
    setclock('2033-10-02T01:50:17+10:00');sid=new();d,_=run(sid,'半小时后安排合成检查',plan(create={'title':'合成检查','date':'半小时后','start':None}));v=preview(d);assert v and v['start']=='2033-10-02T03:20:17+11:00',d
   check('elapsed_duration_crosses_dst_without_wall_clock_guess',dst)
   def absolute_correction():
    setclock(BASE);sid=new();run(sid,'半小时后安排合成练习',plan(create={'title':'合成练习','date':'半小时后','start':None}));setclock('2033-01-02T00:00:17+11:00');d,_=run(sid,'改成明天下午三点',plan(resume=True,create={'date':'明天','start':'15:00'},start_time={'kind':'absolute','section':'create','expression':'明天下午三点'}));v=preview(d);assert v and v['start']=='2033-01-03T15:00:00+11:00' and 'relative_time' not in v,d
   check('absolute_correction_clears_relative_anchor',absolute_correction)
   def quoted_evidence():
    setclock(BASE);sid=new();d,_=run(sid,'这只是引用“半小时后”，不要执行',plan(create={'title':'合成检查','date':None,'start':None},start_time={'kind':'relative','section':'create','expression':'半小时后'}));assert not preview(d),d
   check('quoted_start_cannot_be_current_time_evidence',quoted_evidence)
   def default_then_shorter_lead():
    setclock(BASE);sid=new();p=plan(operation='reminder',create={'title':'合成整理','date':'半小时后','start':None},reminder={'timing':'default','lead_minutes':None,'calendar_policy':'allowed'});d,_=run(sid,'半小时后提醒我合成整理',p);assert preview(d) and preview(d)['start']=='2033-01-02T00:20:17+11:00',d
    setclock('2033-01-02T00:00:17+11:00');d,_=run(sid,'那就提前五分钟提醒',plan(operation='reminder',resume=True,create={},reminder={'timing':'explicit','lead_minutes':5,'calendar_policy':'allowed'}));v=preview(d);assert v and v['start']=='2033-01-02T00:20:17+11:00' and v['assistant_reminder']['due']=='2033-01-02T00:15:17+11:00',d
   check('default_lead_editable_preview_keeps_relative_target',default_then_shorter_lead)
   def retry_without_expression():
    setclock(BASE);sid=new();d,_=run(sid,'半小时后安排合成训练',plan(create={'title':'合成训练','date':'半小时后','start':None}));assert preview(d),d
    setclock('2033-01-02T00:00:17+11:00');d,_=run(sid,'再试一下，事项不变',plan(resume=True,create={}));assert preview(d)['start']=='2033-01-02T00:20:17+11:00',d
   check('retry_supplement_does_not_restart_countdown',retry_without_expression)
   def selected_update():
    setclock(BASE);sid=new();post('/api/bubble/model',session_id=sid,action='select',provider='ollama');event=dict(event_id='selected-one',version='v1',title='合成已选活动',calendar_id='synthetic-local',calendar_title='合成日历',calendar_type='local',start='2033-01-02T09:00:00+11:00',end='2033-01-02T11:00:00+11:00',timezone='Australia/Sydney',all_day=False,writable=True,recurring=False,shared=False,has_attendees=False,alarms=[]);cal.events={'selected-one':event}
    d,_=run(sid,'查询明天的日程',plan(operation='query',query={'date':'明天','period':'day'},create=None));meta=d.get('calendar_events');assert meta,d
    chosen=post('/api/bubble/calendar/update/choose',session_id=sid,snapshot_id=meta['snapshot_id'],card_token=meta['events'][0]['card_token']);edit=chosen.get('calendar_edit');assert edit,chosen
    d,_=run(sid,'把它改到半小时后',plan(operation='update',source={},create=None,changes={'date':'半小时后','start':None}),calendar_edit_id=edit['id']);v=d.get('calendar_update_preview');assert v and v['original']['event_id']=='selected-one' and v['proposed']['start']=='2033-01-02T00:20:17+11:00' and v['proposed']['end']=='2033-01-02T02:20:17+11:00',d
    setclock('2033-01-02T00:21:17+11:00');result=post('/api/bubble/calendar/update/confirm',session_id=sid,preview_id=v['id'],confirm=True);assert result.get('error') and '过去' in result['error'],result;assert cal.events['selected-one']==event;assert not any(x.get('action')=='update' for x in cal.calls);cal.events={}
   check('selected_identity_relative_update_preserves_two_hour_duration',selected_update)
   def correction_contract_update():
    setclock(BASE);sid=new();run(sid,'半小时后安排合成复核',plan(create={'title':'合成复核','date':'半小时后','start':None}));setclock('2033-01-02T00:01:17+11:00');d,_=run(sid,'改成一个半小时后',plan(operation='update',resume=True,create=None,source={},changes={'date':'一个半小时后'}));assert preview(d) and preview(d)['start']=='2033-01-02T01:31:17+11:00' and preview(d)['title']=='合成复核',d
   check('unwritten_draft_correction_not_existing_identity_update',correction_contract_update)
   def missing_title_slot_fill():
    setclock(BASE);sid=new();run(sid,'过两小时帮我安排一下',plan(create={'title':None,'date':None,'start':None},start_time={'kind':'relative','section':'create','expression':'过两小时'}));setclock('2033-01-02T00:00:17+11:00')
    d,_=run(sid,'事项是合成接口复核',plan(create={'title':'合成接口复核','date':None,'start':None},resume=False));assert preview(d) and preview(d)['start']=='2033-01-02T01:50:17+11:00',d
   check('missing_title_supplement_keeps_code_target_despite_model_resume_false',missing_title_slot_fill)
   def copied_pending_without_resume():
    setclock(BASE);sid=new();run(sid,'过两小时帮我安排一下',plan(create={'title':'合成原事项','date':None,'start':None},start_time={'kind':'relative','section':'create','expression':'过两小时'}));setclock('2033-01-02T00:00:17+11:00')
    d,_=run(sid,'事项是合成接口复核',plan(create={'title':'合成接口复核','date':'2033-01-02','start':'01:50'},resume=False));assert not preview(d) and '原目标未重新起算' in d.get('answer',''),d
    d,_=run(sid,'继续刚才的安排',plan(create={},resume=True));assert preview(d) and preview(d)['start']=='2033-01-02T01:50:17+11:00',d
   check('raw_failure_replay_copied_time_without_resume_rejected',copied_pending_without_resume)
   def outer_clarification_continues():
    setclock(BASE);sid=new();run(sid,'过两小时请安排一项活动',plan(create={'title':None,'date':None,'start':None},start_time={'kind':'relative','section':'create','expression':'过两小时'}));setclock('2033-01-02T00:00:17+11:00')
    q='事项是合成演练';Model.routes[q]={'route':'calendar','file':None,'reply':'请核对','calendar_action':'clarify'}
    d,_=run(sid,q,plan(create={'title':'合成演练'},resume=True));assert preview(d) and preview(d)['start']=='2033-01-02T01:50:17+11:00',d;Model.routes.pop(q)
   check('outer_clarify_is_reply_not_loss_of_unwritten_task',outer_clarification_continues)
   def unknown_carrier_is_explicit_preview():
    setclock(BASE);sid=new();q='30min之后提醒我做合成校对，提前10分钟';d,_=run(sid,q,plan(operation='reminder',create={'title':'合成校对'},reminder={'timing':'explicit','lead_minutes':10,'calendar_policy':'unknown'},start_time={'kind':'relative','section':'create','expression':'30min之后'}));assert preview(d) and preview(d)['assistant_reminder']['lead_minutes']==10,d
    sid=new();d,_=run(sid,'30分钟后只提醒，不要加入日历',plan(operation='reminder',create={'title':'合成校对'},reminder={'timing':'default','lead_minutes':None,'calendar_policy':'excluded'},start_time={'kind':'relative','section':'create','expression':'30分钟后'}));assert not preview(d),d
   check('unknown_carrier_preview_does_not_override_explicit_exclusion',unknown_carrier_is_explicit_preview)
   def choose_calendar_without_rebasing():
    setclock(BASE);cal.catalog=[{'id':'synthetic-local','title':'合成日历','default':True,'writable':True},{'id':'synthetic-work','title':'合成工作','default':False,'writable':True}];sid=new();d,_=run(sid,'过两小时安排合成分类',plan(create={'title':'合成分类'},start_time={'kind':'relative','section':'create','expression':'过两小时'}));v=preview(d);setclock('2033-01-02T00:05:17+11:00');r=post('/api/bubble/calendar/revise',session_id=sid,preview_id=v['id'],calendar_id='synthetic-work');assert r['calendar_preview']['calendar_id']=='synthetic-work' and r['calendar_preview']['start']==v['start'] and r['calendar_preview']['relative_time']==v['relative_time'],r;old=post('/api/bubble/calendar/confirm',session_id=sid,preview_id=v['id'],confirm=True);assert old.get('error'),old;cal.catalog=None
   check('calendar_selection_preserves_anchor_and_invalidates_prior_confirmation',choose_calendar_without_rebasing)
   def duplicate():
    setclock(BASE);sid=new();d,_=run(sid,'30分钟后安排合成复盘',plan(create={'title':'合成复盘','date':'30分钟后','start':None}));v=preview(d);assert v,d
    a=post('/api/bubble/calendar/confirm',session_id=sid,preview_id=v['id'],confirm=True);b=post('/api/bubble/calendar/confirm',session_id=sid,preview_id=v['id'],confirm=True);assert a['status']==b['status']=='verified' and len(cal.writes)==1,(a,b,cal.writes)
   check('duplicate_confirmation_one_mock_write',duplicate)
 finally:
  Model.release.set();proc.terminate();proc.wait(8);cal.close();srv.shutdown();log.close();(out/'calendar_calls.json').write_text(json.dumps(cal.calls,ensure_ascii=False,indent=2))
 return rows
if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--expectations',type=Path,default=Path(__file__).with_name('relative-expectations.json'));parser.add_argument('--out',type=Path,required=True);parser.add_argument('--singles',action='store_true');a=parser.parse_args();result=main(json.loads(a.expectations.read_text()),a.out);sys.exit(any(r['status']!='PASS' for r in result))
