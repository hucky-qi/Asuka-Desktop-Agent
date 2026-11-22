"""Security hardening regressions (2026-11-23). Offline: temp files, isolated ports, mocked `open`.

Covers DNS-rebinding Host checks, anti-framing headers, launcher-file refusal, DOCX
zip bombs, bounded sessions/cancels, quiet status polls, python -O integrity checks,
the Live2D static server and the native external-link policy (compiled from source).
"""
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[2]

import backend.settings as settings
# Importing backend.app starts the real reminder scheduler; tests must never deliver
# real notifications when the production backend (which holds scheduler.lock) is down.
import backend.calendar.calendar_reminders as _calendar_reminders
import backend.calendar.reminder_test_service as _reminder_test_service
_calendar_reminders.start = lambda: None
_reminder_test_service.resume = lambda: None
import backend.files.file_tools as file_tools
import backend.files.file_reader as file_reader


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def request(port, method, path, host=None, body=None, origin=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{port}"}
    if origin:
        headers["Origin"] = origin
    if body is not None:
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response, data


class LauncherFileTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        roots = mock.patch.dict(settings.SEARCH_ROOTS, {"workspace": self.root}, clear=True)
        roots.start()
        self.addCleanup(roots.stop)
        self.run_mock = mock.patch.object(file_tools.subprocess, "run").start()
        mock.patch.object(file_tools.sys, "platform", "darwin").start()
        self.addCleanup(mock.patch.stopall)

    def make(self, name, data=b"x", mode=None):
        path = self.root / name
        path.write_bytes(data)
        if mode is not None:
            os.chmod(path, mode)
        return str(path)

    def test_launcher_suffixes_are_never_opened(self):
        for name in ("a.command", "b.py", "c.webloc", "d.pkg", "e.terminal", "f.wflow", "g.appex", "H.COMMAND"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                file_tools.open_file(self.make(name))
        self.run_mock.assert_not_called()

    def test_executable_scripts_and_binaries_are_refused(self):
        script = self.make("run_me", b"#!/bin/sh\necho x", 0o755)
        macho = self.make("tool", b"\xcf\xfa\xed\xfe" + b"\0" * 8, 0o755)
        for path in (script, macho):
            with self.assertRaises(ValueError):
                file_tools.open_file(path)
        self.run_mock.assert_not_called()

    def test_documents_still_open_and_reveal_is_unchanged(self):
        pdf = self.make("ok.pdf", b"%PDF-1.4")
        exec_pdf = self.make("exec.pdf", b"%PDF-1.4", 0o755)  # +x from zips must stay usable
        file_tools.open_file(pdf)
        file_tools.open_file(exec_pdf)
        file_tools.reveal_in_finder(self.make("x.command"))
        self.assertEqual([call.args[0][:2] for call in self.run_mock.call_args_list],
                         [["open", pdf], ["open", exec_pdf], ["open", "-R"]])


class DocxBombTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        roots = mock.patch.dict(settings.SEARCH_ROOTS, {"workspace": self.root}, clear=True)
        roots.start()
        self.addCleanup(roots.stop)

    def test_bomb_refused_before_inflating(self):
        bomb = self.root / "bomb.docx"
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", "<x/>")
            archive.writestr("word/document.xml", b"\0" * (file_reader.MAX_DOCX_UNCOMPRESSED + 1))
        self.assertLess(bomb.stat().st_size, settings.MAX_DOCX_BYTES)
        with mock.patch("docx.Document") as parse, self.assertRaisesRegex(ValueError, "过大"):
            file_reader.extract_sections(str(bomb))
        parse.assert_not_called()

    def test_normal_and_broken_docx_unchanged(self):
        import docx
        good = self.root / "good.docx"
        document = docx.Document()
        document.add_paragraph("正常内容4418")
        document.save(good)
        self.assertIn("4418", file_reader.extract_sections(str(good))[0][1])
        broken = self.root / "broken.docx"
        broken.write_bytes(b"not a zip")
        with self.assertRaisesRegex(ValueError, "损坏"):
            file_reader.extract_sections(str(broken))


class BackendHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.old_port = settings.PORT
        settings.PORT = cls.port
        import backend.app as app
        cls.app = app
        cls.server = app.ThreadingHTTPServer(("127.0.0.1", cls.port), app.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.origin = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        settings.PORT = cls.old_port

    def test_rebinding_host_refused_for_reads_pages_and_reminders(self):
        evil = f"evil.example:{self.port}"
        for path in ("/api/health", "/api/preferences", "/api/duplicates/status", "/api/tasks/status",
                     "/api/index/status", "/", "/bubble", "/speech-view.js"):
            with self.subTest(path=path):
                self.assertEqual(request(self.port, "GET", path, host=evil)[0].status, 403)
        self.assertEqual(request(self.port, "POST", "/api/bubble/reminders", host=evil, body="{}")[0].status, 403)
        self.assertEqual(request(self.port, "POST", "/api/assistant/reminders", host=evil, body="{}")[0].status, 403)
        self.assertEqual(request(self.port, "GET", "/api/health")[0].status, 200)

    def test_pages_deny_framing_and_sniffing(self):
        for path in ("/", "/bubble"):
            response, _ = request(self.port, "GET", path)
            self.assertEqual(response.getheader("X-Frame-Options"), "DENY")
            self.assertIn("frame-ancestors 'none'", response.getheader("Content-Security-Policy") or "")
            self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")

    def test_malformed_bodies_are_client_errors(self):
        for body in ("[1]", '"text"', "[" * 30000 + "]" * 30000):
            with self.subTest(body=body[:10]):
                self.assertEqual(request(self.port, "POST", "/api/preferences", body=body, origin=self.origin)[0].status, 400)
        response, _ = request(self.port, "POST", "/api/assistant/session", origin=self.origin,
                              body=json.dumps({"recover": True, "resume_hint": None}))
        self.assertLess(response.status, 500)

    def test_successful_polls_are_not_logged_but_refusals_are(self):
        logged = []
        with mock.patch.object(self.app.BaseHTTPRequestHandler, "log_message",
                               lambda handler, fmt, *args: logged.append(fmt % args)):
            for _ in range(5):
                request(self.port, "GET", "/api/tasks/status")
            self.assertFalse([line for line in logged if "/api/tasks/status" in line])
            request(self.port, "GET", "/api/tasks/status", host="evil.example")
            request(self.port, "GET", "/missing")
        self.assertTrue(any(" 403 " in line for line in logged))
        self.assertTrue(any(" 404 " in line for line in logged))

    def test_cancel_and_clear_cannot_grow_sessions_or_pending_cancels(self):
        app = self.app
        with mock.patch.dict(app.SESSIONS, clear=True), mock.patch.dict(app.CHAT_CANCELLED, clear=True):
            for index in range(250):
                app.cancel_chat(f"cancel-{index}")
            for index in range(150):
                app.clear_chat(request_id=f"clear-{index}")
            self.assertLessEqual(len(app.SESSIONS), app.MAX_SESSIONS)
            self.assertLessEqual(len(app.CHAT_CANCELLED), app.MAX_PENDING_CANCELS)
            with self.assertRaises(ValueError):
                app.cancel_chat("x", ["unhashable"])
            with self.assertRaises(ValueError):
                app.clear_chat(request_id={"bad": 1})


OPTIMIZED_CHECK = r"""
import json, os, sys, tempfile
from pathlib import Path
from unittest import mock
sys.path.insert(0, sys.argv[1])
import backend.calendar.notification_test_api as api, backend.calendar.reminder_native as rn
import backend.native_context as nc, backend.memory.bubble_memory as bm
results = {}
for module in (api, rn, nc, bm):
    try: module._check(False); results[module.__name__] = "accepted"
    except AssertionError: results[module.__name__] = "refused"
root = Path(tempfile.mkdtemp())
(root/"ready.json").write_text(json.dumps({"bundle": "local.filechat.asuka.launcher", "protocol": 1, "pid": os.getpid()}))
(root/"test-capability.json").write_text(json.dumps({"pid": os.getpid(), "token": "a" * 64}))
with mock.patch.object(api, "ROOT", root), mock.patch.object(api.reminder_native, "status", return_value={"status": "ok"}):
    for label, cap in (("wrong", "b" * 64), ("short", "a" * 10), ("missing", None)):
        try: api.handle({"action": "status"}, cap); results["token_" + label] = "accepted"
        except api.Refused: results["token_" + label] = "refused"
    results["token_genuine"] = api.handle({"action": "status"}, "a" * 64).get("status")
with mock.patch.object(bm, "_bytes", return_value=json.dumps({"version": 2, "sha256": "x", "revision": 0}).encode()):
    try: bm._guard(); results["memory_guard"] = "accepted"
    except ValueError: results["memory_guard"] = "refused"
print(json.dumps({"optimize": sys.flags.optimize, **results}))
"""


class OptimizedModeTests(unittest.TestCase):
    def test_identity_and_integrity_checks_survive_python_O(self):
        output = subprocess.run([sys.executable, "-O", "-c", OPTIMIZED_CHECK, str(PROJECT)], cwd=PROJECT,
                                capture_output=True, text=True, timeout=60, check=True).stdout
        result = json.loads(output.strip().splitlines()[-1])
        self.assertGreaterEqual(result.pop("optimize"), 1)
        self.assertEqual(result.pop("token_genuine"), "ok")
        self.assertEqual(set(result.values()), {"refused"}, result)

    def test_no_assert_left_in_security_modules(self):
        import re
        for name in ("backend/calendar/notification_test_api.py", "backend/calendar/reminder_native.py",
                     "backend/native_context.py", "backend/memory/bubble_memory.py"):
            text = (PROJECT / name).read_text()
            self.assertIsNone(re.search(r"(^\s*|;)assert ", text, re.M), name)


class Live2DServerTests(unittest.TestCase):
    def test_static_server_requires_loopback_host(self):
        sys.path.insert(0, str(PROJECT / "live2d"))
        import importlib
        server_module = importlib.import_module("server")
        root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        (root / "prototype.js").write_text("ok")
        port = free_port()
        from functools import partial
        with mock.patch.object(server_module, "ROOT", root):
            server = server_module.ThreadingHTTPServer(("127.0.0.1", port), partial(server_module.Handler, directory=str(root)))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with mock.patch.object(server_module.Handler, "log_message", lambda *a: None):
                    self.assertEqual(request(port, "GET", "/health", host=f"evil.example:{port}")[0].status, 403)
                    self.assertEqual(request(port, "GET", "/prototype.js", host=f"evil.example:{port}")[0].status, 403)
                    self.assertEqual(request(port, "HEAD", "/prototype.js", host=f"evil.example:{port}")[0].status, 403)
                    response, data = request(port, "GET", "/prototype.js")
                    self.assertEqual((response.status, data), (200, b"ok"))
                    self.assertEqual(request(port, "GET", "/health")[0].status, 200)
            finally:
                server.shutdown()
                server.server_close()


@unittest.skipUnless(shutil.which("swiftc"), "swiftc not installed")
class NativeLinkPolicyTests(unittest.TestCase):
    def test_external_link_and_hex_id_policy(self):
        pet = (PROJECT / "macos/desktop_pet.swift").read_text()
        start = pet.index("    static func isPublicWebURL")
        end = pet.index("    func webView(_ webView: WKWebView, createWebViewWith")
        launcher = (PROJECT / "macos/AsukaLauncher.swift").read_text()
        hex_id = next(line for line in launcher.splitlines() if line.startswith("func isHexID"))
        allowed = ["https://www.baidu.com/s?wd=x", "http://example.com/a", "https://8.8.8.8/", "https://sub.example.co.uk/p#f"]
        refused = ["http://127.0.0.1:8765/", "http://localhost:3000/", "http://[::1]:8080/", "http://[fe80::1]/",
                   "http://192.168.1.1/admin", "http://10.0.0.5/", "http://172.20.1.1/", "http://169.254.169.254/latest",
                   "http://0.0.0.0/", "http://100.64.0.1/", "http://printer.local/", "http://app.localhost/",
                   "http://2130706433/", "https://user:pw@example.com/", "file:///etc/passwd", "javascript:alert(1)",
                   "http://intranet/", "http://300.1.1.1/"]
        source = ("import Foundation\nenum PetDelegate {\n" + pet[start:end] + "}\n" + hex_id + "\n"
                  "let allowed=" + json.dumps(allowed) + "\nlet refused=" + json.dumps(refused) + "\n"
                  "var bad:[String]=[]\n"
                  "for u in allowed where !PetDelegate.isPublicWebURL(URL(string:u)!){bad.append(\"allow \"+u)}\n"
                  "for u in refused where PetDelegate.isPublicWebURL(URL(string:u)!){bad.append(\"refuse \"+u)}\n"
                  "if !isHexID(String(repeating:\"a\",count:32),32){bad.append(\"hex ok\")}\n"
                  "if isHexID(\"../../../../../../../tmp/aaaaaa\",32){bad.append(\"traversal\")}\n"
                  "if isHexID(String(repeating:\"\\u{FF11}\",count:32),32){bad.append(\"fullwidth\")}\n"
                  "print(bad.isEmpty ? \"OK\" : bad.joined(separator:\"|\"))\n")
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "policy.swift").write_text(source)
            subprocess.run(["swiftc", "policy.swift", "-o", "policy"], cwd=folder, check=True,
                           capture_output=True, timeout=300)
            output = subprocess.run([str(Path(folder) / "policy")], capture_output=True, text=True,
                                    timeout=30, check=True).stdout.strip()
        self.assertEqual(output, "OK")


if __name__ == "__main__":
    unittest.main()
