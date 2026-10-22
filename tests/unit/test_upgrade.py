"""Isolated hard-boundary, cancellation, migration and launcher regressions."""
import json
import sqlite3
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import closing

import backend.app as app
import backend.files.file_tools as file_tools
import backend.files.semantic_search as semantic_search
import backend.settings as settings
import start
from tests.unit.test_app import LocalAssistantTests


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        LocalAssistantTests.setUp(self)
        self.addCleanup(app.SESSIONS.clear)

    def model(self, tool, arguments):
        return patch.object(app, 'chat', side_effect=[
            SimpleNamespace(message=SimpleNamespace(content='', tool_calls=[
                SimpleNamespace(function=SimpleNamespace(name=tool, arguments=arguments))])),
            SimpleNamespace(message=SimpleNamespace(content='完成', tool_calls=[]))])

    def test_image_type_is_hard_even_when_model_omits_parameters(self):
        (self.desktop/'art.png').write_bytes(b'png')
        (self.desktop/'photo.webp').write_bytes(b'webp')
        (self.desktop/'not-image.txt').write_text('text')
        with self.model('find_files', {'keyword':'', 'scope':'all'}):
            _, events = app.answer('帮我找桌面上的图片', app.Session())
        self.assertEqual({x['extension'] for x in events[0]['results']}, {'png','webp'})
        folder=self.desktop/'animation';folder.mkdir();(folder/'scene.jpg').write_bytes(b'jpg')
        with self.model('find_files', {'keyword':'', 'scope':'all'}):
            _, events = app.answer('桌面上的动漫图片', app.Session())
        self.assertEqual([x['name'] for x in events[0]['results']], ['scene.jpg'])

    def test_explicit_folder_never_broadens_and_content_intent_is_literal(self):
        folder=self.desktop/'courses';folder.mkdir();(folder/'topic.txt').write_text('strategyproofness')
        (self.desktop/'outside.txt').write_text('strategyproofness')
        with self.model('semantic_search_files', {'query':'anything','scope':'all'}):
            _,events=app.answer('在桌面“courses”文件夹找包含 strategyproofness 的文件',app.Session())
        self.assertEqual(events[0]['tool'],'search_file_content')
        self.assertEqual([x['name'] for x in events[0]['results']],['topic.txt'])
        with self.model('find_files', {'keyword':'','scope':'all'}):
            reply,events=app.answer('在桌面“missing”文件夹找图片',app.Session())
        self.assertFalse(events)
        self.assertIn('未扩大搜索范围',reply)

    def test_assignment_matches_course_folder_and_abbreviation(self):
        course=self.desktop/'COMP4418';course.mkdir()
        (course/'Assignment 1.pdf').write_bytes(b'pdf')
        (course/'asgn2.pdf').write_bytes(b'pdf')
        (self.desktop/'unrelated.pdf').write_bytes(b'pdf')
        found=file_tools.find_files('4418 assignment','desktop')
        self.assertEqual({x['name'] for x in found['results']},{'Assignment 1.pdf','asgn2.pdf'})

    def test_cancel_commits_completed_file_and_resume_is_incremental(self):
        cancel=threading.Event();database=self.root/'index.sqlite3'
        calls=0
        def vectors(texts):
            from array import array
            return [array('f',[1.,0.]) for _ in texts]
        def progress(data):
            if isinstance(data,dict) and data.get('indexed',0)>=1:cancel.set()
        with patch.object(settings,'SEMANTIC_DB',database), patch.object(semantic_search,'embedding_vectors',side_effect=vectors):
            first=semantic_search.index_files('workspace',progress=progress,cancel_event=cancel)
            self.assertTrue(first['cancelled']);self.assertGreaterEqual(first['indexed_total'],1)
            second=semantic_search.index_files('workspace')
            self.assertGreaterEqual(second['unchanged'],1)
            total=second['indexed_total']
            (self.workspace/'COMP4418_Assignment1 notes.txt').unlink()
            third=semantic_search.index_files('downloads')
            with sqlite3.connect(database) as db:
                self.assertGreaterEqual(db.execute('SELECT count(*) FROM documents').fetchone()[0],total)
            self.assertEqual(third['removed'],0)

    def test_unchanged_legacy_extractor_keeps_existing_vectors(self):
        from array import array
        database=self.root/'legacy.sqlite3'
        with patch.object(settings,'SEMANTIC_DB',database),patch.object(semantic_search,'embedding_vectors',side_effect=lambda texts:[array('f',[1.,0.]) for _ in texts]):
            initial=semantic_search.index_files('workspace')
            with closing(sqlite3.connect(database)) as db:
                db.execute('UPDATE documents SET extractor_version=1');db.commit()
                before=db.execute('SELECT path,position,vector FROM chunks ORDER BY path,position').fetchall()
            with patch.object(semantic_search.file_reader,'document_chunks',side_effect=AssertionError('unchanged old files must not be reparsed')):
                resumed=semantic_search.index_files('workspace')
            self.assertEqual(resumed['indexed'],0)
            self.assertEqual(resumed['unchanged'],initial['indexed_total'])
            with closing(sqlite3.connect(database)) as db:
                self.assertEqual(before,db.execute('SELECT path,position,vector FROM chunks ORDER BY path,position').fetchall())

    def test_inaccessible_scope_and_scan_limit_are_partial(self):
        database=self.root/'index.sqlite3'
        from array import array
        with patch.object(settings,'SEMANTIC_DB',database),patch.object(semantic_search,'embedding_vectors',side_effect=lambda texts:[array('f',[1.,0.]) for _ in texts]):
            limited=semantic_search.index_files('workspace',max_files=1)
            self.assertTrue(limited['partial'])
            missing=semantic_search.index_files('documents')
            self.assertTrue(missing['partial'])
            self.assertTrue(missing['missing_roots'])

    def test_readonly_semantic_connection_does_not_modify_data(self):
        database=self.root/'index.sqlite3'
        with patch.object(settings,'SEMANTIC_DB',database):
            semantic_search.connect().close()
            with closing(semantic_search.connect(read_only=True)) as db:
                with self.assertRaises(sqlite3.OperationalError):db.execute('DELETE FROM documents')

    def test_index_backend_singleton_and_cancel_status(self):
        entered=threading.Event()
        def slow(scope,progress,cancel_event):
            entered.set()
            self.assertTrue(cancel_event.wait(3))
            return {'cancelled':True,'partial':True,'indexed':0,'phase':'cancelled'}
        with patch.object(semantic_search,'index_files',side_effect=slow) as index:
            app.start_index('workspace');self.assertTrue(entered.wait(1))
            app.start_index('workspace');self.assertEqual(index.call_count,1)
            app.cancel_index()
            for _ in range(100):
                if app.INDEX_STATUS['status']=='cancelled':break
                threading.Event().wait(.01)
            self.assertEqual(app.INDEX_STATUS['status'],'cancelled')
            self.assertIn('保留',app.INDEX_STATUS['message'])

    def test_permission_error_is_distinct_from_zero_matches(self):
        def blocked(root,followlinks=False,onerror=None):
            onerror(PermissionError(13,'Denied',str(root)))
            return iter(())
        with patch.object(file_tools.os,'walk',side_effect=blocked):
            found=file_tools.find_files('nothing','workspace')
        self.assertEqual(found['total'],0)
        self.assertTrue(found['partial'])
        self.assertTrue(found['permission_errors'])

    def test_failed_migration_rolls_back_and_preserves_backup(self):
        database=self.root/'legacy.sqlite3'
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE documents(path TEXT PRIMARY KEY,mtime_ns INTEGER,size INTEGER,model TEXT,indexed_at REAL)')
            db.execute('CREATE TABLE chunks(path TEXT,position INTEGER,text TEXT,vector BLOB,model TEXT,PRIMARY KEY(path,position))')
        def failing(db):
            db.execute('ALTER TABLE chunks ADD COLUMN page INTEGER')
            raise RuntimeError('migration interrupted')
        with patch.object(settings,'SEMANTIC_DB',database),patch.object(semantic_search,'_ensure_schema',side_effect=failing):
            with self.assertRaises(RuntimeError):semantic_search.connect()
        with sqlite3.connect(database) as db:
            self.assertNotIn('page',{row[1] for row in db.execute('PRAGMA table_info(chunks)')})
        self.assertEqual(len(list((self.root/'.index-backups').glob('*.sqlite3'))),1)

    def test_launcher_reuses_exact_project_and_rejects_other_service(self):
        context=SimpleNamespace(__enter__=None)
        from unittest.mock import MagicMock
        response=MagicMock();response.__enter__.return_value.read.return_value=json.dumps({'service':'local-file-chat','project':str(settings.PROJECT)}).encode()
        with patch.object(start.urllib.request,'urlopen',return_value=response),patch.object(start.subprocess,'Popen') as spawn:
            self.assertEqual(start.ensure_backend(),f'http://{settings.HOST}:{settings.PORT}');spawn.assert_not_called()
        response.__enter__.return_value.read.return_value=b'{"service":"other"}'
        with patch.object(start.urllib.request,'urlopen',return_value=response),patch.object(start.subprocess,'Popen') as spawn:
            with self.assertRaises(RuntimeError):start.ensure_backend()
            spawn.assert_not_called()


if __name__=='__main__':unittest.main()
