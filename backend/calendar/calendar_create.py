"""Confirmed calendar creation. Local journal; never sent to cloud or file tools."""
import datetime as dt,json,os,re,threading,uuid
from zoneinfo import ZoneInfo
import backend.settings as settings, backend.native_context as native_context, backend.calendar.calendar_dates as calendar_dates
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

def validate(data,clock,relative_time=None):
    if data.get('intent')=='none':return None
    if data.get('intent')!='create':raise ValueError('本轮仅支持创建并核对单次日程，不支持修改或删除')
    if data.get('recurrence'):raise ValueError('本轮尚不支持创建重复日程')
    if data.get('alarm_minutes') is not None:raise ValueError('本轮尚不支持日历提醒或助手主动提醒；请去掉提醒要求后重新提交')
    zone=ZoneInfo(relative_time['timezone'] if relative_time else clock['timezone']);now=dt.datetime.fromisoformat(clock['now'])
    if relative_time:now=max(now,dt.datetime.fromisoformat(native_context.system_clock()['now']))
    if data.get('timezone') not in (None,'',getattr(zone,'key',clock['timezone'])):raise ValueError('请按本机时区 '+clock['timezone']+' 明确时间后重新提交')
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
        start=calendar_dates.relative_target(relative_time) if relative_time else local_time(day,data.get('start'),zone)
        if relative_time and (day!=start.date() or data.get('start')!=start.strftime('%H:%M')):raise ValueError('目标时刻与相对时间记录不一致，未执行。')
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
    return {'title':title.strip(),'start':start.isoformat(),'end':end.isoformat(),'timezone':getattr(zone,'key',clock['timezone']),**({'relative_time':relative_time} if relative_time else {}),'all_day':all_day,'date_label':label(start)+' → '+label(end),'alarms':[],'warnings':warnings}

def bridge(payload,cancel):return native_context.bridge_request(payload,cancel,timeout=getattr(settings,'CALENDAR_BRIDGE_TIMEOUT',20))
class CalendarUnavailable(ValueError):
    def __init__(self,status):
        self.status=status;super().__init__(native_context.MESSAGES.get(status,'本机日历服务未完成请求。'))

def require_ok(row):
    if row.get('status')!='ok':raise CalendarUnavailable(row.get('status') if row.get('status') in native_context.MESSAGES else 'unavailable')

def preview(text,provider,cancel):
    data,clock=extract(text,provider,cancel)
    return preview_data(data,clock,cancel)

def preview_data(data,clock,cancel,relative_time=None):
    event=validate(data,clock,relative_time)
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
def reminder_choice(value,p):
    if not isinstance(value,dict) or set(value)!={'enabled','minutes'} or type(value['enabled']) is not bool:raise ValueError('请明确是否同时提醒，并填写整数分钟。')
    if not value['enabled']:return {'enabled':False,'minutes':None}
    lead=value['minutes']
    if type(lead) is not int or not 0<=lead<=10080:raise ValueError('提醒提前量只接受0至10080的整数分钟，0表示开始时提醒。')
    now=dt.datetime.fromisoformat(native_context.system_clock()['now']);start=dt.datetime.fromisoformat(p['start'])
    if p['all_day']:raise ValueError('全天事件暂不支持此助手提醒，日程预览已保留。')
    if start<=now or start-dt.timedelta(minutes=lead)<now:raise ValueError('所选提醒时间已经过去，请调整提前量；事件开始时间和预览已保留。')
    return {'enabled':True,'minutes':lead}
