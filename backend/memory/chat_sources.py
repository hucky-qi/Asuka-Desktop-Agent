"""Opt-in ordinary-chat sources, separate namespace in the existing guarded memory store.
No transcript recovery, permissions, tool objects, prompts or drafts are persisted here.
"""
import copy,datetime as dt,uuid,hashlib,json
from backend.memory import bubble_memory as mem
UTC=dt.timezone.utc;LIMIT=240;GROUP=4

def now():return dt.datetime.now(UTC)
def iso(t):return t.astimezone(UTC).isoformat()
def policy():return {'save':False,'cloud':False,'days':30,'generation':0,'cloud_id':None}
def empty():return {'version':1,'policies':{'desktop':policy(),'web':policy()},'turns':[],'blocked':[]}
def state(s):return s['learning'].setdefault('chat_sources',empty())
def validate(d):
 if not isinstance(d,dict) or set(d)!=set(empty()) or d['version']!=1 or not isinstance(d['policies'],dict) or set(d['policies'])!={'desktop','web'} or not isinstance(d['turns'],list) or len(d['turns'])>LIMIT or not isinstance(d['blocked'],list) or len(d['blocked'])>600:raise ValueError('聊天来源状态损坏，未清空或降级。')
 for p in d['policies'].values():
  if set(p)!=set(policy()) or type(p['save']) is not bool or type(p['cloud']) is not bool or type(p['days']) is not int or not 1<=p['days']<=90 or type(p['generation']) is not int or (p['cloud_id'] is not None and not isinstance(p['cloud_id'],str)):raise ValueError('聊天保存许可无效。')
 ids=set()
 for t in d['turns']:
  if not isinstance(t,dict) or not isinstance(t.get('id'),str) or t['id'] in ids or t.get('entry') not in ('desktop','web') or type(t.get('version')) is not int or t['version']<1 or t.get('status') not in ('completed','cancelled','failed','incomplete') or not isinstance(t.get('session'),str) or not isinstance(t.get('user'),str) or not 0<len(t['user'])<=1000 or not isinstance(t.get('assistant'),str) or len(t['assistant'])>8000 or type(t.get('cloud_allowed')) is not bool or type(t.get('context_exportable')) is not bool or type(t.get('sequence')) is not int:raise ValueError('聊天来源身份/角色/状态无效。')
  if 'segment' in t and (not isinstance(t['segment'],str) or not t['segment']):raise ValueError('聊天重置边界无效。')
  mem._date(t.get('at'),False);ids.add(t['id'])
 return copy.deepcopy(d)
def alive(t,d):return mem._date(t['at'],False)+dt.timedelta(days=d['policies'][t['entry']]['days'])>now()
def permitted(t,d):
 p=d['policies'][t['entry']];return p['cloud'] and t['cloud_allowed'] and t.get('cloud_id')==p['cloud_id'] and alive(t,d) and t['id'] not in d['blocked'] and t['status']=='completed'
def begin(c,rid):
 with mem._locked():
  s,_=mem._read();d=s['learning'].get('chat_sources');p=d['policies'][c.entry] if d else policy()
  if not p['save']:return None
  return {'entry':c.entry,'session':c.memory_owner,'segment':c.chat_source_segment,'request':rid,'generation':p['generation'],'at':iso(now())}
def finish(c,consent,text,answer,status,context_exportable):
 if not consent:return False
 with mem._locked():
  s,legacy=mem._read();d=state(s);p=d['policies'][consent['entry']]
  if not p['save'] or p['generation']!=consent['generation']:return False
  ident=uuid.uuid5(uuid.NAMESPACE_URL,'chat-source/'+consent['entry']+'/'+consent['session']+'/'+consent['request']).hex
  if any(t['id']==ident for t in d['turns']):return False
  if not isinstance(answer,str) or len(answer)>8000:raise ValueError('聊天来源回答超出保存范围，未截断或伪报完整。')
  cleanup_state(s)
  if len(d['turns'])>=LIMIT:raise ValueError('聊天来源容量已满，请管理来源；回答不受影响。')
  sequence=1+max((t['sequence'] for t in d['turns'] if t['session']==consent['session'] and t.get('segment')==consent['segment'] and t['entry']==consent['entry']),default=0)
  d['turns'].append(dict(id=ident,entry=consent['entry'],session=consent['session'],segment=consent['segment'],sequence=sequence,version=1,at=consent['at'],user=text,assistant=answer,status=status,context_exportable=context_exportable,cloud_allowed=p['cloud'],cloud_id=p['cloud_id']))
  s['revision']+=1;mem._write(s,legacy);return True
