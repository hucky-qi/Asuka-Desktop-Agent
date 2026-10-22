"""Finite explicit calendar-mention defaults. Same memory CAS; no permission grants."""
import copy
from assistant_memory import MemoryAccess
SCHEMA={'type':['object','null'],'properties':{'action':{'enum':['save','update','forget','none']},'value':{'enum':['ask_first','quiet','default']},'scope':{'enum':['calendar_mentions','unknown']},'duration':{'enum':['long_term','turn','none']},'evidence':{'type':'string','minLength':1}},'required':['action','value','scope','duration','evidence'],'additionalProperties':False}
KEY='calendar_mentions'
VALUES={'ask_first':'仅提及本人未来安排时，先询问是否需要日程预览。当前明确请求优先，所有写入仍须确认。','quiet':'仅提及本人未来安排时，不主动建议创建；当前明确请求优先，所有写入仍须确认。','default':'恢复未来安排提及的默认询问方式；所有写入仍须确认。'}
PROMPT=' INTENT_BOUNDARY priority: requested action determines route, NOT dates/events. For route=calendar include calendar_action=clock|query|create|offer|update|delete|reminder|clarify|none|unsupported; it fixes the task, later extraction only supplies fields. Explicit diary save/query/correct/forget=journal even future notes; NEVER infer a wish to record. Mere reported/quoted/third-party/hypothetical/negated statements and capability questions=chat, not record/task clarification. Own tentative or definite future plan alone=calendar offer, ask briefly whether scheduling is wanted, never require a repeated command; distinguish an actual tentative personal plan from a fictional hypothetical; explicit booking=create; existing event change/reminder=calendar, no duplicate event. Explicit single future reminder remains calendar_action=reminder even without existing event: fields extraction separates event time, reminder advance and calendar exclusion; code can preview an event carrier after checking existing events. Mere future mention remains offer, not reminder. calendar_pending_fields.stage=uncommitted_carrier means an UNWRITTEN reminder/event preview: corrections resume calendar_action=reminder, not update an existing event. Cancellation is turn-only. Optional behavior is NOT a response style: {action:save|update|forget|none,value:ask_first|quiet|default,scope:calendar_mentions|unknown,duration:long_term|turn|none,evidence:exact current clause}. Only explicit continuing defaults; unknown category scope stays unknown. No permission grants.'

def candidates(c):return [r for r in MemoryAccess.list_for(c)['items'] if r.get('kind')=='preference' and r.get('preference_key')==KEY and r.get('scopes')==['calendar'] and r.get('source')=='user_explicit']
def policy(c):
 rows=candidates(c)
 return rows[0].get('preference_value','ask_first') if len(rows)==1 else 'ask_first'
def apply(c,proposal,text,task,revision):
 import jsonschema,bubble_memory
 jsonschema.validate(proposal,SCHEMA);task.check()
 if proposal['action']=='none' or proposal['duration']!='long_term':return None
 if not proposal['evidence'].strip() or proposal['evidence'] not in text:return '尚未保存安排提及偏好：原文证据不符。'
 if proposal['scope']!='calendar_mentions':return '尚未保存：请明确这项默认规则的适用范围，不把某类事项推广到所有安排。'
 state=MemoryAccess.list_for(c);rows=candidates(c)
 if state['revision']!=revision:return '安排提及偏好在处理期间已变化，本次未覆盖，请刷新记忆后操作。'
 if len(rows)>1:return '有多项安排提及偏好，请在记忆管理中明确保留哪一项，尚未更改。'
 row=rows[0] if rows else None
 if proposal['action']=='forget':
  if not row:return '未找到可可靠遗忘的安排提及偏好。'
  bubble_memory.mutate('delete',id=row['id'],revision=revision);return '已遗忘安排提及偏好，恢复默认询问；历史和备份未同时删除。'
 if proposal['action']=='update' and not row:return '没有可对应的安排提及偏好，请明确保存新的默认规则。'
 params=dict(text=proposal['evidence'],revision=revision,kind='preference',preference_key=KEY,preference_value=proposal['value'],scenario='general',scopes=['calendar'])
 # Never change an existing cloud permission or inherit the response-style grant.
 if row: bubble_memory.mutate('update',id=row['id'],**params)
 else:bubble_memory.mutate('save',cloud_allowed=False,**params)
 return '已保存安排提及时的默认方式：'+VALUES[proposal['value']]+' 云端许可未扩大。'
def receipt(emit,message):
 def send(event):
  if event.get('type')=='done' and message:
   event=copy.deepcopy(event);event['answer']=event.get('answer','')+'\n'+message
   if event.get('assistant_result',{}).get('data') is not None:event['assistant_result']['data']['message']=event['answer']
  emit(event)
 return send

