"""Read-only system clock/calendar context. Calendar stays on this Mac."""
import json,os,re,time,uuid,subprocess,datetime,fcntl
from pathlib import Path
from zoneinfo import ZoneInfo
import backend.settings as settings, backend.calendar.calendar_dates as calendar_dates
IPC=settings.PROJECT/'.runtime/calendar-ipc'
CANDIDATE=re.compile(r'几点|星期|几号|日期|日历|日程|安排|今天|明天|后天|今晚|今早|本周|这周|下周|星期|本月|下月|下个月|\d{4}-\d{2}-\d{2}|改|延后|推迟|提前|挪|调整|reschedule|shift|what time|calendar|schedule|agenda|[0-9一二三四五六七八九十]+点',re.I)

def system_clock(now=None):
    local=Path('/etc/localtime').resolve();name=str(local).split('/zoneinfo/')[-1]
    try:zone=ZoneInfo(name)
    except (ValueError,KeyError):zone=datetime.datetime.now().astimezone().tzinfo
    now=(now or datetime.datetime.now(datetime.timezone.utc)).astimezone(zone)
    return {'now':now.isoformat(timespec='seconds'),'date':now.date().isoformat(),'time':now.strftime('%H:%M:%S'),'weekday':['星期一','星期二','星期三','星期四','星期五','星期六','星期日'][now.weekday()],'timezone':getattr(zone,'key',str(zone))}

def parse_intent(text,provider,cancel,previous=None,references=None):
    if not previous and not references and not CANDIDATE.search(text):return None
    clock=system_clock()
    prompt='CALENDAR_INTENT_SCHEMA 只输出JSON：{"kind":"none|clock|calendar","operation":"read|create|offer|update|unsupported","date":"原文日期表达或YYYY-MM-DD","period":"day|morning|afternoon|evening","calendar_name":null}。只有用户明确限定单个日历时填写calendar_name。理解当前用户表达，先区分查询与创建，不能把查询当成修改。读取安排=read；明确请求加入日历、安排一下、记个日程等=create；仅陈述自己的具体未来安排、未要求创建=offer；知识问答、引用他人的安排、假设讨论=none，不提议创建；明确修改已有单次事件=update；删除已有事件/提醒=unsupported；引用刚才/这个事件的调整仍update，由代码核对真实身份。只在明确补充上一次缺失的时间/标题时延续创建；询问安排仍read。周一为一周开始；支持本周/下周/下下周加星期、本月/下月明确日期，日期表达交给代码计算，不自行算术。不明确星期或上午下午时保留原文，不猜。日期依据真实本机时钟解析，保留明确日期，不猜缺失日期；含糊日期返回原文。今晚/今天=today，明天=tomorrow，后天等换算成完整日期。kind=clock用于时间日期问题。真实本机时钟：'+json.dumps(clock,ensure_ascii=False)
    if references:prompt+='\n当前会话有'+str(len(references))+'个已读取或已核对的真实事件身份；用户明确要求调整它/刚才那个事件时判断update，其余聊天none；具体引用由代码检查，不用序号猜测。'
    if previous:prompt+='\n先前待补充创建请求（只是上下文）：'+previous
    result=provider.chat(model=settings.MODEL,messages=[{'role':'system','content':prompt},{'role':'user','content':text}],stream=False,think=False,options={'num_predict':160,'num_ctx':2048},cancel=cancel)
    content=result.message.content.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
    try:data=json.loads(content)
    except (ValueError,TypeError):raise ValueError('未能确认本机时间/日历意图，请明确日期和时段') from None
    if data.get('kind')=='none':return None
    if data.get('operation')=='write':raise ValueError('旧版日历意图仅支持读取；请明确创建单次日程')
    if data.get('kind') not in ('clock','calendar'):raise ValueError('本机查询类型不明确')
    if data.get('operation') in ('create','offer','update','unsupported'):return data
    if data['kind']=='clock':return data
    if data.get('calendar_name'):raise ValueError('目前查询范围为所有已授权日历；指定单个日历查询尚未支持，请明确是否接受该范围')
    period=data.get('period')
    # An unqualified date denotes the full local day. Missing/whole-day aliases
    # may normalize only when the user did not specify a narrower time range.
    explicit_time=re.search(r'上午|下午|早上|晚上|傍晚|凌晨|中午|清晨|[0-9一二三四五六七八九十]+点|\d{1,2}[:：]\d{2}|morning|afternoon|evening|before|after|之前|之后|之间',text,re.I)
    if period in (None,'','none','null','all','all_day','full_day','whole_day','全天','整天') and not explicit_time:data['period']='day'
    if data.get('period') not in ('day','morning','afternoon','evening'):
        raise ValueError('请明确日历查询时段')
    if 'date' in data:
        source=calendar_dates.from_source(text,clock)
        data['date']=source or calendar_dates.resolve(data['date'],clock).isoformat()
    elif type(data.get('day_offset'))!=int or not 0<=data['day_offset']<=7:raise ValueError('只读日历目前支持明确日期，或今天到未来7天')
    return data

