"""Opt-in local reminder plans. No model polling, EventKit mutation or OS permission request.
Candidate remains off until independent native presentation is accepted.
"""
import copy,datetime as dt,fcntl,hashlib,json,os,threading,uuid
from contextlib import contextmanager
from pathlib import Path
import backend.settings as settings, backend.native_context as native_context
UTC=dt.timezone.utc
PATH=settings.PROJECT/'.runtime/calendar-reminders/plans.json'
LOCK=threading.RLock();STOP=threading.Event();WORKER=None
NOTICE='仅由本机助手提醒，需要助手运行；电脑睡眠或关机期间无法保证准时。不是系统日历闹钟。'
def enabled():
 # Atomic plan snapshot contains the switch; absent legacy configuration stays OFF.
 if PATH.exists():
  try:
   d=json.loads(PATH.read_text());c=d.get('configuration')
   if c is not None:return c.get('enabled') is True and type(c.get('revision')) is int
  except (OSError,ValueError,AttributeError):return False
 return getattr(settings,'CALENDAR_REMINDERS_ENABLED',False) is True
ACTIVE={'scheduled','ready','read_failed','notification_waiting','claimed','delivered','read','system_accepted','system_visible','user_viewed','uncertain','delivery_failed'}
def configuration():
 if not PATH.exists():return {'enabled':enabled(),'revision':0,'active_count':0,'paused_count':0,'uncertain_count':0,'notice':NOTICE,'default_minutes':30}
 with store() as d:
  c=d.get('configuration',{'enabled':getattr(settings,'CALENDAR_REMINDERS_ENABLED',False) is True,'revision':0})
  return dict(c,active_count=sum(r['status'] in ACTIVE for r in d['items'].values()),paused_count=sum(r['status'] in ('read_failed','notification_waiting','delivery_failed') for r in d['items'].values()),uncertain_count=sum(r['status'] in ('claimed','uncertain') for r in d['items'].values()),notice=NOTICE,default_minutes=30)
def configure(value,revision):
 if type(value) is not bool or type(revision) is not int:raise ValueError('请明确选择提醒状态并刷新设置。')
 cancel=[]
 with store() as d:
  c=d.get('configuration',{'enabled':getattr(settings,'CALENDAR_REMINDERS_ENABLED',False) is True,'revision':0})
  if c['revision']!=revision:raise ValueError('提醒设置已变化，请刷新后操作。')
  if not value:
   for r in d['items'].values():
    if r['status'] in ACTIVE:
     if r.get('delivery_id'):cancel.append(r['delivery_id'])
     r.update(status='disabled',version=r['version']+1,delivery_id=None,note='用户关闭助手提醒；再次开启不会恢复此计划。');r.pop('paused_delivery_status',None)
  d['configuration']={'enabled':value,'revision':revision+1}
 if native_enabled():
  for delivery in cancel:__import__('backend.calendar.reminder_native', fromlist=['*']).cancel(delivery)
 if value:start()
 return configuration()

def now():return dt.datetime.fromisoformat(native_context.system_clock()['now']).astimezone(UTC)
def moment(value):
 d=dt.datetime.fromisoformat(value)
 if not d.tzinfo:raise ValueError('事件缺少可靠时区，尚未设置提醒。')
 return d.astimezone(UTC)
def minutes(value):
 if value is None:return 30
 if type(value) is not int or not 1<=value<=10080:raise ValueError('请明确1分钟至7天之间的提前量。')
 return value
@contextmanager
def store():
 PATH.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 with LOCK:
  fd=os.open(PATH.with_suffix('.lock'),os.O_CREAT|os.O_RDWR,0o600)
  with os.fdopen(fd,'a') as f:
   fcntl.flock(f,fcntl.LOCK_EX)
   try:d=json.loads(PATH.read_text()) if PATH.exists() else {'contract':1,'items':{},'operations':{}}
   except (OSError,ValueError):raise ValueError('提醒存储无法读取；未清空，请保留文件并恢复核对。') from None
   if not isinstance(d,dict) or not {'contract','items','operations'}<=set(d) or set(d)-{'contract','items','operations','configuration'} or d['contract']!=1 or not isinstance(d['items'],dict) or not isinstance(d['operations'],dict):raise ValueError('提醒存储版本或内容异常，未覆盖。')
   for k,v in d['items'].items():
    if not isinstance(v,dict) or v.get('id')!=k or type(v.get('version')) is not int:raise ValueError('提醒记录损坏，未覆盖。')
   if 'configuration' in d and (not isinstance(d['configuration'],dict) or set(d['configuration'])!={'enabled','revision'} or type(d['configuration']['enabled']) is not bool or type(d['configuration']['revision']) is not int):raise ValueError('提醒开关配置损坏，未覆盖。')
   original=json.dumps(d,sort_keys=True)
   yield d
   if json.dumps(d,sort_keys=True)!=original:
    temp=PATH.with_name(uuid.uuid4().hex+'.tmp');fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    try:
     with os.fdopen(fd,'w') as out:json.dump(d,out,ensure_ascii=False);out.flush();os.fsync(out.fileno())
     os.replace(temp,PATH);directory=os.open(PATH.parent,os.O_RDONLY)
     try:os.fsync(directory)
     finally:os.close(directory)
    finally:temp.unlink(missing_ok=True)