def reminder_preview(p,selected,references,clock,cancel):
 """Read-only preparation. Existing card select/set is the sole explicit commit path."""
 import datetime as dt
 from zoneinfo import ZoneInfo
 import calendar_reminders as service,assistant_calendar as adapter,native_context
 if not selected and not any(p['source'].get(k) for k in ('title','date','reference')):return None,'尚未设置：独立提醒暂不支持。若是已有日程，请先选定具体事件；也可明确要求先创建日程，仍需预览确认。已保留事项与时间。'
 timing=(p.get('reminder') or {}).get('timing');lead=(p.get('reminder') or {}).get('lead_minutes')
 if timing not in ('default','explicit') or (timing=='explicit' and type(lead) is not int):raise ValueError('请说明提前多久提醒；已保留原事项。')
 if timing=='default' and lead is not None:raise ValueError('默认提前量与显式数值不一致，请确认。')
 lead=service.minutes(lead)
 if selected:
  original=selected.get('original',selected)
  if p['source'].get('title') and p['source']['title']!=original['title']:raise ValueError('原事项与所选事件不同，请先换选；没有绑定其他同名事项。')
  fresh=service.read_event(original['event_id'],original['calendar_id'],cancel)
  if not fresh:raise ValueError('原事件已不存在，尚未设置提醒。')
  if original.get('version')!=fresh['version']:raise ValueError('原事件已变化，请重新选择核对。')
  hits=[fresh]
 elif p['source'].get('reference')=='last_event':
  ids={(e['calendar_id'],e['event_id']):e for e in references if e.get('event_id') and e.get('calendar_id')}
  if len(ids)!=1:raise ValueError('当前没有唯一可靠选定的原日程，请先选择具体事件。')
  return reminder_preview(p,{'original':next(iter(ids.values()))},[],clock,cancel)
 elif p['source'].get('title') or p['source'].get('date'):
  hits,_=adapter.read_source(p['source'],cancel,clock)
  if len(hits)==1:
   fresh=service.read_event(hits[0]['event_id'],hits[0]['calendar_id'],cancel)
   if not fresh:raise ValueError('原事件已不存在，尚未设置提醒。')
   hits=[fresh]
 else:return None,'尚未设置：独立提醒暂不支持。可以先明确创建日程并确认，再从该事件设置助手提醒；已保留给出的事项和时间。'
 now=dt.datetime.fromisoformat(clock['now']);start=dt.datetime.fromisoformat(hits[0]['start']);zone=ZoneInfo(clock['timezone']);date=start.astimezone(zone).date();a=dt.datetime.combine(date,dt.time(),zone);b=dt.datetime.combine(date+dt.timedelta(days=1),dt.time(),zone)
 request={'lead_minutes':lead,'timing':timing,'enabled':service.enabled(),'state':'preview','multiple':len(hits)>1}
 if len(hits)==1:
  due=start-dt.timedelta(minutes=lead)
  if due<=now:raise ValueError('所选提前时间已经过去，请调整提前量或选择未来日程；尚未设置。')
  request['due']=due.isoformat()
 meta=native_context.calendar_list({'clock':clock,'calendar':{'events':hits,'start':a.isoformat(),'end':b.isoformat(),'total':len(hits)}});meta['reminder_request']=request
 message='有多项匹配，请在具体日程卡片中选择并确认；没有默认处理第一项。' if len(hits)>1 else '请核对事件时间及助手提醒时间，再点击卡片确认设置；尚未保存提醒。'
 if not request['enabled']:message+=' 助手提醒当前关闭，请从聊天设置→本机日程提醒启用后返回本卡；不会自动开启。'
 return meta,message

def compact_hint(hint):
 """Deduplicate the wire description, never mutate the authoritative JSON schema."""
 import json
 from style_preferences import VALUES as styles
 result=copy.deepcopy(hint)
 context=result.get('properties',{}).get('answer_context')
 if context:
  context['properties']['requirements']['items']={'type':'object','properties':{'key':{'enum':list(styles)},'value':{'type':'string'},'evidence':{'type':'string','minLength':1,'maxLength':200}},'required':['key','value','evidence'],'additionalProperties':False,'description':'Allowed key/value pairs: '+json.dumps({k:list(v) for k,v in styles.items()},separators=(',',':'))}
 # These annotations duplicate CONTEXT_PROMPT / USER_EXPRESSION_EVIDENCE_V2.
 # Keep every validation keyword and the nonduplicated file-method descriptions.
 # No user/history/memory data is shortened, and the full response schema remains
 # authoritative. This is task-scoped wire documentation, not relaxed validation.
 properties=result.get('properties',{})
 for name in ('domain','mode','needs_history'):
  if context:context['properties'].get(name,{}).pop('description',None)
 for name in ('scenario','applicability'):
  properties.get('preference',{}).get('properties',{}).get(name,{}).pop('description',None)
 properties.get('reply',{}).pop('description',None)
 return result


