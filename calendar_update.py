"""Text-driven, explicitly confirmed updates of one existing local calendar identity."""
import datetime as dt, json, re, threading, uuid, copy
from zoneinfo import ZoneInfo
import settings, native_context, calendar_create as create
LOCK=threading.RLock();INFLIGHT=set();ROOT=settings.PROJECT/'.calendar-update'

def bridge(data,cancel):return create.bridge(data,cancel)
def ok(row):
    if row.get('status')!='ok':raise ValueError(row.get('message') or native_context.MESSAGES.get(row.get('status'),'日历读取失败；未修改'))
def extract(text,provider,cancel):
    clock=native_context.system_clock()
    prompt='''CALENDAR_UPDATE_SCHEMA 仅提取用户明确要求修改已有单次事件的字段，只输出JSON。不执行操作。不默认日期、标题或时间，不省略字母/数字。讨论或假设intent=none。schema: {"intent":"update|none","target":{"title":null,"date":"today|tomorrow|YYYY-MM-DD","reference":"last_event"},"changes":{"title":null,"date":null,"start":"HH:MM","end":"HH:MM","end_date":null,"duration_minutes":null,"shift_minutes":null,"all_day":null},"ambiguous":false}。仅包含明确字段，未提及的字段不要输出null或默认值。target指原事件，changes指新值；未说改日期则changes不含date。‘这个/刚才那场’用reference=last_event，由代码核对身份。绝对新开始时间用start；相对延后/提前用正/负shift_minutes；变更时长用duration_minutes；开始结束与时长同时明说时全部保留以检查冲突。不支持删除、重复、提醒、邀请或更换日历，遇到返回ambiguous=true及reason。真实本机时钟：'''+json.dumps(clock,ensure_ascii=False)
    r=provider.chat(model=settings.MODEL,messages=[{'role':'system','content':prompt},{'role':'user','content':text}],stream=False,think=False,options={'num_predict':450,'num_ctx':2048},cancel=cancel)
    try:d=json.loads(r.message.content.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip())
    except (ValueError,TypeError):raise ValueError('未能明确提取修改，请说明原事件和要改的内容') from None
    if d.get('intent')!='update' or d.get('ambiguous'):raise ValueError('修改内容尚不明确；请明确原事件、日期及需要修改的字段，尚未修改')
    return d,clock

def date(value,clock):
    day=dt.date.fromisoformat(clock['date'])
    if value in ('today','tomorrow'):return day+dt.timedelta(days=int(value=='tomorrow'))
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise ValueError('请明确原事件或目标的完整日期，不会猜测日期')
    return dt.date.fromisoformat(value)
def allowed(e):
    if not e or not e.get('event_id') or not e.get('version'):raise ValueError('原事件已删除或无法核对版本，请重新查询')
    if not e.get('writable'):raise ValueError('原事件所属日历只读，不能修改；不会另建副本')
    if e.get('recurring'):raise ValueError('本轮不支持修改重复日程')
    if e.get('has_attendees'):raise ValueError('该会议含与会者，可能发送更新，本轮不支持修改')

