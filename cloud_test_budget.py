"""Optional explicit developer-only budget. No daily allowance or credential storage."""
import os,json,time,uuid,fcntl,threading,hashlib
from pathlib import Path
from contextlib import contextmanager
LOCK=threading.RLock()
@contextmanager
def locked(path):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 with LOCK:
  with path.with_suffix('.lock').open('a') as f:
   os.chmod(f.name,0o600);fcntl.flock(f,fcntl.LOCK_EX)
   data=json.loads(path.read_text()) if path.exists() else {'version':1,'requests':[],'baseline_microcny':0}
   yield data
   temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
   with temp.open('x') as t:os.chmod(temp,0o600);json.dump(data,t);t.flush();os.fsync(t.fileno())
   os.replace(temp,path)
def liability(row):return row.get('settled_microcny',row['reserved_microcny'])
class Budget:
 def __init__(self,policy):self.policy=Path(policy)
 def reserve(self,payload,request_id):
  # No supplied credentials, auth headers or payload body are persisted.
  p=json.loads(self.policy.read_text())
  if not p.get('enabled') or payload.get('model')!='deepseek-flash':raise ValueError('开发测试授权未开启或模型不在范围，未发送。')
  if p.get('input_microcny_per_token')!=2 or p.get('output_microcny_per_token')!=8:raise ValueError('费用依据缺失，未发送。')
  if not 0<p.get('max_requests',0)<=40 or not 0<p.get('max_microcny',0)<=1000000 or p.get('cumulative_cap_microcny')!=5000000:raise ValueError('开发测试预算配置无效，未发送。')
  messages=payload['messages'];tokens=len(json.dumps(payload,ensure_ascii=False).encode())+64*len(messages)+256
  amount=tokens*2+payload['max_tokens']*8
  with locked(p['ledger']) as d:
   rows=[r for r in d['requests'] if r['run_id']==p['run_id']]
   if any(r['request_id']==request_id for r in d['requests']):raise ValueError('开发请求已预占，禁止重复。')
   total=sum(map(liability,rows));cumulative=d.get('baseline_microcny',0)+sum(map(liability,d['requests']))
   if len(rows)>=p['max_requests'] or total+amount>p['max_microcny'] or cumulative+amount>p['cumulative_cap_microcny']:raise ValueError('开发测试次数或费用预算耗尽，未发送。')
   ident=uuid.uuid4().hex;d['requests'].append({'id':ident,'run_id':p['run_id'],'request_id':request_id,'at':time.time(),'model':'deepseek-flash','payload_sha256':hashlib.sha256(json.dumps(payload,ensure_ascii=False).encode()).hexdigest(),'reserved_input_tokens_upper':tokens,'reserved_output_tokens':payload['max_tokens'],'reserved_microcny':amount,'status':'reserved','pricing':'peak cache-miss 2 / output 8 CNY per million'})
  return ident
 def record(self,ident,status,usage=None):
  p=json.loads(self.policy.read_text())
  with locked(p['ledger']) as d:
   row=next(x for x in d['requests'] if x['id']==ident);row['status']=status
   if usage and all(type(usage.get(k)) is int and usage[k]>=0 for k in ('prompt_tokens','completion_tokens')):
    row['usage']={k:usage[k] for k in ('prompt_tokens','completion_tokens')};row['settled_microcny']=usage['prompt_tokens']*2+usage['completion_tokens']*8
    if row['settled_microcny']>row['reserved_microcny']:row['needs_reconciliation']=True
   elif status not in ('reserved','sent'):row['needs_reconciliation']=True
   if any(r.get('settled_microcny',0)>r['reserved_microcny'] for r in d['requests']):
    p['enabled']=False;self.policy.write_text(json.dumps(p,indent=2))
def configured():
 path=os.getenv('DEEPSEEK_TEST_POLICY')
 scoped=__import__('web_usage').cloud_policy()
 return Budget(scoped or path) if (scoped or path) else None
