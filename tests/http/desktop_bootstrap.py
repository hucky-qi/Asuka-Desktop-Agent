"""Observed subprocess startup with isolated command, locks and Unicode paths. No services."""
from pathlib import Path
import subprocess,sys,tempfile,shutil,json,time,shlex
P=Path(__file__).resolve().parents[2]
def main():
 rows=[]
 with tempfile.TemporaryDirectory(prefix='明日香 startup space ') as tmp:
  root=Path(tmp);(root/'macos').mkdir();(root/'live2d').mkdir();(root/'.runtime').mkdir();entry=root/'macos/desktop_entry.py';shutil.copy2(P/'macos/desktop_entry.py',entry);command=root/'live2d/run-asuka-bubble.command';helper=root/'synthetic_pet.py'
  helper.write_text('''from pathlib import Path
import fcntl,os,time
P=Path(__file__).resolve().parent
with (P/'.desktop-pet.lock').open('a') as f:
 try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise SystemExit(0)
 with (P/'instances').open('a') as out:out.write(str(os.getpid())+'\\n')
 print('Pet hit mask ready: synthetic',flush=True)
 while not (P/'stop').exists():time.sleep(.02)
''')
  command.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(helper))+'\n')
  def run():return subprocess.run([sys.executable,str(entry)],cwd='/',capture_output=True,text=True,timeout=8)
  def state():return json.loads((root/'.runtime/asuka-app-state.json').read_text())
  try:
   r=run();assert r.returncode==0 and state()['status']=='ready',(r.returncode,state());rows.append('fresh_original_entry_unicode_spaces_foreign_cwd')
   r=run();assert r.returncode==0 and state()['status']=='reused' and len((root/'instances').read_text().splitlines())==1;rows.append('existing_component_repeated_entry_one_instance')
  finally:
   (root/'stop').touch()
   for _ in range(100):
    try:
     import fcntl
     with (root/'.desktop-pet.lock').open('a') as gate:fcntl.flock(gate,fcntl.LOCK_EX|fcntl.LOCK_NB)
     break
    except BlockingIOError:time.sleep(.02)
  command.write_text('#!/bin/sh\nexit 17\n');r=run();assert r.returncode!=0 and state()['status']=='error' and '17' in state()['reason'];rows.append('component_failure_clear_status_no_fallback_stack')
  command.unlink();r=run();assert r.returncode!=0 and state()['status']=='error';rows.append('missing_resource_explicit_failure_no_spawn')
  command.write_text('#!/bin/sh\nexit 0\n');command.chmod(0);r=run();assert r.returncode!=0 and state()['status']=='error';rows.append('unreadable_command_clear_failure');command.chmod(0o600)
 print(json.dumps({'passed':rows,'models':0,'notifications':0,'real_service_startups':0},ensure_ascii=False))
if __name__=='__main__':main()
