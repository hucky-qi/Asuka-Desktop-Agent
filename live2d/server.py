"""Loopback-only static display server. No AI backend, index, preferences or writes."""
import argparse
import json
import urllib.parse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent/'.runtime'


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'service': 'local-file-chat-live2d-static', 'root': str(ROOT)}).encode())
            return
        target = (ROOT/path.lstrip('/')).resolve()
        if not target.is_relative_to(ROOT.resolve()) or (target.is_dir() and path != '/'):
            self.send_error(403)
            return
        super().do_GET()

    def list_directory(self, path):
        self.send_error(403)
        return None

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; connect-src 'self'; object-src 'none'")
        super().end_headers()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--root', type=Path, help='独立预览目录，限 live2d/.previews 内')
    args = parser.parse_args()
    global ROOT
    if args.root:
        root = args.root.resolve()
        if not root.is_relative_to(Path(__file__).resolve().parent/'.previews'):
            parser.error('预览服务只允许 live2d/.previews 内的资源')
        ROOT = root
    if not (ROOT/'prototype.js').exists():
        parser.error('请先执行 prepare.py 与 npm run build')
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), partial(Handler, directory=str(ROOT)))
    except OSError as exc:
        parser.exit(1, f'显示原型端口已占用或不可用；没有结束进程：{exc}\n')
    print(f'Live2D 显示原型：http://127.0.0.1:{args.port}/（纯静态，不访问索引）', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
