"""Public HTTP creation + optional assistant reminder, isolated clock/calendar only."""
from pathlib import Path
import sys,json,threading,copy
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parent))
import relative_time as f

def main(out):
 out.mkdir(parents=True);ap,dp=f.bb.port(),f.bb.port();app,env=f.prepare(out,ap,dp);
 with (app/'backend/settings.py').open('a') as h:h.write('\nREMINDER_NATIVE_DELIVERY=False\n')
 with (app/'backend/calendar/calendar_reminders.py').open('a') as h:h.write("\n_test_set_plan=set_plan\ndef set_plan(*a,**kw):\n if (settings.PROJECT/'.reminder-fault').exists():raise OSError('injected write failure')\n return _test_set_plan(*a,**kw)\n")
 server=ThreadingHTTPServer(('127.0.0.1',dp),f.Model);threading.Thread(target=server.serve_forever,daemon=True).start();cal=f.Calendar(app);log=(out/'server.log').open('w');proc=f.bb.launch(app,env,dp,ap,log);base=f'http://127.0.0.1:{ap}';rows=[]
 def post(path,**d):
  r=f.bb.request(base,'/api/bubble/'+path,d);return json.loads(r['body']) if r.get('http_error') else r
 def now(t):
  temp=app/'.clock.tmp';temp.write_text(json.dumps(f.clock(t)));temp.replace(app/'.clock.json')
 def preview(minutes=120):
  now(f.BASE);sid=post('session')['session_id'];q='安排合成检查';f.Model.plans[q]=f.plan(create={'title':'合成检查','date':f'{minutes}分钟后','start':None},start_time={'kind':'relative','section':'create','expression':f'{minutes}分钟后'})
  # Current-user evidence must be present.
  q=f'{minutes}分钟后安排合成检查';f.Model.plans[q]=f.Model.plans.pop('安排合成检查');stream=f.bb.Stream(base,'/api/bubble/chat',{'session_id':sid,'question':q,'web_enabled':False}).start().wait();d=next(v for _,v in reversed(stream.events) if v['type'] in ('done','error'));return sid,d.get('calendar_preview') or d.get('calendar_draft')
 def confirm(sid,p,lead):return post('calendar/confirm',session_id=sid,preview_id=p['id'],confirm=True,assistant_reminder={'enabled':True,'minutes':lead})
 def check(name,fn):
  try:fn();rows.append({'name':name,'pass':True});print(name,'PASS',flush=True)
  except Exception as e:rows.append({'name':name,'pass':False,'error':str(e)});print(name,'FAIL',flush=True)
  (out/'results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
 try:
  f.bb.wait_for(lambda:f.bb.request(base,'/api/health'))
  for value in [-1,1.5,'1e1','',None,10081,True]:
   def invalid(value=value):
    sid,p=preview();assert p;before=len(cal.writes);r=confirm(sid,p,value);assert r.get('error') and len(cal.writes)==before,r
   check('invalid_lead_'+str(value),invalid)
  def stale():
   sid,p=preview(20);before=len(cal.writes);r=confirm(sid,p,30);assert r.get('error') and len(cal.writes)==before,r
   r=confirm(sid,p,0);assert r['status']=='verified' and r['assistant_reminder']['status']=='not_set' and '关闭' in r['assistant_reminder']['message'],r
   again=confirm(sid,p,0);assert len(cal.writes)==before+1 and again['assistant_reminder']==r['assistant_reminder'],again
  check('twenty_minutes_reject30_accept0_switch_off_separate_receipt',stale)
  def expired():
   sid,p=preview(40);now('2033-01-02T00:01:17+11:00');before=len(cal.writes);r=confirm(sid,p,30);assert r.get('error') and len(cal.writes)==before,r
  check('waiting_invalidates_old_advance_without_changing_start',expired)
  def carrier_completed_not_unwritten():
   now(f.BASE);sid=post('session')['session_id'];q='两小时后提醒我做合成复盘，提前10分钟';f.Model.plans[q]=f.plan(operation='reminder',create={'title':'合成复盘'},reminder={'timing':'explicit','lead_minutes':10,'calendar_policy':'allowed'},start_time={'kind':'relative','section':'create','expression':'两小时后'})
   x=f.bb.Stream(base,'/api/bubble/chat',{'session_id':sid,'question':q,'web_enabled':False}).start().wait();d=next(v for _,v in reversed(x.events) if v['type'] in ('done','error'));p=d['calendar_preview'];r=post('calendar/confirm',session_id=sid,preview_id=p['id'],confirm=True);assert r['status']=='verified',r
   q='再补充一个标题';f.Model.plans[q]=f.plan(operation='reminder',resume=True,create={'title':'合成新题'},reminder={'timing':'explicit','lead_minutes':10,'calendar_policy':'allowed'});before=len(cal.writes);x=f.bb.Stream(base,'/api/bubble/chat',{'session_id':sid,'question':q,'web_enabled':False}).start().wait();d=next(v for _,v in reversed(x.events) if v['type'] in ('done','error'));assert not d.get('calendar_preview') and not d.get('calendar_draft') and len(cal.writes)==before,d
  check('verified_carrier_no_longer_reused_as_unwritten_draft',carrier_completed_not_unwritten)
  def ordinary():
   sid,p=preview();r=post('calendar/confirm',session_id=sid,preview_id=p['id'],confirm=True);assert r['status']=='verified' and r.get('assistant_reminder',{}).get('status')!='saved',r
  check('ordinary_creation_never_adds_reminder',ordinary)
  def saved():
   path=app/'.runtime/calendar-reminders/plans.json';path.parent.mkdir(exist_ok=True);path.write_text(json.dumps({'contract':1,'configuration':{'enabled':True,'revision':1},'items':{},'operations':{}}))
   sid,p=preview(120);before=len(cal.writes);r=confirm(sid,p,0);assert r['status']=='verified' and r['assistant_reminder']['status']=='saved',r
   first=json.loads(path.read_text());again=confirm(sid,p,0);assert first==json.loads(path.read_text()) and len(cal.writes)==before+1 and len(first['items'])==1 and len(first['operations'])==1,again
   import datetime as dt
   now((dt.datetime.fromisoformat(p['start'])+dt.timedelta(seconds=3)).isoformat());delivery=post('reminders',session_id=sid,action='poll')['deliveries'];assert len(delivery)==1 and delivery[0]['lead_minutes']==0,delivery;assert not post('reminders',session_id=sid,action='poll')['deliveries']
  check('verified_identity_zero_minute_persisted_once_and_duplicate_response',saved)
  def external_change():
   cal.change_after_create=True;sid,p=preview(120);before=json.loads((app/'.runtime/calendar-reminders/plans.json').read_text());r=confirm(sid,p,10);assert r['status']=='verified' and r['assistant_reminder']['status']=='not_set' and before==json.loads((app/'.runtime/calendar-reminders/plans.json').read_text()),r;cal.change_after_create=False
  check('calendar_verified_but_external_change_blocks_reminder_only',external_change)
  def uncertain():
   marker=app/'.reminder-fault';marker.touch();sid,p=preview(120);path=app/'.runtime/calendar-reminders/plans.json';before=path.read_bytes();writes=len(cal.writes);r=confirm(sid,p,10);marker.unlink();assert r['status']=='verified' and r['assistant_reminder']['status']=='unknown',r
   again=confirm(sid,p,10);assert again['assistant_reminder']['status']=='unknown' and before==path.read_bytes() and len(cal.writes)==writes+1,again
  check('save_failure_unknown_not_success_or_blind_retry',uncertain)
  def restart_receipt():
   nonlocal proc
   sid,p=preview(120);r=confirm(sid,p,5);assert r['assistant_reminder']['status']=='saved',r
   journal=app/'.calendar-create'/ (p['id']+'.json')
   # Find the existing journal location without changing production paths.
   candidates=list(app.rglob(p['id']+'.json'));assert len(candidates)==1,candidates
   journal=candidates[0];entry=json.loads(journal.read_text());entry['reminder_commit']={'status':'saving','message':'故障注入：前端未收到回执'};journal.write_text(json.dumps(entry))
   same=confirm(sid,p,5);assert same.get('assistant_reminder',{}).get('status')=='saved',same
   proc.terminate();proc.wait(8);proc=f.bb.launch(app,env,dp,ap,log);f.bb.wait_for(lambda:f.bb.request(base,'/api/health'));sid=post('session',session_id=sid,recover=True)['session_id'];path=app/'.runtime/calendar-reminders/plans.json';before=path.read_bytes();writes=len(cal.writes);r=confirm(sid,p,5);assert r.get('error') or r.get('assistant_reminder',{}).get('status')=='saved',r;listed=post('reminders',session_id=sid,action='list');assert listed.get('items') and before==path.read_bytes() and len(cal.writes)==writes,(r,listed)
  check('restart_after_saved_receipt_loss_reconciles_without_new_plan',restart_receipt)
 finally:proc.terminate();proc.wait(8);cal.close();server.shutdown();log.close()
 return rows
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();sys.exit(not all(r['pass'] for r in main(a.out)))
