"""Quiet desktop bootstrap: run the existing command, never duplicate services."""
from pathlib import Path
import fcntl,json,os,subprocess,time
PROJECT=Path(__file__).resolve().parents[1]
RUNTIME=PROJECT/'.runtime'
ENTRY=PROJECT/'live2d/run-asuka-bubble.command'
LOG=RUNTIME/'asuka-app.log'
STATE=RUNTIME/'asuka-app-state.json'

def pet_running():
    with (PROJECT/'.desktop-pet.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return True
        return False

def record(status,**values):
    payload={'status':status,'at':time.time(),**values}
    temporary=STATE.with_suffix('.tmp');temporary.write_text(json.dumps(payload,ensure_ascii=False));os.chmod(temporary,0o600);temporary.replace(STATE)

def launch(timeout=210):
    RUNTIME.mkdir(exist_ok=True)
    with (RUNTIME/'asuka-entry.lock').open('a') as gate:
        try:fcntl.flock(gate,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 0  # A startup transaction already owns it.
        if not ENTRY.is_file():
            record('error',reason='缺少项目启动入口 run-asuka-bubble.command');return 1
        reused=pet_running()
        with LOG.open('a') as out:
            os.chmod(LOG,0o600);offset=LOG.stat().st_size
            process=subprocess.Popen(['/bin/sh',str(ENTRY)],cwd=PROJECT,
                env=dict(os.environ,TMPDIR=str(RUNTIME)+'/'),stdin=subprocess.DEVNULL,
                stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
        record('starting',launcher_pid=process.pid,reused_pet=reused)
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            code=process.poll()
            if code is not None:
                if code:
                    record('error',reason=f'项目启动失败（退出码{code}）；查看项目 .runtime/asuka-app.log',launcher_pid=process.pid);return 1
                record('reused' if reused else 'exited',launcher_pid=process.pid,reused_pet=reused);return 0
            # Consume only this launch's own log, never an old success line.
            with LOG.open('rb') as stream:stream.seek(offset);recent=stream.read()[-128000:]
            if not reused and pet_running() and b'Pet hit mask ready:' in recent:
                record('ready',launcher_pid=process.pid,reused_pet=False);return 0
            if not reused and pet_running() and 'Live2D 已回退原蓝色助手'.encode() in recent:
                record('fallback',launcher_pid=process.pid,reused_pet=False);return 0
            time.sleep(.2)
        # Never terminate a startup/helper/service on timeout. It may still be
        # compiling; its existing pet lock prevents a subsequent duplicate.
        record('error',reason='启动等待超时；未终止任何服务。请查看项目启动日志',launcher_pid=process.pid);return 1

if __name__=='__main__':
    try:raise SystemExit(launch())
    except (OSError,ValueError):
        if RUNTIME.exists():record('error',reason='无法启动项目；请检查虚拟环境、目录及启动日志')
        raise SystemExit(1)
