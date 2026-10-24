"""Explicit local provider plus disabled cloud adapter. No discovery or auto-routing."""
import json
import hashlib
from http.client import IncompleteRead
import os
from types import SimpleNamespace
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler, HTTPSHandler
from urllib.error import HTTPError, URLError
import socket
import ssl,certifi
class NoCloudRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise RuntimeError("云端重定向被阻止，未自动重试")
class CloudError(ValueError):pass

def json_document(content):
    # Ambiguous duplicate names and non-JSON numeric constants are never silently accepted.
    def pairs(items):
        obj={}
        for name,value in items:
            if name in obj:raise ValueError('duplicate JSON field')
            obj[name]=value
        return obj
    def constant(value):raise ValueError('non-JSON numeric constant')
    return json.loads(content,object_pairs_hook=pairs,parse_constant=constant)

def safe_cloud_diagnostic(message,key):
    import re
    text=str(message).replace(key,'[REDACTED]') if key else str(message)
    text=re.sub(r'(?i)Bearer\s+[^\s"<>]+','Bearer [REDACTED]',text)
    text=re.sub(r'\bsk-[A-Za-z0-9_-]+','[REDACTED]',text)
    text=re.sub(r'/(?:Users|home)/[^\s"<>]+','[PRIVATE_PATH]',text)
    return text

from ollama import Client

class ProviderCancelled(RuntimeError):pass

def cancelled(cancel):
    if cancel and cancel.is_set():raise ProviderCancelled('请求已取消')

class OllamaProvider:
    memory_destination = "local"
    def __init__(self, client=None):self.client=client or Client(timeout=90)
    def chat(self, cancel=None, dispatch_phase=None, execution_budget=None, **kwargs):
        cancelled(cancel)
        import backend.providers.model_dispatch as model_dispatch
        return model_dispatch.submit(self.client,kwargs,cancel,dispatch_phase,execution_budget)

def schema_instruction(schema,compact=False,example=None):
    instruction=('JSON only. Output contract: ' if compact else 'Return a JSON object only. Application validates the JSON schema. Schema: ')+json.dumps(schema,ensure_ascii=False,separators=(',',':'))
    if example is None:instruction+=' Use valid JSON string escaping for quotes, backslashes and newlines; no prose outside the object.'
    else:instruction+=' Syntax only, choose the current intent. EXAMPLE JSON OUTPUT: '+json.dumps(example,ensure_ascii=False,separators=(',',':'))
    return instruction

