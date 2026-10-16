"""Durable shared rolling budget with separately exhausted developer allowance."""
import fcntl,json,os,time,threading
from contextlib import contextmanager
from datetime import datetime,timezone
import settings
PATH=settings.PROJECT/'.bubble-web-usage.json'
LOCK_PATH=settings.PROJECT/'.bubble-web-usage.lock'
_CONTEXT=threading.local()

def limit():
    n=int(os.environ.get('BAIDU_LOCAL_DAILY_LIMIT','30'))
    if not 1<=n<=100000:raise ValueError('本地联网上限配置不正确')
    return min(n,30)
def test_limit():
    n=int(os.environ.get('BAIDU_TEST_LIMIT','2'))
    if not 1<=n<=100:raise ValueError('累计测试上限配置不正确')
    return n
@contextmanager
def daily_request():
    """Trusted interactive handler only; never selected from a client purpose flag."""
    previous=getattr(_CONTEXT,'purpose',None);_CONTEXT.purpose='daily'
    try:yield
    finally:_CONTEXT.purpose=previous
@contextmanager
def ledger():
    with LOCK_PATH.open('a') as lock:
        os.chmod(LOCK_PATH,0o600);fcntl.flock(lock,fcntl.LOCK_EX)
        try:
            data=json.loads(PATH.read_text()) if PATH.exists() else {'attempts':[],'test_attempts':0}
            if not isinstance(data,dict) or not isinstance(data.get('attempts'),list) or any(not isinstance(x,(int,float)) for x in data['attempts']):raise ValueError('联网计数文件异常，已阻止联网；请保留文件检查')
            corrections=data.get('budget_adjustments',[])
            if not isinstance(corrections,list) or any(not isinstance(c,dict) or not isinstance(c.get('excluded_attempt_indices'),list) or any(type(i)!=int or i<0 or i>=len(data['attempts']) for i in c['excluded_attempt_indices']) for c in corrections):raise ValueError('联网校正记录异常，已阻止联网')
            yield data
        finally:fcntl.flock(lock,fcntl.LOCK_UN)
def _used(data,now):
    excluded={i for c in data.get('budget_adjustments',[]) for i in c['excluded_attempt_indices']}
    return sum(t>now-86400 and i not in excluded for i,t in enumerate(data['attempts']))
def _save(data):
    tmp=PATH.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False));os.chmod(tmp,0o600)
    with tmp.open('rb') as f:os.fsync(f.fileno())
    os.replace(tmp,PATH)
    directory=os.open(PATH.parent,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)
def correct_once(correction_id,target,reason):
    """Explicit offline maintenance action, never invoked by app startup/status."""
    if not correction_id or not reason or type(target)!=int or target<0:raise ValueError('校正参数不正确')
    with ledger() as d:
        adjustments=d.setdefault('budget_adjustments',[])
        old=next((c for c in adjustments if c.get('id')==correction_id),None)
        if old:return old
        now=time.time();used=_used(d,now)
        if target>used:raise ValueError('不能通过校正新增虚构请求')
        excluded={i for c in adjustments for i in c['excluded_attempt_indices']}
        active=[i for i,t in enumerate(d['attempts']) if t>now-86400 and i not in excluded]
        entry={'id':correction_id,'at_utc':datetime.now(timezone.utc).isoformat(),'reason':reason,'previous_used':used,'corrected_used':target,'excluded_attempt_indices':active[:used-target]}
        adjustments.append(entry);_save(d);return entry

def status():
    with ledger() as d:
        used=_used(d,time.time());cap=limit()
        return {'used':used,'limit':cap,'limit_reached':used>=cap,'test_used':d.get('test_attempts',0),'test_limit':test_limit(),'test_limit_reached':d.get('test_attempts',0)>=test_limit(),'window':'rolling_24h','official_reset_timezone':'未核实'}
def dispatch(send,cancel):
    # Reserve/persist under the same lock immediately before every actual send.
    with ledger() as d:
        if cancel.is_set():raise InterruptedError('已取消，未发送联网请求')
        now=time.time()
        if _used(d,now)>=limit():raise ValueError('今日上限已达（本助手保守按近24小时计数）')
        purpose=getattr(_CONTEXT,'purpose',None) or ('test' if os.environ.get('BAIDU_TEST_MODE','true').lower()=='true' else 'daily')
        test=purpose=='test'
        if test and d.get('test_attempts',0)>=test_limit():raise ValueError(f'累计测试预算已达{test_limit()}次，未发送请求')
        d['attempts'].append(now)
        if test:d['test_attempts']=d.get('test_attempts',0)+1
        d.setdefault('request_kinds',[]).append({'attempt_index':len(d['attempts'])-1,'purpose':purpose})
        _save(d)
        return send()