def propose(original,changes,clock):
    allowed(original)
    if not isinstance(changes,dict):raise ValueError('请明确要修改的字段')
    changes={k:v for k,v in changes.items() if v is not None}
    if not changes or set(changes)-{'title','date','start','end','end_date','duration_minutes','shift_minutes','all_day'}:raise ValueError('本轮仅支持修改标题、日期、时间或时长')
    if original.get('all_day') or 'all_day' in changes:raise ValueError('本轮不支持修改全天事件或全天/普通事件转换，请在系统日历操作')
    zone=ZoneInfo(clock['timezone']);a=dt.datetime.fromisoformat(original['start']).astimezone(zone);b=dt.datetime.fromisoformat(original['end']).astimezone(zone);seconds=(b.astimezone(dt.timezone.utc)-a.astimezone(dt.timezone.utc)).total_seconds()
    newa,newb=a,b;note='仅修改明确提出的字段；地点、备注、已有提醒保持不变。'
    for k in ('duration_minutes','shift_minutes'):
        if k in changes and (type(changes[k]) is not int or abs(changes[k])>527040):raise ValueError('时长或偏移需要明确的分钟数')
    if 'shift_minutes' in changes:
        if set(changes)&{'date','start','end','end_date','duration_minutes'}:raise ValueError('相对偏移和绝对时间相互冲突，请明确一种调整方式')
        newa=(a.astimezone(dt.timezone.utc)+dt.timedelta(minutes=changes['shift_minutes'])).astimezone(zone);newb=(b.astimezone(dt.timezone.utc)+dt.timedelta(minutes=changes['shift_minutes'])).astimezone(zone);note+=' 按明确偏移平移，保留原时长。'
    else:
        day=date(changes['date'],clock) if 'date' in changes else a.date()
        if 'date' in changes or 'start' in changes:newa=create.local_time(day,changes.get('start',a.strftime('%H:%M')),zone)
        duration=changes.get('duration_minutes')
        if duration is not None and duration<=0:raise ValueError('时长必须大于零')
        if 'end' in changes or 'end_date' in changes:
            eday=date(changes['end_date'],clock) if 'end_date' in changes else newa.date()+(b.date()-a.date())
            newb=create.local_time(eday,changes.get('end',b.strftime('%H:%M')),zone)
            if duration is not None and (newb.astimezone(dt.timezone.utc)-newa.astimezone(dt.timezone.utc)).total_seconds()!=duration*60:raise ValueError('开始、结束和时长相互冲突，请重新明确')
        elif 'date' in changes or 'start' in changes or duration is not None:
            newb=(newa.astimezone(dt.timezone.utc)+dt.timedelta(seconds=duration*60 if duration is not None else seconds)).astimezone(zone)
            if duration is None:note+=' 只改开始时间/日期，保留原时长'+str(round(seconds/60,2))+'分钟。'
    # Absolute/relative calculations may land on a repeated wall time: require a new unambiguous choice.
    create.local_time(newa.date(),newa.strftime('%H:%M'),zone);create.local_time(newb.date(),newb.strftime('%H:%M'),zone)
    if newb<=newa:raise ValueError('结束必须晚于开始；跨午夜请明确次日结束')
    if newa<=dt.datetime.fromisoformat(clock['now']):raise ValueError('目标开始时间已过去，不会自动改到明天')
    title=changes.get('title',original['title'])
    if not isinstance(title,str) or not 1<=len(title.strip())<=200:raise ValueError('标题必须为1～200字')
    result={**original,'title':title.strip(),'start':newa.isoformat(),'end':newb.isoformat(),'timezone':clock['timezone']}
    return result,note

def inspect(eid,event,cancel):
    row=bridge({'action':'update_check','event_id':eid,'event':event},cancel);ok(row)
    allowed(row.get('event'))
    if not isinstance(row.get('conflicts'),list) or not isinstance(row.get('conflict_version'),str):raise ValueError('未取得完整冲突检查，不能当作没有冲突')
    return row

def make_preview(eid,data,clock,cancel):
    # Identity lookup first, then inspect proposed interval against a fresh store.
    row=inspect(eid,{},cancel);original=row['event'];proposed,note=propose(original,data['changes'],clock)
    row=inspect(eid,proposed,cancel)
    if row['event']['version']!=original['version']:raise ValueError('原事件在读取期间变化，请重新提交')
    return {'id':uuid.uuid4().hex,'state':'pending','original':original,'proposed':proposed,'note':note,'changes_intent':data['changes'],'conflicts':row['conflicts'],'conflict_version':row['conflict_version'],'timezone':clock['timezone']}

