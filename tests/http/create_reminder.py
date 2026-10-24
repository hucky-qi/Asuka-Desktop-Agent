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
  try:cal.reminder_fields={};cal.reminder_status='ok';fn();rows.append({'name':name,'pass':True});print(name,'PASS',flush=True)
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
   r=confirm(sid,p,0);assert r['status']=='verified' and r['assistant_reminder']['status']=='not_set' and '关闭' in r['assistant_reminder'].get('detail',r['assistant_reminder']['message']),r
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
  def recovery():
   cal.reminder_fields={'local_reminder_supported':None};sid,p=preview(120);before=len(cal.writes);r=confirm(sid,p,10);assert r['assistant_reminder']['code']=='bridge_proof_missing',r
   cal.reminder_fields={};r=post('calendar/reminder/recheck',session_id=sid,preview_id=p['id'],action='retry',explicit=True);assert r['assistant_reminder']['status']=='saved' and len(cal.writes)==before+1,r
   again=post('calendar/reminder/recheck',session_id=sid,preview_id=p['id'],action='retry',explicit=True);assert again['assistant_reminder']['status']=='saved' and len(cal.writes)==before+1,again
  check('missing_proof_recheck_same_identity_without_new_calendar',recovery)
  def unsupported():
   cal.reminder_fields={'reminder_supported':False,'shared':None,'calendar_type':1};sid,p=preview(120);r=confirm(sid,p,10);assert r['assistant_reminder']['status']=='saved',r
   r=post('calendar/reminder/recheck',session_id=sid,preview_id=p['id'],action='retry',explicit=True);assert r['assistant_reminder']['status']=='saved',r;cal.reminder_fields={}
  check('caldav_shared_unknown_read_only_plan_supported_without_calendar_copy',unsupported)
  def explicit_no():
   cal.reminder_fields={'local_reminder_supported':False,'recurring':True};sid,p=preview(120);r=confirm(sid,p,10);assert r['assistant_reminder']['code']=='unsupported_event' and r['assistant_reminder']['retryable'] is False,r
   listing=post('calendar/reminder/status',session_id=sid);item=next(x for x in listing['items'] if x['preview_id']==p['id']);assert item['assistant_reminder']['retryable'] is False,item
  check('holdout_explicit_unsupported_not_offered_invalid_retry_in_settings',explicit_no)
  def readonly_shared():
   for shared in [True,False,None]:
    cal.reminder_fields={'reminder_supported':False,'local_reminder_supported':True,'calendar_type':1,'writable':False,'shared':shared};sid,p=preview();writes=len(cal.writes);r=confirm(sid,p,10);assert r['assistant_reminder']['status']=='saved' and len(cal.writes)==writes+1,r
  check('holdout_readonly_and_shared_state_not_calendar_write_capability',readonly_shared)
  def identity_proof():
   for fields in [{'local_reminder_contract':None},{'local_reminder_supported':None},{'version':None},{'local_reminder_timezone':None},{'event_id':'synthetic-wrong'},{'calendar_id':'synthetic-wrong'}]:
    cal.reminder_fields=fields;sid,p=preview();r=confirm(sid,p,10);assert r['assistant_reminder']['status']=='not_set',r
  check('missing_native_proof_identity_version_timezone_never_guess',identity_proof)
  def existing_cards():
   previous=cal.events;old=next(iter(previous.values()));a=copy.deepcopy(old);a.update(event_id='synthetic-existing-A',calendar_id='synthetic-readonly',title='合成同名',version='existing-v1',start='2033-01-02T06:00:00+11:00',end='2033-01-02T07:00:00+11:00',writable=False,shared=True,reminder_supported=False,local_reminder_supported=True,local_reminder_timezone='UTC');b=dict(a,event_id='synthetic-existing-B',calendar_id='synthetic-other');cal.events={a['event_id']:a,b['event_id']:b};now(f.BASE);sid=post('session')['session_id'];q='查询明天合成安排';f.Model.plans[q]=f.plan(operation='query',query={'date':'明天','period':'day'},create=None);x=f.bb.Stream(base,'/api/bubble/chat',{'session_id':sid,'question':q,'web_enabled':False}).start().wait();meta=next(d['calendar_events'] for _,d in x.events if d.get('calendar_events'));cards=meta['events'];assert len(cards)==2,meta
   before=len(cal.writes);picked=post('reminders',session_id=sid,action='select',snapshot_id=meta['snapshot_id'],card_token=cards[0]['card_token']);r=post('reminders',session_id=sid,action='set',selection_token=picked['selection_token'],minutes=10,operation_id=__import__('uuid').uuid4().hex);assert r['reminder']['event_id']==cards[0]['event_id'] and r['reminder']['timezone']=='UTC' and len(cal.writes)==before,r
   second=post('reminders',session_id=sid,action='select',snapshot_id=meta['snapshot_id'],card_token=cards[1]['card_token']);r=post('reminders',session_id=sid,action='set',selection_token=picked['selection_token'],minutes=10,operation_id=__import__('uuid').uuid4().hex);assert r.get('error') and len(cal.writes)==before,r;cal.events=previous
  check('existing_readonly_shared_card_identity_timezone_and_switch_selection',existing_cards)
  def legacy_receipt():
   sid,p=preview();cal.reminder_fields={'local_reminder_supported':None};r=confirm(sid,p,10);cal.reminder_fields={}
   journal=next(app.rglob(p['id']+'.json'));data=json.loads(journal.read_text());data['reminder_commit']={'status':'not_set','code':'calendar_unverified','retryable':False,'message':'旧范围拒绝'};journal.write_text(json.dumps(data))
   listing=post('calendar/reminder/status',session_id=sid);item=next(x for x in listing['items'] if x['preview_id']==p['id']);assert item['assistant_reminder']['retryable'] and item['assistant_reminder']['code']=='recheck',item
   writes=len(cal.writes);r=post('calendar/reminder/recheck',session_id=sid,preview_id=p['id'],action='retry',explicit=True);assert r['assistant_reminder']['status']=='saved' and len(cal.writes)==writes,r
  check('old_caldav_failure_record_gets_valid_read_only_recovery_no_copy',legacy_receipt)
  def permission_only():
   cal.reminder_status='authorization_required';sid,p=preview();r=confirm(sid,p,10);assert r['assistant_reminder']['code']=='permission',r
   before=len(cal.writes);catalogs=sum(c.get('action')=='catalog' for c in cal.calls);cal.permission_mode='authorization_required';r=post('calendar/reminder/recheck',session_id=sid,preview_id=p['id'],action='authorize',explicit=True);assert r['assistant_reminder']['code']=='permission',r
   assert sum(c.get('action')=='catalog' for c in cal.calls)==catalogs and len(cal.writes)==before
   cal.permission_mode='allowed';cal.reminder_status='ok';r=post('calendar/reminder/recheck',session_id=sid,preview_id=p['id'],action='retry',explicit=True);assert r['assistant_reminder']['status']=='needs_confirmation',r
   assert r['assistant_reminder']['current']['start']==p['start'] and len(cal.writes)==before,r
  check('permission_authorize_only_no_calendar_scan_fixed_target_repreview',permission_only)
  def retry_setup():
   cal.reminder_fields={'local_reminder_supported':None};sid,p=preview(120);r=confirm(sid,p,10);assert r['assistant_reminder']['code']=='bridge_proof_missing',r
   cal.reminder_fields={};return sid,p,r
  def options():return json.loads((app/'.runtime/calendar-reminders/plans.json').read_text())
  def retry(s,p,**kw):return post('calendar/reminder/recheck',session_id=s,preview_id=p['id'],action='retry',explicit=True,**kw)
  def changes():
   sid,p,r=retry_setup();e=cal.events[r['event_id']];e.update(version='v2',start='2033-01-02T02:50:17+11:00',end='2033-01-02T03:20:17+11:00');before=options();writes=len(cal.writes)
   d=retry(sid,p);assert d['assistant_reminder']['status']=='needs_confirmation' and options()==before,d
   proof=d['assistant_reminder'];e['version']='v3';bad=post('calendar/reminder/recheck',session_id=sid,preview_id=p['id'],action='confirm',recovery_id=proof['recovery_id'],explicit=True);assert bad['assistant_reminder']['status']=='not_set' and options()==before,bad
   d=retry(sid,p);r=post('calendar/reminder/recheck',session_id=sid,preview_id=p['id'],action='confirm',recovery_id=d['assistant_reminder']['recovery_id'],explicit=True);assert r['assistant_reminder']['status']=='saved' and __import__('datetime').datetime.fromisoformat(r['assistant_reminder']['due'])==__import__('datetime').datetime.fromisoformat('2033-01-02T02:40:17+11:00') and len(cal.writes)==writes,r
  check('holdout_event_change_and_second_change_require_fresh_reminder_confirmation',changes)
  def service_permission():
   for status in ['denied','unavailable','authorization_required']:
    cal.reminder_status=status;sid,p=preview();r=confirm(sid,p,10);receipt=r['assistant_reminder'];assert receipt['status']=='not_set' and receipt['retryable'] and (receipt['code']=='permission')==(status!='unavailable'),r
    cal.reminder_status='ok';d=retry(sid,p);assert d['assistant_reminder']['status']=='needs_confirmation',d
    r=post('calendar/reminder/recheck',session_id=sid,preview_id=p['id'],action='confirm',recovery_id=d['assistant_reminder']['recovery_id'],explicit=True);assert r['assistant_reminder']['status']=='saved',r
  check('permission_and_service_recovery_separate_from_unsupported',service_permission)
  def lost():
   sid,p,r=retry_setup();del cal.events[r['event_id']];before=options();r=retry(sid,p);assert r['assistant_reminder']['code']=='not_found' and not r['assistant_reminder']['retryable'] and before==options(),r
  check('deleted_original_never_binds_similar_event',lost)
  def expiry_retry():
   sid,p,r=retry_setup();now('2033-01-02T01:41:17+11:00');before=options();r=retry(sid,p);assert r['assistant_reminder']['code']=='expired_or_invalid' and not r['assistant_reminder']['retryable'] and before==options(),r
  check('elapsed_advance_preserved_not_restarted_or_rolled',expiry_retry)
  def switch_recovery():
   path=app/'.runtime/calendar-reminders/plans.json';state=options();state['configuration']['enabled']=False;path.write_text(json.dumps(state));sid,p=preview();r=confirm(sid,p,10);assert r['assistant_reminder']['code']=='switch_off',r
   before=options();r=retry(sid,p);assert options()==before and r['assistant_reminder']['code']=='switch_off',r
   state=options();state['configuration']['enabled']=True;path.write_text(json.dumps(state));r=retry(sid,p);assert r['assistant_reminder']['status']=='needs_confirmation',r
  check('switch_remains_off_until_explicit_enable_then_new_proof',switch_recovery)
  def sessions():
   sid,p,r=retry_setup();other=post('session')['session_id'];before=options();r=retry(other,p);assert r.get('error') and before==options(),r
   r=post('calendar/reminder/status',session_id=other);assert not r.get('items'),r
   post('reset',session_id=sid);r=retry(sid,p);assert r.get('error') or r['assistant_reminder']['status']=='needs_confirmation',r;assert before==options()
  check('cross_session_and_reset_cannot_reuse_original_confirmation',sessions)
  def cancelled():
   from concurrent.futures import ThreadPoolExecutor
   sid,p,r=retry_setup();before=options();cal.reminder_entered.clear();cal.reminder_release.clear();cal.reminder_hold=True
   with ThreadPoolExecutor() as pool:
    pending=pool.submit(retry,sid,p,request_id='synthetic-recovery-cancel');assert cal.reminder_entered.wait(3);post('cancel',session_id=sid,request_id='synthetic-recovery-cancel');cal.reminder_release.set();r=pending.result(8)
   cal.reminder_hold=False;assert r.get('error') and before==options(),r
  check('cancel_during_native_read_no_late_binding',cancelled)
  def concurrent():
   from concurrent.futures import ThreadPoolExecutor
   sid,p,r=retry_setup();writes=len(cal.writes);before=len(options()['items']);cal.reminder_entered.clear();cal.reminder_release.clear();cal.reminder_hold=True
   with ThreadPoolExecutor() as pool:
    first=pool.submit(retry,sid,p);assert cal.reminder_entered.wait(3);second=retry(sid,p);assert second.get('error'),second;cal.reminder_release.set();r=first.result(8)
   cal.reminder_hold=False;assert r['assistant_reminder']['status']=='saved' and len(options()['items'])==before+1 and len(cal.writes)==writes,r
  check('concurrent_recheck_one_plan_no_calendar_create',concurrent)
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