def public(p):
    import backend.calendar.calendar_reminders as reminders
    value={k:v for k,v in p.items() if k not in ('session_id','epoch','result','confirmed_reminder','reminder_commit')}
    now=native_context.system_clock()['now'];remaining=(dt.datetime.fromisoformat(p['start'])-dt.datetime.fromisoformat(now)).total_seconds()
    lead=(p.get('assistant_reminder') or {}).get('lead_minutes',30)
    value['reminder_options']={'selected':bool(p.get('assistant_reminder')),'minutes':lead,'enabled':reminders.enabled(),'server_now':now,'max_minutes':min(10080,max(-1,int(remaining//60))),'valid':not p['all_day'] and remaining>0 and type(lead) is int and 0<=lead<=10080 and lead*60<=remaining}
    return value
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
        choice=reminder_choice(body.get('assistant_reminder',{'enabled':False,'minutes':None}),p) if not reconcile else p.get('confirmed_reminder',{'enabled':False,'minutes':None})
        if not reconcile:p['confirmed_reminder']=choice
        if not reconcile and dt.datetime.fromisoformat(p['start'])<=dt.datetime.fromisoformat(native_context.system_clock()['now']) and not p['all_day']:raise ValueError('预览开始时间已经过去，请重新提交')
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


def reminder_receipt(receipt):
    # Old read-only eligibility rejection is not an eternal failure or a new grant.
    import copy
    r=copy.deepcopy(receipt)
    if r.get('status')=='not_set' and r.get('code')=='calendar_unverified':r.update(message='日程已保存；提醒尚未设置。',detail='本机提醒现已与日历写权限分离。可重新检查原事件；不会创建副本或自动启用提醒。',code='recheck',retryable=True)
    return r

def finish_reminder(data,body,c,task):
    """A separate durable receipt, only after verified calendar create/readback."""
    import copy
    import backend.calendar.calendar_reminders as reminders
    with LOCK:
        p=load(body.get('preview_id'),task.sid);choice=p.get('confirmed_reminder',{'enabled':False,'minutes':None})
        if not choice['enabled']:return dict(data,assistant_reminder={'status':'not_requested','message':'本次未设置助手提醒。'})
        previous=p.get('reminder_commit')
        if previous and previous['status'] not in ('saving','unknown'):return dict(data,assistant_reminder=reminder_receipt(previous))
        if previous:
            with reminders.store() as state:
                op=state['operations'].get(p['id']);saved=state['items'].get((op or {}).get('id'))
            receipt={'status':'saved','due':saved['due'],'lead_minutes':saved['lead_minutes'],'message':'助手提醒计划已保存。'} if saved else {'status':'unknown','message':'日历已创建；提醒保存结果待核对，不会盲目重试。'}
            p['reminder_commit']=receipt;save(p);return dict(data,assistant_reminder=receipt)
        p['reminder_commit']={'status':'saving','message':'提醒保存结果待核对。'};save(p)
    try:
        task.check();binding_choice(choice,p)
        if not reminders.enabled():raise reminders.ReminderEligibilityError('switch_off','助手提醒总开关关闭，请从现有设置开启；日历已保存，不会自动开启。',retryable=True)
        fresh=reminders.read_event(data['event_id'],p['calendar_id'],task.cancel)
        if not fresh or any(fresh[k]!=p[k] for k in ('title','start','end')):raise ValueError('事件已变化或不存在；日历创建回执保留，提醒尚未设置。')
        task.check();binding_choice(choice,p)
        with LOCK:
            p=load(p['id'],task.sid);p['reminder_event_version']=fresh['version'];save(p)
        row=reminders.set_plan(fresh,choice['minutes'],task.check,p['id'])
        receipt={'status':'saved','due':row['due'],'lead_minutes':row['lead_minutes'],'message':'助手提醒计划已保存；将在到点时尝试投递。'}
    except ValueError as error:
        safe=__import__('backend.assistant.bubble_chat',fromlist=['*']).user_error(error)['error'];code=getattr(error,'code',None) or ('permission' if getattr(error,'status',None) in ('denied','revoked','restricted','authorization_required','authorization_pending','write_only') else 'recheck')
        receipt={'status':'not_set','message':'日程已保存；提醒尚未设置。','detail':safe,'code':code,'permission_status':getattr(error,'status',None),'retryable':getattr(error,'retryable',True)}
        if getattr(error,'event_version',None):
            with LOCK:p=load(p['id'],task.sid);p['reminder_event_version']=error.event_version;save(p)
    except Exception:receipt={'status':'unknown','message':'日历已创建；提醒保存结果待核对，不会盲目重试。'}
    with LOCK:
        p=load(p['id'],task.sid);p['reminder_commit']=receipt;save(p)
    return dict(data,assistant_reminder=receipt)


def reminder_recovery(body,c,sid,task):
    """Read/retry only the verified original identity; never invokes calendar create."""
    import copy,uuid
    import backend.calendar.calendar_reminders as reminders
    action=body.get('action','retry')
    if action not in ('status','retry','confirm','authorize'):raise ValueError('提醒恢复操作不支持。')
    if action=='status':
        rows=[]
        for f in sorted(ROOT.glob('*.json'),key=lambda f:f.stat().st_mtime,reverse=True)[:128]:
            p=json.loads(f.read_text())
            if p.get('session_id')==sid and p.get('state')=='verified' and p.get('confirmed_reminder',{}).get('enabled') and (p.get('reminder_commit') or {}).get('status')!='saved':rows.append({'preview_id':p['id'],'title':p['title'],'start':p['start'],'minutes':p['confirmed_reminder']['minutes'],'assistant_reminder':reminder_receipt(p.get('reminder_commit') or {'status':'not_set','message':'日程已保存；提醒尚未设置。','retryable':True})})
            if len(rows)==5:break
        return {'items':rows}
    if body.get('explicit') is not True:raise ValueError('请主动点击重新检查或确认设置提醒。')
    with LOCK:
        p=load(body.get('preview_id'),sid)
        if p['state']!='verified' or not p.get('result',{}).get('event_id') or not p.get('confirmed_reminder',{}).get('enabled'):raise ValueError('没有已核验创建且明确要求提醒的原操作；不会创建新日程。')
        result=copy.deepcopy(p['result']);choice=copy.deepcopy(p['confirmed_reminder']);prior=p.get('reminder_commit') or {}
    def done(receipt):
        with LOCK:
            task.check();current=load(p['id'],sid);current['reminder_commit']=receipt;save(current)
        return dict(result,preview_id=p['id'],assistant_reminder=receipt)
    if prior.get('status') in ('saved','saving','unknown'):
        if prior['status']=='saved':return dict(result,preview_id=p['id'],assistant_reminder=prior)
        # Existing durable operation record is the only success evidence.
        with reminders.store() as d:
            op=d['operations'].get(p['id']);row=d['items'].get((op or {}).get('id'))
        if row:return done({'status':'saved','due':row['due'],'lead_minutes':row['lead_minutes'],'message':'助手提醒计划已保存。'})
        return done({'status':'unknown','message':'日程已保存；提醒保存结果待核对。','detail':'没有可靠的成功回执，不会盲目重做。','retryable':False})
    try:
        task.check()
        if action=='authorize':
            # Reuse the existing user-triggered permission/catalog path, read-only.
            permission=bridge({'action':'permission_authorize','explicit_user_action':True},task.cancel);
            if permission.get('status') not in ('allowed','ok'):require_ok(permission)
        if not reminders.enabled():return done({'status':'not_set','message':'日程已保存；提醒尚未设置。','detail':'助手提醒总开关关闭，请从本机日程提醒设置启用后返回；不会自动开启。','code':'switch_off','retryable':True})
        fresh=reminders.read_event(result['event_id'],p['calendar_id'],task.cancel)
        if not fresh:return done({'status':'not_set','message':'日程已保存；原事件已不可读取，提醒未设置。','detail':'原事件可能已删除或移动，请重新查询并选择具体事件；不按标题绑定其他事件。','code':'not_found','retryable':False})
        target=dict(p,start=fresh['start'],all_day=False);binding_choice(choice,target);task.check()
        changed=any(p[k]!=fresh[k] for k in ('title','start','end')) or p.get('reminder_event_version')!=fresh['version'] or p['epoch']!=c.epoch
        if action=='confirm':
            proof=p.get('reminder_recovery') or {}
            if proof.get('id')!=body.get('recovery_id') or proof.get('epoch')!=c.epoch or proof.get('event_version')!=fresh['version'] or proof.get('choice')!=choice or dt.datetime.fromisoformat(proof.get('expires','1970-01-01T00:00:00+00:00'))<dt.datetime.fromisoformat(native_context.system_clock()['now']):raise ValueError('提醒恢复预览已变化或过期，请重新检查；没有保存。')
        elif changed:
            proof={'id':uuid.uuid4().hex,'epoch':c.epoch,'event_version':fresh['version'],'choice':choice,'expires':(dt.datetime.fromisoformat(native_context.system_clock()['now'])+dt.timedelta(minutes=5)).isoformat()}
            with LOCK:task.check();current=load(p['id'],sid);current['reminder_recovery']=proof;save(current)
            return dict(result,preview_id=p['id'],assistant_reminder={'status':'needs_confirmation','message':'日程已保存；请核对这项提醒。','detail':'原确认或事件版本已变化，仅确认提醒，不会再次创建日历。','recovery_id':proof['id'],'original':{k:p[k] for k in ('title','start','end')},'current':{k:fresh[k] for k in ('title','start','end')},'lead_minutes':choice['minutes'],'retryable':True})
        # Same event/parameters under a still-valid confirmation, or explicit new proof.
        with LOCK:
            task.check();current=load(p['id'],sid);current['reminder_commit']={'status':'saving'};save(current)
        row=reminders.set_plan(fresh,choice['minutes'],task.check,p['id'])
        return done({'status':'saved','message':'助手提醒已保存。','due':row['due'],'lead_minutes':row['lead_minutes']})
    except ValueError as error:
        task.check();code=getattr(error,'code',None) or ('permission' if getattr(error,'status',None) in ('denied','revoked','restricted','authorization_required','authorization_pending','write_only') else 'recheck')
        receipt={'status':'not_set','message':'日程已保存；提醒尚未设置。','detail':__import__('backend.assistant.bubble_chat',fromlist=['*']).user_error(error)['error'],'code':code,'permission_status':getattr(error,'status',None),'retryable':getattr(error,'retryable',True)}
        return done(receipt)

def binding_choice(choice,p):
    try:return reminder_choice(choice,p)
    except ValueError as error:
        from backend.calendar.calendar_reminders import ReminderEligibilityError
        raise ReminderEligibilityError('expired_or_invalid',str(error),retryable=False) from None
