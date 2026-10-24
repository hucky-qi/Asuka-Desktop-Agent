"""Bounded RAM-only plain-chat recovery. Never revive tasks, grants or confirmation tokens."""
import copy,json
IDLE=3600;ARCHIVE_TTL=86400;CAP=50
ARCHIVES={};ALIASES={}
def snapshot(c,at):
 rows=[];size=0
 for turn in reversed(c.cloud_history[-4:]):
  if turn.get('exportable') is not True:continue
  row={k:copy.deepcopy(turn[k]) for k in ('question','answer','refs','answer_summary','exportable') if k in turn};n=len(json.dumps(row,ensure_ascii=False).encode())
  if n>6000 or size+n>12000:continue
  rows.insert(0,row);size+=n
 controls=copy.deepcopy(getattr(c,'daily_data_controls',{}));grant=c.cloud_grant
 if grant:
  for k in ('history','memory','file_intent','calendar_intent'):controls.setdefault(k,grant.get(k) is True)
 return {'persona_enabled':getattr(c,'persona_enabled',True),'persona_tone':getattr(c,'persona_tone','default'),'at':at,'entry':c.entry,'rows':rows,'provider':c.selected_provider,'controls':controls,'usage':copy.deepcopy(getattr(c,'daily_usage',{})),'blocked':bool(grant or getattr(c,'recovery_requires_grant',False)),'owner':c.memory_owner,'chat_source_segment':getattr(c,'chat_source_segment',None)}
def cleanup(chat,now,exclude=None):
 for key,row in list(ARCHIVES.items()):
  if now-row['at']>ARCHIVE_TTL:ARCHIVES.pop(key,None)
 for key,(target,at) in list(ALIASES.items()):
  if now-at>300 or target not in chat.SESSIONS:ALIASES.pop(key,None)
 for key,c in list(chat.SESSIONS.items()):
  if now-c.touched>IDLE and c.request is None:
   ARCHIVES[key]=snapshot(c,now);chat.SESSIONS.pop(key,None)
 while len(ARCHIVES)>CAP:ARCHIVES.pop(next(iter(ARCHIVES)))
def make_room(chat,now):
 if len(chat.SESSIONS)<CAP:return
 rows=[(c.touched,key) for key,c in chat.SESSIONS.items() if c.request is None]
 if not rows:raise ValueError('当前并行任务较多，请等待一个任务结束；没有取消正在执行的任务。')
 _,key=min(rows);ARCHIVES[key]=snapshot(chat.SESSIONS.pop(key),now)
 while len(ARCHIVES)>CAP:ARCHIVES.pop(next(iter(ARCHIVES)))
def forget(sid):
 ARCHIVES.pop(sid,None)
 for key,(target,_) in list(ALIASES.items()):
  if key==sid or target==sid:ARCHIVES.pop(key,None);ALIASES.pop(key,None)
def validate_hint(hint):
 if hint is None:return
 if not isinstance(hint,dict) or set(hint)-{'provider','controls','requires_grant'}:raise ValueError('会话恢复设置格式不正确。')
 if hint.get('provider') not in (None,'deepseek','ollama'):raise ValueError('模型恢复设置无效。')
 controls=hint.get('controls',{})
 if not isinstance(controls,dict) or set(controls)-{'history','memory','file_intent','calendar_intent'} or any(type(v) is not bool for v in controls.values()):raise ValueError('数据范围恢复设置无效。')
 if 'requires_grant' in hint and type(hint['requires_grant']) is not bool:raise ValueError('授权状态恢复格式不正确。')
def resume(chat,sid,entry='desktop',hint=None):
 validate_hint(hint)
 if sid is not None and not isinstance(sid,str):raise ValueError('会话身份格式无效。')
 with chat.LOCK:
  now=chat.session_now();cleanup(chat,now)
  if sid in chat.SESSIONS:
   c=chat.SESSIONS[sid]
   if c.entry!=entry:raise ValueError('入口会话不匹配，未共享对话或授权。')
   c.touched=now;return sid,c,{'kind':'existing','message':''}
  if sid in ALIASES:
   key,_=ALIASES[sid];c=chat.SESSIONS[key]
   if c.entry!=entry:raise ValueError('入口会话不匹配。')
   c.touched=now;return key,c,{'kind':'chat_only','message':'已恢复普通聊天；旧工具选择和确认不再有效。'}
  if sid is not None and (not isinstance(sid,str) or not sid.startswith('bubble-') or len(sid)>100):raise ValueError('会话身份格式或入口不匹配。')
  old=ARCHIVES.pop(sid,None)
  if old and old['entry']!=entry:ARCHIVES[sid]=old;raise ValueError('入口会话不匹配。')
  key,c=chat.conversation();c.entry=entry;c.recovery_parent=sid
  if old:
   c.persona_enabled=old.get('persona_enabled',True);c.persona_tone=old.get('persona_tone','default')
   c.memory_owner=old['owner'];c.chat_source_segment=old.get('chat_source_segment') or c.chat_source_segment;c.cloud_history=old['rows'];c.submitted=bool(c.cloud_history);c.selected_provider=old['provider'];c.daily_data_controls=old['controls'];c.daily_usage=old['usage'] or {'calls':0,'output_tokens':0};c.recovery_requires_grant=old['blocked']
  if hint:
   if hint.get('requires_grant') is True:c.recovery_requires_grant=True
   if hint.get('provider')=='ollama':c.selected_provider='ollama'
   for k,v in hint.get('controls',{}).items():
    if k in ('history','memory','file_intent','calendar_intent') and v is False:
     if not hasattr(c,'daily_data_controls'):c.daily_data_controls={}
     c.daily_data_controls[k]=False
  if sid:ALIASES[sid]=(key,now)
  kind='chat_only' if old else 'fresh' if sid else 'new'
  message=('已恢复可安全保留的普通聊天；文件选择、正文许可和日历确认需重新核验。' if old else '连接已恢复为新对话；旧上下文未恢复，可继续输入，无需重置。' if sid else '')
  return key,c,{'kind':kind,'message':message,'restored_chat_turns':len(c.cloud_history)}
