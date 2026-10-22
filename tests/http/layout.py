"""Public paths, durable records and stale confirmation regressions after relocation."""
from pathlib import Path
import tempfile,threading,json,sys,subprocess
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parent))
import run as bb
sys.path.insert(0,str(bb.P/'tests/safety'))
from safety import launch

def main():
 with tempfile.TemporaryDirectory(prefix='layout-http-') as folder:
  root=Path(folder).resolve();ap,dp=bb.port(),bb.port();app,env=bb.prepare(root,ap,dp)
  srv=ThreadingHTTPServer(('127.0.0.1',dp),bb.Dependencies);threading.Thread(target=srv.serve_forever,daemon=True).start();log=(root/'server.log').open('w')
  proc=launch(app,env,dp,ap,log,cwd=root);base=f'http://127.0.0.1:{ap}';rows=[]
  def post(path,**body):
   data=bb.request(base,'/api/bubble/'+path,body)
   return json.loads(data['body']) if data.get('http_error') else data
  def memory(s,action='list',**body):return post('memory',session_id=s,action=action,**body)
  def good(d):assert not d.get('error'),d;return d
  try:
   health=bb.wait_for(lambda:bb.request(base,'/api/health'));assert health['project']==str(app);assert health['database']==str(app/'.semantic_index.sqlite3');rows.append('foreign_cwd_stable_root')
   sid=good(post('session'))['session_id'];second=good(post('session'))['session_id']
   first=good(memory(sid,'record_save',text='合成项目接口完成',date='2026-01-03',explicit=True));record=first['record_results'][0]
   duplicate=good(memory(sid,'record_save',text='合成项目接口完成',date='2026-01-03',explicit=True));assert len(duplicate['record_results'])==1
   edited=good(memory(sid,'record_update',id=record['id'],revision=duplicate['revision'],text='合成项目接口复核完成',date='2026-01-04'))
   assert edited['record_results'][0]['id']==record['id'] and edited['record_results'][0]['version']>record['version'];rows.append('durable_record_dedup_correction')
   good(post('reset',session_id=sid));sid=good(post('session'))['session_id'];rs=good(memory(sid,'record_query',date_from='2026-01-04',topic='复核'))['record_results'];assert len(rs)==1 and rs[0]['id']==record['id']
   proc.terminate();proc.wait(8);proc=launch(app,env,dp,ap,log,cwd=root);bb.wait_for(lambda:bb.request(base,'/api/health'));sid=good(post('session'))['session_id']
   queried=good(memory(sid,'record_query',date_from='2026-01-04',topic='复核'));assert queried['record_results'][0]['id']==record['id'];good(memory(sid,'record_forget',id=record['id'],revision=queried['revision']))
   assert not good(memory(sid,'record_query',topic='复核'))['record_results'];rows.append('reset_restart_recall_forget')
   for operation in ('calendar/confirm','calendar/update/confirm','calendar/delete/confirm'):
    d=post(operation,session_id=sid,token='forged',preview_id='forged',event_id='untrusted');assert d.get('error') or d.get('status')!='success',d
   assert not list((app/'.calendar-create').glob('*.confirmed.json'));rows.append('forged_write_confirmations_rejected')
   r=good(post('reminders',session_id=sid,action='configuration'));assert r['enabled'] is False;assert post('reminders',session_id=sid,action='set',event_id='forged').get('error');rows.append('reminders_remain_off_identity_required')
   assert post('files/qa',session_id=sid,action='authorize',preview_id='forged').get('error');good(post('files/qa',session_id=sid,action='cancel'));rows.append('body_grant_not_revived')
   assert not bb.Dependencies.calls,bb.Dependencies.calls
   print(json.dumps({'passed':rows,'model_or_external_calls':0,'private_calendar':0,'system_notifications':0},ensure_ascii=False))
  except Exception:
   print((root/'server.log').read_text()[-2200:]);raise
  finally:proc.terminate();proc.wait(8);srv.shutdown();log.close()
if __name__=='__main__':main()
