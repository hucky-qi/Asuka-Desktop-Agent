"""Offline by default; only isolated mock services. Does not accept --real."""
import subprocess,sys
from pathlib import Path
P=Path(__file__).resolve().parent
if sys.argv[1:]:raise SystemExit('No real-model mode here; see docs/TESTING.md for explicit budget policy.')
for folder in ['b-memory-web', 'unified-waiting', 'baidu-real', 'baidu-navigation', 'baidu-auth-offline', 'baidu-budget30', 'accuracy-motion', 'search-balance', 'performance/tmp', 'desktop-entry']:(P/'acceptance'/folder).mkdir(parents=True,exist_ok=True)
for args in [['tools/verify_live2d.py'],['acceptance/test-safety/run.py'],['-m','unittest','test_install_ports'],['acceptance/blackbox-concurrency/run.py']]:
    subprocess.run([sys.executable,*args],cwd=P,check=True)
