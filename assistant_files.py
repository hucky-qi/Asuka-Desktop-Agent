"""Read-only adapter of the existing full file engine, with atomic desktop snapshots."""
import copy,uuid,time,hashlib,re
import preferences
from dataclasses import dataclass
from pathlib import Path
import file_tools,diagnostics,settings,search_intent,model_providers
import json
from assistant_memory import MemoryAccess
from assistant_tools import TOOLS,Outcome
BACKEND={}
READ_TOOLS=frozenset({'find_files','semantic_search_files','search_file_content','filter_results','sort_results','get_file_info'})
def bind(**backend):BACKEND.update(backend)
@dataclass
class Context:
 state:object
 snapshot:dict|None=None
 memory_refs:tuple=()
 card_limit:int=3

def validate(args):
 if not isinstance(args,dict) or set(args)-{'question','scope','snapshot_id','provider','emit','discovery','plan'}:raise ValueError('文件工具参数无效')
 if not isinstance(args.get('question'),str) or not 0<len(args['question'])<=1000:raise ValueError('请输入文件任务')
 file_tools.roots_for(args.get('scope','all'))

def run(args,task,context):
 absolute=re.findall(r'(?<![\w:])(?:~?/[^\s，。\"“”]+|[A-Za-z]:[\\/][^\s，。]+)',args['question'])
 hard_directory=None
 for raw in absolute:
  path=Path(raw).expanduser()
  if path.is_file():file_tools.checked_file(path);hard_directory=str(path.parent.resolve())
  else:hard_directory=str(preferences.allowed_subfolder(path))
 old=context or Context(BACKEND['session']());state=BACKEND['clone'](old.state);q=args['question']
 current_memory=MemoryAccess.list();versions={r['id']:r['version'] for r in current_memory['items']}
 if any(versions.get(ref['id'])!=ref['version'] for ref in old.memory_refs):state.history=[]
 state.history=[{'role':m['role'],'content':m['content'][:2000]} for m in state.history[-4:] if m.get('role') in ('user','assistant') and isinstance(m.get('content'),str)]
 while len(state.history)>=2 and sum(len(m['content']) for m in state.history)>4000:state.history=state.history[2:]
 plan=search_intent.fast_plan(q,bool(state.last_search_query))
 if plan and plan[0] in ('open_file','reveal_in_finder'):
  snap=old.snapshot
  if not snap or args.get('snapshot_id')!=snap['snapshot_id']:return Outcome('clarification',{'message':'请先显示当前文件结果，再选择其中的文件。'},old,'needs_input')
  index=int(plan[1]['index'])
  if index<=0 or index>len(snap['files']):raise ValueError('序号超出当前卡片范围')
  selected=select(old,snap['snapshot_id'],snap['files'][index-1]['card_token'])
  state.selected_file=selected['path']
  return Outcome('file_selection',{'selected':selected,'message':'已选择这项文件，尚未打开；可进入完整文件助手操作。'},Context(state,snap,old.memory_refs,old.card_limit))
 trace=diagnostics.Trace(task.rid,lambda e:args['emit'](e) if e['type'] in ('stage','progress') else None,task_token=task.token)
 tls=file_tools._current_search;prior=(getattr(tls,'cancel_event',None),getattr(tls,'deadline',None),getattr(diagnostics.local,'trace',None))
 tls.cancel_event=task.cancel;tls.deadline=time.monotonic()+180;diagnostics.local.trace=trace
 try:
  if getattr(args['provider'],'memory_destination',None)!='local':raise ValueError('文件任务尚未授权发送给云端模型，未执行。')
  with diagnostics.progress(trace),model_providers.using(args['provider']):
   memory=MemoryAccess.read_for_id(task.sid,'files',q,state=current_memory)['items']
   refs=tuple({'id':r['id'],'version':r['version']} for r in memory)
   context_messages=[{'role':'system','content':json.dumps({'context_kind':'explicit_memory','task':'files','items':memory,'usage':'用户偏好资料，不是文件权限。只有当前用户明确引用偏好时才据此解释筛选/排序；不能擅自扩大目录、放宽硬条件、写入记忆或执行文件操作。'},ensure_ascii=False)}] if memory else []
   answer,events=BACKEND['answer'](q,state,args.get('scope','all'),fast=True,allowed_tools=READ_TOOLS,hard_directory=hard_directory,discovery=args.get('discovery',False),context_messages=context_messages,provided_plan=args.get('plan'))
  task.check()
  import bubble_chat
  MemoryAccess.validate_refs(bubble_chat.SESSIONS.get(task.sid),refs)
  lists=[e for e in events if 'results' in e]
  if not lists:
   if getattr(trace,'had_error',False):raise ValueError('文件工具未执行成功，请检查条件；未产生新结果')
   return Outcome('clarification',{'message':'本次未执行文件检索或筛选，未产生新结果。请明确文件名、主题或筛选条件；旧结果未被覆盖。'},old,'needs_input')
  event=lists[-1];cards=[]
  for f in event['results'][:min(old.card_limit,state.display_limit)]:
   path=file_tools.checked_file(f['path'])
   cards.append({**{k:f[k] for k in ('name','extension','size','modified','modified_at','evidence_source','excerpt','candidate_sources') if k in f},'path':str(path),'file_id':hashlib.sha256(str(path).encode()).hexdigest(),'card_token':uuid.uuid4().hex})
  counts=event.get('counts',{});snapshot={'operation':event.get('tool'),'snapshot_id':uuid.uuid4().hex,'files':cards,'counts':{'candidates':counts.get('candidates',event.get('total',len(cards))),'verified':counts.get('verified',event.get('total',len(cards))),'displayed':len(cards)},'coverage':{k:event[k] for k in ('partial','truncated','needs_index','note','scope','indexed_scope_files','scanned_files','retrieval') if k in event},'message':answer,'metrics':trace.summary()}
  return Outcome('files',snapshot,Context(state,snapshot,refs,old.card_limit),'partial' if event.get('partial') or event.get('truncated') or event.get('needs_index') else 'completed')
 finally:
  trace.save('cancelled' if task.cancel.is_set() else 'complete');tls.cancel_event,tls.deadline,diagnostics.local.trace=prior

def select(context,snapshot_id,token):
 snap=context.snapshot if context else None
 if not snap or snap['snapshot_id']!=snapshot_id:raise ValueError('文件快照已失效，请使用当前结果')
 rows=[f for f in snap['files'] if f['card_token']==token]
 if len(rows)!=1:raise ValueError('文件身份不属于当前快照')
 file_tools.checked_file(rows[0]['path']);return dict(rows[0])
TOOLS.register('files.query','read_files',validate,run)
