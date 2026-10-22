"""Confirmed calendar creation. Local journal; never sent to cloud or file tools."""
import datetime as dt,json,os,re,threading,uuid
from zoneinfo import ZoneInfo
import settings,native_context,calendar_dates
LOCK=threading.RLock()
INFLIGHT=set()
ROOT=settings.PROJECT/'.calendar-create'
# Routing is owned by the shared structured calendar intent, not phrase-specific rules.
def source_relative_day(text,clock):
    return calendar_dates.from_source(text,clock)

def extract(text,provider,cancel):
    clock=native_context.system_clock()
    prompt='''CALENDAR_CREATE_SCHEMA 只输出JSON，不执行操作。仅用户明确要求添加单个日历事件 intent=create；讨论、查询 intent=none；修改删除 intent=unsupported。提取用户明说的信息，未说明结束或时长时end/duration_minutes均null，由代码使用默认30分钟；不猜日期或上午下午。schema: {"intent":"create|none|unsupported","title":"事件标题","date":"原文日期表达，如下周二、下月5号，或YYYY-MM-DD","start":"HH:MM","end":"HH:MM","end_date":"YYYY-MM-DD","duration_minutes":null,"next_day":false,"all_day":false,"timezone":null,"recurrence":null,"alarm_minutes":null}。周一为一周开始；本周/下周/下下周必须保留星期；本月/下月保留日期。没有上午下午的1至12点保留歧义，不代选。仅提供明确的字段；跨午夜next_day=true；全天all_day=true；请求重复或提醒保留recurrence/alarm_minutes；含糊日期原样保留。完整日期必须包含年，未明确年则用本机当前年但不能把过去日期自动改明年。本机真实时钟：'''+json.dumps(clock,ensure_ascii=False)
    r=provider.chat(model=settings.MODEL,messages=[{'role':'system','content':prompt},{'role':'user','content':text}],stream=False,think=False,options={'num_predict':350,'num_ctx':2048},cancel=cancel)
    try:data=json.loads(r.message.content.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip())
    except (TypeError,ValueError):raise ValueError('未能明确提取日程，请给出标题、完整日期、开始和结束时间') from None
    if not isinstance(data,dict):raise ValueError('日程提取结构无效；尚未创建，请明确日期、事项及开始和结束时间')
    if data.get('intent')=='create':
        source_day=source_relative_day(text,clock)
        if source_day is not None:data['date']=source_day
        if data.get('all_day') is not True:
            calendar_dates.check_start(text,data.get('start'))
            if not calendar_dates.explicit_length(text):data['end']=None;data['duration_minutes']=None
    return data,clock

def local_time(day,value,zone):
    if not isinstance(value,str) or not re.fullmatch(r'\d{2}:\d{2}',value):raise ValueError('请明确开始和结束时间（小时:分钟）')
    try:naive=dt.datetime.combine(day,dt.time.fromisoformat(value))
    except ValueError:raise ValueError('时间不合法，请修改后重新确认') from None
    variants={}
    for fold in (0,1):
        aware=naive.replace(tzinfo=zone,fold=fold)
        if aware.astimezone(dt.timezone.utc).astimezone(zone).replace(tzinfo=None)==naive:variants[aware.utcoffset()]=aware
    if not variants:raise ValueError('该本地时间因夏令时不存在，请选择其他时间')
    if len(variants)>1:raise ValueError('该本地时间因夏令时重复，请明确选择其他无歧义时间')
    return next(iter(variants.values()))

