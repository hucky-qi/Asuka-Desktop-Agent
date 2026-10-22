import importlib.util,json,tempfile,unittest,fcntl
from pathlib import Path
from unittest.mock import patch,Mock
spec=importlib.util.spec_from_file_location('entry',Path(__file__).resolve().parents[2]/'macos/desktop_entry.py');entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
class EntryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]/'acceptance/desktop-entry');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.runtime=self.root/'runtime';self.runtime.mkdir();self.command=self.root/'run.command';self.command.write_text('mock only')
  for name,value in [('PROJECT',self.root),('RUNTIME',self.runtime),('ENTRY',self.command),('LOG',self.runtime/'log'),('STATE',self.runtime/'state.json')]:
   p=patch.object(entry,name,value);p.start();self.addCleanup(p.stop)
 def test_existing_pet_runs_current_command_without_terminal(self):
  process=Mock();process.pid=123;process.poll.return_value=0
  with patch.object(entry,'pet_running',return_value=True),patch.object(entry.subprocess,'Popen',return_value=process) as spawn:
   self.assertEqual(entry.launch(),0)
  self.assertEqual(spawn.call_args.args[0],['/bin/sh',str(self.command)]);self.assertTrue(spawn.call_args.kwargs['start_new_session']);self.assertEqual(json.loads(entry.STATE.read_text())['status'],'reused')
 def test_ready_uses_only_current_launch_log(self):
  process=Mock();process.pid=123;process.poll.return_value=None
  def spawn(*args,**kw):kw['stdout'].write('Pet hit mask ready: fixture\n');kw['stdout'].flush();return process
  with patch.object(entry,'pet_running',side_effect=[False,True]),patch.object(entry.subprocess,'Popen',side_effect=spawn):self.assertEqual(entry.launch(),0)
  self.assertEqual(json.loads(entry.STATE.read_text())['status'],'ready')
 def test_failure_does_not_print_environment(self):
  process=Mock();process.pid=123;process.poll.return_value=1
  with patch.object(entry,'pet_running',return_value=False),patch.object(entry.subprocess,'Popen',return_value=process):self.assertEqual(entry.launch(),1)
  state=json.loads(entry.STATE.read_text());self.assertEqual(state['status'],'error');self.assertNotIn('Authorization',entry.STATE.read_text())
 def test_concurrent_startup_does_not_spawn_again(self):
  with (self.runtime/'asuka-entry.lock').open('a') as gate:
   fcntl.flock(gate,fcntl.LOCK_EX|fcntl.LOCK_NB)
   with patch.object(entry.subprocess,'Popen') as spawn:self.assertEqual(entry.launch(),0);spawn.assert_not_called()
 def test_timeout_never_terminates_any_process(self):
  process=Mock();process.pid=123;process.poll.return_value=None
  with patch.object(entry,'pet_running',return_value=False),patch.object(entry.subprocess,'Popen',return_value=process):self.assertEqual(entry.launch(timeout=0),1)
  process.terminate.assert_not_called();process.kill.assert_not_called()
 def test_missing_command_fails_without_spawning(self):
  self.command.unlink()
  with patch.object(entry.subprocess,'Popen') as spawn:self.assertEqual(entry.launch(),1);spawn.assert_not_called()

class BuilderSafetyTests(unittest.TestCase):
 def test_existing_foreign_app_is_never_overwritten(self):
  import plistlib
  from macos import create_desktop_app as builder
  with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]/'acceptance/desktop-entry') as root:
   app=Path(root)/'Other.app';(app/'Contents').mkdir(parents=True)
   plist=app/'Contents/Info.plist';original=plistlib.dumps({'CFBundleIdentifier':'another.project'})
   plist.write_bytes(original)
   with patch.object(builder,'compile_helper') as compile:
    with self.assertRaises(ValueError):builder.create(Path(root)/'image.png',app)
    compile.assert_not_called()
   self.assertEqual(plist.read_bytes(),original)
