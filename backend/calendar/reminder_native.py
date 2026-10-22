"""Private local mailbox to the existing app identity, no launch/permission fallback."""
import json,os,time,uuid,threading,fcntl
from pathlib import Path
import backend.settings as settings
ROOT=settings.PROJECT/'.runtime/reminder-ipc'
def request(action,data=None,timeout=4):
 ready=ROOT/'ready.json'
 try:
  r=json.loads(ready.read_text());assert r['bundle']=='local.filechat.asuka.launcher' and r['protocol']==1 and time.time()-ready.stat().st_mtime<5;os.kill(r['pid'],0)
 except (OSError,ValueError,KeyError,AssertionError):return {'status':'unavailable','message':'原入口原生通知桥接未就绪，未请求权限或投递。'}
 ident=uuid.uuid4().hex;path=ROOT/(ident+'.request.json');result=ROOT/(ident+'.response.json');tmp=path.with_suffix('.tmp');payload={'id':ident,'at':time.time(),'action':action,**(data or {})}
 fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
 with os.fdopen(fd,'w') as f:json.dump(payload,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
 os.replace(tmp,path)
 queue=ROOT/'queue.json'
 with (ROOT/'queue.lock').open('a') as f:
  fcntl.flock(f,fcntl.LOCK_EX)
  try:ids=json.loads(queue.read_text())
  except (OSError,ValueError):ids=[]
  ids=[x for x in ids if isinstance(x,str) and __import__('re').fullmatch('[a-f0-9]{32}',x)][-255:]+[ident]
  qt=ROOT/'queue.tmp';qt.write_text(json.dumps(ids));os.chmod(qt,0o600);os.replace(qt,queue)
 deadline=time.monotonic()+timeout
 try:
  while time.monotonic()<deadline:
   if result.exists():
    d=json.loads(result.read_text());assert d['id']==ident;return d
   threading.Event().wait(.05)
  return {'status':'unknown' if action in ('send','test') else 'unavailable','message':'原生投递回执未确认，不自动重发。'}
 finally:path.unlink(missing_ok=True);result.unlink(missing_ok=True);tmp.unlink(missing_ok=True)
def status():return request('status')
def deliver(row):return request('send',{'reminder':row})
def cancel(delivery):return request('cancel',{'delivery_id':delivery})
def authorize(explicit=False):
 if explicit is not True:raise ValueError('仅用户主动点击才请求系统通知权限。')
 return request('authorize',{'explicit_user_action':True},timeout=2)
def test(explicit=False):
 if explicit is not True:raise ValueError('测试通知需明确点击，不自动发送。')
 return request('test',{'explicit_user_action':True,'delivery_id':uuid.uuid4().hex})
def clicks():
 if not ROOT.exists():return []
 rows=[]
 for f in sorted(ROOT.glob('*.click.json'))[:256]:
  try:
   d=json.loads(f.read_text());rows.append(d)
  except (OSError,ValueError):continue
 return rows
