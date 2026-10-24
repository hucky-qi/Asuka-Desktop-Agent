"""Full-page transport adapter. Same task/provider/tools, separate entry-scoped sessions."""
import threading,uuid,json,copy
import backend.assistant.bubble_chat as bubble_chat, backend.assistant.assistant_files as assistant_files
LOCK=threading.RLock();SESSIONS={}
def resolve(key=None):
 with LOCK:
  for old,internal in list(SESSIONS.items()):
   if internal not in bubble_chat.SESSIONS:SESSIONS.pop(old,None)
  if key is None and len(SESSIONS)>=50:
   idle=[(bubble_chat.SESSIONS[v].touched,k) for k,v in SESSIONS.items() if v in bubble_chat.SESSIONS and bubble_chat.SESSIONS[v].request is None]
   if idle:SESSIONS.pop(min(idle)[1],None)
  if key is None:
   sid,c=bubble_chat.conversation();c.entry='web';key='web-'+uuid.uuid4().hex;SESSIONS[key]=sid
   assistant_files.BACKEND['sessions'][key]=assistant_files.BACKEND['session']()
   c.file_context=assistant_files.Context(assistant_files.BACKEND['clone'](assistant_files.BACKEND['sessions'][key]),card_limit=20)
   return key,sid,c
  if key not in SESSIONS and not str(key).startswith('bubble-') and key in assistant_files.BACKEND['sessions']:
   sid,c=bubble_chat.conversation();c.entry='web';c.file_context=assistant_files.Context(assistant_files.BACKEND['clone'](assistant_files.BACKEND['sessions'][key]),card_limit=20);SESSIONS[key]=sid
  if key not in SESSIONS:raise ValueError('网页助手会话已失效，请清空并重试；不能使用桌面会话或授权。')
  sid=SESSIONS[key];_,c=bubble_chat.conversation(sid);return key,sid,c

def mirror(key,c):
 if c.file_context:
  b=assistant_files.BACKEND
  with b['lock']:b['sessions'][key]=b['clone'](c.file_context.state)

def import_files(key,selected=False):
 with LOCK:
  sid=SESSIONS.get(key)
  if not sid:return
  with bubble_chat.LOCK:
   _,c=bubble_chat.conversation(sid)
   if c.request:raise ValueError('当前任务未完成，请先停止或等待。')
   b=assistant_files.BACKEND;state=b['sessions'].get(key)
   if state is not None:
    import hashlib, uuid, backend.files.file_tools as file_tools
    clone=b['clone'](state);cards=[]
    for f in b['visible'](clone)[:20]:
     path=file_tools.checked_file(f['path']);cards.append({**f,'path':str(path),'file_id':hashlib.sha256(str(path).encode()).hexdigest(),'card_token':uuid.uuid4().hex})
    previous=c.file_context
    if previous and previous.state.last_search_query!=clone.last_search_query:__import__('backend.assistant.assistant_file_qa', fromlist=['*']).invalidate(c,True)
    snapshot={'operation':'find_files','snapshot_id':uuid.uuid4().hex,'files':cards,'counts':{'candidates':len(clone.last_search_results),'verified':len(clone.current_filtered_results),'displayed':len(cards)},'coverage':clone.search_coverage}
    c.file_context=assistant_files.Context(clone,snapshot,card_limit=20);c.last_domain='files'
    if selected and clone.selected_file:
     matches=[f for f in cards if f['path']==clone.selected_file]
     if len(matches)==1:__import__('backend.assistant.assistant_file_qa', fromlist=['*']).select(c,matches[0])

def handle(handler,body):
 route=handler.path.removeprefix('/api/assistant/')
 if route not in {'session','model','memory','chat','cancel','reset','calendar/update/choose','calendar/update/deselect','calendar/update/select','calendar/update/confirm','calendar/update/cancel','calendar/delete/preview','calendar/delete/confirm','calendar/delete/cancel','calendar/confirm','calendar/cancel','calendar/revise','calendar/more','calendar/refresh','calendar/offer','calendar/reminder/recheck','calendar/reminder/status','files/sync','files/select','files/qa'}:raise ValueError('未知网页助手接口')
 recovery_note=None
 if route=='session' and body.get('recover') is True:
  __import__('backend.assistant.session_lifecycle', fromlist=['*']).validate_hint(body.get('resume_hint'))
  old=body.get('session_id')
  if old and not str(old).startswith('web-'):
   if str(old).startswith('bubble-') or old not in assistant_files.BACKEND['sessions']:raise ValueError('网页会话不能使用桌面身份。')
   resolve(old)  # Existing explicit full-file handoff, no fresh arbitrary object import.
  with LOCK:
   if old in SESSIONS:
    internal=SESSIONS[old];sid,c,recovery_note=__import__('backend.assistant.session_lifecycle', fromlist=['*']).resume(bubble_chat,internal,'web',body.get('resume_hint'))
    if sid==internal:key=old
    else:
     key='web-'+uuid.uuid4().hex;SESSIONS.pop(old,None);SESSIONS[key]=sid;assistant_files.BACKEND['sessions'][key]=assistant_files.BACKEND['session']()
   else:
    key,sid,c=resolve(None);c.recovery_parent=body.get('session_id')
    if body.get('resume_hint',{}).get('requires_grant') is True:c.recovery_requires_grant=True
    if body.get('resume_hint',{}).get('provider')=='ollama':c.selected_provider='ollama'
    c.daily_data_controls={k:False for k,v in body.get('resume_hint',{}).get('controls',{}).items() if k in ('history','memory','file_intent','calendar_intent') and v is False}
    recovery_note={'kind':'fresh' if old else 'new','message':'已连接新网页对话；旧任务和上下文未恢复。' if old else ''}
 else:key,sid,c=resolve(body.get('session_id'))
 c.public_session_id=key
 payload=dict(body,session_id=sid)
 if route=='files/sync':
  import_files(key);handler.send_json(200,{'status':'synchronized'});return
 if route=='chat' and c.file_context and c.file_context.snapshot:payload['file_snapshot_id']=c.file_context.snapshot['snapshot_id']
 class Transport:
  assistant_entry=True
  path='/api/bubble/'+route
  @property
  def wfile(self):return Output(self)
  def __getattr__(self,name):return getattr(handler,name)
  def send_json(self,status,data):
   if status<400 and route in ('session','reset'):data={**data,'session_id':key,**({'recovery':recovery_note} if recovery_note else {})}
   if status<400 and route in ('session','reset','model'):data={**data,'entry':'web'}
   handler.send_json(status,data)
  def emit_stream(self,data):
   if data.get('type')=='done':mirror(key,c)
   if data.get('assistant_result',{}).get('kind')=='files':
    # Original web cards/actions/form use the same code-owned file session snapshot.
    b=assistant_files.BACKEND;state=b['sessions'].get(key)
    if state:
     event=b['snapshot'](state,{'tool':data['assistant_result']['data']['operation'],'results':b['visible'](state),'total':len(state.current_filtered_results),**state.search_coverage},fresh=False)
     data={**data,'events':[event]}
   handler.emit_stream({**data,'session_id':key})
 if route=='reset':
  assistant_files.BACKEND['sessions'].pop(key,None)
 class Output:
  def __init__(self,transport):self.transport=transport
  def write(self,raw):
   for line in raw.splitlines():
    if line:self.transport.emit_stream(json.loads(line))
   return len(raw)
  def flush(self):handler.wfile.flush()
 # Registered bubble handlers retain their own validation, permissions and confirmation journals.
 bubble_chat.handle(Transport(),payload)
