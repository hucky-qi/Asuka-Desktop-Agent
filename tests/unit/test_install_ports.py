"""Installation contract: explicit isolated ports, ownership checks, no AI calls."""
import json,os,subprocess,sys,tempfile,threading,unittest,shutil
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
P=Path(__file__).resolve().parents[2]
class Health(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(self.server.payload).encode())
class InstallPorts(unittest.TestCase):
 def env(self,**values):
  env={k:v for k,v in os.environ.items() if not k.startswith(('BAIDU_','BUBBLE_WEB_','DEEPSEEK_','OLLAMA_','LOCAL_FILE_CHAT_'))};env.update(values);return env
 def test_explicit_ports(self):
  code='import sys,json;sys.path.insert(0,sys.argv[1]);import backend.settings as settings;print(json.dumps([settings.PORT,settings.LIVE2D_PORT]))'
  r=subprocess.run([sys.executable,'-I','-c',code,str(P)],env=self.env(LOCAL_FILE_CHAT_PORT='18731',LOCAL_FILE_CHAT_LIVE2D_PORT='18732'),capture_output=True,text=True)
  self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout),[18731,18732])
 def test_invalid_port_stops_before_start(self):
  code='import sys;sys.path.insert(0,sys.argv[1]);import backend.settings as settings'
  r=subprocess.run([sys.executable,'-I','-c',code,str(P)],env=self.env(LOCAL_FILE_CHAT_PORT='0'),capture_output=True,text=True)
  self.assertNotEqual(r.returncode,0)
 def test_foreign_resource_server_is_not_reused(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder).resolve()
   for name in ('project_paths.py','launch_desktop.py','start.py'):shutil.copyfile(P/name,root/name)
   shutil.copytree(P/'backend',root/'backend',ignore=shutil.ignore_patterns('__pycache__'))
   (root/'live2d/.runtime').mkdir(parents=True);(root/'live2d/.runtime/prototype.js').write_text('// test-only readiness marker; never executed')
   backend=ThreadingHTTPServer(('127.0.0.1',0),Health);backend.payload={'service':'local-file-chat','project':str(root)}
   resource=ThreadingHTTPServer(('127.0.0.1',0),Health);resource.payload={'service':'local-file-chat-live2d-static','root':str(root/'foreign')}
   for server in (backend,resource):threading.Thread(target=server.serve_forever,daemon=True).start()
   try:
    r=subprocess.run([sys.executable,str(root/'launch_desktop.py')],cwd=root,env=self.env(LOCAL_FILE_CHAT_PORT=str(backend.server_port),LOCAL_FILE_CHAT_LIVE2D_PORT=str(resource.server_port)),capture_output=True,text=True,timeout=10)
    self.assertNotEqual(r.returncode,0);self.assertIn('资源端口属于其他',r.stderr)
    self.assertEqual(resource.fileno()>=0,True)
   finally:
    for server in (backend,resource):server.shutdown();server.server_close()
if __name__=='__main__':unittest.main()
