"""Calendar understanding adapter; all identity, dates, previews and writes remain code-owned."""
import json,copy,uuid,re
import backend.native_context as native_context, backend.calendar.calendar_dates as calendar_dates, backend.calendar.calendar_create as calendar_create, backend.calendar.calendar_update as calendar_update, backend.calendar.calendar_delete as calendar_delete, backend.settings as settings
from backend.assistant.assistant_tools import TOOLS, Outcome
S={'type':['string','null']};I={'type':['integer','null']};B={'type':['boolean','null']}
def obj(fields):return {'type':['object','null'],'properties':fields,'additionalProperties':False}
QUERY=obj({'date':S,'period':{'type':['string','null'],'enum':['day','morning','afternoon','evening',None]},'calendar_name':S})
CREATE=obj({'title':S,'date':S,'start':S,'end':S,'end_date':S,'duration_minutes':I,'next_day':B,'all_day':B,'timezone':S,'recurrence':S,'alarm_minutes':I})
SOURCE=obj({'title':S,'date':S,'calendar_name':S,'old_start':S,'reference':{'type':['string','null'],'enum':['last_event',None]}})
CHANGES=obj({'title':S,'date':S,'start':S,'end':S,'end_date':S,'duration_minutes':I,'shift_minutes':I,'all_day':B})
SCHEMA={'type':'object','properties':{'operation':{'type':'string','enum':['clock','query','create','offer','update','delete','reminder','clarify','none','unsupported']},'query':QUERY,'create':CREATE,'source':SOURCE,'changes':CHANGES,'resume':{'type':'boolean'},'reply':{'type':'string'}},'required':['operation','query','create','source','changes','resume','reply'],'additionalProperties':False}
CREATE['description']='Event occurrence fields, ALSO REQUIRED for operation=reminder when a new subject/date/start is stated. Do not leave null just because operation is reminder. These fields prepare a calendar-carrier preview, never execute.'
SOURCE['description']='Conditions for an already existing event only; never replace new-event occurrence fields. For an explicit event reference, preserve it here.'
SCHEMA['properties']['reminder']=obj({'timing':{'enum':['default','explicit','unknown',None]},'lead_minutes':I,'calendar_policy':{'enum':['allowed','excluded','existing_only','unknown',None]}})
SCHEMA['properties']['start_time']=obj({'kind':{'enum':['relative','absolute','unknown']},'section':{'enum':['create','changes']},'expression':{'type':'string','minLength':1,'maxLength':120}})
SCHEMA['properties']['start_time']['required']=['kind','section','expression']
PROMPT='''UNIFIED_CALENDAR_V1 只提取意图和字段，不执行操作。clock=本机时间日期，query=查询安排，create=明确要求加入单次日程，offer=仅陈述自己的安排但未要求创建，update=修改已有事件，delete=要求删除单个事件，none=普通聊天或引用/假设讨论，reminder=明确单次提醒请求；已有事项填source，新事项填create（事件发生时间），两者与reminder提前量分开；代码先查已有事件，无匹配且未排除日历时生成会创建事件的承载预览，不是独立提醒系统。系统alarm/重复/批量不支持。reminder.calendar_policy必须表达allowed=没有排除日历的明确单次请求，excluded=明确只提醒不要日历，existing_only=只操作已有事件，unknown=意图不明。不因缺少“创建日历”字样要求用户重说。reminder字段timing=explicit（lead_minutes为明确提前量）或default（lead_minutes=null，代码显示默认30分钟）；含糊用unknown。未来自己的安排未明确要求创建只能offer，知识/功能咨询、引用、假设、否定用none。明确安排请求用create，即使不含“加入日历”字样。信息缺失仍保留已知operation及字段，由代码具体追问。不得假造身份或成功结果。query.date为查询原文日期表达，period是时段；未指定子时段可day。create是事件发生字段容器，不是操作限定：reminder操作也必须把用户已说的新事项title、发生date/start填在create，不能因操作是reminder而置null；只有确实未说才留空。source仅用于已有事件条件。create提取新事件字段：未说结束或时长则不填，由代码默认30分钟；缺开始时间不可猜；1至12点未说上午下午不能代选。source仅原事件的标题、日期、所属日历、明确旧开始时刻；明确指代它/刚才那个才reference=last_event，代码查验。changes仅想修改的新字段；不能把目标时间当查找原事件的时间。未要求换日期不能补date；仅改开始时刻不填结束/时长，由代码保留原时长。相对延后正shift_minutes，提前负；改时长duration_minutes。日期保留今天/明天/下周三等原表达交给代码，不猜系统时间、不擅自纠正过去日期。pending_fields.stage=uncommitted_carrier表示未创建的承载草稿，不是已有日程；纠正它保持operation=reminder，把新时间放create，不用update/source，也不丢原标题或提前量。用户补充或纠正当前待补任务时resume=true，未提及字段不填；切换到新任务resume=false。模型输出不含event_id/calendar_id/序号。已选事件仅是布尔元数据，不需要其私人内容或重新选择它。当前用户的问题和待补字段不授权写入、云端外发或提醒。'''
SCHEMA['properties']['resume']['description']='True for supplements/corrections to pending_fields, even when filling a previously missing title. False only for a genuinely separate task. Never copy old fields while claiming a new task.'
PROMPT+=' uncommitted_create is an UNWRITTEN create draft: corrections resume create, never select an existing event. Start_time: CURRENT exact start expression only, kind=relative/absolute/unknown, section=create/changes. Relative elapsed units stay RAW; code adds to receipt clock. No arithmetic, no duration/advance here. Supplements leave start_time null and MUST set resume=true, including filling a missing title or duration. Return only CURRENT fields, never copy pending date/start into a new task. Corrections supply new evidence. Never replay prior evidence.'
def enabled():return getattr(settings,'UNIFIED_CALENDAR_ENABLED',False)
def public_pending(p):
 return {k:copy.deepcopy(p[k]) for k in ('operation','query','create','source','changes','reminder','stage') if k in p} if p else None
