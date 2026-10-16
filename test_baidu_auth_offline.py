import io,json,os,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
import baidu_config as c,bubble_web as w,baidu_error_log as log
class AuthOffline(unittest.TestCase):
 def test_real_config_header_consistency_without_network(self):
  # Actual authorized file, but the only transport is an in-process stub.
  file=c.read_env(c.ENV_PATH);target=file.get('BAIDU_APPBUILDER_API_KEY',file.get('BAIDU_API_KEY',''))
  headers=[]
  class Response:
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def read(self,*a):return b'{"references":[{"title":"Local mock","url":"https://example.org","snippet":"mock"}]}'
  def send(req,timeout):
   self.assertTrue(req.get_header('Authorization')=='Bearer '+target,'Authorization与完整目标值不一致')
   self.assertEqual(req.get_header('Content-type'),'application/json')
   self.assertIsNone(req.get_header('X-appbuilder-authorization'))
   self.assertTrue(req.full_url.endswith('/web_search'));headers.append(True);return Response()
  env={};c.load(environ=env)
  self.assertTrue(env['BAIDU_APPBUILDER_API_KEY']==target,'加载值与目标值不一致')
  with patch.dict(os.environ,{**env,'BUBBLE_WEB_ENABLED':'true','BAIDU_FREE_RESOURCES_CONFIRMED':'true'}),patch('web_usage.dispatch',side_effect=lambda transport,event:transport()),patch.object(w,'urlopen',side_effect=send):
   w.search('公开离线测试',threading.Event())
  self.assertEqual(headers,[True])
 def test_full_error_message_request_id_and_key_redaction(self):
  with tempfile.TemporaryDirectory(dir=Path('acceptance/baidu-auth-offline')) as d:
   path=Path(d)/'mock-error.json'
   message='Mock: invalid authorization; echoed key mock-secret. '+('diagnostic '*500)
   payload={'code':216003,'message':message,'requestId':'mock-request-id'}
   error=HTTPError('https://qianfan.baidubce.com/v2/ai_search/web_search',401,'Unauthorized',{},io.BytesIO(json.dumps(payload).encode()))
   with patch.object(log,'PATH',path),patch.dict(os.environ,{'BAIDU_APPBUILDER_API_KEY':'mock-secret','BUBBLE_WEB_ENABLED':'true','BAIDU_FREE_RESOURCES_CONFIRMED':'true'}),patch('web_usage.dispatch',side_effect=lambda transport,event:transport()):
    with self.assertRaises(RuntimeError):w.search('mock',threading.Event(),lambda *a,**k:(_ for _ in ()).throw(error))
   saved=json.loads(path.read_text());self.assertEqual(saved['request_id'],'mock-request-id');self.assertEqual(saved['message'],message.replace('mock-secret','[REDACTED]'));self.assertTrue(saved['credentials_redacted']);self.assertEqual(saved['http_status'],401)
