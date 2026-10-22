import unittest,tempfile,json,threading
from pathlib import Path
from unittest.mock import patch
import baidu_config as c,bubble_web as w
class ConfigTests(unittest.TestCase):
 def test_alias_precedence_quotes_and_no_execution(self):
  with tempfile.TemporaryDirectory(dir=Path('acceptance/baidu-real')) as d:
   root=Path(d);p=root/'.env';f=root/'flags.json'
   p.write_text('BAIDU_API_KEY="test-key" # comment\nUNRELATED_KEY="private"\nBAIDU_TEST_MODE=true\n')
   f.write_text(json.dumps({'BUBBLE_WEB_ENABLED':'true'}))
   env={};self.assertTrue(c.load(p,f,env)['key_loaded']);self.assertEqual(env['BAIDU_APPBUILDER_API_KEY'],'test-key');self.assertNotIn('UNRELATED_KEY',env)
   env={'BAIDU_API_KEY':'process-key'};c.load(p,f,env);self.assertEqual(env['BAIDU_APPBUILDER_API_KEY'],'process-key')
   env={'BAIDU_APPBUILDER_API_KEY':'canonical','BAIDU_API_KEY':'alias'};c.load(p,f,env);self.assertEqual(env['BAIDU_APPBUILDER_API_KEY'],'canonical')
   p.write_text("BAIDU_API_KEY='$(touch forbidden)'\n");env={};c.load(p,f,env);self.assertEqual(env['BAIDU_APPBUILDER_API_KEY'],'$(touch forbidden)');self.assertFalse((root/'forbidden').exists())
 def test_authenticated_request_and_snippet_compatibility(self):
  import os
  class Response:
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def read(self,*a):return b'{"references":[{"title":"Title","url":"https://example.org","snippet":"summary only"}]}'
  requests=[]
  def send(req,timeout):requests.append(req);return Response()
  with patch.dict(os.environ,{'BUBBLE_WEB_ENABLED':'true','BAIDU_FREE_RESOURCES_CONFIRMED':'true','BAIDU_APPBUILDER_API_KEY':'mock-only'}),patch('web_usage.dispatch',side_effect=lambda send,cancel:send()):
   data=w.search('公开问题',threading.Event(),send)
  self.assertEqual(requests[0].get_header('Authorization'),'Bearer mock-only');self.assertEqual(data[0]['snippet'],'summary only')
