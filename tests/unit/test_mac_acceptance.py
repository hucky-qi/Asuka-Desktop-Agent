"""Targeted regressions; all cleanup calls are mocked."""
import tempfile
import subprocess
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import file_relationships
import file_tools
import settings
import swift_build


class MacAcceptanceTests(unittest.TestCase):
    def test_swift_crash_fallback_keeps_project_cache_and_sdk(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'pet.swift'
            with patch.object(swift_build.subprocess, 'check_output', return_value='/sdk\n'), \
                 patch.object(swift_build.subprocess, 'run', side_effect=[
                     subprocess.CalledProcessError(-5, ['swiftc']), None]) as run:
                swift_build.compile_helper(source, Path(directory) / 'pet')
            self.assertEqual(run.call_count, 2)
            self.assertIn('/sdk', run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs['env']['SWIFT_USE_OLD_DRIVER'], '1')
            self.assertTrue(Path(run.call_args.kwargs['env']['TMPDIR']).is_relative_to(settings.PROJECT / '.swift-build'))

    def test_clear_first_inflight_request_discards_its_server_session(self):
        key, state = app.session_for(None)
        state.history.append({'role': 'user', 'content': 'old'})
        cancel = threading.Event()
        with patch.dict(app.CHAT_JOBS, {'first-inflight': {
                'cancel': cancel, 'state': state, 'session_id': key}}):
            new = app.clear_chat(None, 'first-inflight')
        self.assertTrue(cancel.is_set())
        self.assertNotIn(key, app.SESSIONS)
        self.assertEqual(app.SESSIONS[new].history, [])

    def test_cleanup_rejects_changed_keep_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            safe = root / 'safe'
            safe.mkdir()
            keep, remove = safe / 'keep.txt', safe / 'remove.txt'
            keep.write_text('same')
            remove.write_text('same')
            outside = root / 'outside.txt'
            outside.write_text('same')
            with patch.object(settings, 'SEARCH_ROOTS', {'workspace': safe}), \
                 patch.object(file_relationships, 'trash_file') as trash:
                groups = [[file_tools.file_info(keep), file_tools.file_info(remove)]]
                plan = file_relationships.plan_duplicate_cleanup(groups, [str(remove)])
                keep.write_text('changed')
                with self.assertRaises(ValueError):
                    file_relationships.execute_duplicate_cleanup(plan)
                trash.assert_not_called()
                keep.write_text('same')
                plan = file_relationships.plan_duplicate_cleanup(groups, [str(remove)])
                remove.unlink()
                remove.symlink_to(outside)
                with self.assertRaises(ValueError):
                    file_relationships.execute_duplicate_cleanup(plan)
                trash.assert_not_called()


if __name__ == '__main__':
    unittest.main()
