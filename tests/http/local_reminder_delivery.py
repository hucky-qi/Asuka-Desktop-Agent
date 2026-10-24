"""Current public HTTP flow + existing external native IPC mock; never EventKit."""
from pathlib import Path
import sys,json,threading,copy,datetime as dt,uuid
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parent))
import relative_time as f
class Native:
 def __init__(self):
  self.root=app/'.runtime/reminder-ipc';self.root.mkdir(parents=True,exist_ok=True);self.mode='authorization_required';self.outcome='system_accepted';self.visible=[];self.calls=[];self.sends=[];self.stop=threading.Event();self.seen=set();self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()
 def loop(self):
  import os
  while not self.stop.wait(.01):
   p=self.root/'ready.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps({'bundle':'local.filechat.asuka.launcher','protocol':1,'pid':os.getpid()}));tmp.replace(p)
   for p in self.root.glob('*.request.json'):
    try:d=json.loads(p.read_text())
    except (OSError,ValueError):continue
    if d['id'] in self.seen:continue
    self.seen.add(d['id']);self.calls.append(copy.deepcopy(d));action=d['action'];status=self.mode
    if action=='authorize':
     status='authorized' if d.get('explicit_user_action') is True else 'refused';self.mode=status
    elif action=='visible':status='ok'
    elif action=='send':
     e=d['reminder'];plans=json.loads((app/'.runtime/calendar-reminders/plans.json').read_text())['items'];row=plans[e['id']]
     if row['version']!=e['version'] or row['delivery_id']!=e['delivery_id'] or row['status']!='claimed':status='stale'
     elif self.mode=='authorized':status=self.outcome;self.sends.append(copy.deepcopy(e));(self.root/(e['delivery_id']+'.delivery.json')).write_text(json.dumps({'delivery_id':e['delivery_id'],'status':status}))
    elif action=='cancel':status='cancel_requested'
    elif action=='test':status='system_accepted' if self.mode=='authorized' else self.mode
    r={'id':d['id'],'status':status,'delivery_ids':self.visible};p=self.root/(d['id']+'.response.json');tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(r));tmp.replace(p)
 def close(self):self.stop.set();self.thread.join(2)

