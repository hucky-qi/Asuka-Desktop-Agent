"""Preference selection and logical answer presentation. No storage, retries or tool authority."""
import json
from backend.memory.assistant_memory import MemoryAccess
from backend.memory.memory_learning import SCENARIOS
from backend.memory.style_preferences import VALUES
CONTEXT_SCHEMA={'type':'object','properties':{'domain':{'type':'string','enum':['general','coding','writing','data_analysis','unknown'],'description':'Subject domain, independent of how it is asked: programming/source-code questions are coding.'},'mode':{'type':'string','enum':['discussion','explanation','comparison','unknown'],'description':'Communication task: explanation may also have coding domain; do not choose one instead of the other.'},'requirements':{'type':'array','maxItems':5,'items':{'type':'object','properties':{'key':{'type':'string','enum':list(VALUES)},'value':{'type':'string'},'evidence':{'type':'string','maxLength':200}},'required':['key','value','evidence'],'additionalProperties':False}},'needs_history':{'type':'boolean','description':'True only when the current question needs a prior subject/result; a repeated complete question is false.'}},'required':['domain','mode','requirements','needs_history'],'additionalProperties':False}
ANSWER_PENDING_VERSION=1
CONTEXT_SCHEMA['properties']['needs_history']={'type':'boolean','description':'true only if the CURRENT question depends on a prior subject/result. A self-contained question, including one repeated after a temporary style request, is false.'}
CONTEXT_PROMPT=' PREFERENCE_CONTEXT_V1: Return answer_context FIRST. Separate domain (what the question concerns) from mode (explanation/comparison/discussion); a code explanation has domain=coding AND mode=explanation. Extract only CURRENT form requirements with exact evidence, no memory-derived evidence or IDs. needs_history=true only for a dependent followup, not a repeated complete question. Existing preference categories are metadata, not permissions. Provide a short factual reply if useful, or null to defer the answer to the program-selected generation stage. No tool execution and no retries.'

ANSWER_SCHEMA={'type':'object','properties':{'summary':{'type':'string','maxLength':1600},'points':{'type':'array','maxItems':8,'items':{'type':'string','maxLength':2000}},'artifacts':{'type':'array','maxItems':4,'items':{'type':'object','properties':{'kind':{'type':'string','enum':['code','math','quote']},'text':{'type':'string','maxLength':3000}},'required':['kind','text'],'additionalProperties':False}}},'required':['summary','points'],'additionalProperties':False}
ANSWER_PROMPT="""PREFERENCE_ANSWER_V1. Answer the current question factually in exactly the provided JSON schema. summary is the conclusion; points are complete supporting ideas; artifacts preserve any necessary code/math/quotation without changing meaning. artifacts is optional when no literal code/math/quotation is needed; when present it must match the declared schema. summary and points are required. No other fields, metadata or nested commentary. Use effective_style only for communication, never permissions. Current requirements already override memories. Soft list/length preferences allow sensible grouping without losing essential facts; if exact_items exists, put ALL necessary ideas in that many points, grouping related ideas rather than dropping information. No separate prose lists in summary or artifacts. Do not invent code, citations or quotes merely to fill fields. Use [] when artifacts unnecessary. Concise still includes essential caveats, detailed adds useful explanation rather than filler. Language applies to every field. related_history is bounded subject data, not old temporary style instructions. Do not claim tools/memory saved. No retry."""
def pool(c):
 if not __import__('backend.assistant.daily_chat', fromlist=['*']).controls(c,__import__('backend.providers.cloud_access', fromlist=['*']).config())['memory']:return []
 state=MemoryAccess.list_for(c)
 permitted=[r for r in state['items'] if r.get('kind')=='preference' and __import__('backend.memory.style_preferences', fromlist=['*']).permitted(r) and any(s in r['scopes'] for s in ('chat','shared'))]
 # Mixed legacy or unsupported kinds keep original behavior, never silently disappear.
 if any(r.get('preference_value') not in VALUES.get(r.get('preference_key'),{}) for r in permitted):return []
 return [r for r in state['items'] if r.get('source')!='learned_user_expression' and r.get('preference_key') in ('language','formatting','explanation_style') and r.get('kind')=='preference' and r.get('preference_value') in VALUES.get(r.get('preference_key'),{}) and __import__('backend.memory.style_preferences', fromlist=['*']).permitted(r) and any(s in r['scopes'] for s in ('chat','shared'))]