def extract(text,provider,cancel,pending=None,selected=False,operation=None,clock=None):
 clock=clock or native_context.system_clock();messages=[{'role':'system','content':PROMPT+json.dumps({'clock':clock,'pending_fields':public_pending(pending),'has_selected_event':bool(selected),**({'chosen_operation':operation,'constraint':'Operation is code-kept from the understanding step; supply fields, do not reclassify.'} if operation else {})},ensure_ascii=False)},{'role':'user','content':text}]
 result=provider.chat(model=settings.MODEL,messages=messages,format=SCHEMA,stream=False,think=False,cancel=cancel,options={'temperature':0,'num_predict':600,'num_ctx':6144})
 try:plan=json.loads(result.message.content.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip());__import__('jsonschema').validate(plan,SCHEMA)
 except (ValueError,TypeError,__import__('jsonschema').ValidationError):raise ValueError('日历意图或字段格式无效，未执行日历操作。') from None
 # Clarification is a reply state, not a replacement for a code-kept draft.
 # Resume is semantic only; identity/fields/confirmation remain code-owned.
 if operation=='clarify' and pending and pending.get('stage') in ('uncommitted_create','uncommitted_carrier') and plan.get('resume') and plan['operation'] in (pending['operation'],'clarify'):
  operation=pending['operation']
 if operation is not None and plan['operation']!='unsupported':plan['operation']=operation
 # Unsupported can only veto execution; it never changes to another executable action.
 for section in ('query','create','source','changes'):plan[section]=plan[section] or {}
 return plan,clock

def merge(plan,pending):
 result=copy.deepcopy(plan)
 result['reminder']={k:v for k,v in (plan.get('reminder') or {}).items() if v is not None}
 if plan.get('resume') and pending and plan['operation']==pending['operation']:result['reminder']={**pending.get('reminder',{}),**result['reminder']}
 if plan.get('reminder',{} ) and plan['reminder'].get('timing') is not None:result['reminder']['lead_minutes']=plan['reminder'].get('lead_minutes')
 for section in ('query','create','source','changes'):result[section]={k:v for k,v in plan[section].items() if v is not None}
 if plan.get('resume') and pending and plan['operation']==pending['operation']:
  for section in ('query','create','source','changes'):result[section]={**pending.get(section,{}),**result[section]}
  result['_selected']=copy.deepcopy(pending.get('_selected'))
  if pending.get('stage'):result['stage']=pending['stage']
 if plan.get('resume') and pending and ((pending['operation']=='reminder' and plan['operation']=='create') or (pending['operation']=='offer' and plan['operation']=='reminder')):result['create']={**pending.get('create',{}),**result['create']}
 change=plan.get('changes',{})
 if change.get('shift_minutes') is not None:
  for k in ('date','start','end','end_date','duration_minutes'):
   if change.get(k) is None:result['changes'].pop(k,None)
 elif any(change.get(k) is not None for k in ('date','start','end','end_date','duration_minutes')):result['changes'].pop('shift_minutes',None)
 if change.get('duration_minutes') is not None and change.get('end') is None:result['changes'].pop('end',None);result['changes'].pop('end_date',None)
 if change.get('end') is not None and change.get('duration_minutes') is None:result['changes'].pop('duration_minutes',None)
 return result

