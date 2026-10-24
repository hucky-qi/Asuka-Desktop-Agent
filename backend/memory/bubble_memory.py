"""Original JSON memory with CAS, scoped validity and explicit recoverable commits."""
import json,os,threading,uuid,unicodedata,fcntl,hashlib
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime,timezone
import backend.settings as settings
PATH=settings.PROJECT/'.bubble-memory.json';LOCK=threading.RLock();LIMIT=330;RECORD_LIMIT=300;LEGACY_LIMIT=30
SCOPES=frozenset({'chat','files','calendar','shared'})
def _now():return datetime.now(timezone.utc).isoformat()
def _norm(text):return ' '.join(unicodedata.normalize('NFKC',text).strip().split()).casefold().rstrip('。.!！')
def _valid_text(text):
 if not isinstance(text,str) or not 0<len(text.strip())<=300:raise ValueError('请保存1～300字的明确事实或偏好，不保存整份文件或网页。')
 return text.strip()
def _scopes(scopes):
 if not isinstance(scopes,list) or not scopes or any(x not in SCOPES for x in scopes):raise ValueError('记忆范围只能是聊天、文件、日历或共享。')
 return sorted(set(scopes))
def _date(value,optional=True):
 if value is None and optional:return None
 if not isinstance(value,str):raise ValueError('记忆时间格式无效，未覆盖原文件。')
 try:d=datetime.fromisoformat(value.replace('Z','+00:00'))
 except ValueError:raise ValueError('记忆时间格式无效，未覆盖原文件。') from None
 if d.tzinfo is None:raise ValueError('记忆时间必须包含时区。')
 return d.astimezone(timezone.utc)
def active(row):return row.get('status','active')=='active' and (not row.get('expires_at') or _date(row['expires_at'])>datetime.now(timezone.utc))
def visible(row,entry=None,session_id=None):return row.get('audience','shared')=='shared' or (row.get('audience')=='entry' and row.get('owner')==entry) or (row.get('audience')=='session' and session_id is not None and row.get('owner')==session_id)
def _side(name):return PATH.with_name(PATH.stem+'.'+name+'.json')
def _sha(raw):return hashlib.sha256(raw).hexdigest() if raw is not None else None
def _bytes(path):return path.read_bytes() if path.exists() else None
def _encode(data):return json.dumps(data,ensure_ascii=False,indent=2).encode()
def _fsync_directory(directory=None):
 fd=os.open(directory or PATH.parent,os.O_RDONLY)
 try:os.fsync(fd)
 finally:os.close(fd)
def _atomic(path,raw):
 temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
 try:
  fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
  with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
  os.replace(temp,path);_fsync_directory(path.parent)
 finally:temp.unlink(missing_ok=True)
@contextmanager
def _locked():
 try:
  with LOCK:
   PATH.parent.mkdir(parents=True,exist_ok=True)
   with PATH.with_name(PATH.name+'.lock').open('a') as f:
    os.chmod(f.name,0o600);fcntl.flock(f,fcntl.LOCK_EX)
    try:yield
    finally:fcntl.flock(f,fcntl.LOCK_UN)
 except OSError:raise ValueError('长期记忆读写失败；未报告成功，请保留文件并查看恢复状态。') from None

