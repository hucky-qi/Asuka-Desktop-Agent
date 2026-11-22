"""Run every tests/unit module under a network/process guard and verify real data is untouched.

No real model, web, calendar or notification access: non-loopback sockets and DNS, the real
Ollama/backend/Live2D ports and app launchers (`open`, `osascript`) are blocked in-process;
child processes get model/web switches turned off. Usage: python tests/run_all_isolated.py [--report PATH]
"""
import argparse
import hashlib
import json
import os
import sys
import time
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
PROTECTED_PORTS = {11434, 8765, 8766, 8770}
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
LAUNCHERS = {"open", "/usr/bin/open", "osascript", "/usr/bin/osascript"}
REAL_DATA = [".bubble-memory.json", ".assistant_preferences.json", ".bubble-web-usage.json",
             ".deepseek-usage.json", ".runtime/web-acceptance.json",
             ".semantic_index.sqlite3", ".runtime/calendar-reminders/plans.json",
             ".runtime/daily-chat-policy.json", ".runtime/deepseek-capabilities.json"]


class Blocked(RuntimeError):
    pass


CURRENT = {"test": None}


class TrackingResult(unittest.TextTestResult):
    def startTest(self, test):
        CURRENT["test"] = test.id()
        super().startTest(test)


def fingerprint():
    rows = {}
    for name in REAL_DATA:
        path = PROJECT / name
        if not path.exists():
            rows[name] = None
            continue
        stat = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if stat.st_size < 50_000_000 else None
        rows[name] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": digest}
    return rows


def install_guard(blocked):
    def audit(event, args):
        if event == "socket.connect":
            address = args[1]
            if isinstance(address, tuple):
                host, port = address[0], address[1]
                if host not in LOOPBACK or port in PROTECTED_PORTS:
                    blocked.append((f"connect {host}:{port}", CURRENT["test"]))
                    raise Blocked(f"isolated test run blocked connection to {host}:{port}")
        elif event in ("socket.getaddrinfo", "socket.gethostbyname"):
            host = args[0]
            if isinstance(host, bytes):
                host = host.decode()
            if host not in LOOPBACK and host is not None:
                blocked.append((f"dns {host}", CURRENT["test"]))
                raise Blocked(f"isolated test run blocked DNS for {host}")
        elif event == "subprocess.Popen":
            argv = args[1] if isinstance(args[1], (list, tuple)) else [args[0]]
            if argv and str(argv[0]) in LAUNCHERS:
                blocked.append(("launch " + str(argv[0]), CURRENT["test"]))
                raise Blocked("isolated test run blocked launching a real application")
    sys.addaudithook(audit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("pattern", nargs="?", default="test_*.py")
    args = parser.parse_args()
    for key in [k for k in os.environ if k.startswith(("BAIDU_", "DEEPSEEK_", "BUBBLE_WEB_"))]:
        del os.environ[key]
    os.environ.update(DEEPSEEK_ENABLED="false", BUBBLE_WEB_ENABLED="false", MODEL_PROVIDER="ollama",
                      OLLAMA_HOST="http://127.0.0.1:9")
    sys.path.insert(0, str(PROJECT))
    os.chdir(PROJECT)
    for folder in ("search-balance", "desktop-entry", "performance/tmp", "b-memory-web", "unified-waiting",
                   "baidu-real", "baidu-navigation", "baidu-auth-offline", "baidu-budget30", "accuracy-motion"):
        (PROJECT / "acceptance" / folder).mkdir(parents=True, exist_ok=True)
    import backend.calendar.calendar_reminders as calendar_reminders
    import backend.calendar.reminder_test_service as reminder_test_service
    calendar_reminders.start = lambda: None
    reminder_test_service.resume = lambda: None
    # The daily DeepSeek switch lives in .runtime/daily-chat-policy.json, not in the environment,
    # and reservations are written to the real .deepseek-usage.json before sending. Fail closed
    # as "not configured" so no test can reserve against the real ledger; tests that need cloud
    # behaviour patch config()/transport themselves.
    import backend.providers.cloud_access as cloud_access
    disabled = {"daily_chat_enabled": False, "explicit_call_limit": None, "explicit_output_limit": None,
                "calendar_enabled": False, "session_opt_in_enabled": False, "understanding_enabled": False,
                "enabled": False, "api_key": "", "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
                "timeout": 30.0, "call_limit": 5, "input_limit": 16000, "output_limit": 2560}  # production defaults, no key
    cloud_access.config = lambda: dict(disabled)
    before = fingerprint()
    blocked = []
    install_guard(blocked)
    started = time.time()
    suite = unittest.defaultTestLoader.discover(str(PROJECT / "tests/unit"), pattern=args.pattern,
                                                top_level_dir=str(PROJECT))
    result = unittest.TextTestRunner(verbosity=1, resultclass=TrackingResult).run(suite)
    after = fingerprint()
    changed = sorted(name for name in REAL_DATA if before[name] != after[name])
    report = {"ran": result.testsRun, "failures": [t.id() for t, _ in result.failures],
              "errors": [t.id() for t, _ in result.errors], "skipped": [t.id() for t, _ in result.skipped],
              "blocked_attempts": sorted({f"{what} <- {test}" for what, test in blocked}), "real_data_changed": changed,
              "seconds": round(time.time() - started, 1)}
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    ok = result.wasSuccessful() and not changed
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