def reminder_route(p,selected,references,clock,cancel,text):
 """No new scheduler: query -> existing card OR explicit event-carrier preview."""
 import datetime as dt,calendar_create,calendar_dates
 source=p['source'];values=copy.deepcopy(p['create']);request=p.get('reminder') or {};policy=request.get('calendar_policy')
 if policy=='excluded':return None,None,'尚未设置：你已明确不要加入日历；当前不支持独立提醒，没有创建日历事件或提醒。'
 # Legacy existing-event requests stay supported; absent new consent semantics never creates an event.
 title=source.get('title') or values.get('title');date=source.get('date') or values.get('date')
 if selected and (source.get('reference')=='last_event' or not title or title==selected.get('original',selected).get('title')):
  meta,message=reminder_preview(p,selected,references,clock,cancel);return meta,None,message
 if source.get('reference')=='last_event':
  meta,message=reminder_preview(p,None,references,clock,cancel);return meta,None,message
 if not title or not date:
  return None,None,'尚未设置：请补充'+('要提醒的事项' if not title else '事项发生日期')+'；已保留其他已知信息。'
 import assistant_calendar as adapter
 lookup=dict(source,title=title,date=date)
 hits,row=adapter.read_source(lookup,cancel,clock,allow_empty=True)
 if hits:
  # The existing path re-reads concrete identities and validates versions/type.
  matching=copy.deepcopy(p);matching['source']=lookup
  meta,message=reminder_preview(matching,None,[],clock,cancel);return meta,None,message
 if policy!='allowed':return None,None,'未找到已有日程。请确认是否允许通过日历事件承载这项提醒；尚未创建或设置。'
 if not values.get('start'):return None,None,'尚未设置：请补充事项的开始时间；已保留事项、日期和提醒提前量。'
 values.update(title=title,date=date,intent='create');values.pop('alarm_minutes',None)
 if p.get('_new_start'):calendar_dates.check_start(text,values['start'])
 event=calendar_create.validate(values,clock)
 timing=request.get('timing');lead=request.get('lead_minutes')
 if timing not in ('default','explicit') or (timing=='explicit' and type(lead) is not int) or (timing=='default' and lead is not None):raise ValueError('请说明提前多久提醒；已保留事项及时间。')
 import calendar_reminders as reminders
 lead=reminders.minutes(lead);due=dt.datetime.fromisoformat(event['start'])-dt.timedelta(minutes=lead)
 if due<=dt.datetime.fromisoformat(clock['now']):raise ValueError('提醒时间已经过去，请调整事项时间或提前量；尚未创建。')
 preview=calendar_create.preview_data(values,clock,cancel)
 preview['assistant_reminder']={'lead_minutes':lead,'timing':timing,'due':due.isoformat(),'status':'not_set'}
 preview['warnings'].append('会创建日历事件承载提醒，只有确认创建后才写入；不是独立提醒。')
 preview['warnings'].append(('默认提前30分钟；' if timing=='default' else '提前'+str(lead)+'分钟；')+'提醒时间：'+due.isoformat()+'。事件时长与提前量独立。')
 preview['warnings'].append('助手提醒'+('已开启，但仍未设置本次提醒。' if reminders.enabled() else '当前关闭，不会自动开启。')+'创建成功后在同一事件卡片确认设置；未设置系统日历提醒。')
 return None,preview,'请核对承载预览：将创建日历事件；助手提醒尚未设置。无需重新输入创建命令。'

def after_carrier_confirmation(data,body,c,task):
 """Separate receipts, trusted readback, existing reminder-card confirmation only."""
 import calendar_create,uuid
 preview=calendar_create.load(body.get('preview_id'),task.sid);request=preview.get('assistant_reminder')
 if not request:return data
 result=copy.deepcopy(data);result['assistant_reminder']=dict(request,status='not_set');result['message']='日历事件已创建并读回核对；助手提醒未设置，请在此事件卡片确认设置；开关关闭时先从设置启用。'
 try:
  task.check();event=result['event'];original=dict(event,event_id=result['event_id'])
  # A new identity is obtained only from verified native create/readback, never model text.
  plan={'source':{},'reminder':{'timing':'explicit','lead_minutes':request['lead_minutes']}}
  meta,_=reminder_preview(plan,{'original':original},[],__import__('native_context').system_clock(),task.cancel)
  task.check()
  c.calendar_snapshot=dict(meta,snapshot_id=uuid.uuid4().hex,epoch=c.epoch)
  c.calendar_snapshot['events']=[dict(e,card_token=uuid.uuid4().hex) for e in meta['events']]
  c.calendar_references=list(meta['events']);result['calendar_events']=copy.deepcopy(c.calendar_snapshot)
 except Exception:
  result['message']='日历事件已创建并读回核对；助手提醒未设置，当前无法核验提醒卡片。请查询该事件后设置，不要重复创建。'
 return result
