"""One explicit microphone lease, original app only; no audio or transcript logging."""
import json, os, time, uuid, threading
import backend.settings as settings
ROOT=settings.PROJECT/'.runtime/speech-ipc'
LOCK=threading.RLock()
LEASE=None

def native(action,token='',timeout=3):
 try:
  r=json.loads((ROOT/'ready.json').read_text())
  if r['bundle']!='local.filechat.asuka.launcher' or r['protocol']!=1 or time.time()-(ROOT/'ready.json').stat().st_mtime>5:raise ValueError()
  os.kill(r['pid'],0)
 except (OSError,ValueError,KeyError):return {'status':'unavailable','message':'中文本地语音尚未就绪，请从原桌面助手启动后重试。'}
 if action=='capability':return {'status':'ready' if r.get('available') else 'unavailable','message':r.get('message','中文本地资产准备中。'),'engine':'Apple SpeechTranscriber · 本机识别'}
 ident=uuid.uuid4().hex;cmd=ROOT/'command.json';reply=ROOT/(ident+'.response.json')
 tmp=ROOT/(ident+'.tmp')
 fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:json.dump(dict(id=ident,action=action,token=token,at=time.time()),f)
 os.replace(tmp,cmd)
 end=time.monotonic()+timeout
 try:
  while time.monotonic()<end:
   if reply.exists():return json.loads(reply.read_text())
   threading.Event().wait(.04)
  return {'status':'unknown','message':'原生录音状态未确认；不会自动重录。'}
 finally:reply.unlink(missing_ok=True)

def handle(body,entry):
 global LEASE
 import backend.assistant.bubble_chat as bubble_chat
 sid=body.get('session_id')
 public_sid=sid
 if body.get('action')=='cancel':
  with LOCK:
   if LEASE and LEASE['public_sid']==sid and LEASE['entry']==entry and LEASE['token']==body.get('token'):
    result=native('cancel',LEASE['token']);LEASE=None;return result
 if entry=='assistant':
  import backend.assistant.unified_web as unified_web
  _,sid,c=unified_web.resolve(sid)
 with bubble_chat.LOCK:
  c=bubble_chat.SESSIONS.get(sid)
  if not c or (c.entry=='web')!=(entry=='assistant'):raise ValueError('语音会话已变化，请重新开始；没有发送转写。')
  epoch=c.epoch
 if set(body)-{'session_id','action','token','explicit'}:raise ValueError('语音请求包含不支持的字段。')
 action=body.get('action')
 if action not in ('capability','start','poll','stop','cancel'):raise ValueError('未知语音操作。')
 with LOCK:
  now=time.monotonic()
  if LEASE and (LEASE['until']<now or LEASE['c'].epoch!=LEASE['epoch'] or bubble_chat.SESSIONS.get(LEASE['sid']) is not LEASE['c']):
   native('cancel',LEASE['token']);LEASE=None
  if action=='capability':return native(action)
  if action=='start':
   if body.get('explicit') is not True:raise ValueError('请主动点击麦克风后录音。')
   if LEASE:raise ValueError('已有录音或转写正在进行，请先停止或取消。')
   token=uuid.uuid4().hex;LEASE=dict(token=token,public_sid=public_sid,entry=entry,c=c,sid=sid,epoch=epoch,until=now+160)
   result=native('start',token)
   if result.get('status') not in ('requesting','recording','unknown'):LEASE=None
   return dict(result,token=token)
  if not LEASE or LEASE['sid']!=sid or LEASE['token']!=body.get('token') or LEASE['epoch']!=epoch:raise ValueError('录音已取消或会话已变化；旧转写不会回填。')
  result=native(action,LEASE['token'])
  # Text is consumed exactly once; never persisted in the backend.
  if action=='cancel' or result.get('status') in ('complete','cancelled','error','unavailable'):LEASE=None
  return result