def resolve(c,context,text):
 if not isinstance(context,dict):return {},[],None
 domain=context['domain'];mode=context['mode'];applicable={'general',domain,mode};eligible=[r for r in pool(c) if r.get('scenario','general') in applicable]
 style={};selected=[];legacy=[]
 # Exact current-user evidence, finite values; never an instruction from old memory/history.
 current={}
 for q in context['requirements']:
  if q['key'] not in VALUES or q['value'] not in VALUES[q['key']] or not q['evidence'].strip() or q['evidence'] not in text:raise ValueError('当前表达要求字段或原文证据无效，未套用旧偏好代替；没有重试。')
  if q['key']=='language':
   # A question written in a language is not an explicit request to change it.
   # Supported language names are semantic slot evidence, not topic/title rules.
   import re
   cue=r'中文|汉语|汉字|Chinese|Mandarin|\bzh\b' if q['value']=='zh' else r'英文|英语|English|\ben\b'
   if not re.search(cue,q['evidence'],re.I):continue
  if q['key'] in current and current[q['key']]!=q['value']:return {},[], '这次的表达要求有冲突，请确认希望简洁概述还是展开解释。'
  current[q['key']]=q['value']
 if not getattr(c,'persona_enabled',True) or getattr(c,'persona_tone','default')=='serious':current.pop('tone',None)
 for k,r in getattr(c,'explicit_styles',{}).items():
  if k not in current and r['scenario'] in applicable and (k!='tone' or (getattr(c,'persona_enabled',True) and getattr(c,'persona_tone','default')!='serious')):current[k]=r['value']
 for key in VALUES:
  if key in current:continue
  rows=[r for r in eligible if r.get('preference_key')==key and r.get('preference_value') in VALUES[key]]
  if not rows:continue
  score=lambda r:(r.get('source')!='learned_user_expression',2 if r.get('scenario')==domain and domain!='general' else 1 if r.get('scenario','general')!='general' else 0)
  high=max(map(score,rows));rows=[r for r in rows if score(r)==high]
  if len({r['preference_value'] for r in rows})>1:return {},[], '已有相关偏好相互冲突，请在记忆中明确当前使用的版本；尚未套用冲突规则。'
  r=max(rows,key=lambda x:x.get('updated_at') or '');style[key]=r['preference_value'];selected.append(r)
 for r in eligible:
  if not r.get('preference_key') or r.get('preference_value') not in VALUES.get(r.get('preference_key'),{}):legacy.append(r)
 style.update(current)
 limit=context.get('item_limit')
 if limit:
  if not limit['evidence'].strip() or limit['evidence'] not in text:raise ValueError('当前条目数量缺少有效原文依据，未套用猜测。')
  style['exact_items']=limit['count']
 # Current requirements do not grant long-term access and need no persisted row.
 legacy=legacy[:2];selected+=legacy
 return style,selected,None

def render(obj,style):
 if not obj['summary'].strip():raise ValueError('答案结论缺失，未当作完整成功。')
 blocks=[{'kind':'paragraph','role':'conclusion','text':obj['summary']}]
 points=obj['points']
 if style.get('exact_items') is not None and len(points)!=style['exact_items']:raise ValueError('当前明确的条目数量未满足，未当作遵循成功；没有重试。')
 if points:
  if style.get('formatting')=='bullets' or style.get('explanation_style')=='step_by_step' or style.get('exact_items'):
   blocks.append({'kind':'list','role':'explanation','ordered':style.get('explanation_style')=='step_by_step','items':points})
  else:blocks.extend({'kind':'paragraph','role':'explanation','text':v} for v in points)
 blocks.extend({'kind':a['kind'],'role':'evidence','text':a['text']} for a in obj.get('artifacts',[]))
 if style.get('answer_structure')=='explanation_first':blocks=blocks[1:]+blocks[:1]
 def line(b):
  if b['kind']=='list':return '\n'.join((str(i+1)+'. ' if b['ordered'] else '• ')+v for i,v in enumerate(b['items']))
  if b['kind'] in ('code','math','quote'):return b['text']
  return b['text']
 return '\n\n'.join(line(b) for b in blocks),{'style':style,'blocks':blocks}