def main(out):
 global app
 out.mkdir(parents=True);ap,dp=f.bb.port(),f.bb.port();app,env=f.prepare(out,ap,dp)
 native=Native();native.mode='authorized';server=ThreadingHTTPServer(('127.0.0.1',dp),f.Model);threading.Thread(target=server.serve_forever,daemon=True).start();cal=f.Calendar(app)
 path=app/'.runtime/calendar-reminders/plans.json';path.parent.mkdir(exist_ok=True);path.write_text(json.dumps({'contract':1,'configuration':{'enabled':True,'revision':1},'items':{},'operations':{}}))
 log=(out/'server.log').open('w');proc=f.bb.launch(app,env,dp,ap,log);base=f'http://127.0.0.1:{ap}';rows=[]
 def post(route,**kw):
  r=f.bb.request(base,'/api/bubble/'+route,kw);return json.loads(r['body']) if r.get('http_error') else r
 def now(t):
  p=app/'.clock.tmp';p.write_text(json.dumps(f.clock(t)));p.replace(app/'.clock.json')
 def state(ident):return next(x for x in post('reminders',session_id=sid,action='list')['items'] if x['id']==ident)
 def sync():return post('reminders',session_id=sid,action='sync')
 def check(name,fn):
  try:fn();rows.append({'name':name,'pass':True});print(name,'PASS',flush=True)
  except Exception as e:rows.append({'name':name,'pass':False,'error':str(e)[:1800]});print(name,'FAIL',flush=True)
  (out/'results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
 try:
  f.bb.wait_for(lambda:f.bb.request(base,'/api/health'));sid=post('session')['session_id'];q='两小时后安排合成原生投递复核';f.Model.plans[q]=f.plan(create={'title':'合成原生投递复核','date':'两小时后','start':None},start_time={'kind':'relative','section':'create','expression':'两小时后'})
  x=f.bb.Stream(base,'/api/bubble/chat',{'session_id':sid,'question':q,'web_enabled':False}).start().wait();p=next(d['calendar_preview'] for _,d in x.events if d.get('calendar_preview'))
  cal.reminder_fields={'reminder_supported':False,'writable':False,'shared':None,'calendar_type':1};r=post('calendar/confirm',session_id=sid,preview_id=p['id'],confirm=True,assistant_reminder={'enabled':True,'minutes':10});assert r['assistant_reminder']['status']=='saved',r
  eid=r['event_id'];plan=next(iter(json.loads(path.read_text())['items'].values()));ident=plan['id']
  def due_once():
   assert plan['event_id']==eid and plan['timezone']=='Australia/Sydney' and len(cal.writes)==1 and not native.sends
   now('2033-01-02T01:40:17+11:00');sync();row=state(ident);assert row['status']=='system_accepted' and len(native.sends)==1,row;sync();assert len(native.sends)==1
   native.visible=[row['delivery_id']];sync();assert state(ident)['status']=='system_visible';(native.root/(row['delivery_id']+'.click.json')).write_text(json.dumps({'id':ident,'version':row['version'],'delivery_id':row['delivery_id']}));sync();assert state(ident)['status']=='user_viewed'
  check('readonly_caldav_create_to_saved_to_due_accept_visible_viewed_once',due_once)
  def snooze():
   row=state(ident);op=uuid.uuid4().hex;now('2033-01-02T01:41:17+11:00');r=post('reminders',session_id=sid,action='snooze',id=ident,version=row['version'],operation_id=op,minutes=2);assert r['reminder']['due']=='2033-01-01T14:43:17+00:00',r
   post('reminders',session_id=sid,action='snooze',id=ident,version=row['version'],operation_id=op,minutes=2);assert len(json.loads(path.read_text())['items'])==1
   now('2033-01-02T01:43:17+11:00');sync();assert len(native.sends)==2 and state(ident)['status']=='system_accepted'
  check('snooze_persistent_from_click_duplicate_one_plan_next_delivery_once',snooze)
  def read_failure():
   cal.reminder_status='read_error';sync();assert state(ident)['status']=='read_failed';n=len(native.sends);cal.reminder_status='ok';sync();assert state(ident)['status']=='system_accepted' and len(native.sends)==n
  check('read_failure_pauses_not_deleted_recovery_no_repeat',read_failure)
  def reschedule():
   e=cal.events[eid];e.update(version='v2',start='2033-01-02T03:50:17+11:00',end='2033-01-02T04:20:17+11:00');sync();row=state(ident);assert row['status']=='scheduled' and row['start']==e['start'] and row['event_id']==eid,row
  check('reschedule_same_identity_adjusts_plan_without_calendar_write',reschedule)
  def restart():
   nonlocal proc,sid
   proc.terminate();proc.wait(8);proc=f.bb.launch(app,env,dp,ap,log);f.bb.wait_for(lambda:f.bb.request(base,'/api/health'));sid=post('session')['session_id'];now('2033-01-02T03:48:17+11:00');n=len(native.sends);sync();assert state(ident)['status']=='system_accepted' and len(native.sends)==n+1
  check('restart_no_page_or_original_chat_session_delivers_persisted_plan',restart)
  def remove():
   del cal.events[eid];n=len(native.sends);sync();assert state(ident)['status']=='event_missing' and len(native.sends)==n and len(cal.writes)==1
  check('deleted_event_stops_only_bound_plan_no_calendar_changes',remove)
 finally:proc.terminate();proc.wait(8);cal.close();native.close();server.shutdown();log.close()
 return rows
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();sys.exit(not all(x['pass'] for x in main(a.out)))
