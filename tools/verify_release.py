"""No-key startup smoke test in a copied app with loopback-only dependencies."""
from pathlib import Path
import tempfile,subprocess,sys,os,json,socket,time,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.request import urlopen,Request
P=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(P/'tests/http'))
import run as bb
class Trap(BaseHTTPRequestHandler):
 count=0
 def log_message(self,*a):pass
 def do_POST(self):type(self).count+=1;self.send_error(503)
def get(base,path,body=None):
 with urlopen(Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'}),timeout=3) as r:return r.read()
if __name__=='__main__':
 dep=ThreadingHTTPServer(('127.0.0.1',0),Trap);threading.Thread(target=dep.serve_forever,daemon=True).start()
 with tempfile.TemporaryDirectory() as folder:
  appport=bb.port();app,env=bb.prepare(Path(folder),appport,dep.server_port)
  import shutil
  shutil.copy2(P/'tests/safety/safety.py',app/'_test_safety.py')
  (app/'_isolation.json').write_text(json.dumps({'connect':[['127.0.0.1',dep.server_port]],'listen':[['127.0.0.1',appport]]}))
  boot=app/'_boot.py';boot.write_text("import sys,runpy\nfrom pathlib import Path\np=Path(__file__).resolve().parent\nsys.path.insert(0,str(p))\nfrom _test_safety import install_guard\ninstall_guard(p/'_isolation.json')\nrunpy.run_path(str(p/'app.py'),run_name='__main__')\n")
  env={k:v for k,v in os.environ.items() if not k.startswith(('BAIDU_','BUBBLE_WEB_','DEEPSEEK_','OLLAMA_'))};env.update(BUBBLE_WEB_ENABLED='false',DEEPSEEK_ENABLED='false',MODEL_PROVIDER='ollama',OLLAMA_HOST=f'http://127.0.0.1:{dep.server_port}')
  with (Path(folder)/'server.log').open('w') as log:
   child=subprocess.Popen([sys.executable,'-I',str(boot)],cwd=app,env=env,stdout=log,stderr=log)
   try:
    base=f'http://127.0.0.1:{appport}';bb.wait_for(lambda:get(base,'/api/health'),10)
    assert b'<!doctype' in get(base,'/').lower()
    assert b'<html' in get(base,'/bubble').lower()
    import hashlib
    for name in ('speech-view.js','shared-reminder.js','input-guard.js','unified-web.js','calendar-view.js'):
     assert get(base,'/'+name)==(P/'frontend'/name).read_bytes(),name
    assert get(base,'/reminder-notifications')==(P/'frontend/reminder-notifications.html').read_bytes()
    # The complete page is an unchanged extracted asset; all paths remain root URLs.
    assert get(base,'/')==(P/'frontend/index.html').read_bytes()
    config=json.loads(get(base,'/api/bubble/config'));assert not config.get('web_configured')
    result=json.loads(get(base,'/api/files/search',{'scope':'workspace','keyword':'ZX8427','extensions':['pdf']}))
    rows=[r for e in result.get('events',[]) for r in e.get('results',[])];assert rows and all('ZX8427' in r['path'] and r['path'].endswith('.pdf') for r in rows)
    native_result='not_requested'
    if '--native-smoke' in sys.argv:
     binary=P/'.desktop_pet'
     assert binary.is_file(),'Build desktop_pet.swift before native smoke'
     # Separate no-model instance, bound only to this isolated backend. No clicks,
     # no calendar launcher, no real model or web endpoints.
     native=subprocess.Popen([str(binary),base,'--bubble-chat'],cwd=app,env=env,stdout=log,stderr=log)
     try:
      time.sleep(1.5);assert native.poll() is None,'Native fallback exited during startup'
      assert 'Native blue fallback ready' in (Path(folder)/'server.log').read_text(),'No native fallback readiness marker'
      native_result='native_window_ready_without_model; mouse interaction not automated'
     finally:native.terminate();native.wait(timeout=5)
    sid=json.loads(get(base,'/api/bubble/session',{}))['session_id']
    import uuid
    result=bb.request(base,'/api/bubble/chat',{'session_id':sid,'request_id':uuid.uuid4().hex,'question':'用一句话解释API。','web_enabled':False})
    assert result.get('http_error') in (400,403) and any(word in result.get('body','') for word in ('DeepSeek','未配置','未启用')),'Missing configuration must be explicit before outbound'
    assert Trap.count==0,'Unexpected outbound dependency request'
    print(json.dumps({'no_key_local_startup':'PASS','web_and_bubble_pages':'PASS','fixture_filename_search':'PASS','model_web_dependency_calls':Trap.count,'native_fallback':native_result}))
   finally:child.terminate();child.wait(timeout=8)
 dep.shutdown();dep.server_close()