class DeepSeekProvider:
    memory_destination = "cloud"
    def __init__(self, *, enabled=False, api_key=None, base_url='https://api.deepseek.com', model='deepseek-flash', transport=None, timeout=30):
        self.enabled=enabled;self.api_key=api_key;self.base_url=base_url.rstrip('/');self.model=model
        self.transport=transport or build_opener(NoCloudRedirect(),HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where()))).open;self.timeout=timeout
    def chat(self, *, messages, tools=None, stream=False, cancel=None, permit=None, request_id=None, max_tokens=512, format=None, schema_hint=None, schema_example=None, **unused):
        if not self.enabled:raise CloudError('DeepSeek出站未启用；未发送请求')
        if not self.api_key:raise CloudError('DeepSeek未配置凭据；未发送请求')
        from backend.providers.cloud_access import Permit
        if not isinstance(permit,Permit) or permit.rid!=request_id:raise CloudError('云端未授权，未发送请求')
        cancelled(cancel)
        from ollama._utils import convert_function_to_tool
        payload={'model':self.model,'messages':[self.message(m) for m in messages],'stream':False,'max_tokens':max_tokens,'thinking':{'type':'disabled'}}
        if tools:payload['tools']=[convert_function_to_tool(t).model_dump(exclude_none=True) if callable(t) else t for t in tools]
        if format:
            payload['response_format']={'type':'json_object'}
            payload['messages']=[{'role':'system','content':schema_instruction(schema_hint if schema_hint is not None else format,compact=schema_hint is not None,example=schema_example)}]+payload['messages']
        # Ordinary desktop answers are returned as a complete result. Streaming conversion
        # remains reserved; never starts an unguarded generator or performs hidden retries.
        if stream:raise CloudError('云端流式入口本轮未开放；请使用完整回答接口')
        permit.reserve(payload,self.base_url,self.api_key)
        cancelled(cancel)
        request=Request(self.base_url+'/chat/completions',data=json.dumps(payload,ensure_ascii=False).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+self.api_key})
        invalid_recorded=False
        try:
            permit.record('sent')
            with self.transport(request,timeout=self.timeout) as response:
                http_status=response.status
                response_request_id=response.headers.get('x-request-id') or response.headers.get('request-id')
                response_headers=getattr(response,'headers',{})
                try:
                    raw=response.read()
                    if isinstance(raw,str):raw=raw.encode('utf-8')
                    response_meta={'http_status':http_status,'body_bytes':len(raw),'body_sha256':hashlib.sha256(raw).hexdigest(),'request_id':safe_cloud_diagnostic(response_request_id,self.api_key) if response_request_id else None}
                    length=response_headers.get('Content-Length')
                    if length and str(length).isdigit():response_meta['declared_body_bytes']=int(length)
                    data=json_document(raw)
                    if not isinstance(data,dict):raise ValueError('response envelope is not an object')
                except IncompleteRead as error:
                    invalid_recorded=True
                    permit.record('failed',diagnostic={'stage':'transport','code':'incomplete_transfer','http_status':http_status,'received_bytes':len(error.partial)})
                    raise CloudError('服务响应未接收完整；本次未完成、结果待核对，未自动重试') from None
                except (ValueError,TypeError,UnicodeError):
                    invalid_recorded=True
                    permit.record('failed',diagnostic={'stage':'response_envelope','code':'invalid_envelope','http_status':http_status,'body_bytes':len(raw)})
                    raise CloudError('服务响应格式无法读取；本次未完成、结果待核对，未自动重试') from None
            permit.record('response_received',data.get('usage'),dict(response_meta,stage='response_received'))
            cancelled(cancel)
            permit.check_memory_refs()
            choice=data['choices'][0]
            if choice.get('finish_reason') not in ('stop','tool_calls'):
                invalid_recorded=True
                reason=choice.get('finish_reason')
                permit.record('truncated' if reason=='length' else 'invalid_response',data.get('usage'),{'stage':'model_finish','code':'output_limit' if reason=='length' else 'incomplete_generation','finish_reason':reason})
                raise CloudError('回答达到长度上限，尚未完整结束；未自动续写' if reason=='length' else '服务未完整生成回答；本次未完成，未自动重试')
            if not isinstance(choice.get('message'),dict) or (choice['message'].get('content') is not None and not isinstance(choice['message']['content'],str)):
                invalid_recorded=True
                permit.record('failed',diagnostic={'stage':'response_envelope','code':'invalid_envelope','http_status':http_status,'request_id':response_meta['request_id']})
                raise CloudError('服务响应字段无法读取；本次未完成、结果待核对，未自动重试')
            message=self.response_message(choice['message'])
            if message.tool_calls:raise CloudError('本次普通聊天不执行云端建议的工具操作')
            if format:
                import jsonschema
                try:obj=json_document(message.content)
                except (ValueError,TypeError):
                    invalid_recorded=True
                    permit.record('invalid_response',data.get('usage'),{'stage':'model_json','code':'invalid_json','http_status':http_status,'request_id':response_meta['request_id'],'content_bytes':len(message.content.encode('utf-8')),'content_sha256':hashlib.sha256(message.content.encode('utf-8')).hexdigest()})
                    raise CloudError('DeepSeek返回的JSON不合法；本次未完成、未执行，未自动重试') from None
                try:jsonschema.validate(obj,format)
                except jsonschema.ValidationError:
                    invalid_recorded=True
                    permit.record('invalid_response',data.get('usage'),{'stage':'model_schema','code':'invalid_schema','http_status':http_status,'request_id':response_meta['request_id']})
                    raise CloudError('DeepSeek返回的字段不符合契约；本次未完成、未执行，未自动重试') from None
            usage={k:v for k,v in (data.get('usage') or {}).items() if k in ('prompt_tokens','completion_tokens','total_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens') and type(v) is int and v>=0};permit.record('completed',usage,{'http_status':http_status,'request_id':safe_cloud_diagnostic(response_request_id,self.api_key) if response_request_id else None,'response_id':safe_cloud_diagnostic(data.get('id',''),self.api_key),'response_model':safe_cloud_diagnostic(data.get('model',self.model),self.api_key)})
            return SimpleNamespace(message=message,usage=usage,provider='deepseek',model=data.get('model',self.model),request_id=request_id,response_id=data.get('id'))
        except HTTPError as error:
            raw=error.read(65536)
            try:
                body=json.loads(raw);detail=body.get('error',{});detail=detail if isinstance(detail,dict) else {'message':str(detail)}
            except (ValueError,TypeError):body={};detail={'message':raw.decode('utf-8',errors='replace')}
            request_ident=error.headers.get('x-request-id') or error.headers.get('request-id') or body.get('request_id') or detail.get('request_id')
            permit.record('http_'+str(error.code),diagnostic={'stage':'http_response','http_status':error.code,'request_id':safe_cloud_diagnostic(request_ident,self.api_key) if request_ident else None,'error':{k:safe_cloud_diagnostic(detail[k],self.api_key) for k in ('message','type','code') if k in detail},'body_truncated':len(raw)==65536})
            labels={401:'鉴权失败',402:'余额不足',403:'服务拒绝访问',429:'请求限流',500:'服务异常',503:'服务繁忙'}
            raise CloudError('DeepSeek'+labels.get(error.code,'HTTP请求失败')+'；未自动重试或切换服务') from None
        except (TimeoutError,socket.timeout):
            permit.record('timeout');raise CloudError('DeepSeek请求超时；结果未完成，已发送请求可能计费') from None
        except ProviderCancelled:
            permit.record('cancelled');raise
        except CloudError:
            if not invalid_recorded:permit.record('invalid_response')
            raise
        except Exception as error:
            permit.record('failed',diagnostic={'stage':'connect_or_parse','http_status':None,'request_id':None,'error_type':type(error).__name__,'message':safe_cloud_diagnostic(error,self.api_key)});raise CloudError('DeepSeek连接或响应格式错误；未自动重试') from None
    @staticmethod
    def message(message):
        m=message.model_dump(exclude_none=True) if hasattr(message,'model_dump') else dict(message) if isinstance(message,dict) else dict(vars(message))
        m.pop('thinking',None)
        normalized=[]
        for call in m.get('tool_calls') or []:
            c=dict(call) if isinstance(call,dict) else dict(vars(call))
            fn=c['function'];fn=dict(fn) if isinstance(fn,dict) else dict(vars(fn))
            if isinstance(fn.get('arguments'),dict):fn['arguments']=json.dumps(fn['arguments'])
            c['function']=fn;c['type']='function';normalized.append(c)
        if normalized:m['tool_calls']=normalized
        else:m.pop('tool_calls',None)
        if m.get('role')=='tool' and 'tool_call_id' not in m:
            raise ValueError('云端工具结果必须提供tool_call_id，未发送请求')
        m.pop('tool_name',None)
        return m
    @staticmethod
    def response_message(m):
        calls=[]
        for call in m.get('tool_calls') or []:
            fn=call['function'];args=fn.get('arguments') or '{}'
            calls.append(SimpleNamespace(id=call.get('id'),function=SimpleNamespace(name=fn['name'],arguments=json.loads(args) if isinstance(args,str) else args)))
        return SimpleNamespace(role='assistant',content=m.get('content') or '',tool_calls=calls)
    def events(self,*args,**kwargs):
        raise CloudError('云端流式入口未开放，禁止绕过请求授权')

def configured_provider():
    name=os.getenv('MODEL_PROVIDER','ollama').lower()
    if name=='ollama':return OllamaProvider()
    if name=='deepseek':
        # Interface reserved for an explicit future opt-in; no startup probes.
        return DeepSeekProvider(enabled=os.getenv('DEEPSEEK_ENABLED','').lower()=='true',api_key=os.getenv('DEEPSEEK_API_KEY'),base_url=os.getenv('DEEPSEEK_BASE_URL','https://api.deepseek.com'),model=os.getenv('DEEPSEEK_MODEL','deepseek-flash'))
    raise ValueError('未知MODEL_PROVIDER；不会自动切换云端')

# Request-scoped override; never changes the global provider or routes to cloud.
from contextvars import ContextVar
from contextlib import contextmanager
CURRENT=ContextVar('assistant_provider',default=None)
@contextmanager
def using(provider):
 token=CURRENT.set(provider.chat)
 try:yield
 finally:CURRENT.reset(token)