def validate(args):
 if set(args)!={'text','plan','clock','selected','references','owner'}:raise ValueError('日历工具参数无效')
 if args['plan']['operation'] not in SCHEMA['properties']['operation']['enum']:raise ValueError('日历操作不支持')

def read_source(source,cancel,clock,allow_empty=False):
 if not source.get('title'):raise ValueError('请补充要处理的原事项；尚未修改或删除。')
 if not source.get('date'):raise ValueError('请补充原事件日期；已保留事项和目标时间。')
 day=calendar_dates.resolve(source['date'],clock).isoformat();row=calendar_create.bridge({'action':'read','date':day,'period':'day'},cancel);calendar_create.require_ok(row)
 if row.get('partial'):raise ValueError('原事件查询覆盖不完整，请缩小范围，尚未生成可确认操作。')
 hits=[x for x in row.get('events',[]) if source['title'].casefold() in x.get('title','').casefold() and (not source.get('calendar_name') or source['calendar_name']==x.get('calendar_title'))]
 if not hits and not allow_empty:raise ValueError('该日期没有可靠匹配的原事件，尚未修改或删除。')
 return hits,row

def run(args,task,context):
 p=copy.deepcopy(args['plan']);op=p['operation'];clock=args['clock'];text=args['text'];cancel=task.cancel;selected=copy.deepcopy(args['selected'] or p.get('_selected'));references=args['references'];extra={};message='';state='completed';pending=None
 task.stage('executing','generating')
 try:
  if p.get('_time_error'):raise ValueError(p['_time_error'])
  if op=='clock':message=clock['now']+' · '+clock['weekday']+' · '+clock['timezone']
  elif op=='query':
   q=p['query']
   if q.get('calendar_name'):raise ValueError('目前读取所有已授权日历；指定单个日历查询尚未支持，请确认该范围。')
   if not q.get('date'):raise ValueError('请补充要查询哪一天的安排。')
   tokens=calendar_dates.DATE_TOKEN.findall(calendar_dates.plain(text))
   if len(tokens)>1:
    days={calendar_dates.resolve(t,clock) for t in tokens}
    if len(days)>1:raise ValueError('目前一次查询一个明确日期或当日时段，请缩小查询范围。')
   # Reuse the old hard date/period validator without a second inference.
   from types import SimpleNamespace
   class ValidatedFields:
    def chat(self,**unused):return SimpleNamespace(message=SimpleNamespace(content=json.dumps({'kind':'calendar','operation':'read',**q})))
   intent=native_context.parse_intent(text,ValidatedFields(),cancel,previous='code validation only')
   data=native_context.context(text,None,cancel,intent=intent);error=native_context.direct_error(data)
   if error:calendar_create.require_ok(data['calendar'])
   meta=native_context.calendar_list(data);extra['calendar_events']=meta;message=native_context.list_text(meta)
  elif op=='create':
   values=dict(p['create'],intent='create');source_day=calendar_dates.from_source(text,clock)
   if source_day and not p.get('_relative_time'):values['date']=source_day
   if values.get('start') and args['plan'].get('_new_start'):calendar_dates.check_start(text,values['start'])
   if p.get('reminder'):values.pop('alarm_minutes',None)
   preview=calendar_create.preview_data(values,clock,cancel,relative_time=p.get('_relative_time'))
   if p.get('reminder'):preview.setdefault('warnings',[]).append('本预览仅创建日程，尚未设置助手提醒或系统日历提醒；事件确认并取得可信身份后，再从该事件卡片确认提醒。')
   extra['create_preview']=preview
   if preview.get('calendar_status') not in ('ready',None):extra['_recovery_status']=preview['calendar_status']
   message=preview.get('message') or '请核对日程预览；尚未创建，明确确认后才写入。';state='needs_input'
  elif op=='reminder':
   if not selected and not p['source'] and p.get('reminder',{}).get('calendar_policy')=='allowed':p['stage']='uncommitted_carrier'
   meta,preview,message=__import__('backend.calendar.calendar_semantics', fromlist=['*']).reminder_route(p,selected,references,clock,cancel,text)
   if meta:extra['calendar_events']=meta
   if preview:extra['create_preview']=preview
   state='needs_input'
  elif op=='offer':extra['offer']={'id':uuid.uuid4().hex,'text':text,'epoch':task.version,'clock':copy.deepcopy(clock)};message='需要把这件事加入日历吗？选择需要后先预览，确认后才写入。';state='needs_input'
  elif op in ('update','delete'):
   source=p['source'];changes=p['changes']
   if selected and source.get('title') and source['title']!=selected['original']['title']:raise ValueError('原事项与当前所选事件不同，请取消当前选择并明确新对象，尚未修改。')
   if not selected:
    if source.get('reference')=='last_event' and not (source.get('title') and source.get('date')):
     ids={e.get('event_id'):e for e in references if e.get('event_id')}
     if source.get('title'):ids={k:e for k,e in ids.items() if source['title'].casefold()==e.get('title','').casefold()}
     if len(ids)!=1:raise ValueError('当前对话还没有唯一选定日程，请补充日期和事项，例如：把明天的面试改到晚上8点。')
     original=calendar_update.inspect(next(iter(ids)),{},cancel)['event'];selected={'id':uuid.uuid4().hex,'event_id':original['event_id'],'original':original,'changes':{}}
    else:
     hits,row=read_source(source,cancel,clock)
     old_start=source.get('old_start');exact=[e for e in hits if __import__('datetime').datetime.fromisoformat(e['start']).strftime('%H:%M')==old_start] if old_start else hits
     if len(exact)!=1:
      if op=='update' and changes:
       extra['update_selection']={'id':uuid.uuid4().hex,'state':'pending','candidates':hits if not exact else exact,'data':{'intent':'update','target':source,'changes':changes},'clock':clock};message='原时间不一致，请选择实际事件再核对。' if old_start and not exact else '有多个匹配日程，请明确选择原事件。'
      else:
       meta=native_context.calendar_list({'kind':'calendar','clock':clock,'calendar':dict(row,events=hits,total=len(hits))});extra['calendar_events']=meta;message='有多个匹配日程，请在目标卡片选择修改或删除；尚未执行。'
      return Outcome('calendar',{'message':message,'operation':op,'extras':extra},{'pending':p,'selected':None},'needs_input')
     original=calendar_update.inspect(exact[0]['event_id'],{'calendar_id':exact[0]['calendar_id']},cancel)['event'];selected={'id':uuid.uuid4().hex,'event_id':original['event_id'],'original':original,'changes':{}}
   if op=='delete':
    extra['delete_selected']=selected;message='请核对删除预览；将删除1项，只有点击确认删除才执行。';state='needs_input'
   else:
    if 'all_day' in changes:raise ValueError('本轮不支持全天事件转换，尚未修改。')
    values={k:None for k in CHANGES['properties'] if k!='all_day'};values.update(changes);values['operation']='update'
    preview,selected,clarification=calendar_update.selected_preview(text,None,cancel,selected,values=values,relative_time=p.get('_relative_time'))
    extra['calendar_edit']=selected
    if preview:extra['update_preview']=preview;message='请核对原事件、目标时间与冲突；尚未修改，明确确认后才更新原事件。'
    else:message=clarification
    p['_selected']=selected;p['changes']=copy.deepcopy(selected['changes']);state='needs_input'
  elif op in ('clarify','none'):message=p.get('reply') or '请明确要查询、创建、修改还是删除，以及缺少的日期或事项。';state='needs_input' if op=='clarify' else 'completed'
  else:message='目前不支持独立提醒、重复日程操作或批量处理；没有执行日历写入。';state='needs_input'
 except ValueError as error:
  if isinstance(error,calendar_create.CalendarUnavailable):extra['_recovery_status']=error.status
  if op=='create' and '下午' in str(error):p['create'].pop('start',None)
  message=__import__('backend.assistant.bubble_chat', fromlist=['*']).user_error(error,{'operation':'create' if op=='create' else 'update' if op=='update' else None})['error'];state='needs_input'
 if state=='needs_input':pending=p
 return Outcome('calendar' if op not in ('clarify','none') else 'clarification' if op=='clarify' else 'text',{'message':message,'operation':op,'extras':extra},{'pending':pending,'selected':selected},state)
