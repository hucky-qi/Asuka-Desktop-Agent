"""Ephemeral shared activity only: no prompts, paths, sessions or document text."""
import threading,time,uuid
from contextlib import contextmanager
LOCK=threading.RLock();LOCAL=threading.local();INSTANCE=uuid.uuid4().hex
TASKS={};CURRENT={};VERSION=0;REVISION=0;LAST_COMPLETE=0.0
PHASES={'queued','searching','generating','external_search'}
def begin(request_id,channel):
 global VERSION,REVISION
 with LOCK:
  VERSION+=1;token=VERSION;key=(channel,request_id)
  previous=CURRENT.get(key)
  if previous:TASKS.pop(previous,None)
  CURRENT[key]=token;TASKS[token]={'request_id':request_id,'channel':channel,'version':token,'phase':'queued','touched':time.monotonic()}
  REVISION+=1
  return token
def stage(token,phase):
 global REVISION
 with LOCK:
  row=TASKS.get(token)
  if not row:return False
  if phase not in PHASES:return False
  row.update(phase=phase,touched=time.monotonic());REVISION+=1;return True
def touch(token):
 with LOCK:
  if token in TASKS:TASKS[token]['touched']=time.monotonic()
def finish(token,outcome='idle'):
 global REVISION,LAST_COMPLETE
 with LOCK:
  row=TASKS.pop(token,None)
  if not row:return False
  key=(row['channel'],row['request_id'])
  if CURRENT.get(key)!=token:return False
  CURRENT.pop(key,None);REVISION+=1
  LAST_COMPLETE=time.monotonic() if outcome=='complete' else 0
  return True
def finish_request(request_id,channel):
 if not isinstance(request_id,str):return
 with LOCK:finish(CURRENT.get((channel,request_id)))
def snapshot():
 with LOCK:
  now=time.monotonic()
  for token,row in list(TASKS.items()):
   if now-row['touched']>210:finish(token)
  rows=[{k:r[k] for k in ('request_id','channel','version','phase')} for r in TASKS.values()]
  phases={r['phase'] for r in rows}
  waiting='wifi' if 'external_search' in phases else 'dots' if rows else 'none'
  state='searching' if 'searching' in phases else 'generating' if rows else 'complete' if now-LAST_COMPLETE<3 else 'idle'
  return {'instance':INSTANCE,'revision':REVISION,'state':state,'waiting':waiting,'active_count':len(rows),'tasks':rows}
@contextmanager
def bind(token):
 previous=getattr(LOCAL,'token',None);LOCAL.token=token
 try:yield
 finally:LOCAL.token=previous
def stage_current(phase):stage(getattr(LOCAL,'token',None),phase)
