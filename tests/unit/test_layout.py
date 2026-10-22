"""Layout contracts: cwd independence, exact paths and unchanged entry identity."""
import fcntl,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from project_paths import PROJECT,FRONTEND,MACOS,SWIFT_CACHE

class LayoutTests(unittest.TestCase):
 def test_foreign_cwd_preserves_every_data_root(self):
  with tempfile.TemporaryDirectory() as cwd:
   code="import sys,json;sys.path.insert(0,sys.argv[1]);from backend import settings;from project_paths import PROJECT,FRONTEND,MACOS,SWIFT_CACHE;print(json.dumps([str(x) for x in (PROJECT,FRONTEND,MACOS,SWIFT_CACHE,settings.SEMANTIC_DB,settings.PREFERENCES_PATH)]))"
   r=subprocess.run([sys.executable,'-I','-c',code,str(PROJECT)],cwd=cwd,capture_output=True,text=True,check=True)
   self.assertEqual(json.loads(r.stdout),[str(PROJECT),str(FRONTEND),str(MACOS),str(SWIFT_CACHE),str(PROJECT/'.semantic_index.sqlite3'),str(PROJECT/'.assistant_preferences.json')])
 def test_old_lock_prevents_second_pet_from_foreign_cwd(self):
  with (PROJECT/'.desktop-pet.lock').open('a') as lock,tempfile.TemporaryDirectory() as cwd:
   fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   r=subprocess.run([sys.executable,str(PROJECT/'desktop_pet.py'),'--bubble-chat'],cwd=cwd,capture_output=True,text=True,timeout=8)
   self.assertEqual(r.returncode,0,r.stderr);self.assertIn('已运行',r.stdout)
 def test_synthetic_runtime_uses_packaged_scheduler_without_notifications(self):
  from backend.calendar import reminder_test_service
  service=reminder_test_service.runtime()
  self.assertEqual(Path(service.__file__).resolve(),PROJECT/'backend/calendar/calendar_reminders.py')
  self.assertEqual(service.PATH,PROJECT/'.runtime/reminder-tests/plans.json')
  self.assertIsNone(service.WORKER)
 def test_resource_files_and_stable_entries_present(self):
  for name in ('bubble.html','index.html','input-guard.js','unified-web.js','speech-view.js','shared-reminder.js'):
   self.assertTrue((FRONTEND/name).is_file(),name)
  for name in ('app.py','start.py','launch_desktop.py','desktop_pet.py','build_index.py','run_tests.py'):
   self.assertTrue((PROJECT/name).is_file(),name)
  for name in ('desktop_pet.swift','ocr_vision.swift','AsukaLauncher.swift','InstallSpeechAssets.swift'):
   self.assertTrue((MACOS/name).is_file(),name)

if __name__=='__main__':unittest.main()
