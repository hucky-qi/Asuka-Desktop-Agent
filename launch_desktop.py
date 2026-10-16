"""Public distribution entry. Optional user-built Live2D; otherwise native blue fallback."""
from pathlib import Path
import sys,subprocess,time,urllib.request
from start import ensure_backend
P=Path(__file__).resolve().parent
if __name__=='__main__':
    ensure_backend()
    args=[sys.executable,str(P/'desktop_pet.py'),'--bubble-chat']
    if (P/'live2d/.runtime/prototype.js').is_file():
        from live2d import server
        address='http://127.0.0.1:8766'
        try:urllib.request.urlopen(address+'/health',timeout=1).close()
        except OSError:
            (P/'.runtime').mkdir(exist_ok=True)
            with (P/'.runtime/live2d.log').open('a') as log:
                subprocess.Popen([sys.executable,str(P/'live2d/server.py')],cwd=P,stdout=log,stderr=log,start_new_session=True)
            for _ in range(30):
                try:urllib.request.urlopen(address+'/health',timeout=.3).close();break
                except OSError:time.sleep(.1)
        args += ['--live2d','--live2d-url',address]
    subprocess.run(args,cwd=P,check=True)