def validate(data,clock):
    if data.get('intent')=='none':return None
    if data.get('intent')!='create':raise ValueError('本轮仅支持创建并核对单次日程，不支持修改或删除')
    if data.get('recurrence'):raise ValueError('本轮尚不支持创建重复日程')
    if data.get('alarm_minutes') is not None:raise ValueError('本轮尚不支持日历提醒或助手主动提醒；请去掉提醒要求后重新提交')
    zone=ZoneInfo(clock['timezone']);now=dt.datetime.fromisoformat(clock['now'])
    if data.get('timezone') not in (None,'',clock['timezone']):raise ValueError('请按本机时区 '+clock['timezone']+' 明确时间后重新提交')
    def date(value):return calendar_dates.resolve(value,clock)
    title=data.get('title')
    if not isinstance(title,str) or not 1<=len(title.strip())<=200:raise ValueError('请明确事件标题（1～200字）')
    day=date(data.get('date'));all_day=data.get('all_day') is True
    warnings=[]
    if all_day:
        endday=date(data['end_date']) if data.get('end_date') else day+dt.timedelta(days=1)
        start=local_time(day,'00:00',zone);end=local_time(endday,'00:00',zone)
        if day<now.date():raise ValueError('日期已经过去，请重新明确日期，不会自动改到明天')
        if day==now.date():warnings.append('今日全天事件已经开始；确认即按今日全天创建。')
    else:
        start=local_time(day,data.get('start'),zone)
        if data.get('end'):
            endday=date(data['end_date']) if data.get('end_date') else day+dt.timedelta(days=1 if data.get('next_day') is True else 0)
            end=local_time(endday,data['end'],zone)
        elif type(data.get('duration_minutes')) is int and data['duration_minutes']>0:
            end=(start.astimezone(dt.timezone.utc)+dt.timedelta(minutes=data['duration_minutes'])).astimezone(zone)
        else:
            if data.get('duration_minutes') is not None:raise ValueError('时长不合法，请明确正整数分钟；尚未创建')
            end=(start.astimezone(dt.timezone.utc)+dt.timedelta(minutes=30)).astimezone(zone)
            warnings.append('默认30分钟，可修改')
        if start<=now:raise ValueError('开始时间已经过去，请重新明确日期时间，不会自动改到明天')
    if end.astimezone(dt.timezone.utc)<=start.astimezone(dt.timezone.utc):raise ValueError('结束必须晚于开始；跨午夜请明确次日结束')
    if (end-start).days>366:raise ValueError('日期跨度过长，请缩小为单次明确日程')
    def label(d):return d.strftime('%Y-%m-%d')+' '+['星期一','星期二','星期三','星期四','星期五','星期六','星期日'][d.weekday()]
    return {'title':title.strip(),'start':start.isoformat(),'end':end.isoformat(),'timezone':clock['timezone'],'all_day':all_day,'date_label':label(start)+' → '+label(end),'alarms':[],'warnings':warnings}

def bridge(payload,cancel):return native_context.bridge_request(payload,cancel,timeout=getattr(settings,'CALENDAR_BRIDGE_TIMEOUT',20))
class CalendarUnavailable(ValueError):
    def __init__(self,status):
        self.status=status;super().__init__(native_context.MESSAGES.get(status,'本机日历服务未完成请求。'))

def require_ok(row):
    if row.get('status')!='ok':raise CalendarUnavailable(row.get('status') if row.get('status') in native_context.MESSAGES else 'unavailable')

def preview(text,provider,cancel):
    data,clock=extract(text,provider,cancel)
    return preview_data(data,clock,cancel)

def preview_data(data,clock,cancel):
    event=validate(data,clock)
    if event is None:return None
    catalog=bridge({'action':'catalog','event':event},cancel)
    calendars=catalog.get('calendars',[]) if catalog.get('status')=='ok' else []
    status='ready' if calendars else catalog.get('status') if catalog.get('status')!='ok' else 'no_writable_calendar'
    message='' if calendars else catalog.get('message') or native_context.MESSAGES.get(status,'没有可写日历，请先选择可写日历；尚未创建')
    return dict(event,id=uuid.uuid4().hex,calendars=calendars,calendar_id=next((c['id'] for c in calendars if c.get('default')),calendars[0]['id']) if calendars else None,duplicates=catalog.get('duplicates',[]),state='pending',calendar_status=status,message=message)