TOOLS.register('calendar.task','calendar_read_preview',validate,run)

def execute(c,sid,epoch,rid,cancel,text,provider,emit,task,owner='local',restored_time=None):
 from backend.assistant.bubble_chat import LOCK
 with LOCK:
  prior=c.calendar_pending if c.calendar_pending and c.calendar_pending.get('owner')==owner else None
  pending=prior.get('fields') if prior else None;selected=copy.deepcopy(c.calendar_edit);reminder_selected=copy.deepcopy(getattr(c,'reminder_selection',None));refs=list(c.calendar_references)
 plan,clock=extract(text,provider,cancel,pending,bool(selected or reminder_selected or (pending or {}).get('_selected')),getattr(provider,'calendar_action',None),clock=getattr(task,'received_clock',None));task.check()
 # Resume a code-kept uncommitted draft, never turn its text into an existing event identity.
 if plan.get('resume') and pending and pending.get('stage') in ('uncommitted_carrier','uncommitted_create') and not selected and plan['operation']=='update':
  if plan['source'].get('title') and plan['source']['title']!=pending['create'].get('title'):raise ValueError('草稿与原事项不同，请明确是修改草稿还是已有日程。')
  plan['operation']=pending['operation'];plan['create']={**{k:v for k,v in plan['changes'].items() if k in CREATE['properties']},**plan['create']};plan['source']={};plan['changes']={}
  if (plan.get('reminder') or {}).get('calendar_policy')!='excluded':plan['reminder']=dict(plan.get('reminder') or {});plan['reminder']['calendar_policy']=pending.get('reminder',{}).get('calendar_policy','unknown')
 # Fill a code-kept missing title in an unwritten timed draft. A model's
 # new-task flag cannot discard that target when all supplied fields are compatible.
 if pending and pending.get('stage') in ('uncommitted_create','uncommitted_carrier') and pending.get('_relative_time') and plan['operation']==pending['operation'] and not plan.get('resume') and not pending.get('create',{}).get('title') and plan.get('create',{}).get('title') and not plan.get('start_time'):
  incoming={k:v for k,v in plan['create'].items() if v is not None and k!='title'}
  if not plan.get('query') and not plan.get('source') and not plan.get('changes') and all(v==pending.get('create',{}).get(k) for k,v in incoming.items()) and all(v is None or v==pending.get('reminder',{}).get(k) for k,v in (plan.get('reminder') or {}).items()):plan['resume']=True
 merged=merge(plan,pending)
 try:merged=calendar_dates.bind_start(plan,merged,pending,text,clock,restored_time)
 except ValueError as error:merged['_time_error']=str(error)
 if merged['operation']=='create' and merged.get('_relative_time'):merged['stage']='uncommitted_create'
 if plan['operation']=='reminder' and not selected:selected=reminder_selected
 if plan.get('create',{}).get('start'):merged['_new_start']=True
 if merged['operation']=='offer' and __import__('backend.calendar.calendar_semantics', fromlist=['*']).policy(c)=='quiet':merged.update(operation='none',reply='按你保存的安排提及偏好，本次未建议或创建日程；明确要求创建时仍先预览。')
 if plan['operation'] in ('reminder','create'):
  with LOCK:task.check();calendar_create.invalidate(sid)
 out=TOOLS.run('calendar.task',{'text':text,'plan':merged,'clock':clock,'selected':selected,'references':refs,'owner':owner},task,None,{'calendar_read_preview'})
 with LOCK:
  task.check();extra=out.data.pop('extras');state=out.context or {}
  c.calendar_pending=prior if plan['operation']=='none' else {'owner':owner,'fields':state['pending']} if state.get('pending') else None
  if state.get('selected'):c.calendar_edit=state['selected']
  if extra.get('_recovery_status'):
   extra['calendar_recovery']=recovery_draft(c,sid,epoch,state.get('pending') or merged,clock,extra.pop('_recovery_status'))
  else:c.calendar_recovery=None
  if extra.get('create_preview'):
   p=extra.pop('create_preview');calendar_create.invalidate(sid);extra['calendar_preview' if p['calendar_status']=='ready' else 'calendar_draft']=calendar_create.publish(p,sid,epoch)
  if extra.get('update_preview'):
   p=extra.pop('update_preview');extra['calendar_update_preview']=calendar_update.publish(p,sid,epoch);c.calendar_references=[p['original']]
  if extra.get('update_selection'):extra['calendar_update_selection']=calendar_update.publish(extra.pop('update_selection'),sid,epoch)
  if extra.get('delete_selected'):extra['calendar_delete_preview']=calendar_delete.preview(extra.pop('delete_selected'),sid,epoch)
  if extra.get('calendar_edit'):extra['calendar_edit']=calendar_update.edit_public(extra['calendar_edit'])
  if extra.get('offer'):c.calendar_offer=extra.pop('offer');extra['calendar_offer']={'id':c.calendar_offer['id']}
  if extra.get('calendar_events'):
   meta=extra['calendar_events'];c.calendar_snapshot=dict(meta,snapshot_id=uuid.uuid4().hex,epoch=epoch);c.calendar_snapshot['events']=[dict(e,card_token=uuid.uuid4().hex) for e in meta['events']];c.calendar_references=list(meta['events']);extra['calendar_events']={**c.calendar_snapshot,'events':c.calendar_snapshot['events'][:meta['shown']]}
  out.data.update(extra)
  result=task.result(out.kind,out.data,out.status);c.task_view=result['task'];c.last_domain='chat' if plan['operation']=='none' else 'calendar';c.request=None;c.cancel=None;c.active_domain=None
  emit({'type':'done','state':'complete','answer':out.data['message'],'request_id':rid,'assistant_result':result,**extra})
 return out