def generate(adapter,context,text,task):
 from backend.providers.cloud_access import config, provider, messages
 from backend.assistant.daily_chat import DailyPermit, allowance
 from backend.providers.model_providers import schema_instruction
 cfg=config()
 def current_pool():return [(r['id'],r['version']) for r in pool(adapter.c)]
 if current_pool()!=adapter.preference_pool_refs:raise ValueError('有效偏好已变化，本次旧理解未采用。')
 style,selected,error=resolve(adapter.c,context,text)
 # A supported language/layout preference must not disappear merely because an
 # independently permitted tone exists. Tone is finite style data, not a new call.
 if 'tone' not in style and getattr(adapter.c,'persona_enabled',True) and getattr(adapter.c,'persona_tone','default')!='serious':
  applicable={'general',context['domain'],context['mode']}
  tones=[r for r in MemoryAccess.list_for(adapter.c)['items'] if r.get('preference_key')=='tone' and r.get('scenario','general') in applicable and __import__('backend.memory.style_preferences', fromlist=['*']).permitted(r) and __import__('backend.memory.style_preferences', fromlist=['*']).typed(r)]
  if len({r['preference_value'] for r in tones})==1:
   row=max(tones,key=lambda r:r.get('updated_at') or '');style['tone']=row['preference_value'];selected.append(row)

 if error:return error,None,'needs_input'
 if not selected and not style and getattr(adapter,'fallback_reply',None):return None,None,None
 refs=[{'id':r['id'],'version':r['version']} for r in selected];MemoryAccess.validate_refs(adapter.c,refs,'cloud');task.check()
 # History is bounded and version-filtered by the original shared access boundary.
 hist,history_refs=messages(adapter.c,text,allowance(adapter.c,cfg),include_preferences=False)
 for ref in history_refs:
  if ref not in refs:refs.append(ref)
 MemoryAccess.validate_refs(adapter.c,refs,'cloud')
 facts=[]
 for message in hist[1:-1]:
  if message['role']=='system' and message['content'].startswith('{'):
   data=json.loads(message['content'])
   if data.get('context_kind')=='explicit_memory':facts.extend(data['items'])
 related=[m for m in hist[1:-1] if m['role'] in ('user','assistant')] if context['needs_history'] else []
 request=[{'role':'system','content':__import__('backend.persona', fromlist=['*']).system(ANSWER_PROMPT,adapter.c,'preference_answer')},{'role':'user','content':json.dumps({'question':text,'related_history':related,'effective_style':style,'legacy_style_data':[{'text':r['text'],'scenario':r.get('scenario','general')} for r in selected if r.get('preference_value') not in VALUES.get(r.get('preference_key'),{})]},ensure_ascii=False,separators=(',',':'))}]
 schema=__import__('copy').deepcopy(ANSWER_SCHEMA)
 if style.get('exact_items'):schema['properties']['points'].update(minItems=style['exact_items'],maxItems=style['exact_items'])
 if facts:
  payload=json.loads(request[-1]['content']);payload['authorized_facts']=facts;request[-1]['content']=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
 expected=[{'role':'system','content':schema_instruction(schema)}]+request
 permit=DailyPermit(adapter.c,adapter.rid,adapter.cancel,adapter.epoch,expected,refs,slot='preference_answer')
 result=provider(adapter.c).chat(messages=request,format=schema,stream=False,cancel=adapter.cancel,permit=permit,request_id=adapter.rid,max_tokens=384 if style.get('response_length')=='concise' else 1024 if style.get('response_length')=='detailed' else 768)
 task.check();MemoryAccess.validate_refs(adapter.c,refs,'cloud')
 if current_pool()!=adapter.preference_pool_refs:raise ValueError('有效偏好已变化，本次旧回答未采用。')
 obj=json.loads(result.message.content);text,presentation=render(obj,style);adapter.answer_summary=obj['summary'];adapter.refs=refs
 adapter.usage={k:adapter.usage.get(k,0)+result.usage.get(k,0) for k in ('prompt_tokens','completion_tokens','total_tokens')};adapter.preference_trace={'domain':context['domain'],'mode':context['mode'],'refs':refs,'stages':2,'generation_input_bytes':permit.input_bytes};return text,presentation if style else None,None

def understanding_hint():
 # A real compact JSON Schema. The old human-readable required-map did not make
 # context a required JSON property and encouraged generating reply before selection.
 import copy
 from backend.assistant.assistant_understanding import ARGUMENTS, WIRE_CONTRACT, SCHEMA
 from backend.assistant.daily_chat import PREFERENCE
 preference=copy.deepcopy(PREFERENCE);preference['properties'].pop('scope',None);preference['required']=list(preference['properties'])
 return {'type':'object','properties':{'answer_context':CONTEXT_SCHEMA,'route':SCHEMA['properties']['route'],'file':{'type':['object','null'],'properties':{'method':{'type':'string','enum':list(WIRE_CONTRACT['methods'])},'arguments':{'type':'object','properties':ARGUMENTS,'additionalProperties':False}},'required':['method','arguments'],'additionalProperties':False,'description':'Method-specific allowed fields: '+json.dumps(WIRE_CONTRACT['methods'])},'preference':preference,'reply':{'type':['string','null'],'maxLength':200,'description':'Short routing fallback or clarification. Ordinary final answer is generated after code selects preferences; do not replace answer_context with a long answer.'}},'required':['answer_context','route','file','reply'],'additionalProperties':False}

CONTEXT_SCHEMA['properties']['item_limit']={'anyOf':[{'type':'null'},{'type':'object','properties':{'count':{'type':'integer','minimum':1,'maximum':8},'evidence':{'type':'string','maxLength':120}},'required':['count','evidence'],'additionalProperties':False}]}
CONTEXT_SCHEMA['required'].append('item_limit')
CONTEXT_PROMPT+=' item_limit is null for soft preferences; only a CURRENT exact number of answer points gets count plus exact current evidence. Never infer a count from memory.'

CONTEXT_SCHEMA['properties']['requirements']['items']={'oneOf':[{'type':'object','properties':{'key':{'const':k},'value':{'type':'string','enum':list(v)},'evidence':{'type':'string','minLength':1,'maxLength':200}},'required':['key','value','evidence'],'additionalProperties':False} for k,v in VALUES.items()]}