def read_event(eid,cid,cancel):
 # Reuse exact-identity read-only branch. No range/conflict query parameters.
 r=native_context.bridge_request({'action':'reminder_check','event_id':eid,'event':{}},cancel)
 if r.get('status')=='not_found':return None
 if r.get('status')!='ok':__import__('backend.calendar.calendar_create', fromlist=['*']).require_ok(r)
 if not isinstance(r.get('event'),dict):raise ValueError('日历读取失败，暂不投递；不把读取失败当作已删除。')
 e=r['event']
 if e.get('event_id')!=eid or e.get('calendar_id')!=cid:raise ValueError('提醒事件身份不匹配，停止投递。')
 if e.get('reminder_supported') is not True:raise ValueError('当前原生桥接未证明该事件支持提醒；已暂停，未猜测共享状态。')
 if not e.get('version') or e.get('recurring') is not False or e.get('all_day') is not False or e.get('has_attendees') is not False or e.get('shared') is not False:raise ValueError('初版仅支持普通单次非共享、无与会者的定时事件。')
 if moment(e['end'])<=moment(e['start']):raise ValueError('事件起止时间无效，未设置提醒。')
 return {k:copy.deepcopy(e.get(k)) for k in ('event_id','calendar_id','calendar_title','title','start','end','version','timezone')}
def key(e):return hashlib.sha256((e['calendar_id']+'\0'+e['event_id']).encode()).hexdigest()
def public(row):return {k:copy.deepcopy(v) for k,v in row.items() if k!='claim_at'}
def set_plan(event,lead,check=lambda:None,operation_id=None):
 if not isinstance(operation_id,str) or not __import__('re').fullmatch('[a-f0-9-]{32,36}',operation_id):raise ValueError('提醒请求身份无效，未保存。')
 lead=minutes(lead);at=now();due=moment(event['start'])-dt.timedelta(minutes=lead)
 if moment(event['start'])<=at:raise ValueError('事件已经开始或已过去，尚未设置提醒。')
 if due<=at:raise ValueError('所选提前时间已过去，请选择更短的提前量。')
 ident=key(event)
 with store() as d:
  check();
  if not enabled():raise ValueError('助手提醒已关闭，未保存计划。')
  sig=hashlib.sha256(json.dumps([ident,event['version'],lead]).encode()).hexdigest()
  if operation_id in d['operations']:
   oldop=d['operations'][operation_id]
   if oldop['signature']!=sig:raise ValueError('重复请求内容不一致，未保存。')
   return public(d['items'][oldop['id']])
  d['operations'][operation_id]={'id':ident,'signature':sig}
  old=d['items'].get(ident)
  if old and old['event_version']==event['version'] and old['lead_minutes']==lead and old['status'] in ('scheduled','ready','claimed','delivered','read','dismissed'):return public(old)
  row={'id':ident,'version':old['version']+1 if old else 1,'event_id':event['event_id'],'calendar_id':event['calendar_id'],'calendar_title':event['calendar_title'],'title':event['title'],'start':event['start'],'end':event['end'],'event_version':event['version'],'timezone':native_context.system_clock()['timezone'],'lead_minutes':lead,'due':due.isoformat(),'status':'scheduled','updated_at':at.isoformat(),'delivery_id':None,'catchup':False,'note':''};d['items'][ident]=row
  return public(row)
def listing():
 with store() as d:return [public(x) for x in d['items'].values()]