def save(p):
    ROOT.mkdir(exist_ok=True,mode=0o700);os.chmod(ROOT,0o700)
    file=ROOT/(p['id']+'.json');temp=file.with_suffix('.tmp')
    with open(temp,'w',opener=lambda path,flags:os.open(path,flags,0o600)) as f:json.dump(p,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
    os.replace(temp,file)
def load(pid,sid):
    if not isinstance(pid,str) or not re.fullmatch('[a-f0-9]{32}',pid):raise ValueError('预览无效，请重新提交')
    try:p=json.loads((ROOT/(pid+'.json')).read_text())
    except (OSError,ValueError):raise ValueError('预览已失效，请重新提交') from None
    if p.get('session_id')!=sid:raise ValueError('日程预览不属于当前闲聊会话')
    return p

def publish(p,sid,epoch):
    with LOCK:p.update(session_id=sid,epoch=epoch);save(p)
    return public(p)
def public(p):return {k:v for k,v in p.items() if k not in ('session_id','epoch','result')}
def invalidate(sid):
    if not ROOT.exists():return
    with LOCK:
        for f in ROOT.glob('*.json'):
            p=json.loads(f.read_text())
            if p.get('session_id')==sid and p.get('state')=='pending':p['state']='cancelled';save(p)

def action(body,c,session_id,cancel=False):
    # Calendar lock atomically reserves pending previews; native I/O runs outside locks.
    with LOCK:
        p=load(body.get('preview_id'),session_id)
        if p['state']=='verified':return p['result']
        if cancel:
            if p['state'] in ('writing','uncertain'):return {'status':'writing','message':'写入已经开始，不能声明已取消；请核对原操作'}
            if p['state']=='pending':p['state']='cancelled';save(p)
            return {'status':p['state']}
        if p['state'] not in ('writing','uncertain') and (p['state']!='pending' or c.epoch!=p['epoch']):raise ValueError('预览已取消或被新请求替代，请重新提交')
        if p['state']=='writing' and p['id'] in INFLIGHT:return {'status':'writing','message':'正在处理同一次确认，请勿重复创建'}
        if body.get('confirm') is not True:raise ValueError('必须明确确认后才能创建')
        event={k:p[k] for k in ('title','start','end','timezone','all_day','alarms')}
        calendar_id=body.get('calendar_id',p['calendar_id'])
        if calendar_id not in [x['id'] for x in p['calendars']]:raise ValueError('目标日历不在可写列表')
        if calendar_id!=p['calendar_id']:raise ValueError('目标日历已修改，请先更新预览并重新确认')
        if p['duplicates'] and body.get('accept_duplicate') is not True:return {'status':'duplicate_confirmation_required','message':'可能重复；请勾选仍要创建后再次确认'}
        reconcile=p['state'] in ('uncertain','writing')
        if not reconcile and dt.datetime.fromisoformat(p['start'])<=dt.datetime.now(dt.timezone.utc) and not p['all_day']:raise ValueError('预览开始时间已经过去，请重新提交')
        p['state']='writing';save(p);INFLIGHT.add(p['id'])
    try:
        result=bridge({'action':'reconcile' if reconcile else 'create','operation_id':p['id'],'event':event,'calendar_id':calendar_id,'accept_duplicate':body.get('accept_duplicate') is True},threading.Event())
    except Exception:
        result={'status':'uncertain','message':'原操作核对失败，不会自动再次写入'}
    with LOCK:
        INFLIGHT.discard(p['id'])
        if result.get('status')=='verified':
            returned=result.get('event',{})
            try:
                matches=bool(result.get('event_id')) and returned.get('title')==event['title'] and returned.get('all_day')==event['all_day'] and returned.get('timezone')==event['timezone'] and not returned.get('alarms') and all(dt.datetime.fromisoformat(returned[k])==dt.datetime.fromisoformat(event[k]) for k in ('start','end'))
            except (ValueError,TypeError,KeyError):matches=False
            if not matches:result={'status':'uncertain','message':'读回结果与确认预览不一致，未宣称成功；请核对原操作，不会重新写入'}
        if result.get('status') in ('timeout','unavailable'):result={'status':'uncertain','message':'写入结果尚不确定；再次核对只读取原操作，不会再次写入'}
        if not reconcile and result.get('status') in ('authorization_required','denied','revoked','restricted','write_only'):
            # Native entry rejected before EventKit mutation. Never use this path
            # for an uncertain/reconcile request that may already have written.
            p['state']='cancelled';save(p)
            p=dict(p,id=uuid.uuid4().hex,state='pending',calendars=[],calendar_id=None,
                   calendar_status=result['status'],message=result.get('message') or native_context.MESSAGES[result['status']])
            p.pop('result',None);save(p)
            return {'status':'preview','calendar_preview':public(p)}
        p['state']=result.get('status','uncertain');p['result']=result;save(p)
    return result

def revise(body,c,sid):
    with LOCK:
        p=load(body.get('preview_id'),sid)
        if p['state']!='pending' or p['epoch']!=c.epoch:raise ValueError('预览已失效')
        cid=body.get('calendar_id')
        if cid not in [x['id'] for x in p['calendars']]:raise ValueError('目标日历不可写')
        p['state']='cancelled';save(p);p=dict(p,id=uuid.uuid4().hex,state='pending',calendar_id=cid);save(p)
        return {'status':'preview','calendar_preview':public(p)}

def refresh(body,c,sid):
    # Each retry asks the existing native bridge for CURRENT status. No denial cache,
    # no inference call, no create/reconcile. Epoch guards cover correction/reset.
    with LOCK:
        p=load(body.get('preview_id'),sid)
        if c.request or p['state']!='pending' or p['epoch']!=c.epoch:raise ValueError('预览已失效或任务处理中，请使用当前预览')
        epoch=p['epoch'];event={k:p[k] for k in ('title','start','end','timezone','all_day','alarms')}
    row=bridge({'action':'catalog','event':event},threading.Event())
    with LOCK:
        old=load(p['id'],sid)
        if old['state']!='pending' or c.epoch!=epoch:raise ValueError('刷新期间预览已失效，未写入')
        calendars=row.get('calendars',[]) if row.get('status')=='ok' else []
        status='ready' if calendars else row.get('status') if row.get('status')!='ok' else 'no_writable_calendar'
        old['state']='cancelled';save(old)
        p.update(id=uuid.uuid4().hex,calendars=calendars,calendar_status=status,
                 message='' if calendars else row.get('message') or native_context.MESSAGES.get(status,'没有可写日历；尚未创建'),
                 calendar_id=next((x['id'] for x in calendars if x.get('default')),calendars[0]['id']) if calendars else None,
                 duplicates=row.get('duplicates',[]))
        save(p);return {'status':'preview','calendar_preview':public(p)}