def bridge_request(payload,cancel,timeout=20):
    if cancel.is_set():raise InterruptedError('已取消本机日历读取')
    IPC.mkdir(parents=True,exist_ok=True);os.chmod(IPC,0o700)
    ready=IPC/'ready.json'
    healthy=False
    try:
        state=json.loads(ready.read_text())
        if state.get('bundle')=='local.filechat.asuka.launcher' and type(state.get('pid'))==int and time.time()-ready.stat().st_mtime<4:
            os.kill(state['pid'],0);healthy=True
    except (OSError,ValueError,TypeError):pass
    if not healthy:
        import plistlib
        candidates=[Path.home()/'Desktop/明日香小助手.app',Path.home()/'Desktop/明日香助手.app']
        app=next((p for p in candidates if (p/'Contents/Info.plist').exists()),None)
        if app is None:return {'status':'unavailable','message':'桌面统一入口未找到，无法连接本机日历读取服务'}
        info=plistlib.loads((app/'Contents/Info.plist').read_bytes())
        if info.get('CFBundleIdentifier')!='local.filechat.asuka.launcher' or info.get('AsukaProjectPath')!=str(settings.PROJECT):return {'status':'unavailable','message':'日历入口身份不匹配，未读取日历'}
        subprocess.run(['/usr/bin/open','-g',str(app)],check=True,timeout=5,capture_output=True)
    rid=uuid.uuid4().hex;request=IPC/(rid+'.request.json');response=IPC/(rid+'.response.json')
    data={'id':rid,'created':time.time(),**payload}
    temp=request.with_suffix('.tmp');temp.write_text(json.dumps(data));os.chmod(temp,0o600);os.replace(temp,request)
    # Known-file mailbox: native reader need not enumerate the desktop directory.
    queue=IPC/'queue.json'
    with (IPC/'queue.lock').open('a') as gate:
        os.chmod(IPC/'queue.lock',0o600);fcntl.flock(gate,fcntl.LOCK_EX)
        try:ids=json.loads(queue.read_text())
        except (OSError,ValueError):ids=[]
        ids=[x for x in ids if isinstance(x,str) and re.fullmatch('[a-f0-9]{32}',x)][-255:]+[rid]
        qt=IPC/'queue.tmp';qt.write_text(json.dumps(ids));os.chmod(qt,0o600);os.replace(qt,queue)
    deadline=time.monotonic()+timeout
    try:
        while time.monotonic()<deadline:
            if cancel.is_set():raise InterruptedError('已取消本机日历读取')
            if response.exists():
                if response.stat().st_size>8000000:raise ValueError('日历响应过大，未读取')
                result=json.loads(response.read_text());assert result.get('id')==rid
                return result
            cancel.wait(.1)
        return {'status':'timeout','message':'本机日历读取超时；若出现系统授权窗口，请完成授权后再试'}
    finally:
        request.unlink(missing_ok=True);response.unlink(missing_ok=True);temp.unlink(missing_ok=True)

