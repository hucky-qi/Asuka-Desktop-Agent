"""Explicit daily records, SAME guarded memory store. No background inference/calendar."""
import copy,datetime as dt,json,re,uuid,calendar
from zoneinfo import ZoneInfo
import backend.memory.bubble_memory as store, backend.native_context as native_context
FIELDS={'action','content','date_from','date_to','new_date','topic','target','resume','use_selected','summarize','self_report','evidence'}
SCHEMA={'type':['object','null'],'properties':{k:({'type':'string','enum':['save','query','update','forget','clarify','none']} if k=='action' else {'type':'boolean'} if k in ('resume','use_selected','summarize','self_report') else {'type':['string','null'],'maxLength':300}) for k in FIELDS},'required':sorted(FIELDS),'additionalProperties':False}
SCHEMA['properties']['date_basis']={'type':['string','null'],'enum':['occurred','recorded',None]}
PROMPT=(' DAILY_RECORD_V1: journal operates on explicitly requested saved daily records, not preferences/calendar. '
'Reporting an experience is not requesting storage: completed progress, personal facts, plans and corrections to this conversation stay chat unless the user requests a saved-record operation. '
'This intent distinction is identical in every language; self_report proves attribution, NOT permission to save. '
'Only a CURRENT explicit request to save/query/correct/forget a record selects journal; quoted, hypothetical or third-person requests are not authority. '
'Use CURRENT exact original clauses for all record fields; never translate/paraphrase evidence or content. '
'Continued record criteria are code-kept; do not resume them merely because chat shares a topic. Code executes. No success claims.')
def wire_schema():
 # Compact documentation; SCHEMA remains the full validation boundary.
 return ('null or {action:save|query|update|forget|clarify|none, '
 'content:CURRENT new record text/null (not query topic), date_from/date_to:RAW lookup dates/null, '
 'new_date:RAW occurrence date/null, topic/target:CURRENT exact substrings/null, '
 'resume:boolean reuse criteria, use_selected:boolean code-kept selection, summarize:boolean, '
 'self_report:boolean own experience incl implicit self (not quotes/others/hypotheses), '
 'evidence:nonempty CURRENT exact clause incl queries, date_basis?:occurred|recorded|null(no change)}. '
 'ALL fields except date_basis required. No other fields/IDs. Code calculates dates and enforces writes/permissions.')

def policy():return __import__('backend.memory.style_preferences', fromlist=['*']).policy('record_cloud')
def allowed(r):
 if not r.get('journal'):return False
 if r.get('cloud_allowed') is True:return True
 p=policy();return bool(p['enabled'] and p['id'] and r.get('record_cloud_policy')==p['id'] and not r.get('record_cloud_denied'))
def validate_meta(m):
 if not isinstance(m,dict) or set(m)!={'occurred_on','recorded_at','timezone','entry','message_id','previous_version'}:raise ValueError('日常记录元数据不完整，未覆盖。')
 if m['occurred_on'] is not None:dt.date.fromisoformat(m['occurred_on'])
 store._date(m['recorded_at'],False);ZoneInfo(m['timezone'])
 if m['entry'] not in ('desktop','web') or not isinstance(m['message_id'],str) or not m['message_id'] or (m['previous_version'] is not None and type(m['previous_version']) is not int):raise ValueError('记录来源或版本无效。')
def day(value,clock,end=False):
 if value is None:return None
 today=dt.date.fromisoformat(clock['date']);v=value.strip();offset={'昨天':-1,'昨日':-1,'前天':-2,'yesterday':-1,'today':0}
 if v in offset:return (today+dt.timedelta(days=offset[v])).isoformat()
 if v in ('这周','本周','上周','this week','last week'):
  a=today-dt.timedelta(days=today.weekday()+(7 if v in ('上周','last week') else 0));return (a+dt.timedelta(days=6 if end else 0)).isoformat()
 if v in ('本月','这个月','this month'):
  return today.replace(day=calendar.monthrange(today.year,today.month)[1] if end else 1).isoformat()
 n=re.fullmatch(r'最近(\d{1,3})天',v)
 if n:
  count=int(n[1])
  if not 1<=count<=366:raise ValueError('请查询1至366天范围。')
  return (today if end else today-dt.timedelta(days=count-1)).isoformat()
 return __import__('backend.calendar.calendar_dates', fromlist=['*']).resolve(v,clock).isoformat()
def rows(c):
 return [r for r in store.snapshot()['items'] if r.get('journal') and store.visible(r,c.entry,c.memory_owner)]
def search(c,criteria):
 rs=rows(c);basis=criteria.get('basis','occurred');lo=criteria.get('from');hi=criteria.get('to');topic=criteria.get('topic') or '';target=criteria.get('target') or ''
 def match(r):
  date=r['journal']['occurred_on'] if basis=='occurred' else dt.datetime.fromisoformat(r['journal']['recorded_at']).astimezone(ZoneInfo(r['journal']['timezone'])).date().isoformat()
  return (not lo or bool(date and date>=lo)) and (not hi or bool(date and date<=hi)) and all(not q or store._norm(q) in store._norm(r['text']) for q in (topic,target))
 return sorted([r for r in rs if match(r)],key=lambda r:(r['journal']['occurred_on'] or '',r['created_at'] or '',r['id']),reverse=True)