def update(ident,version,action,check=lambda:None):
 with store() as d:
  check();r=d['items'].get(ident)
  if not r or r['version']!=version:raise ValueError('提醒已变化，请刷新后操作。')
  delivery=r.get('delivery_id')
  if action=='disable':r.update(status='disabled',version=r['version']+1,delivery_id=None)
  elif action in ('read','dismiss'):
   if r['status'] not in ('delivered','read','dismissed','system_accepted','system_visible','user_viewed'):raise ValueError('该提醒尚未确认展示，不能标记已读。')
   r['status']='read' if action=='read' else 'dismissed'
   if action=='dismiss':r['version']+=1;r['delivery_id']=None
  else:raise ValueError('未知提醒操作。')
  result=public(r)
 if action in ('disable','dismiss') and delivery and native_enabled():
  state=__import__('backend.calendar.reminder_native', fromlist=['*']).cancel(delivery);result['note']='后续提醒已关闭；系统已有通知移除状态：'+state.get('status','unknown')
 return result
def sync():
 at=now()
 for old in listing():
  if old['status'] not in ACTIVE or old['status'] in ('uncertain','delivery_failed'):continue
  try:e=read_event(old['event_id'],old['calendar_id'],STOP);state=None if e else 'event_missing';note='原事件已不可读取，停止提醒。' if e is None else ''
  except Exception:state='read_failed';e=None;note='日历读取或事件类型核对失败，未投递；可在恢复后重新核对。'
  with store() as d:
   r=d['items'].get(old['id'])
   if not r or r['version']!=old['version'] or r['status']!=old['status']:continue
   if state:
    if r.get('delivery_id') and r['status'] not in ('read_failed',):
     if native_enabled():__import__('backend.calendar.reminder_native', fromlist=['*']).cancel(r['delivery_id'])
     if state=='read_failed':r['paused_delivery_status']=r['status']
    if state=='event_missing':r.update(version=r['version']+1,delivery_id=None);r.pop('paused_delivery_status',None)
    r.update(status=state,note=note);continue
   if r['event_version']!=e['version']:
    if r.get('delivery_id') and native_enabled():__import__('backend.calendar.reminder_native', fromlist=['*']).cancel(r['delivery_id'])
    r.pop('paused_delivery_status',None)
    r.update(title=e['title'],start=e['start'],end=e['end'],timezone=e['timezone'],calendar_title=e['calendar_title'],event_version=e['version'],due=(moment(e['start'])-dt.timedelta(seconds=r.get('snooze_before_start_seconds',r['lead_minutes']*60))).isoformat(),status='scheduled',delivery_id=None,version=r['version']+1)
   r['timezone']=native_context.system_clock()['timezone']
   if r.get('paused_delivery_status'):
    r['status']=r.pop('paused_delivery_status');r['note']='事件已重新核对，保留原投递结果，不重复发送。'
   if r['status'] in ('delivered','read','system_accepted','system_visible','user_viewed'):
    if at>=moment(r['start']):
     if r.get('delivery_id') and native_enabled():__import__('backend.calendar.reminder_native', fromlist=['*']).cancel(r['delivery_id'])
     r.update(status='expired',version=r['version']+1,delivery_id=None,note='事件已开始，旧提醒展示已失效。')
    continue
   if r['status']=='claimed':
    if (at-moment(r['claim_at'])).total_seconds()>60:r.update(status='uncertain',note='展示结果未知，未自动重发。')
    continue
   late=(at-moment(r['due'])).total_seconds()
   if at>=moment(r['start']) or late>600:r.update(status='missed',note='已错过补提醒窗口，未集中弹出过期提醒。')
   elif late>=0:r.update(status='ready',catchup=late>5,note='补提醒' if late>5 else '')
   else:r.update(status='scheduled',note='')
def claim(channel='page'):
 if not enabled() or (channel=='page' and native_enabled()):return []
 sync();at=now();result=[]
 with store() as d:
  for r in d['items'].values():
   if r['status']=='ready' and enabled():
    r.update(status='claimed',claim_at=at.isoformat(),delivery_id=uuid.uuid4().hex)
    item=public(r);item['synthetic']=bool(r.get('synthetic'));item['minutes_until_start']=max(0,round((moment(r['start'])-at).total_seconds()/60));result.append(item)
 return result
def acknowledge(ident,version,delivery,result):
 if result not in ('presented','system_accepted','unknown','denied','unavailable','failed'):raise ValueError('展示结果无效。')
 with store() as d:
  r=d['items'].get(ident)
  if not r or r['version']!=version or r['delivery_id']!=delivery:raise ValueError('旧提醒投递已经失效。')
  if r['status']=='delivered' and result=='presented':return public(r)
  if r['status']!='claimed':raise ValueError('提醒状态已变化，未接受迟到回执。')
  r.update(status='delivered' if result=='presented' else 'system_accepted' if result=='system_accepted' else 'uncertain' if result=='unknown' else 'delivery_failed',note='页面通道已确认展示；不代表系统通知已投递。' if result=='presented' else '展示通道'+result+'；没有宣称已经提醒。')
  return public(r)