def sources(s,entry):
 d=s['learning'].get('chat_sources')
 if not d:return {}
 turns=[t for t in d['turns'] if t['entry']==entry and permitted(t,d)];groups={}
 for t in turns:groups.setdefault((t['entry'],t['session'],t.get('segment',t['session']),(t['sequence']-1)//GROUP),[]).append(t)
 result={}
 for (owner,session,segment,bucket),new in groups.items():
  new.sort(key=lambda x:x['sequence']);prior=[t for t in turns if t['session']==session and t.get('segment',t['session'])==segment and t['sequence']<new[0]['sequence']][-GROUP:];allturns=prior+new;messages=[]
  for t in allturns:
   messages.append({'id':t['id']+':user','role':'user','content':t['user'],'new':t in new,'at':t['at']})
   if t['assistant'] and t['context_exportable']:messages.append({'id':t['id']+':assistant','role':'assistant','content':t['assistant'],'new':False,'at':t['at']})
  ident='chat:'+owner+':'+session+(':'+segment if segment!=session else '')+':'+str(bucket)
  result[ident]=dict(id=ident,version=sum(t['version'] for t in allturns),text='\n'.join(t['user'] for t in allturns),source='chat_saved',kind='fact',status='active',expires_at=None,scopes=['chat'],audience='entry',owner=owner,cloud_allowed=True,journal={'occurred_on':None,'recorded_at':new[0]['at']},updated_at=new[-1]['at'],_chat={'messages':messages,'turn_ids':[t['id'] for t in allturns],'new_ids':[t['id'] for t in new],'cloud_id':d['policies'][owner]['cloud_id']})
 return result

def clean_refs(s,removed,forget=False):
 """Raw retention/deletion retires evidence; it does not silently erase reviewed memories."""
 d=s['learning'].get('distillation')
 if not d:return
 for c in d['candidates']:
  matched=False
  for ref in c['sources']:
   if ref.get('kind')=='chat' and set(ref.get('turn_ids',[]))&set(removed):ref.update(retired=True,quote='',messages=[]);matched=True
  if not matched:continue
  # Other independent current sources can still support the candidate/curated memory.
  from backend.memory import periodic_distillation as dist
  independent=any(not ref.get('retired') and dist.valid_refs(s,d,[ref]) for ref in c['sources'])
  if c['status']=='adopted':
   if forget and not independent:
    for row in s['items']:
     if row.get('distillation_id')==c['id'] and row['status']=='active':row.update(status='inactive',version=row['version']+1,updated_at=mem._now())
    if c['fingerprint'] not in d['blocked']:d['blocked'].append(c['fingerprint'])
    c['status']='rejected'
  elif not independent:c.update(status='invalid',text='（聊天来源已清理，候选失效）',subject='')
  c['version']+=1;c['updated_at']=mem._now();c['reason']='原聊天来源已清理；已采纳记忆按你的独立选择保留或遗忘。'
def cleanup_state(s):
 d=s['learning'].get('chat_sources')
 if not d:return False
 gone=[t['id'] for t in d['turns'] if not alive(t,d)]
 if not gone:return False
 d['turns']=[t for t in d['turns'] if t['id'] not in gone];clean_refs(s,gone);return True
def cleanup():
 with mem._locked():
  s,legacy=mem._read()
  if cleanup_state(s):s['revision']+=1;mem._write(s,legacy)
def suppress(s,refs):
 d=s['learning'].get('chat_sources')
 if not d:return
 for ref in refs:
  if ref.get('kind')=='chat':
   for ident in ref.get('turn_ids',[]):
    if ident not in d['blocked']:d['blocked'].append(ident)
def view(s,c):
 d=s['learning'].get('chat_sources',empty());turns=[dict(copy.deepcopy(t),cloud_stored=t['cloud_allowed'],suppressed=t['id'] in d['blocked'],cloud_allowed=bool(permitted(t,d))) for t in d['turns'] if t['entry']==c.entry and alive(t,d)]
 links=[]
 for candidate in s['learning'].get('distillation',{}).get('candidates',[]):
  if candidate.get('adopted_id') and not candidate.get('existing_memory') and any(r.get('kind')=='chat' and any(t['id'] in r.get('turn_ids',[]) for t in turns) for r in candidate['sources']):links.append({'candidate':candidate['id'],'memory_id':candidate['adopted_id'],'text':next((r['text'] for r in s['items'] if r['id']==candidate['adopted_id']),''),'turn_ids':list({x for r in candidate['sources'] if r.get('kind')=='chat' for x in r.get('turn_ids',[])})})
 return {'policy':copy.deepcopy(d['policies'][c.entry]),'entry':c.entry,'turns':turns,'eligible_turns':sum(permitted(t,d) for t in turns),'links':links,'limit':LIMIT}
def preview(s,ref,entry):
 d=s['learning'].get('chat_sources',empty());messages=[]
 if ref.get('retired'):return messages
 for t in d['turns']:
  if t['id'] not in ref.get('turn_ids',[]) or t['entry']!=entry or not alive(t,d):continue
  messages.extend([{'id':t['id']+':user','role':'user','content':t['user'],'at':t['at']},{'id':t['id']+':assistant','role':'assistant','content':t['assistant'],'at':t['at'],'status':t['status']}])
 return messages
def status(c):
 with mem._locked():s,_=mem._read();return view(s,c)
def manage(body,c):
 from backend.memory import periodic_distillation as dist
 with mem._locked():
  s,legacy=mem._read()
  if type(body.get('revision')) is not int or body['revision']!=s['revision']:raise ValueError('资料已变化，请刷新；未覆盖。')
  action=body['action'];d=state(s);p=d['policies'][c.entry]
  if action=='chat_source_config':
   if type(body.get('save')) is not bool or type(body.get('cloud')) is not bool or type(body.get('days')) is not int or not 1<=body['days']<=90:raise ValueError('请选择明确保存/云端范围及1～90天保留时间。')
   cloud=body['cloud'];changed=p['cloud']!=cloud
   p.update(save=body['save'],cloud=cloud,days=body['days'],generation=p['generation']+1)
   if changed:p['cloud_id']=uuid.uuid4().hex if cloud else None
  elif action in ('chat_source_delete','chat_source_allow'):
   ids=body.get('ids')
   if not isinstance(ids,list) or not ids or any(not isinstance(x,str) for x in ids) or not set(ids)<= {t['id'] for t in d['turns'] if t['entry']==c.entry}:raise ValueError('来源身份或入口不符，请刷新。')
   if action=='chat_source_delete':
    if type(body.get('forget_linked')) is not bool:raise ValueError('请明确是否同时遗忘仅由此来源支持的关联记忆。')
    d['turns']=[t for t in d['turns'] if t['id'] not in ids];clean_refs(s,ids,body['forget_linked'])
   else:
    if type(body.get('allowed')) is not bool or (body['allowed'] and not p['cloud']):raise ValueError('先明确启用本入口聊天提炼范围，再允许所选来源。')
    for t in d['turns']:
     if t['id'] in ids:t.update(cloud_allowed=body['allowed'],cloud_id=p['cloud_id'],version=t['version']+1)
  elif action!='chat_source_cleanup':raise ValueError('未知聊天来源操作。')
  cleanup_state(s)
  if 'distillation' in s['learning']:dist.refresh(s,s['learning']['distillation'])
  s['revision']+=1;mem._write(s,legacy)
 return status(c)