def calendar_events(intent,cancel,timeout=20):
    return bridge_request({**({'date':intent['date']} if 'date' in intent else {'day_offset':intent['day_offset']}),'period':intent['period']},cancel,timeout)

def context(text,provider,cancel,intent=None):
    intent=intent if intent is not None else parse_intent(text,provider,cancel)
    if intent is None:return None
    clock=system_clock()
    if intent['kind']=='clock':return {'kind':'clock','clock':clock}
    result=calendar_events(intent,cancel)
    return {'kind':'calendar','clock':clock,'calendar':result}

MESSAGES={'authorization_required':'请在“明日香助手”的系统日历授权窗口允许访问；本次尚未读取日程。','denied':'日历访问被拒绝。可在系统设置→隐私与安全性→日历中调整“明日香助手”；尚未读取日程。','revoked':'系统当前未允许完全访问日历；历史允许记录不代表当前权限。尚未读取日程。','restricted':'系统限制了日历访问，尚未读取日程。','write_only':'当前仅允许写入，不能读取日历；请在系统设置授权完全访问，本次需完全访问以读取并核对；未写入。','read_error':'本机日历读取失败，没有生成虚构日程。','unavailable':'本机日历服务不可用，尚未读取日程。','timeout':'本机日历服务超时，尚未获得日程。'}
def direct_error(data):
    if data['kind']=='calendar' and data['calendar'].get('status')!='ok':
        row=data['calendar'];return row.get('message') or MESSAGES.get(row.get('status'),'未能读取本机日历，未生成日程。')
    return None

def calendar_list(data):
    """Trusted text and structured evidence; no LLM owns membership or counts."""
    row=data['calendar'];clock=data['clock'];zone=ZoneInfo(row.get('timezone') or clock['timezone'])
    events=[]
    for e in row.get('events',[]):
        try:
            start=datetime.datetime.fromisoformat(e['start']).astimezone(zone);end=datetime.datetime.fromisoformat(e['end']).astimezone(zone)
            if end<=start:raise ValueError()
            events.append({'title':str(e.get('title','无标题'))[:200],'start':start.isoformat(),'end':end.isoformat(),'all_day':e.get('all_day') is True,'calendar_title':str(e.get('calendar_title',''))[:100],'calendar_id':e.get('calendar_id'),'event_id':e.get('event_id'),'version':e.get('version')})
        except (ValueError,KeyError,TypeError):raise ValueError('日历返回了无效起止时间，不能当成无安排') from None
    start=row.get('start');end=row.get('end');total=row.get('total',len(events))
    if type(total)!=int or total<len(events):raise ValueError('日历事件计数无效')
    meta={'events':events,'total':total,'retrieved':len(events),'shown':min(10,len(events)),'partial':bool(row.get('partial')),'start':start,'end':end,'timezone':str(zone),'calendars_scope':'all_authorized','fetched_at':system_clock()['now']}
    return meta

def list_text(meta,events=None):
    rows=meta['events'][:meta['shown']] if events is None else events
    def stamp(value):
        d=datetime.datetime.fromisoformat(value)
        return d.strftime('%Y-%m-%d')+' '+['星期一','星期二','星期三','星期四','星期五','星期六','星期日'][d.weekday()]+' '+d.strftime('%H:%M')
    interval=(stamp(meta['start'])+' → '+stamp(meta['end'])) if meta.get('start') and meta.get('end') else '指定范围'
    heading='查询范围：'+interval+'（结束不含）\n时区：'+meta['timezone']+' · 所有已授权日历\n共'+str(meta['total'])+'项；已读取'+str(meta['retrieved'])+'项；显示'+str(len(rows))+'项。'
    if not meta['total']:heading+='\n该范围内没有安排。'
    lines=[('全天 · ' if e['all_day'] else '')+stamp(e['start'])+' → '+stamp(e['end'])+' · '+e['title']+('（'+e['calendar_title']+'）' if e.get('calendar_title') else '') for e in rows]
    if meta['partial']:lines.append('读取覆盖不完整；请缩小日期/时段，未将部分结果冒充全部。')
    return heading+('\n'+'\n'.join(lines) if lines else '')