def _decode(raw):
 try:data=json.loads(raw)
 except (ValueError,TypeError):raise ValueError('长期记忆文件损坏；未清空或覆盖。请查看恢复状态。') from None
 if not isinstance(data,dict) or data.get('namespace')!='desktop-smalltalk-v1':raise ValueError('长期记忆格式不正确，未覆盖原文件。')
 items=data.get('items');metadata=data.get('metadata');v=data.get('version',1)
 if type(v) is not int:raise ValueError('记忆格式版本无效。')
 if not isinstance(items,list) or len(items)>LIMIT or any(not isinstance(x,str) or not 0<len(x.strip())<=300 for x in items):raise ValueError('长期记忆内容不正确，未覆盖原文件。')
 if v in (2,3):
  if not isinstance(metadata,list) or len(metadata)!=len(items) or type(data.get('revision')) is not int or data['revision']<0:raise ValueError('记忆元数据不完整，未覆盖原文件。')
 elif v!=1:raise ValueError('不支持的记忆版本，未覆盖原文件。')
 else:metadata=None
 rows=[]
 for i,text in enumerate(items):
  meta=metadata[i] if metadata is not None else {'id':uuid.uuid5(uuid.NAMESPACE_URL,'local-file-chat/legacy/'+str(i)+'/'+text).hex,'source':'legacy_explicit','updated_at':None,'created_at':None,'scopes':['chat'],'cloud_allowed':False,'version':0,'kind':'fact'}
  if not isinstance(meta,dict) or not isinstance(meta.get('id'),str) or not meta['id'] or type(meta.get('version')) is not int or meta['version']<0 or type(meta.get('cloud_allowed')) is not bool:raise ValueError('记忆元数据无效，未覆盖原文件。')
  _scopes(meta.get('scopes'));_date(meta.get('created_at'));_date(meta.get('updated_at'));_date(meta.get('expires_at'))
  if meta.get('status','active') not in ('active','inactive') or meta.get('kind','fact') not in ('fact','preference') or meta.get('audience','shared') not in ('shared','entry','session') or not isinstance(meta.get('source',''),str):raise ValueError('记忆有效性或来源字段无效，未覆盖原文件。')
  audience=meta.get('audience','shared');owner=meta.get('owner')
  if (audience=='entry' and owner not in ('desktop','web')) or (audience=='session' and (not isinstance(owner,str) or not owner)) or (audience=='shared' and owner is not None):raise ValueError('记忆所属范围无效，未覆盖原文件。')
  rows.append(dict(meta,text=text,status=meta.get('status','active'),expires_at=meta.get('expires_at'),audience=audience,owner=owner,kind=meta.get('kind','fact')))
 if sum(bool(r.get('journal')) for r in rows)>RECORD_LIMIT or sum(not r.get('journal') for r in rows)>LEGACY_LIMIT:raise ValueError('记忆分类容量超限，未覆盖。')
 for r in rows:
  if r.get('journal'):
   if r['kind']!='fact' or r.get('preference_key'):raise ValueError('记录不能同时是偏好。')
   __import__('backend.memory.daily_records', fromlist=['*']).validate_meta(r['journal'])
 if len({r['id'] for r in rows})!=len(rows):raise ValueError('记忆身份重复，未覆盖原文件。')
 import backend.memory.memory_learning as memory_learning
 learning=memory_learning.validate(data['learning']) if 'learning' in data else memory_learning.empty()
 if v==3 and 'learning' not in data:raise ValueError('学习状态缺失，不能降级为空记忆。')
 return {'revision':data.get('revision',0),'items':rows,'learning':learning}
def _guard():
 raw=_bytes(_side('guard'))
 if raw is None:return None
 try:g=json.loads(raw);assert g['version']==1 and isinstance(g['sha256'],str) and type(g['revision']) is int and g['revision']>=0
 except Exception:raise ValueError('记忆保护记录损坏；请保留文件，未自动恢复旧备份。') from None
 return g
def _read():
 if _side('pending').exists():raise ValueError('记忆有未完成写入，已停止召回/修改；请查看恢复状态并明确恢复。')
 raw=_bytes(PATH);guard=_guard()
 if guard and _sha(raw)!=guard['sha256']:raise ValueError('记忆与最新提交不一致，可能损坏或恢复了旧备份；未召回，需明确恢复。')
 if raw is None:return {'revision':0,'items':[],'learning':__import__('backend.memory.memory_learning', fromlist=['*']).empty()},None
 state=_decode(raw)
 if guard is None and (json.loads(raw).get('contract',0)>=3 or _side('checkpoint').exists()):raise ValueError('记忆保护记录缺失；未采用可能过期的内容，请保留关联文件人工核对。')
 if guard and state['revision']!=guard['revision']:raise ValueError('记忆版本与保护记录不一致。')
 return state,raw if guard is None else None
def _public(state):
 if 'distillation' in state['learning']:__import__('backend.memory.periodic_distillation',fromlist=['*']).refresh(state,state['learning']['distillation'])
 return {'revision':state['revision'],'learning':__import__('backend.memory.memory_learning', fromlist=['*']).public(state['learning']),'items':[dict(r) for r in state['items'] if active(r)],'inactive':[dict(r,effective_status='inactive' if r['status']=='inactive' else 'expired') for r in state['items'] if not active(r)]}
