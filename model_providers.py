"""Explicit local provider plus disabled cloud adapter. No discovery or auto-routing."""
import json
import os
from types import SimpleNamespace
from urllib.request import Request, urlopen
from ollama import Client

class ProviderCancelled(RuntimeError):pass

def cancelled(cancel):
    if cancel and cancel.is_set():raise ProviderCancelled('请求已取消')

class OllamaProvider:
    def __init__(self, client=None):self.client=client or Client(timeout=90)
    def chat(self, cancel=None, dispatch_phase=None, execution_budget=None, **kwargs):
        cancelled(cancel)
        import model_dispatch
        return model_dispatch.submit(self.client,kwargs,cancel,dispatch_phase,execution_budget)

class DeepSeekProvider:
    def __init__(self, *, enabled=False, api_key=None, base_url='https://api.deepseek.com', model='deepseek-flash', transport=None):
        self.enabled=enabled;self.api_key=api_key;self.base_url=base_url.rstrip('/');self.model=model
        self.transport=transport or urlopen
    def chat(self, *, messages, tools=None, stream=False, cancel=None, **unused):
        # Guard before constructing a request, even when a key happens to exist.
        if not self.enabled:raise RuntimeError('DeepSeek接口已预留，未启用、未真实验证')
        if not self.api_key:raise RuntimeError('DeepSeek未配置Key；不会发送请求')
        cancelled(cancel)
        from ollama._utils import convert_function_to_tool
        payload={'model':self.model,'messages':[self.message(m) for m in messages],'stream':stream}
        if tools:payload['tools']=[convert_function_to_tool(t).model_dump(exclude_none=True) if callable(t) else t for t in tools]
        request=Request(self.base_url+'/chat/completions',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+self.api_key})
        if stream:return self.events(request,cancel)
        with self.transport(request,timeout=60) as response:data=json.load(response)
        cancelled(cancel)
        return SimpleNamespace(message=self.response_message(data['choices'][0]['message']))
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
    def events(self,request,cancel):
        assembled={'role':'assistant','content':'','tool_calls':[]};calls={}
        with self.transport(request,timeout=60) as response:
            for line in response:
                cancelled(cancel)
                if not line.startswith(b'data:'):continue
                raw=line[5:].strip()
                if raw==b'[DONE]':break
                item=json.loads(raw)
                if item.get('error'):raise RuntimeError('DeepSeek stream error')
                for choice in item.get('choices',[]):
                    delta=choice.get('delta',{})
                    assembled['content'] += delta.get('content') or ''
                    for fragment in delta.get('tool_calls') or []:
                        index=fragment['index'];call=calls.setdefault(index,{'id':'','type':'function','function':{'name':'','arguments':''}})
                        if fragment.get('id'):call['id']=fragment['id']
                        fn=fragment.get('function') or {}
                        call['function']['name'] += fn.get('name') or ''
                        call['function']['arguments'] += fn.get('arguments') or ''
                    yield {'type':'delta','delta':delta,'finish_reason':choice.get('finish_reason')}
        cancelled(cancel)
        assembled['tool_calls']=[calls[i] for i in sorted(calls)]
        yield {'type':'message','message':self.response_message(assembled)}

def configured_provider():
    name=os.getenv('MODEL_PROVIDER','ollama').lower()
    if name=='ollama':return OllamaProvider()
    if name=='deepseek':
        # Interface reserved for an explicit future opt-in; no startup probes.
        return DeepSeekProvider(enabled=os.getenv('DEEPSEEK_ENABLED','').lower()=='true',api_key=os.getenv('DEEPSEEK_API_KEY'),base_url=os.getenv('DEEPSEEK_BASE_URL','https://api.deepseek.com'),model=os.getenv('DEEPSEEK_MODEL','deepseek-flash'))
    raise ValueError('未知MODEL_PROVIDER；不会自动切换云端')