def criteria(c,p,clock):
 prior=getattr(c,'record_context',{}) if p.get('resume') else {};d=copy.deepcopy(prior)
 if p.get('date_basis'):d['basis']=p['date_basis']
 if p.get('date_from'):
  d['from']=day(p['date_from'],clock);d['to']=day(p.get('date_to') or p['date_from'],clock,True)
 elif p.get('date_to'):d['to']=day(p['date_to'],clock,True)
 for k in ('topic','target'):
  if p.get(k) is not None:d[k]=p[k]
 if d.get('from') and d.get('to') and d['from']>d['to']:raise ValueError('日期范围前后矛盾，请补充正确范围。')
 return d
def output(records,message=''):
 lines=[message] if message else []
 lines.append('日常记录 · '+str(len(records))+'项（按发生日期查询；未注明时刻不推测）')
 for r in records[:30]:lines.append((r['journal']['occurred_on'] or '发生日期未注明')+' · '+r['text'])
 if len(records)>30:lines.append('仅显示前30项，请缩小日期或主题；未显示项仍保留。')
 if not records:lines.append('当前范围未找到记录，不代表没有发生。')
 return '\n'.join(lines)
def choose_list(c,rs,plan):
 c.record_pending={'epoch':c.epoch,'revision':store.snapshot()['revision'],'plan':copy.deepcopy(plan),'choices':{uuid.uuid4().hex:{'id':r['id'],'version':r['version']} for r in rs[:30]}}
 return [{'token':k,'id':v['id'],'version':v['version'],'text':next(r['text'] for r in rs if r['id']==v['id']),'date':next(r['journal']['occurred_on'] for r in rs if r['id']==v['id'])} for k,v in c.record_pending['choices'].items()]
def perform(c,p,text='',check=lambda:None,revision=None,selected=None,from_api=False,rid=None,options=None):
 if not isinstance(p,dict) or set(p)-{'date_basis'}!=FIELDS:raise ValueError('日常记录结构不完整，未写入。')
 __import__('jsonschema').validate(p,SCHEMA)
 clock=native_context.system_clock();action=p['action'];check();options=options or {}
 extras={k:options[k] for k in ('scopes','cloud_allowed','audience') if k in options}
 if 'audience' in extras:extras['owner']=c.entry if extras['audience']=='entry' else c.memory_owner if extras['audience']=='session' else None
 if action in ('none','clarify'):return {'message':'请说明要记录的内容、日期或具体要改/忘的那条记录。','records':[]}
 if not from_api:
  e=p['evidence'] or '';at=text.find(e)
  if not e or at<0:raise ValueError('记录原文证据与当前用户输入不符，未写入。')
  for quote in re.finditer(r'“[^”]*”|「[^」]*」|"[^"\n]*"',text):
   if quote.start()<=at and at+len(e)<=quote.end():raise ValueError('引用内容不作为本人真实记录保存。')
  for k in ('content','date_from','date_to','new_date','topic','target'):
   if p.get(k) and p[k] not in text:raise ValueError('记录字段没有当前原文依据，未猜测补齐。')
 if action=='save':
  if not p['self_report']:raise ValueError('没有本人明确记录要求，未保存。')
  content=store._valid_text(p['content']);date=day(p['new_date'],clock)
  meta=dict(occurred_on=date,recorded_at=clock['now'],timezone=clock['timezone'],entry=c.entry,message_id=rid or uuid.uuid4().hex,previous_version=None)
  result=store.mutate('save',text=content,journal=meta,revision=revision,kind='fact',record_policy=True,**extras)
  rs=[r for r in rows(c) if store._norm(r['text'])==store._norm(content) and r['journal']['occurred_on']==date];c.record_selected={'id':rs[0]['id'],'version':rs[0]['version']};c.record_context={};return {'message':'已记下这条日常记录；没有创建日历或提醒。','records':rs,'saved':True}
 filt=criteria(c,p,clock);c.record_context=filt;rs=search(c,filt)
 if selected:rs=[r for r in rows(c) if r['id']==selected['id'] and r['version']==selected['version']]
 elif p.get('use_selected'):
  ref=getattr(c,'record_selected',None)
  if not ref:raise ValueError('当前没有可靠选定的日常记录，请补充日期或事项。')
  rs=[r for r in rows(c) if r['id']==ref['id'] and r['version']==ref['version']]
 if action=='query':
  c.record_selected={'id':rs[0]['id'],'version':rs[0]['version']} if len(rs)==1 else None
  return {'message':output(rs),'records':rs[:30],'total':len(rs),'summary_requested':p['summarize']}
 if action not in ('update','forget'):raise ValueError('未知日常记录操作。')
 if not selected and not p.get('use_selected') and not any(filt.get(k) for k in ('from','to','topic','target')):return {'message':'请说明要更改或忘记的原日期、事项，或先选定一条记录。','records':[]}
 if len(rs)!=1:
  if len(rs)>1:return {'message':'有多条匹配记录，请选定具体一条；尚未更改。','records':rs[:30],'choices':choose_list(c,rs,p),'total':len(rs)}
  return {'message':'没有找到可靠匹配，尚未更改；请补充原日期或事项。','records':[]}
 r=rs[0];check();revision=store.snapshot()['revision'] if revision is None else revision
 if action=='forget':store.mutate('delete',id=r['id'],revision=revision);c.record_selected=None;c.record_pending=None;return {'message':'已忘记这条日常记录，后续不再召回；历史聊天和备份不会同时删除。','records':[]}
 if p['content'] is None and not p['new_date']:return {'message':'请补充要纠正的内容或发生日期，已保留选定记录。','records':[r]}
 content=store._valid_text(r['text'] if p['content'] is None else p['content']);meta=copy.deepcopy(r['journal']);meta['previous_version']=r['version']
 if p['new_date']:meta['occurred_on']=day(p['new_date'],clock)
 store.mutate('update',id=r['id'],text=content,revision=revision,journal=meta,record_policy=True,**extras);r=next(x for x in rows(c) if x['id']==r['id']);c.record_selected={'id':r['id'],'version':r['version']};c.record_pending=None
 return {'message':'已纠正指定日常记录，旧版本不再召回。','records':[r],'saved':True}