def preview(text,provider,cancel,references):
    data,clock=extract(text,provider,cancel);target=data.get('target',{})
    if not isinstance(target,dict):raise ValueError('请明确原事件')
    if target.get('reference')=='last_event':
        ids=[x for x in references if x.get('event_id')]
        if not ids:raise ValueError('当前对话还没有选定日程，请补充日期和事项，例如：把明天的面试改到晚上8点。')
        if target.get('title'):ids=[x for x in ids if target['title'].casefold() in x.get('title','').casefold()]
        if len(ids)!=1:raise ValueError('当前引用没有唯一事件身份，请重新查询或明确日期和标题；不能按列表序号猜测')
        return make_preview(ids[0]['event_id'],data,clock,cancel),None
    day=date(target.get('date'),clock);title=target.get('title')
    if not isinstance(title,str) or not title.strip():raise ValueError('请明确原事件标题和日期')
    row=bridge({'action':'read','date':day.isoformat(),'period':'day'},cancel);ok(row)
    if row.get('partial'):raise ValueError('原事件查询覆盖不完整，请缩小范围后重试')
    candidates=[e for e in row.get('events',[]) if title.strip().casefold() in str(e.get('title','')).casefold()]
    if not candidates:raise ValueError('该日期没有可靠匹配的原事件，尚未修改')
    if len(candidates)>1:return None,{'id':uuid.uuid4().hex,'state':'pending','candidates':candidates,'data':data,'clock':clock}
    return make_preview(candidates[0]['event_id'],data,clock,cancel),None

