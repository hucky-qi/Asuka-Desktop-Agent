"""Distribution entry. Optional local Live2D; otherwise native blue fallback."""
from pathlib import Path
import sys,subprocess,time,urllib.request,json
import backend.settings as settings
from start import ensure_backend
P=Path(__file__).resolve().parent

def resource_ready(address):
    try:
        with urllib.request.urlopen(address+'/health',timeout=1) as response:data=json.load(response)
    except (OSError,ValueError):return False
    expected=(P/'live2d/.runtime').resolve()
    if data.get('service')!='local-file-chat-live2d-static' or Path(data.get('root','')).resolve()!=expected:
        raise RuntimeError('资源端口属于其他项目或版本；请调整LOCAL_FILE_CHAT_LIVE2D_PORT，未结束任何进程')
    return True

def main():
    ensure_backend()
    args=[sys.executable,str(P/'desktop_pet.py'),'--bubble-chat']
    if (P/'live2d/.runtime/prototype.js').is_file():
        address=f'http://127.0.0.1:{settings.LIVE2D_PORT}'
        if not resource_ready(address):
            (P/'.runtime').mkdir(exist_ok=True)
            with (P/'.runtime/live2d.log').open('a') as log:
                process=subprocess.Popen([sys.executable,str(P/'live2d/server.py'),'--port',str(settings.LIVE2D_PORT)],cwd=P,stdout=log,stderr=log,start_new_session=True)
            for _ in range(30):
                if process.poll() is not None:raise RuntimeError('资源服务启动失败或端口被占用；请检查.runtime/live2d.log，未结束其他进程')
                if resource_ready(address):break
                time.sleep(.1)
            else:raise RuntimeError('资源服务启动等待超时；未加载其他项目资源')
        args += ['--live2d','--live2d-url',address]
    subprocess.run(args,cwd=P,check=True)

if __name__=='__main__':
    try:main()
    except (OSError,RuntimeError,subprocess.CalledProcessError) as error:raise SystemExit(str(error))
