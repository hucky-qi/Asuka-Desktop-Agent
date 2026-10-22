"""Synthetic acceptance adapter over the existing reminder scheduler, never EventKit."""
from pathlib import Path
import copy,datetime as dt,fcntl,importlib.util,json,os,secrets,threading,types,sys
import backend.settings as settings
ROOT=settings.PROJECT/'.runtime/reminder-tests';SERVICE=None;LOCK=threading.RLock()
def write(path,value):
 path.parent.mkdir(parents=True,exist_ok=True,mode=0o700);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False));os.chmod(tmp,0o600);os.replace(tmp,path)
def runtime():
 global SERVICE
 with LOCK:
  if SERVICE:return SERVICE
  spec=importlib.util.spec_from_file_location('synthetic_reminder_runtime',settings.PROJECT/'calendar_reminders.py');s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
  s.PATH=ROOT/'plans.json';s.settings=types.SimpleNamespace(**vars(settings));s.settings.REMINDER_POLL_SECONDS=1;s.enabled=lambda:True
  def read(eid,cid,cancel):
   if not eid.startswith('synthetic-') or cid!='synthetic-local':raise ValueError('不是隔离合成事件，未访问日历。')
   events=json.loads((ROOT/'events.json').read_text());e=events.get(eid)
   if e and (e['event_id']!=eid or e['calendar_id']!=cid):raise ValueError('合成事件身份失效。')
   return copy.deepcopy(e)
  s.read_event=read;SERVICE=s;return s

def handle(body):
 action=body['action'];s=runtime()
 if action=='synthetic_create':
  ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
  with (ROOT/'creation.lock').open('a') as f:
   fcntl.flock(f,fcntl.LOCK_EX)
   events=json.loads((ROOT/'events.json').read_text()) if (ROOT/'events.json').exists() else {}
   if len([r for r in s.listing() if r['status'] not in ('disabled','dismissed','missed','event_missing')])>=1:raise ValueError('请先关闭现有合成测试提醒，未新增。')
   at=s.now();eid='synthetic-'+secrets.token_hex(16);e={'event_id':eid,'calendar_id':'synthetic-local','calendar_title':'隔离测试（非真实日历）','title':'测试 · 整理资料','start':(at+dt.timedelta(minutes=30,seconds=2)).isoformat(),'end':(at+dt.timedelta(minutes=60,seconds=2)).isoformat(),'version':secrets.token_hex(16),'timezone':s.native_context.system_clock()['timezone']};events[eid]=e;write(ROOT/'events.json',events)
   r=s.set_plan(e,30,operation_id=secrets.token_hex(16))
   with s.store() as d:d['items'][r['id']]['synthetic']=True
   write(ROOT/'active.json',{'enabled':True})
   if s.STOP.is_set():
    if s.WORKER:s.WORKER.join(2)
    s.STOP.clear();s.WORKER=None
   s.start();return {'status':'scheduled','message':'合成提醒已保存，约2秒后到期；需要原应用运行。','reminder':r}
 if action=='synthetic_status':return {'status':'ok','items':s.listing() if (ROOT/'plans.json').exists() else []}
 if action=='synthetic_close_all':
  for r in s.listing():
   if r['status'] not in ('disabled','dismissed'):s.update(r['id'],r['version'],'disable')
  write(ROOT/'active.json',{'enabled':False});s.STOP.set();return {'status':'cancel_requested','message':'已关闭隔离合成计划，不操作正式提醒或日历。'}
 if action=='synthetic_action':
  if body.get('operation')=='snooze':r=s.snooze(body.get('id'),body.get('version'),body.get('operation_id'),body.get('minutes'),body.get('until'))
  elif body.get('operation')=='dismiss':r=s.update(body.get('id'),body.get('version'),'dismiss')
  elif body.get('operation')=='hide':r=s.hide(body.get('id'),body.get('version'))
  else:raise ValueError('未知测试操作。')
  return {'status':'saved','reminder':r}
 raise ValueError('未知合成测试操作。')
def resume():
 try:
  if json.loads((ROOT/'active.json').read_text()).get('enabled') is True:runtime().start()
 except FileNotFoundError:pass
