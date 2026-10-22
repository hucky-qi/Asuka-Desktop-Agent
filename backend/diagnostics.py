"""Request timings/events; logs contain IDs, counts and durations, never user text."""
import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
import backend.settings as settings
import backend.assistant.task_status as task_status
local = threading.local()

class Trace:
    def __init__(self, request_id, emit=None, task_token=None):
        self.id = request_id
        self.task_token = task_token if task_token is not None else task_status.begin(request_id, "file")
        self.owns_task = task_token is None
        self.emit_callback = emit
        self.started = time.perf_counter()
        self.sequence = 0
        self.timings = {}
        self.model_calls = []
        self.tool_calls = 0
        self.first_cards = None
        self.coverage = []
        self.retrieval = []
        self.emit_lock = threading.RLock()
        self.phase = 'queued'
    def emit(self, kind, **data):
        with self.emit_lock:
            self.sequence += 1
            payload = {'type':kind, 'request_id':self.id, 'sequence':self.sequence, **data}
            if self.emit_callback:
                self.emit_callback(payload)
    def stage(self, phase):
        self.phase = phase
        if phase in task_status.PHASES:task_status.stage(self.task_token,phase)
        self.emit('stage', state=phase)
    def tool(self, event):
        if 'results' in event and self.first_cards is None:
            self.first_cards = time.perf_counter()-self.started
        self.coverage.append({k:event[k] for k in ('tool','total','scanned','partial','truncated','indexed_scope_files','stale_files','unindexed_files','parsed_files','scanned_files') if k in event})
        self.emit('tool', event=event)
    def summary(self):
        return {'request_id':self.id,'timings':self.timings,'model_calls':self.model_calls,'tool_calls':self.tool_calls,
                'first_cards_seconds':self.first_cards,'total_seconds':time.perf_counter()-self.started,'coverage':self.coverage,'retrieval':self.retrieval}
    def save(self, outcome):
        if self.owns_task:task_status.finish(self.task_token,outcome)
        data={**self.summary(),'outcome':outcome}
        folder=settings.PROJECT/'.runtime';folder.mkdir(exist_ok=True)
        with (folder/'performance.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(data,ensure_ascii=False)+'\n')

@contextmanager
def span(name, phase=None):
    trace=getattr(local,'trace',None)
    if trace and phase:trace.stage(phase)
    started=time.perf_counter()
    try:yield
    finally:
        if trace:trace.timings[name]=trace.timings.get(name,0)+time.perf_counter()-started

def stage(phase):
    trace=getattr(local,'trace',None)
    if trace:trace.stage(phase)

def record(branch, **facts):
    """Only technical counts/reasons; callers never pass query or body text."""
    trace=getattr(local,'trace',None)
    if trace:trace.retrieval.append({'branch':branch,**facts})

@contextmanager
def progress(trace):
    """Report long-running work without claiming partial cards are complete."""
    stop=threading.Event()
    def monitor():
        while not stop.wait(5):
            try:
                trace.emit('progress',state=trace.phase,elapsed_seconds=int(time.perf_counter()-trace.started),
                    message='正在排队等待本地模型，可停止当前请求' if trace.phase=='queued' else '仍在检索/核验相关性，可停止当前请求' if trace.phase=='searching' else '仍在处理当前请求，可停止')
            except Exception:break  # Cancellation/disconnect ends progress too.
    thread=threading.Thread(target=monitor,daemon=True);thread.start()
    try:yield
    finally:stop.set();thread.join(1)

class Events(list):
    def __init__(self, validator=None):
        super().__init__();self.validator=validator
    def append(self, event):
        if self.validator:self.validator(event)
        super().append(event)
        trace=getattr(local,'trace',None)
        if trace:trace.tool(event)
