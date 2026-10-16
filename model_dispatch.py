"""One cancellable FIFO for this process's chat-model calls, not file/embedding work.
A cancelled consumer discards output; the worker drains the existing response before
starting another request. This does not assert anything about Ollama's internal queue.
"""
import json,threading,time,queue,uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass,field
from pathlib import Path
import settings

class Cancelled(RuntimeError):pass
LOCAL=threading.local();CONDITION=threading.Condition();PENDING=deque();WORKER=None;ACTIVE=None;LOG_LOCK=threading.Lock()

@contextmanager
def context(request_id,token,cancel,emit=None,phase='generating'):
 previous=getattr(LOCAL,'context',None);LOCAL.context=(request_id,token,cancel,emit,phase)
 try:yield
 finally:LOCAL.context=previous

def capture(cancel,phase):
 ctx=getattr(LOCAL,'context',None)
 if ctx:
  rid,token,prior,emit,default=ctx
  def stage(value):
   import task_status
   task_status.stage(token,value)
   if emit:emit({'type':'stage','state':value,'detail':'本地模型排队中，可停止' if value=='queued' else '本地模型处理中','request_id':rid})
  return rid,token,cancel or prior,stage,phase or default
 import diagnostics,file_tools
 trace=getattr(diagnostics.local,'trace',None)
 if trace:return trace.id,trace.task_token,cancel or getattr(file_tools._current_search,'cancel_event',None),trace.stage,phase or 'generating'
 return None,None,cancel,None,phase or 'generating'

@dataclass
class Job:
 client:object
 kwargs:dict
 context:tuple
 budget:float|None
 id:str=field(default_factory=lambda:uuid.uuid4().hex)
 enqueued:float=field(default_factory=time.monotonic)
 started:float|None=None
 done:threading.Event=field(default_factory=threading.Event)
 values:queue.Queue=field(default_factory=queue.Queue)
 result:object=None
 error:Exception|None=None
 abandoned:bool=False
 first:bool=False

 def cancelled(self):return self.context[2] is not None and self.context[2].is_set()

def record(job,event,**fields):
 row={'at':time.time(),'call_id':job.id,'request_id':job.context[0],'event':event,'stage':job.context[4],**fields}
 folder=settings.PROJECT/'.runtime';folder.mkdir(exist_ok=True)
 with LOG_LOCK:
  with (folder/'model-dispatch.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')

def stats(response):
 return {key:getattr(response,key)/1e9 for key in ('total_duration','load_duration','prompt_eval_duration','eval_duration') if isinstance(getattr(response,key,None),(int,float))}

def worker():
 global ACTIVE
 while True:
  with CONDITION:
   while not PENDING:CONDITION.wait()
   job=PENDING.popleft()
   if job.cancelled() or job.abandoned:
    job.error=Cancelled('排队请求已取消，未发起模型调用');job.done.set();record(job,'cancelled_before_dispatch');continue
   ACTIVE=job;job.started=time.monotonic();CONDITION.notify_all()
  record(job,'request_start',queue_seconds=job.started-job.enqueued,model=job.kwargs.get('model'))
  response=None;terminal=False
  try:
   response=job.client.chat(**job.kwargs)
   if job.kwargs.get('stream'):
    for part in response:
     if not job.first:job.first=True;record(job,'first_response',seconds=time.monotonic()-job.started,kind='stream_chunk')
     if not job.cancelled() and not job.abandoned:job.values.put(part)
     if getattr(part,'done',False):terminal=True;record(job,'ollama_stats',**stats(part))
   else:
    terminal=True;record(job,'first_response',seconds=time.monotonic()-job.started,kind='complete_response_first_token_unknown')
    record(job,'ollama_stats',**stats(response));job.result=response
  except Exception as exc:
   job.error=exc;record(job,'transport_error',error_type=type(exc).__name__,server_release='unknown')
  finally:
   if response is not None and hasattr(response,'close'):
    try:response.close()
    except Exception:pass
   record(job,'request_end',execution_seconds=time.monotonic()-job.started,terminal_response=terminal,discarded=job.cancelled() or job.abandoned,server_internal_queue='unknown')
   with CONDITION:ACTIVE=None;job.done.set();CONDITION.notify_all()

def submit(client,kwargs,cancel=None,phase=None,budget=None):
 global WORKER
 job=Job(client,kwargs,capture(cancel,phase),budget)
 with CONDITION:
  waiting=ACTIVE is not None or bool(PENDING);PENDING.append(job)
  if WORKER is None:WORKER=threading.Thread(target=worker,daemon=True,name='local-qwen-fifo');WORKER.start()
  CONDITION.notify_all()
 record(job,'enqueued',waiting=waiting)
 callback=job.context[3]
 if waiting and callback:callback('queued')
 def check():
  if job.cancelled():job.abandoned=True;record(job,'consumer_cancelled',dispatched=job.started is not None,server_release='unknown' if job.started is not None else 'not_dispatched');raise Cancelled('请求已取消')
  if job.started is not None and job.budget is not None and time.monotonic()-job.started>job.budget and not job.done.is_set():
   job.abandoned=True;record(job,'consumer_timeout',execution_budget=job.budget);raise RuntimeError('本地模型执行达到时间上限，未确认的结果不展示')
 def started():
  while job.started is None and not job.done.wait(.05):check()
  check()
  if callback:callback(job.context[4])
 def result():
  started()
  while not job.done.wait(.05):check()
  check()
  if job.error:raise job.error
  return job.result
 def stream():
  try:
   started()
   while True:
    check()
    try:value=job.values.get(timeout=.05)
    except queue.Empty:
     if job.done.is_set():break
     continue
    yield value
   if job.error:raise job.error
  finally:
   if not job.done.is_set():job.abandoned=True
 if kwargs.get('stream'):return stream()
 return result()