def _finish(raw,guard):
 _atomic(PATH,raw);_atomic(_side('guard'),_encode(guard));_atomic(_side('checkpoint'),raw);_side('pending').unlink(missing_ok=True);_fsync_directory()
def _write(state,legacy):
 # Back up verified previous format once, without changing formal data on read/startup.
 if legacy is None:
  current=_bytes(PATH)
  if current is not None and json.loads(current).get('version',1)<3:legacy=current
 # One pre-contract backup; content hash filename avoids repeated migration copies.
 if legacy is not None:
  directory=PATH.with_name(PATH.stem+'.backups');directory.mkdir(mode=0o700,exist_ok=True);backup=directory/(_sha(legacy)+'.json')
  if not backup.exists():_atomic(backup,legacy)
 data={'namespace':'desktop-smalltalk-v1','version':3,'contract':4,'learning':state['learning'],'revision':state['revision'],'items':[r['text'] for r in state['items']],'metadata':[{k:v for k,v in r.items() if k!='text'} for r in state['items']]};raw=_encode(data);_decode(raw)
 old=_guard();guard={'version':1,'revision':state['revision'],'sha256':_sha(raw),'commit_id':uuid.uuid4().hex,'at':_now()}
 pending={'version':1,'id':uuid.uuid4().hex,'previous_sha256':_sha(_bytes(PATH)),'previous_guard':old,'next_guard':guard,'data':data}
 _atomic(_side('pending'),_encode(pending));_finish(raw,guard)
def snapshot():
 with _locked():state,_=_read();return _public(state)
def recovery_status():
 with _locked():return _recovery_status()
def _recovery_status():
 guard=_guard();pendingraw=_bytes(_side('pending'));raw=_bytes(PATH)
 if pendingraw is not None:
  try:
   p=json.loads(pendingraw);assert p['version']==1;new=_encode(p['data']);_decode(new);assert _sha(new)==p['next_guard']['sha256'];assert _sha(raw) in (p['previous_sha256'],p['next_guard']['sha256']);assert guard in (p['previous_guard'],p['next_guard']);assert p['next_guard']['revision']==p['data']['revision']
  except Exception:raise ValueError('未完成事务或关联文件不一致；需保留原文件人工核对，未自动恢复。') from None
  return {'recoverable':True,'kind':'pending','recovery_id':_sha(pendingraw),'message':'存在未完成写入；确认恢复将完成这次提交，不再次执行模型或工具。'}
 if guard and _sha(raw)!=guard['sha256']:
  good=_bytes(_side('checkpoint'))
  if _sha(good)!=guard['sha256']:return {'recoverable':False,'message':'当前文件与最新提交不一致，且没有可验证的最新副本。请保留原文件人工恢复；不会自动使用更旧备份。'}
  state=_decode(good)
  if state['revision']!=guard['revision']:raise ValueError('恢复副本版本不一致。')
  return {'recoverable':True,'kind':'checkpoint','recovery_id':_sha(_encode(guard)),'message':'确认恢复最新已提交副本；不会使用较旧备份，也不会复活已遗忘条目。'}
 try:state,_=_read()
 except ValueError:return {'recoverable':False,'message':'记忆格式损坏且无可验证恢复记录；请保留文件，不能当空库使用。'}
 return {'recoverable':False,'message':'没有待恢复操作。'}
def recover(ident,confirm):
 with _locked():
  status=_recovery_status()
  if confirm is not True or not status['recoverable'] or ident!=status.get('recovery_id'):raise ValueError('恢复未明确确认或恢复状态已变化；未写入。')
  if status['kind']=='pending':p=json.loads(_side('pending').read_bytes());_finish(_encode(p['data']),p['next_guard'])
  else:
   # Preserve damaged/replaced bytes privately before replacing them.
   raw=_bytes(PATH)
   if raw is not None:
    directory=PATH.with_name(PATH.stem+'.backups');directory.mkdir(mode=0o700,exist_ok=True);_atomic(directory/('rejected-'+_sha(raw)+'.json'),raw)
   _atomic(PATH,_bytes(_side('checkpoint')))
  state,_=_read();return dict(_public(state),message='已恢复可验证的最新提交。历史聊天与私人备份不会同时删除。')
