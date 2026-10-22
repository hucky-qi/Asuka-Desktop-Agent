"""Explicit Apple asset preparation. Status never requests download or microphone access."""
import argparse,sys
from pathlib import Path
import subprocess
P=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(P))
from macos.swift_build import compile_helper

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 choice=parser.add_mutually_exclusive_group(required=True)
 choice.add_argument('--status',action='store_true');choice.add_argument('--install',action='store_true')
 args=parser.parse_args()
 if sys.platform!='darwin':parser.exit(1,'此原生语音仅支持macOS，其他平台请使用文字输入。\n')
 binary=P/'.runtime/speech-tools/install-speech-assets';binary.parent.mkdir(parents=True,exist_ok=True)
 source=P/'macos/InstallSpeechAssets.swift'
 if not binary.exists() or source.stat().st_mtime_ns>binary.stat().st_mtime_ns:compile_helper(source,binary)
 return subprocess.run([str(binary),'--install' if args.install else '--status']).returncode
if __name__=='__main__':
 try:raise SystemExit(main())
 except (OSError,subprocess.SubprocessError):raise SystemExit('语音准备失败；检查macOS版本、兼容的Xcode工具和网络。未改文字输入，未请求麦克风。')
