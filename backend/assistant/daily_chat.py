"""Daily understanding allowance; never a grant for private bodies or tool writes."""
import json,time,uuid,hmac,re
from backend.providers.cloud_access import Permit, ledger, identity
from backend.memory.assistant_memory import MemoryAccess
PREFERENCE={'type':['object','null'],'properties':{'action':{'type':'string','enum':['save','update','forget','clarify','none']},'scope':{'type':'string','enum':['long_term','session','turn','none']},'key':{'type':'string','enum':['response_length','answer_structure','comparison_format','language','tone','explanation_style','examples','formatting','recent','unspecified']},'evidence':{'type':'string','maxLength':300},'sensitive':{'type':'boolean'}},'required':['action','scope','key','evidence','sensitive'],'additionalProperties':False}
PROMPT=''' DAILY_CHAT_PREFERENCE_V1 当前用户已授权普通聊天，不需要再请求聊天授权。一次返回普通回答/任务分流及可选preference。只处理当前用户亲自明确表达的非敏感回答方式偏好（长短、结构、语气、语言、比较呈现等）。不能从文件、历史助手回答、引用、否定、假设、第三人称推断偏好。临时“这次”用turn，当前会话用session，不持久化。长期明确要求才long_term；key使用结构定义的属性；response_length=回答详略，answer_structure=结论在前还是解释在前的内容顺序（不指列表排版），comparison_format=比较呈现，language=回答语言，tone=语气，explanation_style=解释方式，examples=举例偏好，formatting=列表/项目符号/段落/要点呈现的排版方式。不能用其它属性冒充。刚才那个用recent，无法确定用unspecified并clarify；evidence必须逐字摘自当前用户原文，不生成或改写。save=新偏好或明确重述同属性值（用户对自己的新要求可替代旧值）；update=明确纠正同一属性；forget=用户明确遗忘已存偏好，必须route=memory；“不用记住”若只是不要保存当前内容，则none而不是forget，不能借已有偏好作为遗忘对象；敏感内容不可保存；不明确则clarify。指代“刚才那个”只有程序表示存在可靠最近偏好时才可update，不能猜其它对象。无需读取未许可旧记忆正文来提取当前偏好。不能声称已经记住、已忘记或执行了工具：持久化成功由程序生成回执。仅某类任务的明确偏好必须填scenario（coding/比较comparison等），不能推广为general。记忆不授予工具/数据/费用权限。普通聊天应根据获准历史自然追问；日历、文件、联网仍由各自权限处理。'''
def enabled(cfg):return cfg.get('daily_chat_enabled') is True
CONTROL_KEYS=('history','memory','file_intent','calendar_intent')
def controls(c,cfg):
 # Old grant flags are explicit choices, never interpreted as new defaults.
 old=getattr(c,'cloud_grant',None) or {}
 chosen=getattr(c,'daily_data_controls',{})
 defaults={'history':True,'memory':True,'file_intent':bool(cfg.get('understanding_enabled')),'calendar_intent':bool(cfg.get('calendar_enabled'))}
 result={k:chosen.get(k,old.get(k,defaults[k])) is True for k in CONTROL_KEYS}
 result['file_intent'] &= bool(cfg.get('understanding_enabled'));result['calendar_intent'] &= bool(cfg.get('calendar_enabled'))
 return result
def preserve_controls(c):
 if getattr(c,'cloud_grant',None):c.daily_data_controls=controls(c,__import__('backend.providers.cloud_access', fromlist=['*']).config())
def allowance(c,cfg):
 return dict(id='daily:'+c.memory_owner,config_id=identity(cfg),daily=True,web_summary=False,**controls(c,cfg))
def check_limits(c,cfg,output=512,size=0):
 used=getattr(c,'daily_usage',{'calls':0,'output_tokens':0})
 if cfg.get('explicit_call_limit') is not None and used['calls']>=cfg['explicit_call_limit']:raise ValueError('已达到你配置的会话调用保护，请查看设置；未发送。')
 if cfg.get('explicit_output_limit') is not None and used['output_tokens']+output>cfg['explicit_output_limit']:raise ValueError('已达到你配置的会话输出保护，请查看设置；未发送。')
 g=getattr(c,'cloud_grant',None)
 if g and (g['calls']>=g['max_calls'] or g['input_bytes']+size>g['max_input_bytes'] or g['output_tokens']+output>g['max_output_tokens']):raise ValueError('已达到原会话授权的消费保护；未发送，请在设置中查看或重新授权。')
