"""Public local reminder adapter: backend selected identity, never an LLM/client event ID."""
import copy,threading
import backend.calendar.calendar_reminders as service, backend.native_context as native_context

def handle(body,entry='bubble'):
 import backend.assistant.bubble_chat as bubble_chat
 action=body.get('action')
 if action in ('set','adjust') and (type(body.get('minutes')) is not int or not 0<=body['minutes']<=10080):raise ValueError('请填写0至10080的整数分钟，0表示开始时提醒。')
 testing=getattr(__import__('backend.settings', fromlist=['*']),'REMINDER_NOTIFICATION_TESTING',False) is True and action in ('notification_status','notification_authorize','notification_test')
 if not service.enabled() and action not in ('configuration','configure','notification_status','notification_authorize') and not testing:raise ValueError('本机助手提醒当前未启用（开关关闭），没有设置提醒；请从聊天设置→本机日程提醒开启后返回此卡。')
 sid=body.get('session_id')
 if entry=='assistant':
  import backend.assistant.unified_web as unified_web
  _,sid,c=unified_web.resolve(sid)
 with bubble_chat.LOCK:
  if sid not in bubble_chat.SESSIONS:raise ValueError('会话已过期，请重新选择日程。')
  c=bubble_chat.SESSIONS[sid]
  if (entry=='bubble' and c.entry=='web') or (entry=='assistant' and c.entry!='web'):raise ValueError('入口会话不匹配。')
  epoch=c.epoch;action=body.get('action');selection_token=getattr(c,'reminder_selection_token',None)
  selected=copy.deepcopy(getattr(c,'reminder_selection',None))
  if action=='set' and 'selection_token' in body and body['selection_token']!=selection_token:raise ValueError('已换选日程，旧卡片设置失效；没有给其他事项设置提醒。')
  if action=='select':
   selection_token=__import__('uuid').uuid4().hex;c.reminder_selection_token=selection_token
   snap=c.calendar_snapshot
   if c.request or not snap or snap['snapshot_id']!=body.get('snapshot_id'):raise ValueError('查询结果或任务已变化，请重新选择。')
   rows=[e for e in snap['events'] if e.get('card_token')==body.get('card_token')]
   if len(rows)!=1:raise ValueError('没有唯一可信日程卡片。')
   selected=copy.deepcopy(rows[0])
 def valid():
  if action in ('select','set','adjust') and getattr(c,'reminder_selection_token',None)!=selection_token:raise ValueError('已换选日程，旧操作失效。')
  if c.epoch!=epoch or sid not in bubble_chat.SESSIONS:raise ValueError('会话或选择已失效，未保存提醒。')
  if action not in ('select','status','list','poll','ack','read','dismiss') and c.request:raise ValueError('当前任务正在处理，请稍后设置提醒。')
 if action=='deselect':
  with bubble_chat.LOCK:
   c.reminder_selection=None;c.reminder_selection_token=__import__('uuid').uuid4().hex
  return {'status':'selection_cancelled','message':'已取消本次选择；已设置提醒需另行关闭，日历未变。'}
 if action=='configuration':
  return dict(service.configuration(),notification=__import__('backend.calendar.reminder_native', fromlist=['*']).status().get('status','unavailable'))
 if action=='configure':
  with bubble_chat.LOCK:
   valid()
   if body.get('explicit') is not True:raise ValueError('仅用户主动操作才能切换助手提醒。')
   if body.get('enabled') is True and __import__('backend.calendar.reminder_native', fromlist=['*']).status().get('status')!='authorized':raise ValueError('请先在此设置中授权系统通知，再开启助手提醒。')
   result=service.configure(body.get('enabled'),body.get('revision'))
   # A switch transition is a new intent even OFF→ON before an old read returns.
   # Invalidate unsubmitted selection leases, never persisted plans or chat tasks.
   for other in bubble_chat.SESSIONS.values():
    other.reminder_selection=None;other.reminder_selection_token=__import__('uuid').uuid4().hex
   return result
 if action in ('notification_status','notification_authorize','notification_test'):
  import backend.calendar.reminder_native as reminder_native
  return reminder_native.status() if action=='notification_status' else reminder_native.authorize(body.get('explicit') is True) if action=='notification_authorize' else reminder_native.test(body.get('explicit') is True)
 if action in ('snooze','hide'):
  return {'reminder':service.hide(body.get('id'),body.get('version')) if action=='hide' else service.snooze(body.get('id'),body.get('version'),body.get('operation_id'),body.get('minutes'),body.get('until'))}
 if action=='sync':
  service.native_cycle();return {'items':service.listing()}
 if action=='status':return {'enabled':True,'native_channel':'candidate','notice':service.NOTICE}
 if action=='list':return {'items':service.listing(),'notice':service.NOTICE}
 if action=='poll':return {'deliveries':service.claim()}
 if action=='ack':return {'reminder':service.acknowledge(body.get('id'),body.get('version'),body.get('delivery_id'),body.get('result'))}
 if action in ('disable','read','dismiss'):
  with bubble_chat.LOCK:valid();return {'reminder':service.update(body.get('id'),body.get('version'),action,valid)}
 if action=='adjust':
  old=next((r for r in service.listing() if r['id']==body.get('id') and r['version']==body.get('version')),None)
  if not old:raise ValueError('提醒已经变化，请刷新。')
  selected=dict(old,version=old['event_version']);action='set'
 if action not in ('select','set'):raise ValueError('未知提醒操作。')
 if not selected:raise ValueError('请先从日历结果选定一项普通日程；没有猜测其他事件。')
 fresh=service.read_event(selected['event_id'],selected['calendar_id'],threading.Event())
 if not fresh:raise ValueError('所选事件已不存在，尚未设置提醒。')
 keys=('version',) if selected.get('version') else ('title','start','end','calendar_id')
 if any(selected.get(k)!=fresh.get(k) for k in keys):raise ValueError('日程已变化，请重新查询并选择。')
 with bubble_chat.LOCK:
  valid()
  if action=='select':c.reminder_selection=fresh;return {'selected':fresh,'selection_token':selection_token,'notice':service.NOTICE}
  current=getattr(c,'reminder_selection',None)
  if body.get('action')!='adjust' and current!=selected:raise ValueError('已换选日程，旧设置未保存。')
  return {'reminder':service.set_plan(fresh,body.get('minutes'),valid,body.get('operation_id')),'message':('已保存本机提醒计划（默认30分钟，可调整）。' if body.get('minutes') is None else '已保存本机提醒计划（提前'+str(body['minutes'])+'分钟）。')+service.NOTICE}