def load():return [r['text'] for r in snapshot()['items'] if not r.get('journal') and r['audience']=='shared' and ('chat' in r['scopes'] or 'shared' in r['scopes'])]
def mutate(action,*,text=None,id=None,revision=None,scopes=None,cloud_allowed=None,kind=None,status=None,expires_at=None,audience=None,owner=None,ids=None,preference_key=None,scenario=None,preference_value=None,style_policy=False,journal=None,record_policy=False):
 with _locked():
  state,legacy=_read()
  if (action in ('update','delete','clear') or revision is not None) and (type(revision) is not int or revision!=state['revision']):raise ValueError('记忆已变化，请刷新后再操作；没有覆盖新内容。')
  items=state['items'];changed=False;message=''
  if scenario is not None and scenario not in __import__('backend.memory.memory_learning', fromlist=['*']).SCENARIOS:raise ValueError('偏好适用场景无效。')
  if preference_key is not None and (not isinstance(preference_key,str) or not __import__('re').fullmatch(r'[a-z][a-z0-9_]{0,47}',preference_key)):raise ValueError('偏好属性无效。')
  if preference_value is not None and preference_value not in (__import__('backend.calendar.calendar_semantics', fromlist=['*']).VALUES if preference_key=='calendar_mentions' else __import__('backend.memory.style_preferences', fromlist=['*']).VALUES.get(preference_key,{})):raise ValueError('偏好属性和值不匹配，未更改。')
  if status is not None and status not in ('active','inactive'):raise ValueError('记忆状态无效。')
  if kind is not None and kind not in ('fact','preference'):raise ValueError('记忆类型无效。')
  if expires_at not in (None,''):_date(expires_at)
  if journal is not None:
   __import__('backend.memory.daily_records', fromlist=['*']).validate_meta(journal)
   if kind not in (None,'fact') or preference_key is not None:raise ValueError('日常记录不能改为偏好。')
  if action=='save':
   text=_valid_text(text);scope=_scopes(scopes or ['chat']);cloud=False if cloud_allowed is None else cloud_allowed
   if type(cloud) is not bool:raise ValueError('云端使用许可必须明确选择。')
   audience=audience or 'shared'
   same=next((r for r in items if _norm(r['text'])==_norm(text) and r['scopes']==scope and r['audience']==audience and r['owner']==owner and bool(r.get('journal'))==bool(journal) and (not journal or r['journal']['occurred_on']==journal['occurred_on'])),None)
   if same:message='该记忆已存在，没有增加副本；范围、状态和许可未自动改变。'
   else:
    if sum(bool(r.get('journal'))==bool(journal) for r in items)>=(RECORD_LIMIT if journal else LEGACY_LIMIT):raise ValueError('该分类记忆已满，请先管理已有条目。')
    at=_now();items.append({'id':uuid.uuid4().hex,'text':text,'source':'user_explicit','created_at':at,'updated_at':at,'scopes':scope,'cloud_allowed':cloud,'version':1,'kind':kind or 'fact','status':status or 'active','expires_at':expires_at,'audience':audience,'owner':owner});changed=True;message='已保存长期记忆；普通重置保留。范围和云端许可按此条目设置。'
   if changed and journal is not None:items[-1]['journal']=journal
   if changed and preference_key is not None:items[-1].update(preference_key=preference_key,scenario=scenario or 'general',**({'preference_value':preference_value} if preference_value is not None else {}));__import__('backend.memory.memory_learning', fromlist=['*']).unblock(state,preference_key,scenario or 'general',items[-1])
  elif action in ('update','delete'):
   row=next((r for r in items if r['id']==id),None)
   if row is None:raise ValueError('该记忆已不存在，请刷新列表。')
   if action=='delete':
    __import__('backend.memory.periodic_distillation',fromlist=['*']).forgotten(state,row)
    __import__('backend.memory.memory_learning', fromlist=['*']).suppress(state,row.get('preference_key'),row.get('scenario','general'))
    items.remove(row);message='已忘记这条长期记忆，后续召回不再使用；历史聊天和私人备份不会同时删除。'
   else:
    value=_valid_text(text);scope=row['scopes'] if scopes is None else _scopes(scopes);cloud=row['cloud_allowed'] if cloud_allowed is None else cloud_allowed
    if type(cloud) is not bool:raise ValueError('云端使用许可必须明确选择。')
    if any(r['id']!=id and _norm(r['text'])==_norm(value) and r['scopes']==scope and r['audience']==(audience or row['audience']) and r['owner']==(owner if audience is not None else row['owner']) and bool(r.get('journal'))==bool(row.get('journal')) and (not row.get('journal') or r['journal']['occurred_on']==(journal or row['journal'])['occurred_on']) for r in items):raise ValueError('相同范围已有这条记忆，请明确要保留哪条。')
    if preference_key is not None:
     row['preference_key']=preference_key;row['scenario']=scenario or row.get('scenario','general');
     if preference_value is not None:row['preference_value']=preference_value
     else:row.pop('preference_value',None)
    if preference_key is not None:__import__('backend.memory.memory_learning', fromlist=['*']).unblock(state,preference_key,row['scenario'],row)
    elif (value!=row['text'] or status=='inactive') and row.get('preference_key'):
     __import__('backend.memory.memory_learning', fromlist=['*']).suppress(state,row['preference_key'],row.get('scenario','general'))
     if value!=row['text']:row.pop('preference_key',None);row.pop('preference_value',None)
    elif value!=row['text']:row.pop('preference_key',None);row.pop('preference_value',None)
    explicit_change=value!=row['text'] or (kind is not None and kind!=row['kind'])
    if row.get('distillation_id') and explicit_change:row['previous_distillation_id']=row.pop('distillation_id')
    if row.get('learning_id') and explicit_change:
     for c in state['learning']['candidates']:
      if c['id']==row['learning_id']:c['status']='superseded';c['reason']='用户明确修改优先，原推断已被替代'
     row.pop('learning_id',None)
     if preference_value is None:row.pop('preference_value',None)
    row.update(text=value,scopes=scope,cloud_allowed=cloud,source='user_explicit' if explicit_change else row['source'],updated_at=_now(),version=row['version']+1)
    if row.get('journal') and kind=='preference':raise ValueError('请保持日常记录分类，不自动转成偏好。')
    if journal is not None:row['journal']=journal
    if kind is not None:row['kind']=kind
    if status is not None:row['status']=status
    if expires_at is not None:row['expires_at']=expires_at or None
    if audience is not None:row.update(audience=audience,owner=owner)
    message='已更新指定记忆；旧版本不再召回，历史记录不会同时删除。'
   changed=True
  elif action=='clear':
   for r in items:
    if ids is None or r['id'] in ids:__import__('backend.memory.periodic_distillation',fromlist=['*']).forgotten(state,r)
   for r in items:
    if ids is None or r['id'] in ids:__import__('backend.memory.memory_learning', fromlist=['*']).suppress(state,r.get('preference_key'),r.get('scenario','general'))
   for c in list(state['learning']['candidates']):__import__('backend.memory.memory_learning', fromlist=['*']).suppress(state,c['key'],c['scenario'])
   keep=[] if ids is None else [r for r in items if r['id'] not in ids]
   if keep!=items or state['learning']!=_read()[0]['learning']:items[:]=keep;changed=True
   message=('长期记忆已清空；' if ids is None else '当前可见长期记忆已清空，其他入口/会话范围保留；')+'历史聊天和私人备份需另行处理。索引、保存搜索和日历未删除。'
  else:raise ValueError('未知记忆操作。')
  if changed and action in ('save','update'):
   affected=items[-1] if action=='save' else row
   if cloud_allowed is not None:
    affected.pop('style_cloud_policy',None);affected['style_cloud_denied']=cloud_allowed is False
    if affected.get('journal'):affected.pop('record_cloud_policy',None);affected['record_cloud_denied']=cloud_allowed is False
   if style_policy:__import__('backend.memory.style_preferences', fromlist=['*']).stamp(affected)
   if record_policy and affected.get('journal') and not affected.get('record_cloud_denied'):
    policy=__import__('backend.memory.daily_records', fromlist=['*']).policy()
    if policy['enabled']:affected['record_cloud_policy']=policy['id']
  if changed:
   if 'distillation' in state['learning']:__import__('backend.memory.periodic_distillation',fromlist=['*']).refresh(state,state['learning']['distillation'])
   state['revision']+=1;_write(state,legacy)
  return dict(_public(state),message=message)
