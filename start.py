"""Reuse this project's backend, or start it without touching other processes."""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import backend.settings as settings


def ensure_backend():
    from backend.providers.baidu_config import load
    load()
    address = f"http://{settings.HOST}:{settings.PORT}"
    try:
        with urllib.request.urlopen(address + '/api/health', timeout=2) as response:
            data = json.load(response)
        if data.get('service') == 'local-file-chat' and data.get('project') == str(settings.PROJECT):
            return address
        raise RuntimeError('端口已被其他项目使用，未结束任何进程')
    except (OSError, ValueError):
        pass
    probe = socket.socket()
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind((settings.HOST, settings.PORT))
    except OSError as exc:
        raise RuntimeError(f'端口 {settings.PORT} 已占用且未识别为当前项目；请检查原服务，未结束进程') from exc
    finally:
        probe.close()
    runtime = settings.PROJECT / '.runtime'
    runtime.mkdir(exist_ok=True)
    with (runtime / 'server.log').open('a') as log:
        process = subprocess.Popen([sys.executable, '-u', str(settings.PROJECT / 'app.py')],
                                   cwd=settings.PROJECT, stdout=log, stderr=log,
                                   env=dict(os.environ, TMPDIR=str(runtime) + '/'), start_new_session=True)
    for _ in range(40):
        if process.poll() is not None:
            raise RuntimeError(f'后端启动失败，请查看 {runtime / "server.log"}')
        try:
            with urllib.request.urlopen(address + '/api/health', timeout=.5) as response:
                data = json.load(response)
            if data.get('project') == str(settings.PROJECT) and data.get('service') == 'local-file-chat':
                return address
        except (OSError, ValueError):
            time.sleep(.2)
    raise RuntimeError(f'后端启动等待超时，请查看 {runtime / "server.log"}')


def main():
    parser = argparse.ArgumentParser(description='启动或复用本项目后端和桌面助手')
    parser.add_argument('--web-only', action='store_true', help='只启动或复用网页服务')
    args = parser.parse_args()
    try:
        print('网页：' + ensure_backend(), flush=True)
        if not args.web_only:
            subprocess.run([sys.executable, str(settings.PROJECT / 'desktop_pet.py')], cwd=settings.PROJECT, check=True)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