def action_validate(args):
 if args.get('operation') not in ('create','update','delete') or not isinstance(args.get('body'),dict):raise ValueError('确认操作类型无效')
def action_run(args,task,c):
 operation=args['operation'];body=args['body'];cancel=args.get('cancel',False)
 fn={'create':calendar_create.action,'update':calendar_update.action,'delete':calendar_delete.action}[operation]
 data=fn(body,c,task.sid,cancel=cancel)
 if operation=='create' and not cancel and data.get('status')=='verified':
  data=calendar_create.finish_reminder(data,body,c,task)
 status=data.get('status')
 if operation=='create' and not cancel and status in ('verified','uncertain','writing') and ((c.calendar_pending or {}).get('fields') or {}).get('stage') in ('uncommitted_create','uncommitted_carrier'):c.calendar_pending=None
 state='completed' if status=='verified' else 'writing' if status=='writing' else 'cancelled' if status=='cancelled' else 'needs_input' if status in ('preview','conflict_confirmation_required','duplicate_confirmation_required','uncertain','stale') else 'failed'
 return Outcome('calendar_confirmation',data,status=state)
TOOLS.register('calendar.confirmation','calendar_confirm',action_validate,action_run)
def action(operation,body,c,sid,token,cancel=False):
 from backend.assistant.assistant_tasks import Task
 version=c.epoch;event=__import__('threading').Event();task=Task(sid,body.get('request_id') or uuid.uuid4().hex,version,event,token,lambda:c.epoch==version)
 out=TOOLS.run('calendar.confirmation',{'operation':operation,'body':body,'cancel':cancel},task,c,{'calendar_confirm'})
 if cancel and out.data.get('status')!='writing' and ((c.calendar_pending or {}).get('fields') or {}).get('stage')!='uncommitted_carrier':c.calendar_pending=None
 result=task.result(out.kind,out.data,out.status);c.task_view=result['task']
 return {**out.data,'assistant_result':result}