def start():
 global WORKER
 if not enabled() or WORKER:return
 def loop():
  # Cross-process lifetime lock: two entries cannot start two schedulers.
  PATH.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
  with (PATH.parent/'scheduler.lock').open('a') as lease:
   try:fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:return
   while not STOP.is_set():
    try:
     if enabled():
      if native_enabled():native_cycle()
      else:sync()
    except Exception:pass  # API reports storage/read status; never clear or send on failure.
    STOP.wait(getattr(settings,'REMINDER_POLL_SECONDS',30))
 WORKER=threading.Thread(target=loop,daemon=True,name='calendar-reminders');WORKER.start()

def native_enabled():return getattr(settings,'REMINDER_NATIVE_DELIVERY',True) is True

def native_cycle():
 import backend.calendar.reminder_native as reminder_native
 if not enabled():return
 process_native_actions()
 sync();state=reminder_native.status()['status']
 if state!='authorized':
  with store() as d:
   for r in d['items'].values():
    if r['status']=='ready':r.update(status='notification_waiting',note='通知通道：'+state+'；未报告已投递。')
  return
 for row in claim('native'):
  with store() as d:
   current=d['items'].get(row['id']);valid=bool(current and current['version']==row['version'] and current['status']=='claimed' and current['delivery_id']==row['delivery_id'])
  if not valid:continue
  result=reminder_native.deliver(row)['status']
  try:acknowledge(row['id'],row['version'],row['delivery_id'],result if result in ('system_accepted','unknown','denied','unavailable','failed') else 'failed')
  except ValueError:
   # Cancellation may precede an already queued OS add. Retract after its late
   # response as well; never send the reminder again.
   reminder_native.cancel(row['delivery_id'])
 visible=reminder_native.request('visible')
 with store() as d:
  for r in d['items'].values():
   if r['status']=='system_accepted' and r['delivery_id'] in visible.get('delivery_ids',[]):r.update(status='system_visible',note='系统通知中心可见；尚不代表用户已查看。')
   elif r['status']=='uncertain':
    delivery=r.get('delivery_id')
    if isinstance(delivery,str) and __import__('re').fullmatch('[a-f0-9]{32}',delivery):
     try:journal=json.loads((reminder_native.ROOT/(delivery+'.delivery.json')).read_text())
     except (OSError,ValueError):continue
     if journal.get('delivery_id')==delivery and journal.get('status')=='system_accepted':r.update(status='system_accepted',note='通过原生投递记录核对系统接受；没有再次投递。')
 for click in reminder_native.clicks():
  with store() as d:
   r=d['items'].get(click.get('id'))
   if r and r['version']==click.get('version') and r['delivery_id']==click.get('delivery_id') and r['status'] in ('system_accepted','system_visible'):r.update(status='user_viewed',note='用户点击了对应通知；只读展示，没有重放工具操作。')


def hide(ident,version):
 with store() as d:
  r=d['items'].get(ident)
  if not r or r['version']!=version:raise ValueError('提醒已变化，旧提示已失效。')
  return public(r) # Visibility is native-only; never cancel a persisted reminder.

def snooze(ident,version,operation_id,minutes=None,until=None,clicked_at=None):
 if not isinstance(operation_id,str) or not __import__('re').fullmatch('[a-f0-9-]{32,36}',operation_id):raise ValueError('稍后提醒请求身份无效。')
 at=now();clicked=moment(clicked_at) if clicked_at else at
 if abs((at-clicked).total_seconds())>30:raise ValueError('操作已过期，请重新选择稍后时间。')
 if (minutes is None)==(until is None):raise ValueError('请选择10分钟后或具体再次提醒时间。')
 if minutes is not None:
  if type(minutes) is not int or not 1<=minutes<=10080:raise ValueError('请输入有效的分钟数。')
  due=clicked+dt.timedelta(minutes=minutes)
 else:due=moment(until)
 signature=hashlib.sha256(json.dumps([ident,version,minutes,until]).encode()).hexdigest()
 with store() as d:
  oldop=d['operations'].get(operation_id)
  if oldop:
   if oldop['signature']!=signature:raise ValueError('重复请求内容不同，未修改。')
   r=d['items'].get(oldop['id'])
   if not r or r['version']!=oldop['result_version']:raise ValueError('该次操作后提醒又有变化，请刷新。')
   return public(r)
  old=copy.deepcopy(d['items'].get(ident))
 if not old or old['version']!=version:raise ValueError('提醒已变化，请刷新。')
 fresh=read_event(old['event_id'],old['calendar_id'],STOP)
 if not fresh or fresh['version']!=old['event_version']:raise ValueError('原事件已变化或不可核实，未保存再次提醒。')
 if due<=at:raise ValueError('再次提醒时间已过去，请重新选择。')
 if due>=moment(fresh['start']):raise ValueError('再次提醒时间必须早于事件开始；没有静默安排。')
 delivery=old.get('delivery_id')
 with store() as d:
  r=d['items'].get(ident)
  if not enabled():raise ValueError('助手提醒已关闭，未保存再次提醒。')
  if not r or r['version']!=version or r['status'] not in ('delivered','read','system_accepted','system_visible','user_viewed'):raise ValueError('提醒已关闭或变化，旧操作无效。')
  r.update(version=version+1,due=due.isoformat(),status='scheduled',delivery_id=None,catchup=False,note='再次提醒已保存',snooze_before_start_seconds=(moment(r['start'])-due).total_seconds(),updated_at=at.isoformat())
  d['operations'][operation_id]={'id':ident,'signature':signature,'result_version':r['version'],'saved_due':r['due']};result=public(r)
 if delivery and native_enabled():__import__('backend.calendar.reminder_native', fromlist=['*']).cancel(delivery)
 return result

