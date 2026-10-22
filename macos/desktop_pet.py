"""Build and launch the small native macOS floating companion."""

import argparse
import shutil
import subprocess
import sys
import fcntl
import urllib.request
import urllib.parse
import json
from pathlib import Path

import backend.settings as settings
from macos.swift_build import compile_helper


def main():
    parser = argparse.ArgumentParser(description="启动本地文件助手的桌面悬浮宠物")
    parser.add_argument('--live2d', action='store_true', help='使用本机 Live2D 显示原型；失败回退蓝色助手')
    parser.add_argument('--live2d-url', help='独立本机模型预览 URL；与 --live2d 一起使用')
    parser.add_argument('--bubble-chat', action='store_true', help='独立本地闲聊气泡（原文件聊天仍可从网页打开）')
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise SystemExit("桌面悬浮宠物需要 macOS")
    if not shutil.which("swiftc"):
        raise SystemExit("未找到 swiftc。请先安装 Xcode Command Line Tools")
    lock = (settings.PROJECT / ".desktop-pet.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("当前项目悬浮助手已运行，未重复启动。若现有窗口使用旧模型，请先右键退出角色，再运行本启动项；启动项不会静默替换正在使用的窗口。")
        return
    source = (settings.MACOS / "desktop_pet.swift")
    binary = (settings.PROJECT / ".desktop_pet")
    if not binary.exists() or binary.stat().st_mtime_ns < source.stat().st_mtime_ns:
        compile_helper(source, binary)
    address = f"http://{settings.HOST}:{settings.PORT}"
    print(f"悬浮助手已启动；单击切换聊天小窗；完整网页 {address}，右键可退出。")
    command = [str(binary), address]
    if args.live2d:
        # Reuse static resources only; never start a second AI/index backend.
        model_address = args.live2d_url or 'http://127.0.0.1:8766'
        try:
            parsed = urllib.parse.urlsplit(model_address)
            if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
                raise ValueError('只允许 http://127.0.0.1:端口 的本机资源地址')
            model_address = model_address.rstrip('/')
            with urllib.request.urlopen(model_address+'/health', timeout=2) as response:
                health = json.load(response)
            expected = str(settings.PROJECT/'live2d/.runtime')
            actual = Path(health.get('root','')).resolve()
            preview_root = settings.PROJECT/'live2d/.previews'
            if health.get('service') != 'local-file-chat-live2d-static' or not (str(actual)==expected or (args.live2d_url and actual.is_relative_to(preview_root))):
                raise ValueError('显示端口属于其他程序')
            command.append(model_address+'/?pet=1')
        except (OSError, ValueError) as exc:
            print(f'Live2D 静态资源不可用，沿用蓝色助手：{exc}')
    if args.bubble_chat:command.append('--bubble-chat')
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