def recovery_draft(c,sid,epoch,fields,clock,status):
 # Bounded field-only draft, NOT a selected identity, grant or confirmation token.
 import time
 plan={k:copy.deepcopy(fields.get(k)) for k in SCHEMA['properties'] if k in fields}
 for k in ('query','create','source','changes'):plan[k]=plan.get(k) or {}
 for k in ('query','create','source','changes'):
  for key in ('date','end_date'):
   if plan[k].get(key):
    try:plan[k][key]=calendar_dates.resolve(plan[k][key],clock).isoformat()
    except ValueError:pass
 plan.update(resume=False,reply='')
 if fields.get('_relative_time'):
  anchor=fields['_relative_time'];plan['start_time']={'kind':'relative','section':anchor['section'],'expression':anchor['expression']}
 draft={'id':uuid.uuid4().hex,'owner':getattr(c,'public_session_id',sid),'version':epoch,'entry':c.entry,'saved_at':time.time(),'plan':plan,'status':status,'source':'native_bridge'}
 if fields.get('_relative_time'):
  calendar_create.save({'id':draft['id'],'state':'relative_recovery','session_id':sid,'draft':draft,'relative_time':copy.deepcopy(fields['_relative_time'])})
 c.calendar_recovery=draft;return draft

def recover(body,c,sid):
 import time, threading, jsonschema, backend.assistant.task_status as task_status
 from backend.assistant.bubble_chat import LOCK
 draft=body.get('draft')
 if not isinstance(draft,dict) or set(draft)!={'id','owner','version','entry','saved_at','plan','status','source'}:raise ValueError('恢复草稿格式无效，未执行。')
 if type(draft['version']) is not int or not isinstance(draft['id'],str) or not re.fullmatch('[a-f0-9]{32}',draft['id']):raise ValueError('恢复草稿身份或版本格式无效。')
 if len(json.dumps(draft,ensure_ascii=False).encode())>8192 or type(draft['saved_at']) not in (int,float) or not 0<=time.time()-draft['saved_at']<=86400:raise ValueError('恢复草稿已过期，请明确新的请求。')
 if draft['entry']!=c.entry or draft['source']!='native_bridge':raise ValueError('恢复入口不匹配。')
 try:jsonschema.validate(draft['plan'],SCHEMA)
 except jsonschema.ValidationError:raise ValueError('草稿包含不支持的字段，未恢复对象、许可或确认。') from None
 if draft['plan']['operation'] not in ('query','create','reminder','update','delete'):raise ValueError('该草稿不属于可继续的日历任务。')
 restored_time=None
 path=calendar_create.ROOT/(draft['id']+'.json')
 if path.exists():
  saved=__import__('json').loads(path.read_text())
  if saved.get('state')=='relative_recovery':
   if saved.get('draft')!=draft:raise ValueError('相对时间恢复记录已变化，未重新起算。')
   restored_time=saved['relative_time'];calendar_dates.relative_target(restored_time)
 if draft['plan'].get('start_time') and draft['plan']['start_time'].get('kind')=='relative' and restored_time is None:raise ValueError('缺少原相对时间基准，请明确目标时刻，未重新起算。')
 with LOCK:
  owner=getattr(c,'public_session_id',sid);prior=getattr(c,'calendar_recovery',None)
  current=draft['owner']==owner and draft['version']==c.epoch and prior and draft==prior
  restarted=draft['owner']==getattr(c,'recovery_parent',None) and c.epoch==0
  if c.request or not (current or restarted):raise ValueError('草稿或任务已经变化，请使用当前请求；未恢复旧确认。')
  if getattr(c,'daily_data_controls',{}).get('calendar_intent') is False:raise ValueError('此会话的日历资料范围已关闭，请从设置恢复；未绕过许可。')
  c.epoch+=1;c.recovery_parent=None;c.calendar_pending=None;c.calendar_edit=None;c.calendar_references=[];c.reminder_selection=None
  epoch=c.epoch;rid=body.get('request_id') or uuid.uuid4().hex;cancel=threading.Event();c.request=rid;c.cancel=cancel;c.active_domain='calendar'
  token=task_status.begin(rid,'bubble');c.task_token=token
 from backend.assistant.assistant_tasks import Task
 from types import SimpleNamespace
 plan=copy.deepcopy(draft['plan']);plan['resume']=False
 class RecoveredFields:
  # Schema-validated fields, zero inference or paid retry. Still uses the same tools.
  calendar_action=plan['operation']
  def chat(self,**unused):return SimpleNamespace(message=SimpleNamespace(content=json.dumps(plan)))
 task=Task(sid,rid,epoch,cancel,token,lambda:c.epoch==epoch and c.request==rid);events=[]
 try:
  execute(c,sid,epoch,rid,cancel,'',RecoveredFields(),events.append,task,owner=((getattr(c,'cloud_grant',None) or {}).get('id') or 'daily:'+c.memory_owner) if c.selected_provider=='deepseek' else 'local',restored_time=restored_time)
  return events[-1]
 finally:
  with LOCK:
   if c.epoch==epoch:c.request=None;c.cancel=None
  task_status.finish(token)