def process_native_actions():
 import backend.calendar.reminder_native as reminder_native
 processed=0
 # Completed receipts do not consume the bounded batch and starve later actions.
 for f in sorted(reminder_native.ROOT.glob('*.action.json')):
  result=f.with_suffix('.result.json')
  if result.exists():continue
  if processed>=256:break
  processed+=1
  try:
   q=json.loads(f.read_text());ident=q['id'];v=q['version'];delivery=q['delivery_id'];op=q['operation_id'];action=q['action']
   if f.name!=op+'.action.json' or not __import__('re').fullmatch('[a-f0-9]{32}',op):raise ValueError('操作身份无效。')
   if action not in ('snooze','dismiss'):raise ValueError('未知提醒操作。')
   signature=hashlib.sha256(json.dumps([ident,v,q.get('minutes'),q.get('until')] if action=='snooze' else [ident,v,delivery,'dismiss']).encode()).hexdigest()
   with store() as d:
    prior=copy.deepcopy(d['operations'].get(op));current=copy.deepcopy(d['items'].get(ident))
   if prior:
    if prior.get('id')!=ident or prior.get('signature')!=signature:raise ValueError('重复操作内容不一致，未执行。')
    if action=='snooze' and (not current or current['version']!=prior.get('result_version') or current['status'] not in ('scheduled','ready','claimed','delivered','read','system_accepted','system_visible','user_viewed')):
     answer={'status':'superseded','action':action,'message':'该次保存后提醒又已关闭、暂停或变化；旧时间不再代表当前计划。'}
    elif action=='snooze' and prior.get('saved_due'):
     answer={'status':'saved','action':action,'due':prior['saved_due'],'message':'该次再次提醒已保存；后续状态以当前计划为准。'}
    elif action=='dismiss' and prior.get('receipt'):answer=prior['receipt']
    else:raise ValueError('旧操作缺少可核验结果，请查看当前计划，不自动重做。')
   else:
    old=next((r for r in listing() if r['id']==ident and r['version']==v and r['delivery_id']==delivery),None)
    if not old:raise ValueError('提醒已变化，旧操作无效。')
    if action=='snooze':
     r=snooze(ident,v,op,q.get('minutes'),q.get('until'),q.get('clicked_at'))
     answer={'status':'saved','action':action,'due':r['due'],'message':'再次提醒已保存'}
    else:
     answer={'status':'saved','action':action,'message':'已关闭本次提醒，日历未变。'}
     with store() as d:
      r=d['items'].get(ident)
      if not r or r['version']!=v or r.get('delivery_id')!=delivery or r['status'] not in ('system_accepted','system_visible','user_viewed','delivered','read'):raise ValueError('提醒已变化，旧操作无效。')
      r.update(status='dismissed',version=v+1,delivery_id=None)
      d['operations'][op]={'id':ident,'signature':signature,'result_version':v+1,'receipt':answer}
     if native_enabled():reminder_native.cancel(delivery)
  except Exception as e:answer={'status':'failed','message':str(e)[:160] if isinstance(e,ValueError) else '保存结果尚未确认，请核对当前计划，不要重复操作。'}
  tmp=result.with_suffix('.tmp');tmp.write_text(json.dumps(answer,ensure_ascii=False));os.chmod(tmp,0o600);os.replace(tmp,result)
