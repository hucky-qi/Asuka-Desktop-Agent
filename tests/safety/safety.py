"""Test-only fail-closed process boundary and atomic independent validation quotas."""
from pathlib import Path
from contextlib import contextmanager
from urllib.parse import urlsplit
from urllib.request import Request,build_opener,ProxyHandler,HTTPRedirectHandler
import json,os,fcntl,uuid,time,threading,shutil,sys,subprocess

class SafetyError(RuntimeError):pass
class BudgetError(SafetyError):pass

class Ledger:
    def __init__(self,path,query_limit,call_limit):
        if any(type(n) is not int or n<0 for n in (query_limit,call_limit)):raise BudgetError('Invalid explicit budget')
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.limits={'queries':query_limit,'calls':call_limit}
        with self.locked() as row:
            if not row:row.update(version=1,limits=self.limits,used={'queries':0,'calls':0},queries={},calls=[])
            elif row.get('limits')!=self.limits:raise BudgetError('Existing budget must not be reset or silently changed')
    @contextmanager
    def locked(self):
        with self.path.with_suffix(self.path.suffix+'.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            row=json.loads(self.path.read_text()) if self.path.exists() else {}
            yield row
            temp=self.path.with_suffix('.'+uuid.uuid4().hex+'.tmp');temp.write_text(json.dumps(row,indent=2));os.replace(temp,self.path)
    def query(self):
        with self.locked() as row:
            if row['used']['queries']>=row['limits']['queries']:raise BudgetError('Query budget exhausted before send')
            token=uuid.uuid4().hex;row['used']['queries']+=1;row['queries'][token]={'active':True,'at':time.time()}
        return token
    def finish(self,token):
        with self.locked() as row:
            if token in row['queries']:row['queries'][token]['active']=False
    def call(self,token):
        with self.locked() as row:
            if not row['queries'].get(token,{}).get('active'):raise BudgetError('No active budgeted test query; bypass refused')
            if row['used']['calls']>=row['limits']['calls']:raise BudgetError('Model-call budget exhausted before send')
            ident=uuid.uuid4().hex;row['used']['calls']+=1;row['calls'].append({'id':ident,'query':token,'at':time.time()})
        return ident
    def snapshot(self):
        with self.locked() as row:return json.loads(json.dumps(row))

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise SafetyError('Redirect refused; no second endpoint')

def checked_endpoint(url,loopback=False):
    if not isinstance(url,str):raise SafetyError('Dependency endpoint must be explicit')
    u=urlsplit(url)
    if u.scheme not in ('http','https') or not u.hostname or not u.port or u.username or u.password or u.query or u.fragment:raise SafetyError('Invalid explicit endpoint')
    if loopback and u.hostname not in ('127.0.0.1','::1'):raise SafetyError('Mock dependency must use explicit loopback IP')
    return u

def post_json(url,body,timeout=2):
    # No inherited proxy, retries, discovery or fallback endpoints.
    checked_endpoint(url)
    request=Request(url,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    with build_opener(ProxyHandler({}),NoRedirect()).open(request,timeout=timeout) as response:return json.load(response)

class MockDependency:
    def __init__(self,endpoint,paths):
        self.endpoint=endpoint.rstrip('/');checked_endpoint(endpoint,loopback=True)
        self.paths=frozenset(paths)
        if not self.paths or any(not p.startswith('/') for p in paths):raise SafetyError('Mock interface fixtures required')
    def post(self,path,body):
        if path not in self.paths:raise SafetyError('Unimplemented mock interface; stopped before send')
        return post_json(self.endpoint+path,body)

class RealGate:
    """Must be explicitly constructed from enabled policy, then reserve query and call."""
    def __init__(self,policy,ledger_path):
        required={'enabled','query_limit','call_limit','model_endpoint'}
        if not required<=policy.keys() or policy['enabled'] is not True:raise BudgetError('Real validation disabled or incomplete')
        if any(type(policy[k]) is not int or policy[k]<=0 for k in ('query_limit','call_limit')):raise BudgetError('Real validation needs positive explicit query and call budgets')
        checked_endpoint(policy['model_endpoint']);self.endpoint=policy['model_endpoint'];self.ledger=Ledger(ledger_path,policy['query_limit'],policy['call_limit'])
    def query(self):return self.ledger.query()
    def finish(self,token):return self.ledger.finish(token)
    def model(self,token,body):
        self.ledger.call(token)  # atomic pre-reservation; failed attempts are not refunded
        return post_json(self.endpoint,body,timeout=55)

# Invoked explicitly before production imports in an isolated Python child.
def install_guard(config_path):
    cfg=json.loads(Path(config_path).read_text())
    peers=cfg.get('connect');listeners=cfg.get('listen')
    if not isinstance(peers,list) or not peers or not isinstance(listeners,list):raise SafetyError('Isolation configuration incomplete')
    for pair in peers+listeners:
        if not isinstance(pair,list) or len(pair)!=2 or pair[0] not in ('127.0.0.1','::1') or type(pair[1]) is not int or not 0<pair[1]<65536:raise SafetyError('Invalid isolation endpoint')
    allowed={tuple(p) for p in peers};binds={tuple(p) for p in listeners}
    def audit(event,args):
        if event in ('socket.connect','socket.sendto','socket.sendmsg'):
            address=args[1] if event!='socket.sendmsg' else args[-1]
            if not isinstance(address,tuple) or tuple(address[:2]) not in allowed:raise SafetyError('Isolation blocked unconfigured connection before send')
        elif event=='socket.bind':
            address=args[1]
            if not isinstance(address,tuple) or tuple(address[:2]) not in binds:raise SafetyError('Isolation blocked unconfigured listener')
        elif event in ('socket.getaddrinfo','socket.gethostbyname','socket.gethostbyaddr'):
            if args[0] not in ('127.0.0.1','::1'):raise SafetyError('Isolation blocked DNS before resolution')
        elif event=='socket.getnameinfo':
            if args[0][0] not in ('127.0.0.1','::1'):raise SafetyError('Isolation blocked reverse DNS before resolution')
        elif event in ('subprocess.Popen','os.system','os.posix_spawn','os.exec','os.fork','os.forkpty'):
            raise SafetyError('Isolation blocked subprocess/native-service escape')
    sys.addaudithook(audit)

def launch(app,env,depport,appport,log,mock_open=False,cwd=None):
    """Always called AFTER overlays. No reliance on a sitecustomize import succeeding."""
    app=Path(app).resolve()
    if type(depport) is not int or depport==11434 or type(appport) is not int or appport==8765:raise SafetyError('Explicit isolated ports required')
    for f in app.rglob('*.py'):
        f.write_text(f.read_text().replace("host='http://127.0.0.1:11434'",f"host='http://127.0.0.1:{depport}'"))
    # Remove old optional guard: startup must fail loudly, not ignore import errors.
    (app/'sitecustomize.py').unlink(missing_ok=True)
    shutil.copy2(Path(__file__).resolve(),app/'_test_safety.py')
    cfg=app/'_isolation.json';cfg.write_text(json.dumps({'connect':[['127.0.0.1',depport]],'listen':[['127.0.0.1',appport]]}))
    boot=app/'_isolated_boot.py';boot.write_text("import sys,runpy\nfrom pathlib import Path\np=Path(__file__).resolve().parent\nsys.path.insert(0,str(p))\nfrom _test_safety import install_guard\ninstall_guard(p/'_isolation.json')\nrunpy.run_path(str(p/'app.py'),run_name='__main__')\n")
    if mock_open:
        # Simulate the external macOS open command over the allowed HTTP peer;
        # all other subprocesses stay prohibited by the audit guard.
        original=boot.read_text();target="runpy.run_path(str(p/'app.py'),run_name='__main__')"
        adapter=f"""import subprocess
from _test_safety import post_json,SafetyError
def external_open(args,**kwargs):
    if not isinstance(args,list) or len(args)!=2 or args[0]!='open' or not Path(args[1]).resolve().is_relative_to(p/'fixtures'):
        raise SafetyError('Unimplemented external process dependency')
    post_json('http://127.0.0.1:{depport}/open',{{'path':args[1]}})
    return subprocess.CompletedProcess(args,0)
subprocess.run=external_open
"""
        boot.write_text(original.replace(target,adapter+target))
    clean={k:v for k,v in env.items() if not k.startswith(('PYTHON','BAIDU_','DEEPSEEK_','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'))}
    clean.update(OLLAMA_HOST=f'http://127.0.0.1:{depport}',DEEPSEEK_ENABLED='false',BAIDU_API_KEY='blackbox-nonsecret-placeholder',BAIDU_FREE_RESOURCES_CONFIRMED='true',BAIDU_LOCAL_DAILY_LIMIT='30',BAIDU_TEST_MODE='true',BAIDU_TEST_LIMIT='7')
    return subprocess.Popen([sys.executable,'-I','-u',str(boot)],cwd=app if cwd is None else cwd,env=clean,stdout=log,stderr=log)
