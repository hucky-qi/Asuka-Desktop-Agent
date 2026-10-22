"""Durable shared rolling budget with separately exhausted developer allowance."""
import fcntl,json,os,time,threading,hashlib
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime,timezone
import settings
PATH=settings.PROJECT/'.bubble-web-usage.json'
LOCK_PATH=settings.PROJECT/'.bubble-web-usage.lock'
_CONTEXT=threading.local()
ROUND_PATH=settings.PROJECT/'.runtime/web-acceptance.json'
def _round():
    context=getattr(_CONTEXT,'acceptance',None)
    if context is None:return None
    p=json.loads(ROUND_PATH.read_text())
    if p.get('session_sha256')!=context['session_sha256']:raise ValueError('本次验收会话配置已变化，未发送')
    if p.get('max_searches')!=1:raise ValueError('单次联网验收许可配置无效，未发送')
    for key in ('ledger','deepseek_policy'):
        path=Path(p[key]).resolve()
        if not path.is_relative_to((settings.PROJECT/'acceptance').resolve()):raise ValueError('验收记录位置无效，未发送')
    return p
def _check_round(p):
    c=_CONTEXT.acceptance
    if not p.get('enabled') or time.time()>p['expires_at']:raise ValueError('本轮联网验收许可已关闭或过期，未发送')
    if p.get('messages',{}).get(c['question_sha256']) is not c['web_enabled']:raise ValueError('请求不在本轮冻结验收范围，未发送')
def cloud_policy():
    p=_round()
    if p is None:return None
    _check_round(p)
    return p['deepseek_policy']
def _reserve_round(p):
    from cloud_test_budget import locked
    _check_round(p)
    with locked(p['ledger']) as d:
        if len(d['requests'])>=1:raise ValueError('本轮唯一百度尝试已使用，未重复发送')
        d['requests'].append({'run_id':p['run_id'],'request_id':_CONTEXT.acceptance['request_id'],'at':time.time(),'status':'reserved','model_or_service':'baidu_web_search'})
def record_round(http_status=None,data=None,headers=None,error_type=None):
    p=_round()
    if p is None:return
    from cloud_test_budget import locked
    data=data if isinstance(data,dict) else {}
    # This receiver exists only for the explicitly bound public acceptance session.
    result={'http_status':http_status,'code':data.get('code',data.get('error_code')),'message':str(data.get('message',data.get('error_msg',''))),'request_id':data.get('request_id',data.get('requestId')) or ((headers.get('x-bce-request-id') or headers.get('x-request-id')) if headers else None),'error_type':error_type}
    if isinstance(data.get('references'),list):result['references']=data['references']
    raw=json.dumps(result,ensure_ascii=False)
    for name in ('BAIDU_API_KEY','BAIDU_APPBUILDER_API_KEY'):
        key=os.environ.get(name)
        if key:raw=raw.replace(key,'[REDACTED]')
    with locked(p['ledger']) as d:
        if d['requests'] and d['requests'][-1]['request_id']==_CONTEXT.acceptance['request_id'] and not d['requests'][-1].get('completed_at'):
            d['requests'][-1].update(status='response' if http_status else 'failed',response=json.loads(raw),completed_at=time.time())

def limit():
    n=int(os.environ.get('BAIDU_LOCAL_DAILY_LIMIT','30'))
    if not 1<=n<=100000:raise ValueError('本地联网上限配置不正确')
    return min(n,30)
def test_limit():
    n=int(os.environ.get('BAIDU_TEST_LIMIT','2'))
    if not 1<=n<=100:raise ValueError('累计测试上限配置不正确')
    return n
@contextmanager
def daily_request(session_id=None,question=None,web_enabled=None,request_id=None):
    """Trusted handler identity; no client 'purpose' can enable test permission."""
    previous=getattr(_CONTEXT,'purpose',None);old=getattr(_CONTEXT,'acceptance',None);_CONTEXT.purpose='daily';_CONTEXT.acceptance=None
    if session_id and ROUND_PATH.exists():
        p=json.loads(ROUND_PATH.read_text());digest=hashlib.sha256(session_id.encode()).hexdigest()
        if p.get('session_sha256')==digest:_CONTEXT.acceptance={'session_sha256':digest,'question_sha256':hashlib.sha256(question.encode()).hexdigest(),'web_enabled':web_enabled is True,'request_id':request_id}
    try:yield
    finally:_CONTEXT.purpose=previous;_CONTEXT.acceptance=old
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
        round_policy=_round()
        process_policy=os.environ.get('BAIDU_TEST_POLICY')
        if process_policy and (round_policy is None or Path(process_policy).resolve()!=ROUND_PATH.resolve()):raise ValueError('开发测试进程未绑定有效单次许可，未发送请求')
        if round_policy is not None:_reserve_round(round_policy)
        test=purpose=='test' and round_policy is None
        if test and d.get('test_attempts',0)>=test_limit():raise ValueError(f'累计测试预算已达{test_limit()}次，未发送请求')
        d['attempts'].append(now)
        if test:d['test_attempts']=d.get('test_attempts',0)+1
        kind={'attempt_index':len(d['attempts'])-1,'purpose':purpose}
        if round_policy is not None:kind['development_run']=round_policy['run_id']
        d.setdefault('request_kinds',[]).append(kind)
        _save(d)
        return send()