class DailyPermit(Permit):
 def reserve(self,payload,base_url,api_key):
  from backend.assistant.bubble_chat import LOCK
  import backend.providers.cloud_access as cloud_access, backend.providers.cloud_test_budget as cloud_test_budget
  with LOCK:
   if self.used or self.cancel.is_set() or self.c.epoch!=self.version or self.c.request!=self.rid:raise ValueError('请求已失效，未发送。')
   cfg=cloud_access.config()
   if not enabled(cfg) or not cloud_access.available(cfg) or not cfg['api_key']:raise ValueError('DeepSeek未配置或日常聊天关闭；可主动选择本地Qwen。')
   self.check_memory_refs()
   if cfg['base_url']!=base_url or not hmac.compare_digest(cfg['api_key'],api_key) or payload.get('model')!=cfg['model'] or payload.get('tools') or payload['messages']!=self.expected:raise ValueError('请求超出当前聊天资料范围，未发送。')
   output=payload.get('max_tokens');size=len(json.dumps(payload,ensure_ascii=False).encode())
   if type(output) is not int or not 1<=output<=1024:raise ValueError('回答长度设置无效，未发送。')
   if size>min(16000,cfg.get('input_limit',16000)):raise ValueError('这条消息连同必要上下文过长，请缩短或分段发送；当前任务保留，尚未发送。')
   # Only explicitly configured user session protections apply. Default developer/session
   # proposal caps do not become a daily usage lock. No new daily/monthly allowance.
   used=getattr(self.c,'daily_usage',{'calls':0,'output_tokens':0})
   check_limits(self.c,cfg,output,size)
   self.test_budget=cloud_test_budget.configured()
   if self.test_budget:self.test_reservation=self.test_budget.reserve(payload,self.call_id)
   with ledger() as d:
    if any(x['request_id']==self.call_id for x in d['requests']):raise ValueError('请求已提交，未重复发送。')
    self.input_bytes=size;self.record_id=uuid.uuid4().hex
    d['requests'].append({'id':self.record_id,'request_id':self.call_id,'parent_request_id':self.rid,'provider':'deepseek','model':cfg['model'],'at':time.time(),'profile':self.profile,'usage_class':'development' if self.test_budget else 'daily','reserved_input_bytes':size,'reserved_output_tokens':output,'reserved_microcny':(size+64*len(payload['messages'])+256)*2+output*8,'pricing':'conservative 2/8 CNY per million tokens; estimate, not bill','status':'reserved'})
   g=self.c.cloud_grant
   if g:g['calls']+=1;g['input_bytes']+=size;g['output_tokens']+=output
   self.c.daily_usage={'calls':used['calls']+1,'output_tokens':used['output_tokens']+output};self.used=True
 def record(self,status,usage=None,diagnostic=None):
  safe={k:v for k,v in (diagnostic or {}).items() if k in ('http_status','request_id','error_type','finish_reason') and isinstance(v,(int,float,bool))} or None
  super().record(status,usage,safe)
  if self.record_id:
   with ledger() as d:
    r=next(x for x in d['requests'] if x['id']==self.record_id)
    if usage and all(type(usage.get(k)) is int and usage[k]>=0 for k in ('prompt_tokens','completion_tokens')):r['estimated_microcny']=usage['prompt_tokens']*2+usage['completion_tokens']*8
    elif status not in ('reserved','sent') and not r.get('usage'):r['usage_unknown']=True

