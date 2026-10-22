"""Explicit original-app notification acceptance channel; no calendar or model import."""
import json,os,time,secrets,fcntl,hashlib
from pathlib import Path
import backend.settings as settings, backend.calendar.reminder_native as reminder_native
ROOT=settings.PROJECT/'.runtime/reminder-ipc'
class Refused(ValueError):pass
class Limited(ValueError):pass
def handle(body,cap):
 try:
  ready=json.loads((ROOT/'ready.json').read_text());control=json.loads((ROOT/'test-capability.json').read_text())
  assert ready['bundle']=='local.filechat.asuka.launcher' and ready['protocol']==1 and ready['pid']==control['pid']
  assert time.time()-(ROOT/'ready.json').stat().st_mtime<5
  os.kill(ready['pid'],0)
  assert isinstance(cap,str) and len(cap)==64 and secrets.compare_digest(control['token'],cap)
 except (OSError,ValueError,KeyError,AssertionError,TypeError):raise Refused('请从原明日香应用的“测试提醒”菜单打开此页；没有请求权限或发送通知。') from None
 action=body.get('action')
 if action=='status':return reminder_native.status()
 if isinstance(action,str) and action.startswith('synthetic_'):
  if body.get('explicit') is not True:raise ValueError('需要用户明确操作。')
  import backend.calendar.reminder_test_service as reminder_test_service
  return reminder_test_service.handle(body)
 if action not in ('authorize','test','clear_test') or body.get('explicit') is not True:raise ValueError('需要用户明确操作，未执行。')
 if action=='authorize':return reminder_native.authorize(True)
 with (ROOT/'test-control.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  record=ROOT/'test-control.json';state={}
  if record.exists():
   try:state=json.loads(record.read_text())
   except (OSError,ValueError):raise ValueError('通知验收记录不可读，已停止；没有清空记录或重试。') from None
  if action=='clear_test':
   delivery=state.get('delivery_id')
   if not delivery:return {'status':'nothing_to_clear'}
   return reminder_native.cancel(delivery)
  if time.time()-state.get('at',0)<60:raise Limited('测试通知每分钟最多一次；没有再次投递。')
  fingerprint=hashlib.sha256(cap.encode()).hexdigest()
  count=state.get('count',0) if state.get('capability_hash')==fingerprint else 0
  if count>=3:raise Limited('本次启动的测试通知次数已用完；没有再次投递。')
  delivery=secrets.token_hex(16)
  state={'at':time.time(),'count':count+1,'capability_hash':fingerprint,'delivery_id':delivery}
  tmp=record.with_suffix('.tmp');fd=os.open(tmp,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
  with os.fdopen(fd,'w') as f:json.dump(state,f);f.flush();os.fsync(f.fileno())
  os.replace(tmp,record) # Reserve before IPC, including timeout/unknown. Never auto retry.
 return reminder_native.request('test',{'explicit_user_action':True,'delivery_id':delivery})