def blank(**kw):return dict(action='query',content=None,date_from=None,date_to=None,new_date=None,topic=None,target=None,resume=False,use_selected=False,summarize=False,self_report=True,evidence='',**kw) if not kw else {**blank(),**kw}
def api(c,body):
 from backend.memory.assistant_memory import MemoryAccess
 action=body['action']
 if action=='record_cloud_config':__import__('backend.memory.style_preferences', fromlist=['*']).configure(body.get('enabled'),body.get('policy_revision'),'record_cloud');__import__('backend.memory.periodic_distillation',fromlist=['*']).invalidate_sources();return MemoryAccess.list_for(c)
 selected=None;revision=body.get('revision')
 if action=='record_choose':
  pending=getattr(c,'record_pending',None);token=body.get('token')
  if not pending or pending['epoch']!=c.epoch or token not in pending['choices']:raise ValueError('记录选择已过期，请重新查询。')
  selected=pending['choices'][token];p=pending['plan'];revision=pending['revision'];c.record_pending=None
 else:
  verb={'record_save':'save','record_query':'query','record_update':'update','record_forget':'forget'}.get(action)
  if not verb:raise ValueError('未知记录操作。')
  p=blank(date_basis=body.get('date_basis','occurred'),action=verb,content=body.get('text'),date_from=body.get('date_from'),date_to=body.get('date_to'),new_date=body.get('date'),topic=body.get('topic'),target=body.get('target'),resume=body.get('resume') is True)
  if verb=='save' and body.get('explicit') is not True:raise ValueError('需要明确记录操作。')
  if verb in ('update','forget') and body.get('id'):
   r=next((r for r in rows(c) if r['id']==body['id']),None)
   if not r:raise ValueError('该记录不属于当前范围或已遗忘。')
   if type(revision) is not int:raise ValueError('请刷新记录版本后操作。')
   selected={'id':r['id'],'version':r['version']}
 result=perform(c,p,from_api=True,revision=revision,selected=selected,options=body)
 return dict(MemoryAccess.list_for(c),message=result['message'],record_results=result['records'],record_choices=result.get('choices',[]))
def execute(c,sid,epoch,rid,cancel,text,plan,emit,task,adapter=None,reply=''):
 from backend.assistant.bubble_chat import LOCK
 with LOCK:
  task.check()
  try:result=perform(c,plan,text,check=task.check,revision=getattr(adapter,'preference_revision',None),rid=rid)
  except (ValueError,OSError) as error:
   task.check();result={'message':'日常记录未完成：'+(str(error) if isinstance(error,ValueError) else '保存失败，请查看记忆恢复状态；未报告成功。'),'records':[],'operation_failed':True}
  # Record operation receipts are code-owned. Model prose cannot pre-claim a commit.
  # The ordinary-chat answer path is unchanged and never replaced by this adapter.
  if result.get('summary_requested') and result['records']:
   # Local list is usable without record export. Never silently send even a subset.
   if not all(allowed(r) for r in result['records']):result['message']+='\n记录未获完整云端许可，已留在本机；如需归纳，请在记忆管理中设置日常记录许可。'
   else:result['message']+='\n本版提供有依据的本地记录列表；自动归纳尚未接入，没有额外发送记录。'
  c.last_domain='records' # no record answer is appended to exportable chat history
  outcome=task.result('daily_records',result,'failed' if result.get('operation_failed') else 'needs_input' if result.get('choices') else 'completed');c.task_view=outcome['task'];c.request=None;c.cancel=None;c.active_domain=None
  emit({'type':'done','state':'complete','answer':result['message'],'request_id':rid,'assistant_result':outcome})