def from_plan(selected,plan,task):
 from backend.assistant.bubble_chat import LOCK
 from backend.assistant.assistant_tools import Outcome
 action=plan.get('action');lead=plan.get('lead_minutes');check=task.check
 if action in ('set','adjust'):
  if plan.get('timing') not in ('default','explicit') or (plan.get('timing')=='explicit' and lead is None):raise ValueError('请明确要提前多少分钟；已保留所选日程。')
  if plan.get('timing')=='default' and lead is not None:raise ValueError('提前量字段矛盾，未设置。')
 if not service.enabled():raise ValueError('本机提醒尚未通过原生展示验收，当前未启用。')
 if action=='list':return Outcome('reminder',{'message':service.NOTICE,'items':service.listing()})
 if not selected:raise ValueError('请先在日历卡片中选定具体事项，再设置本机提醒。')
 if action not in ('set','adjust','disable'):raise ValueError('请明确设置、调整或关闭本机提醒。')
 fresh=service.read_event(selected['event_id'],selected['calendar_id'],task.cancel)
 if not fresh:raise ValueError('所选事件已不存在，没有设置提醒。')
 if fresh['version']!=selected['version']:raise ValueError('所选日程已变化，请重新查询并选择。')
 with LOCK:
  check()
  if action=='disable':
   row=next((r for r in service.listing() if r['id']==service.key(fresh)),None)
   if not row:raise ValueError('这项日程尚未设置助手提醒。')
   result=service.update(row['id'],row['version'],'disable',check);message='已关闭这项助手提醒，日历事件未变。'
  else:result=service.set_plan(fresh,lead,check,task.rid);message='已保存提前'+str(result['lead_minutes'])+'分钟的本机提醒计划。'+service.NOTICE
 return Outcome('reminder',{'message':message,'reminder':result})