def apply(c,proposal,text,epoch,revision):
 """Current user evidence only; semantic IDs/permission cannot be supplied by model."""
 import backend.memory.bubble_memory as bubble_memory
 if not proposal or proposal['action']=='none' or proposal.get('duration',proposal.get('scope'))!='long_term':return None,[]
 evidence=proposal['evidence'].strip();key=proposal['key'];action=proposal['action'];scenario=proposal.get('scenario','unknown' if 'duration' in proposal else 'general')
 if c.epoch!=epoch or not c.cancel or c.cancel.is_set():raise ValueError('请求已失效，没有保存偏好。')
 if not evidence or evidence not in text:return None,[]
 if proposal['sensitive'] or re.search(r'密码|密钥|身份证|银行卡|病历|诊断|住址|电话|邮箱|token|password|secret|[\w.+-]+@[\w.-]+\.[a-z]+',evidence,re.I):return '未自动保存：本功能仅记住你明确表达的非敏感偏好。',[]
 if action=='forget' and not re.search(r'忘|删除|移除|清除|撤销|不再保留|取消(?:保存|记忆)|\b(?:forget|remove|erase)\b',evidence,re.I):return '未更改长期记忆：本次内容不会自动保存；若要遗忘已有偏好，请说明具体条目或在“记忆”中操作。',[]
 state=MemoryAccess.list_for(c)
 if state['revision']!=revision:raise ValueError('记忆在处理期间已变化，本条偏好未更改；请刷新记忆后重试。')
 rows=state['items']
 if key=='recent':
  ref=getattr(c,'preference_ref',None);row=next((r for r in rows if ref and r['id']==ref['id'] and r['version']==ref['version']),None)
  if not row:return '尚未更改：当前没有可靠的最近偏好，请说明具体属性。',[]
  key=row['preference_key'];scenario=row.get('scenario','general')
 if scenario not in __import__('backend.memory.memory_learning', fromlist=['*']).SCENARIOS:return '尚未更改：请说明这项偏好适用于哪些任务。',[]
 if key=='unspecified':return '尚未更改：请说明你希望长期保留的具体回答方式。',[]
 targets=[r for r in rows if r.get('preference_key')==key and r['kind']=='preference' and r['audience']=='shared' and r['scopes']==['chat'] and r.get('scenario','general')==scenario]
 if len(targets)>1:return '尚未更改：有多个相关偏好，请在记忆中选择具体条目。',[]
 if action=='clarify':return '尚未更改：请说明要调整或忘记哪项偏好。',[]
 if action in ('update','forget') and not targets:return '尚未更改：没有可靠对应的偏好，请在记忆中选择条目，或完整说明新偏好。',[]
 # An old row without semantic key cannot safely be replaced from model text.
 if not targets and any(r['kind']=='preference' and not r.get('preference_key') for r in rows):return '尚未保存：已有旧格式偏好，请在记忆中明确选择要更新的条目，避免留下矛盾版本。',[]
 if action=='forget':
  # The extractor's semantic key alone is insufficient authority for deletion.
  names={'response_length':('长度','长短','详略','verbosity','length'),'answer_structure':('结论','顺序','结构','structure','order'),'comparison_format':('比较','对比','对照','表格','comparison','table'),'language':('语言','中文','英文','language'),'tone':('语气','口吻','tone'),'explanation_style':('讲解方式','讲解步骤','分步','解释方式','explanation style'),'examples':('例子','举例','example'),'formatting':('要点','排版','列表','段落','项目符号','formatting','bullet')}
  named={k for k,labels in names.items() if any(label in evidence.casefold() for label in labels)}
  if proposal['key']!='recent' and named!={key}:return '尚未遗忘：偏好类别未能与原文可靠对应，请在“记忆”中选择具体条目。',[]
  bubble_memory.mutate('delete',id=targets[0]['id'],revision=state['revision']);c.preference_ref=None
  return '已忘记这项长期偏好，后续记忆召回不再使用；历史聊天与备份不随此操作删除。',[]
 if action not in ('save','update'):raise ValueError('偏好操作格式无效，没有保存。')
 if targets and bubble_memory._norm(targets[0]['text'])==bubble_memory._norm(evidence) and (proposal.get('value') is None or targets[0].get('preference_value')==proposal.get('value')):result=state
 elif targets:result=bubble_memory.mutate('update',id=targets[0]['id'],text=evidence,revision=state['revision'],preference_key=key,scenario=scenario,preference_value=proposal.get('value'),kind='preference',style_policy=True)
 else:result=bubble_memory.mutate('save',text=evidence,revision=state['revision'],preference_key=key,scenario=scenario,preference_value=proposal.get('value'),kind='preference',style_policy=True)
 row=next(r for r in result['items'] if r.get('preference_key')==key and r.get('scenario','general')==scenario);c.preference_ref={'id':row['id'],'key':key,'version':row['version']}
 return ('行，这项长期偏好已更新。' if targets else '行，这项长期偏好已记住。')+('可按已许可范围用于云端回答。' if __import__('backend.memory.style_preferences', fromlist=['*']).permitted(row) else '长期召回目前仅限本地；云端使用可在记忆管理中设置。'),[{'id':row['id'],'version':row['version']}]

# Scoped explicit preferences share the original identity/CAS path.
PREFERENCE['properties']['scenario']={'type':'string','enum':__import__('backend.memory.memory_learning', fromlist=['*']).SCENARIOS}

PREFERENCE['properties']['value']={'type':'string','enum':sorted({v for values in __import__('backend.memory.memory_learning', fromlist=['*']).VALUES.values() for v in values})}
PREFERENCE['properties']['signal']={'type':'string','enum':['request','self_preference','endorsement','none']}

# Semantic duration is distinct from program-owned storage audience/task scopes.
PREFERENCE['properties']['duration']={'type':'string','enum':['long_term','unspecified','turn','session','object','unknown']}
PREFERENCE['properties']['scope']['enum']+=['object','unknown','unspecified']
PREFERENCE['properties']['scenario']['enum']=list(__import__('backend.memory.memory_learning', fromlist=['*']).SCENARIOS)+['unknown']
PREFERENCE['properties']['value']['type']=['string','null']
PREFERENCE['properties']['value']['enum']+=[None]
PREFERENCE['required']=['action','key','evidence','sensitive']  # replay legacy responses with scope; new wire requires complete semantics

PREFERENCE['properties']['applicability']={'type':'string','description':'Exact current-user clause limiting applicable task; empty only if general/unknown. Include restrictions even when separate from preference evidence.'}
PREFERENCE['properties']['scenario']['description']='Most specific explicitly limited activity. coding includes programming/code explanations; do NOT use generic explanation for coding. explanation only a broad explanation preference without a more specific activity; general is unrestricted communication, unknown is uncertain.'

PREFERENCE['properties']['duration']['enum']+=['next_turns']
PREFERENCE['properties']['turns']={'type':['integer','null'],'minimum':1,'maximum':10}

PREFERENCE['properties']['value']['enum']+=['tsundere','warm','playful']