def save(items,revision=None):
 # No current caller needs blind replacement. Fail closed rather than silently bypass CAS.
 raise ValueError('旧版整表保存已停用；请刷新记忆后使用逐条版本化操作，不会覆盖其他窗口修改。')
def rank(rows,query):
 def grams(s):
  import re
  s=unicodedata.normalize('NFKC',s).casefold();return set(re.findall(r'[a-z0-9_]+',s))|{s[i:i+2] for i in range(len(s)-1) if all('\u4e00'<=c<='\u9fff' for c in s[i:i+2])}
 tokens=grams(query);rows.sort(key=lambda r:(len(tokens&grams(r['text'])),r.get('kind')=='preference',r.get('updated_at') or ''),reverse=True)
 return rows
def recall(task,query='',destination='local',state=None,entry=None,session_id=None):
 if task not in ('chat','files','calendar') or destination not in ('local','cloud'):raise ValueError('记忆使用范围不正确。')
 latest=snapshot();allowed=None if state is None else {(r['id'],r['version']) for r in state['items']}
 rows=[r for r in latest['items'] if not r.get('journal') and (allowed is None or (r['id'],r['version']) in allowed) and visible(r,entry,session_id) and (task in r['scopes'] or 'shared' in r['scopes']) and (destination=='local' or __import__('backend.memory.style_preferences', fromlist=['*']).permitted(r))]
 explicit=[r for r in rows if r.get('source')!='learned_user_expression' and r.get('preference_key')]
 rows=[r for r in rows if r.get('source')!='learned_user_expression' or not any(e.get('preference_key')==r.get('preference_key') and __import__('backend.memory.memory_learning', fromlist=['*']).overlaps(e.get('scenario','general'),r.get('scenario','general')) for e in explicit)]
 rows=rank(rows,query);selected=[];size=0
 for row in rows:
  entryrow={k:row[k] for k in ('id','text','version','scopes','cloud_allowed')}
  if destination=='cloud' and not row['cloud_allowed']:
   entryrow['text']=__import__('backend.memory.style_preferences', fromlist=['*']).canonical(row);entryrow['cloud_allowed']=True
  if row.get('kind')=='preference':entryrow.update({k:row[k] for k in ('kind','preference_key','preference_value','scenario') if k in row})
  n=len(json.dumps(entryrow,ensure_ascii=False).encode())
  if len(selected)>=6 or size+n>2100:continue
  selected.append(entryrow);size+=n
 return {'revision':latest['revision'],'items':selected}
def command(text):
 # Legacy exact commands still use the same store, no new implicit writes.
 if text in ('查看记忆','查看长期记忆'):
  rows=load();return '长期记忆：\n'+'\n'.join(f'{i+1}. {x}' for i,x in enumerate(rows)) if rows else '暂无长期记忆。'
 if text in ('清空长期记忆','彻底清除记忆'):return mutate('clear',revision=snapshot()['revision'],ids=[r['id'] for r in snapshot()['items']+snapshot().get('inactive',[]) if not r.get('journal') and r['audience']=='shared'])['message']
 if text.startswith('忘记'):
  value=text[2:].lstrip('，,：: ');state=snapshot();rows=[r for r in state['items'] if not r.get('journal') and r['audience']=='shared'];row=rows[int(value)-1] if value.isdecimal() and 1<=int(value)<=len(rows) else next((r for r in rows if r['text']==value or r['id']==value),None)
  return mutate('delete',id=row['id'],revision=state['revision'])['message'] if row else '没有找到该记忆，请查看长期记忆后选择。'
 if text.startswith('记住'):
  try:return mutate('save',text=text[2:].lstrip('，,：: '))['message']
  except ValueError as e:return str(e)
 return None