def save(p):
    # Separate namespace; keep creation's durable/idempotent journal semantics.
    ROOT.mkdir(exist_ok=True,mode=0o700);f=ROOT/(p['id']+'.json');t=f.with_suffix('.tmp')
    import os
    with open(t,'w',opener=lambda path,flags:os.open(path,flags,0o600)) as stream:json.dump(p,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
    os.replace(t,f)
def load(pid,sid):
    if not isinstance(pid,str) or not re.fullmatch('[a-f0-9]{32}',pid):raise ValueError('修改预览无效')
    try:p=json.loads((ROOT/(pid+'.json')).read_text())
    except (ValueError,OSError):raise ValueError('修改预览已失效') from None
    if p.get('session_id')!=sid:raise ValueError('修改预览不属于当前会话')
    return p
def public(p):
    def shown(e):return {k:e[k] for k in ('event_id','title','start','end','all_day','calendar_id','calendar_title','timezone','advisory','overlap_start','overlap_end') if k in e}
    if 'candidates' in p:return {'id':p['id'],'candidates':[shown(e) for e in p['candidates']]}
    return {**{k:p[k] for k in ('id','note','timezone')},'original':shown(p['original']),'proposed':shown(p['proposed']),'conflicts':[shown(e) for e in p['conflicts']]}
def publish(p,sid,epoch):
    with LOCK:p.update(session_id=sid,epoch=epoch);save(p);return public(p)
def invalidate(sid):
    with LOCK:
        if ROOT.exists():
            for f in ROOT.glob('*.json'):
                p=json.loads(f.read_text())
                if p.get('session_id')==sid and p['state']=='pending':p['state']='cancelled';save(p)
def select(body,c,sid):
    with LOCK:
        p=load(body.get('selection_id'),sid);version=c.epoch
        if p['state']!='pending' or p['epoch']!=version or 'candidates' not in p:raise ValueError('事件选择已失效')
        eid=body.get('event_id')
        if eid not in [e['event_id'] for e in p['candidates']]:raise ValueError('所选身份不在候选中')
    fresh=make_preview(eid,p['data'],native_context.system_clock(),c.cancel or threading.Event())
    with LOCK:
        old=load(p['id'],sid)
        if c.epoch!=version or old['state']!='pending':raise ValueError('选择期间请求已失效')
        old['state']='cancelled';save(old)
        return {'status':'preview','calendar_update_preview':publish(fresh,sid,version)}

def action(body,c,sid,cancel=False):
    with LOCK:
        p=load(body.get('preview_id'),sid)
        if cancel:
            if p['state'] in ('writing','uncertain'):return {'status':'writing','message':'核对或写入已开始，不能宣称已取消；请核对原操作'}
            if p['state'] in ('pending','checking'):p['state']='cancelled';save(p)
            return {'status':p['state']}
        # Epoch is checked even for a previous success: stale UI cannot open an old answer.
        if c.epoch!=p['epoch']:raise ValueError('修改预览已被重置或新请求替代')
        if p['state']=='verified':return p['result']
        if p['id'] in INFLIGHT:return {'status':'writing','message':'同一次修改正在处理，请勿重复确认'}
        if p['state'] not in ('pending','uncertain','writing'):raise ValueError('修改预览已失效')
        if body.get('confirm') is not True:raise ValueError('必须明确确认后才修改')
        if p['conflicts'] and body.get('accept_conflicts') is not True:return {'status':'conflict_confirmation_required','message':'请确认已查看冲突，选择仍然修改或重新调整'}
        reconcile=p['state'] in ('uncertain','writing');p['state']='writing' if reconcile else 'checking';save(p);INFLIGHT.add(p['id'])
    try:
        if not reconcile:
            fresh=inspect(p['original']['event_id'],p['proposed'],threading.Event())
            changed=fresh['event']['version']!=p['original']['version'] or fresh['conflict_version']!=p['conflict_version']
            with LOCK:
                # Reset/new task during preflight must prevent a write.
                if c.epoch!=p['epoch'] or load(p['id'],sid)['state']=='cancelled':raise ValueError('核对期间操作失效，未修改')
                if changed:p['state']='cancelled';save(p)
                else:
                    # Reservation is durable before native I/O, outside this lock.
                    p['state']='writing';save(p)
            if changed:
                new=make_preview(p['original']['event_id'],{'changes':p['changes_intent']},native_context.system_clock(),threading.Event())
                with LOCK:
                    if c.epoch!=p['epoch']:raise ValueError('预览已失效')
                    return {'status':'needs_confirmation','message':'原事件或冲突发生变化，请重新核对并确认','calendar_update_preview':publish(new,sid,c.epoch)}
        changes={k:p['proposed'][k] for k in ('title','start','end') if p['proposed'][k]!=p['original'][k]}
        result=bridge({'action':'update_reconcile' if reconcile else 'update','operation_id':p['id'],'event_id':p['original']['event_id'],'event':p['proposed'],'changes':changes,'expected_version':p['original']['version'],'expected_conflict_version':p['conflict_version']},threading.Event())
        if result.get('status')=='stale':
            new=make_preview(p['original']['event_id'],{'changes':p['changes_intent']},native_context.system_clock(),threading.Event())
            with LOCK:
                if c.epoch!=p['epoch']:raise ValueError('预览已失效')
                p['state']='cancelled';save(p)
                return {'status':'needs_confirmation','message':'保存前数据发生变化，请重新确认','calendar_update_preview':publish(new,sid,c.epoch)}
        if result.get('status')=='verified':
            back=result.get('event',{});good=result.get('event_id')==p['original']['event_id'] and back.get('event_id')==p['original']['event_id']
            good=good and all(back.get(k)==p['proposed'].get(k) for k in ('title','all_day','calendar_id'))
            good=good and all(dt.datetime.fromisoformat(back[k])==dt.datetime.fromisoformat(p['proposed'][k]) for k in ('start','end'))
            if p['original'].get('preserved_version'):good=good and back.get('preserved_version')==p['original']['preserved_version']
            if not good:result={'status':'uncertain','message':'读回身份或内容不一致，未宣称成功；只核对原操作，不再次写入'}
        if result.get('status') in ('timeout','unavailable'):result={'status':'uncertain','message':'修改结果不确定；再次确认只读取核对，不自动重复修改'}
    except ValueError as error:result={'status':'stale','message':str(error)}
    except Exception:result={'status':'uncertain','message':'修改或核对未明确完成，不自动重试写入'}
    finally:
        with LOCK:INFLIGHT.discard(p['id'])
    with LOCK:p['state']=result.get('status','uncertain');p['result']=result;save(p)
    return result
