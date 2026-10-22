"""Focused integration tests with temporary local files and a scripted model."""

import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docx import Document
from reportlab.pdfgen import canvas

try:
    import ollama
except ModuleNotFoundError:
    sys.modules["ollama"] = SimpleNamespace(chat=lambda **kwargs: None, embed=lambda **kwargs: None)

import app
import desktop_pet
import file_reader
import file_relationships
import file_tools
import preferences
import settings
import semantic_search


class LocalAssistantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=settings.PROJECT / 'acceptance/search-balance')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        database = patch.object(settings, "SEMANTIC_DB", self.root / "semantic-test.sqlite3")
        database.start()
        self.addCleanup(database.stop)
        self.workspace = self.root / "workspace"
        self.desktop = self.root / "Desktop"
        self.downloads = self.root / "Downloads"
        for directory in (self.workspace, self.desktop, self.downloads):
            directory.mkdir()
        self.roots = patch.object(settings, "SEARCH_ROOTS", {
            "workspace": self.workspace, "desktop": self.desktop,
            "documents": self.root / "Documents", "downloads": self.downloads,
        })
        self.roots.start()
        self.addCleanup(self.roots.stop)
        pdf = canvas.Canvas(str(self.workspace / "COMP4418_Assignment1.pdf"))
        pdf.drawString(20, 760, "COMP4418 Assignment 1")
        pdf.save()
        (self.workspace / "COMP4418_Assignment1 notes.txt").write_text("uni lecture")
        (self.workspace / "data-v1-dl-4644182.arff.gz").write_text("data")
        (self.workspace / "中文 4418 作业.txt").write_text("带空格")
        (self.downloads / "PRD final.md").write_text("requirements")
        for name in (".venv", "venv", "node_modules", "site-packages", "Library"):
            folder = self.workspace / name
            folder.mkdir()
            (folder / "COMP4418_hidden.pdf").write_text("hidden")

    def test_search_quality_scopes_metadata(self):
        data = file_tools.find_files("4418", scope="workspace")
        names = [x["name"] for x in data["results"]]
        self.assertEqual(len(names), 3)
        self.assertNotIn("data-v1-dl-4644182.arff.gz", names)
        self.assertTrue(names[0].startswith("COMP4418"))
        self.assertIn("中文 4418 作业.txt", names)
        self.assertEqual(file_tools.find_files("4418 assignment", extensions=["pdf"])["results"][0]["name"], "COMP4418_Assignment1.pdf")
        self.assertEqual(len(file_tools.find_files("4418", extensions=["pdf"])["results"]), 1)
        self.assertEqual(file_tools.find_files("PRD", scope="downloads")["results"][0]["name"], "PRD final.md")
        self.assertFalse(file_tools.find_files("PRD", scope="workspace")["results"])
        self.assertTrue(file_tools.find_files("中文 4418")["results"])
        self.assertFalse(file_tools.find_files("not-found")["results"])
        self.assertIn("modified_time", data["results"][0])
        self.assertIn("created_time", data["results"][0])
        self.assertIn("size", data["results"][0])
        self.assertIn("score", data["results"][0])
        self.assertEqual(file_tools.find_files("", scope="downloads", extensions=["md"])["total"], 1)
        self.assertEqual(len(file_tools.filter_results(data["results"], extensions=["pdf"])), 1)

    def test_path_validation_and_actions(self):
        file = self.workspace / "a file;$(touch pwned).txt"
        file.write_text("ok")
        outside = self.root / "outside.txt"
        outside.write_text("secret")
        (self.workspace / "escape.txt").symlink_to(outside)
        self.assertTrue(file_tools.is_path_allowed(file))
        self.assertFalse(file_tools.is_path_allowed(outside))
        self.assertFalse(file_tools.is_path_allowed(self.workspace / "escape.txt"))
        with self.assertRaises(ValueError):
            file_reader.read_file(outside)
        with patch.object(file_tools.sys, "platform", "darwin"), patch.object(file_tools.subprocess, "run") as run:
            file_tools.open_file(file)
            file_tools.reveal_in_finder(file)
            self.assertEqual(run.call_args_list[0].args[0], ["open", str(file.resolve())])
            self.assertEqual(run.call_args_list[1].args[0], ["open", "-R", str(file.resolve())])

    def test_date_size_and_fuzzy_filters(self):
        recent = self.workspace / "COMP4418_Assignment1 notes.txt"
        old = self.workspace / "COMP4418_Assignment1.pdf"
        os.utime(old, (1_600_000_000, 1_600_000_000))
        data = file_tools.find_files("4418", modified_after="2021-01-01", sort_by="modified_desc")
        self.assertNotIn(old.name, [x["name"] for x in data["results"]])
        self.assertTrue(file_tools.find_files("comp4418 asignment", scope="workspace")["results"])
        self.assertEqual(file_tools.find_files("", scope="downloads", min_size=1000000)["results"], [])
        self.assertEqual(file_tools.find_files("", scope="downloads", max_size=1000000)["total"], 1)

    def test_read_formats_and_content_search(self):
        (self.workspace / "topic.txt").write_text("strategyproofness: no one benefits from misreporting")
        doc = Document()
        doc.add_paragraph("Question two asks about strategyproofness in TTC")
        doc.save(self.workspace / "assignment.docx")
        pdf = self.workspace / "lecture.pdf"
        writer = canvas.Canvas(str(pdf))
        writer.drawString(20, 760, "strategyproofness lecture")
        writer.save()
        for name in ("topic.txt", "assignment.docx", "lecture.pdf"):
            data = file_reader.read_file(self.workspace / name, "strategyproofness")
            self.assertIn("strategyproofness", " ".join(s["text"] for s in data["snippets"]))
        found = file_reader.search_file_content("strategyproofness", scope="workspace")
        self.assertEqual({x["name"] for x in found["results"]}, {"topic.txt", "assignment.docx", "lecture.pdf"})
        self.assertEqual(file_reader.search_file_content("not-there", "workspace")["results"], [])

    def test_multiturn_search_filter_sort_open_read(self):
        state = app.Session()
        script = [
            ("find_files", {"keyword": "4418", "scope": "workspace"}),
            ("filter_results", {"extensions": ["pdf"]}),
            ("sort_results", {"sort_by": "modified_desc"}),
            ("open_file", {"index": 1}),
            ("find_files", {"keyword": "PRD", "scope": "downloads"}),
            ("read_file", {"index": 1, "question": "讲什么"}),
        ]
        turns = iter(script)
        waiting_for_final = False

        def model(**kwargs):
            nonlocal waiting_for_final
            if waiting_for_final:
                waiting_for_final = False
                return SimpleNamespace(message=SimpleNamespace(content="已完成。", tool_calls=[]))
            name, args = next(turns)
            waiting_for_final = True
            tool = SimpleNamespace(function=SimpleNamespace(name=name, arguments=args))
            return SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tool]))

        with patch.object(app, "chat", side_effect=model), patch.object(file_tools.sys, "platform", "darwin"), patch.object(file_tools.subprocess, "run"):
            _, events = app.answer("找 4418", state)
            self.assertEqual(events[0]["results"][0]["name"], "COMP4418_Assignment1.pdf")
            app.answer("只看 PDF", state)
            self.assertEqual([x["extension"] for x in state.current_filtered_results], ["pdf"])
            app.answer("最近修改的是哪个", state)
            _, events = app.answer("打开它", state)
            self.assertEqual(events[0]["tool"], "open_file")
            app.answer("在 Downloads 找 PRD", state)
            self.assertEqual(state.last_search_query["scope"], "downloads")
            _, events = app.answer("第一个讲了什么", state)
            self.assertEqual(events[0]["tool"], "read_file")
            self.assertEqual(state.current_filtered_results[0]["name"], "PRD final.md")
            self.assertLessEqual(len(state.history), 12)

    def test_local_http_chat_and_action(self):
        calls = 0

        def model(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                tool = SimpleNamespace(function=SimpleNamespace(name="find_files", arguments={"keyword": "4418", "scope": "workspace"}))
                return SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tool]))
            return SimpleNamespace(message=SimpleNamespace(content="找到。", tool_calls=[]))

        server = app.ThreadingHTTPServer((settings.HOST, 0), app.Handler)
        original_port = settings.PORT
        settings.PORT = server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(setattr, settings, "PORT", original_port)
        url = f"http://{settings.HOST}:{settings.PORT}"

        def post(endpoint, data):
            request = urllib.request.Request(url + endpoint, data=json.dumps(data).encode(),
                                             headers={"Content-Type": "application/json", "Origin": url})
            return json.load(urllib.request.urlopen(request))

        with patch.object(app, "chat", side_effect=model), patch.object(file_tools.sys, "platform", "darwin"), patch.object(file_tools.subprocess, "run"):
            self.assertIn("本地文件助手", urllib.request.urlopen(url).read().decode())
            response = post("/api/chat", {"question": "找 4418"})
            self.assertTrue(response["events"][0]["results"])
            path = response["events"][0]["results"][0]["path"]
            self.assertEqual(post("/api/action", {"session_id": response["session_id"], "action": "open_file", "path": path})["file"]["path"], path)
            with self.assertRaises(urllib.error.HTTPError):
                post("/api/action", {"session_id": response["session_id"], "action": "open_file", "path": str(self.root / "outside")})

    def test_semantic_index_incremental_scope_and_retrieval(self):
        db_patch = patch.object(settings, "SEMANTIC_DB", self.root / "index.sqlite3")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        topic = self.workspace / "topic.md"
        topic.write_text("An agent cannot gain by falsely reporting preferences in housing allocation.")
        other = self.downloads / "recipe.txt"
        other.write_text("Boil noodles and add tomato sauce.")
        outside = self.root / "private.txt"
        outside.write_text("An agent cannot gain by falsely reporting preferences.")
        (self.workspace / "link.txt").symlink_to(outside)

        def fake_embed(**kwargs):
            inputs = kwargs["input"]
            vectors = []
            for text in inputs:
                lower = text.casefold()
                vectors.append([1.0, 0.0] if any(term in lower for term in
                               ("preferences", "misreport", "strategyproofness", "housing")) else [0.0, 1.0])
            return {"embeddings": vectors}

        with patch.object(semantic_search, "embed", side_effect=fake_embed) as calls:
            first = semantic_search.index_files("all", max_files=30)
            self.assertGreater(first["indexed"], 0)
            found = semantic_search.search_semantic("strategyproofness", scope="workspace")
            self.assertEqual(found["results"][0]["name"], "topic.md")
            self.assertNotIn("private.txt", [item["name"] for item in found["results"]])
            self.assertFalse(any("hidden" in item["name"] for item in found["results"]))
            self.assertTrue(all(Path(item["path"]).is_relative_to(self.downloads.resolve()) for item in
                                semantic_search.search_semantic("strategyproofness", scope="downloads")["results"]))
            self.assertEqual(semantic_search.search_semantic("strategyproofness", scope="workspace", extensions=["md"])["total"], 1)
            self.assertTrue(semantic_search.relevant_file_chunks(topic, "strategyproofness"))
            before = calls.call_count
            second = semantic_search.index_files("all", max_files=30)
            self.assertEqual(second["indexed"], 0)
            self.assertEqual(calls.call_count, before + 1)  # model connectivity probe only
            topic.write_text(topic.read_text() + " Individual rationality.")
            self.assertNotIn("topic.md", [item["name"] for item in
                              semantic_search.search_semantic("strategyproofness", scope="workspace")["results"]])
            updated = semantic_search.index_files("workspace", max_files=30)
            self.assertEqual(updated["indexed"], 1)
            topic.unlink()
            removed = semantic_search.index_files("workspace", max_files=30)
            self.assertEqual(removed["removed"], 1)
            self.assertNotIn("topic.md", [item["name"] for item in
                              semantic_search.search_semantic("strategyproofness", scope="workspace")["results"]])

    def test_broken_docx_does_not_stop_index_or_content_search(self):
        broken = self.workspace / "broken.docx"
        broken.write_text("This is not a zip container")
        valid = self.workspace / "after-broken.txt"
        valid.write_text("resume experience and education")
        with patch.object(settings, "SEMANTIC_DB", self.root / "bad-docx.sqlite3"), \
             patch.object(semantic_search, "embed", return_value={"embeddings": [[1.0, 0.0]]}):
            result = semantic_search.index_files("workspace", max_files=30)
            self.assertGreaterEqual(result["skipped"], 1)
            with semantic_search.connect() as db:
                self.assertIn(str(valid.resolve()), {
                    row[0] for row in db.execute("SELECT path FROM documents")
                })
        with self.assertRaisesRegex(ValueError, "DOCX"):
            file_reader.read_file(broken)
        matches = file_reader.search_file_content("resume", "workspace")
        self.assertIn("after-broken.txt", [item["name"] for item in matches["results"]])

    def test_blue_ui_and_welcome_example(self):
        self.assertIn("#2459c7", app.HTML)
        self.assertIn("帮我找一下简历", app.HTML)
        self.assertNotIn("帮我找 COMP9321 的 PDF", app.HTML)
        for control in ("scope", "build", "autoIndex", "savedSearches", "duplicates", "form"):
            self.assertIn(f'id="{control}"', app.HTML)
        self.assertIn("session_id:sessionId,scope:scope.value", app.HTML)

    def test_photo_requests_find_png_outside_photo_folder(self):
        album = self.workspace / "个人照片"
        album.mkdir()
        for number in range(15):
            (album / f"portrait-{number:02}.jpg").write_bytes(b"jpeg")
        other = self.desktop / "设计文件"
        other.mkdir()
        (other / "diagram.png").write_bytes(b"png")
        (other / "readme.txt").write_text("not a photo")
        for query in ("照片", "帮我找一下照片", "图片"):
            result = file_tools.find_files(query, limit=10)
            self.assertEqual(result["total"], 16)
            self.assertIn("diagram.png", [item["name"] for item in result["results"][:10]])
        typed = file_tools.find_files("", extensions=["图片"], limit=10)
        self.assertIn("diagram.png", [item["name"] for item in typed["results"][:10]])

        state = app.Session()
        calls = 0
        def model(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                tool = SimpleNamespace(function=SimpleNamespace(
                    name="semantic_search_files", arguments={"query": "照片"}))
                return SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tool]))
            return SimpleNamespace(message=SimpleNamespace(content="好的", tool_calls=[]))
        with patch.object(app, "chat", side_effect=model), \
             patch.object(semantic_search, "search_semantic", side_effect=AssertionError("should use image file types")):
            _, events = app.answer("帮我找一下照片", state)
        self.assertEqual(events[0]["total"], 16)
        self.assertIn("diagram.png", [item["name"] for item in events[0]["results"]])

    def test_desktop_anime_image_query_keeps_subject_and_scope(self):
        folder = self.desktop / "animation"
        folder.mkdir()
        for name in ("呆毛王.png", "明日香.jpg"):
            (folder / name).write_bytes(b"image")
        (self.desktop / "unrelated.png").write_bytes(b"image")
        (self.workspace / "also-unrelated.jpg").write_bytes(b"image")
        direct = file_tools.find_files("动漫", scope="desktop", extensions=["图片"])
        self.assertEqual({x["name"] for x in direct["results"]}, {"呆毛王.png", "明日香.jpg"})
        self.assertTrue(all("同义词" in x["evidence_source"] for x in direct["results"]))
        state = app.Session()
        decisions = iter([
            ("find_files", {"keyword": "动漫", "scope": "all", "extensions": ["图片"]}),
            ("find_files", {"keyword": "", "scope": "all", "extensions": ["图片"]}),
        ])
        def model(**kwargs):
            try:
                name, args = next(decisions)
            except StopIteration:
                return SimpleNamespace(message=SimpleNamespace(content="完成", tool_calls=[]))
            tool = SimpleNamespace(function=SimpleNamespace(name=name, arguments=args))
            return SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tool]))
        with patch.object(app, "chat", side_effect=model):
            _, events = app.answer("查找一下桌面有关动漫的图片", state)
        self.assertEqual([event["scope"] for event in events], ["desktop", "desktop"])
        self.assertEqual([event["total"] for event in events], [2, 2])
        self.assertEqual(state.last_search_query["keyword"], "动漫")
        self.assertEqual(state.last_search_query["scope"], "desktop")
        # A selected UI scope is a hard search boundary even if the model asks for all.
        with patch.object(app, "chat", side_effect=lambda **kwargs: SimpleNamespace(message=SimpleNamespace(
            content="", tool_calls=[SimpleNamespace(function=SimpleNamespace(
                name="find_files", arguments={"keyword": "", "scope": "all", "extensions": ["图片"]}))]))):
            _, constrained = app.answer("找动漫图片", app.Session(), search_scope="desktop")
        self.assertEqual(constrained[0]["scope"], "desktop")
        self.assertEqual(constrained[0]["total"], 2)

    def test_semantic_search_requires_index(self):
        with patch.object(settings, "SEMANTIC_DB", self.root / "missing.sqlite3"):
            result = semantic_search.search_semantic("housing allocation")
            self.assertTrue(result["needs_index"])
            self.assertEqual(result["results"], [])

    def test_missing_embedding_model_message(self):
        with patch.object(semantic_search, "embed", side_effect=Exception("model not found")):
            with self.assertRaisesRegex(RuntimeError, "ollama|Ollama|embedding"):
                semantic_search.embedding_vectors(["test"])

    def test_semantic_tool_updates_chat_results(self):
        db_patch = patch.object(settings, "SEMANTIC_DB", self.root / "tool-index.sqlite3")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        (self.workspace / "housing.md").write_text("Housing exchange where false preferences cannot improve an allocation.")

        def fake_embed(**kwargs):
            return {"embeddings": [[1.0, 0.0] if "housing" in item.casefold() or
                    "strategyproofness" in item.casefold() else [0.0, 1.0]
                    for item in kwargs["input"]]}

        with patch.object(semantic_search, "embed", side_effect=fake_embed):
            semantic_search.index_files("workspace", max_files=30)
            calls = 0

            def model(**kwargs):
                nonlocal calls
                calls += 1
                if calls in (1, 3):
                    name = "semantic_search_files" if calls == 1 else "read_file"
                    args = {"query": "strategyproofness", "scope": "workspace"} if calls == 1 else {"index": 1, "question": "strategyproofness"}
                    tool = SimpleNamespace(function=SimpleNamespace(name=name, arguments=args))
                    return SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tool]))
                if calls == 4:
                    self.assertIn("semantic_index", kwargs["messages"][-1]["content"])
                return SimpleNamespace(message=SimpleNamespace(content="已找到相关内容。", tool_calls=[]))

            state = app.Session()
            with patch.object(app, "chat", side_effect=model):
                _, events = app.answer("语义搜索 strategyproofness", state)
                self.assertEqual(events[0]["tool"], "semantic_search_files")
                self.assertEqual(events[0]["results"][0]["name"], "housing.md")
                _, events = app.answer("第一个主要讲什么", state)
                self.assertEqual(events[0]["tool"], "read_file")

    def test_semantic_index_status_endpoint(self):
        server = app.ThreadingHTTPServer((settings.HOST, 0), app.Handler)
        original_port = settings.PORT
        settings.PORT = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(setattr, settings, "PORT", original_port)
        url = f"http://{settings.HOST}:{settings.PORT}"
        with patch.object(semantic_search, "index_files", return_value={"indexed": 1, "unchanged": 0}):
            request = urllib.request.Request(url + "/api/index", data=b'{"scope":"workspace"}',
                                             headers={"Content-Type": "application/json", "Origin": url})
            json.load(urllib.request.urlopen(request))
            for _ in range(100):
                data = json.load(urllib.request.urlopen(url + "/api/index/status"))
                if data["status"] == "completed":
                    break
                threading.Event().wait(.01)
            self.assertEqual(data["result"]["indexed"], 1)

    def test_complete_index_hybrid_evidence_and_weak_results(self):
        with patch.object(settings, "SEMANTIC_DB", self.root / "complete.sqlite3"), \
             patch.object(settings, "SEMANTIC_MAX_FILES", 1000):
            for number in range(505):
                (self.workspace / f"document_{number:03}.txt").write_text("ordinary code listing")
            wanted = self.workspace / "resume application.txt"
            wanted.write_text("CV interview job application")

            def fake_embed(**kwargs):
                return {"embeddings": [[1.0, 0.0] if "job" in item.casefold()
                        else [0.2, 0.98] for item in kwargs["input"]]}

            with patch.object(semantic_search, "embed", side_effect=fake_embed):
                built = semantic_search.index_files("workspace")
                self.assertFalse(built["partial"])
                self.assertEqual(built["seen"], 509)  # three eligible fixtures plus 506 new files
                self.assertEqual(built["indexed_total"], built["indexed"])
                found = semantic_search.search_semantic("job", "workspace")
                self.assertEqual([item["name"] for item in found["results"]], [wanted.name])
                self.assertIn("内容", found["results"][0]["evidence_source"])
                self.assertIn("job application", found["results"][0]["excerpt"])
                # Reopening an older DB with embeddings but no FTS rows migrates it.
                with semantic_search.connect() as db:
                    db.execute("DELETE FROM chunks_fts")
                    db.commit()
                self.assertEqual(semantic_search.search_semantic("job", "workspace")["total"], 1)

    def test_search_reply_does_not_use_invented_model_overview(self):
        def model(**kwargs):
            if not hasattr(model, "called"):
                model.called = True
                tool = SimpleNamespace(function=SimpleNamespace(name="find_files", arguments={"keyword": "4418"}))
                return SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tool]))
            return SimpleNamespace(message=SimpleNamespace(content="I see an active workspace with 83 projects", tool_calls=[]))

        with patch.object(app, "chat", side_effect=model):
            reply, events = app.answer("找 4418", app.Session())
        self.assertIn("找到 3 个匹配文件", reply)
        self.assertNotIn("83 projects", reply)
        self.assertEqual(len(events[0]["results"]), 3)

    def test_pdf_page_citations_and_preview(self):
        path = self.workspace / "two pages.pdf"
        writer = canvas.Canvas(str(path))
        writer.drawString(30, 750, "Introduction to course")
        writer.showPage()
        writer.drawString(30, 750, "Strategyproofness means truthful reporting")
        writer.save()
        answer = file_reader.read_file(path, "Strategyproofness")
        self.assertTrue(any(item["page"] == 2 and "Strategyproofness" in item["text"]
                            for item in answer["snippets"]))
        self.assertEqual(file_reader.preview_file(path)["file"]["pages"], 2)
        self.assertEqual(file_reader.preview_file(path)["page"], 1)
        self.assertEqual(file_reader.search_file_content("Strategyproofness", "workspace")["results"][0]["page"], 2)

        def fake_embed(**kwargs):
            return {"embeddings": [[1.0, 0.0] if "strategyproofness" in text.casefold()
                    else [0.0, 1.0] for text in kwargs["input"]]}

        with patch.object(settings, "SEMANTIC_DB", self.root / "pdf-index.sqlite3"), \
             patch.object(semantic_search, "embed", side_effect=fake_embed):
            semantic_search.index_files("workspace", max_files=30)
            found = semantic_search.search_semantic("strategyproofness", "workspace")
            match = next(item for item in found["results"] if item["name"] == path.name)
            self.assertEqual(match["page"], 2)
            self.assertTrue(any(item["page"] == 2 and "Strategyproofness" in item["text"]
                                for item in semantic_search.relevant_file_chunks(path, "strategyproofness")))

    def test_ocr_adapter_and_custom_scope_security(self):
        image = self.workspace / "photo.png"
        image.write_bytes(b"fake image bytes")
        with patch.object(file_reader.sys, "platform", "darwin"), \
             patch.object(file_reader, "ocr_command", return_value=["/usr/local/bin/test-ocr"]), \
             patch.object(file_reader.subprocess, "run", return_value=SimpleNamespace(stdout="resume interview\n")) as run:
            self.assertEqual(file_reader.extract_text(image), "resume interview")
            preview = file_reader.preview_file(image)
            self.assertIn("data:image/png;base64", preview["thumbnail"])
            self.assertEqual(run.call_args_list[0].args[0][-1], str(image.resolve()))
        with patch.object(settings, "PREFERENCES_PATH", self.root / "prefs.json"):
            course = self.desktop / "COMP4418"
            course.mkdir()
            key = preferences.add_scope("4418 课程", course)
            self.assertEqual(file_tools.roots_for(key), [course.resolve()])
            resume = self.desktop / "resume"
            resume.mkdir()
            short = preferences.add_scope("简历", "/desktop/resume")
            self.assertEqual(file_tools.roots_for(short), [resume.resolve()])
            self.assertEqual(preferences.allowed_subfolder("desktop/resume"), resume.resolve())
            self.assertEqual(preferences.allowed_subfolder(resume), resume.resolve())
            for unsafe in ("/desktop/../outside", "desktop/.hidden", "/desktop/resume/../../outside"):
                with self.assertRaises(ValueError):
                    preferences.add_scope("bad", unsafe)
            with self.assertRaises(ValueError):
                preferences.add_scope("private", self.root)
            alias = self.desktop / "escape"
            alias.symlink_to(self.root, target_is_directory=True)
            with self.assertRaises(ValueError):
                preferences.add_scope("escape", alias)
            saved = preferences.save_search("最近课件", {"keyword": "4418", "scope": key})
            preferences.set_auto_index(True)
            self.assertTrue(preferences.load()["auto_index"])
            self.assertEqual(preferences.load()["searches"][saved]["query"]["scope"], key)
            self.assertEqual(app.saved_search(app.Session(), saved)["total"], 0)
            preferences.remove_scope(key)
            preferences.remove_scope(short)
            preferences.remove_search(saved)

    def test_folder_form_controls(self):
        for control in ("folderDialog", "folderBase", "folderPath", "folderName", "folderForm"):
            self.assertIn(f'id="{control}"', app.HTML)

    def test_duplicate_hash_and_similar_indexed_files(self):
        first = self.workspace / "lecture one.txt"
        second = self.downloads / "lecture copy.txt"
        close = self.workspace / "lecture related.md"
        first.write_text("housing exchange matching rules")
        second.write_text(first.read_text())
        close.write_text("housing exchange incentive rules")
        groups = file_relationships.find_duplicates("all")["groups"]
        self.assertTrue(any({first.name, second.name} <= {file["name"] for file in group} for group in groups))
        self.assertFalse(any({first.name, close.name} <= {file["name"] for file in group} for group in groups))

        def fake_embed(**kwargs):
            return {"embeddings": [[1.0, 0.0] if "housing" in text.casefold()
                    else [0.0, 1.0] for text in kwargs["input"]]}

        with patch.object(settings, "SEMANTIC_DB", self.root / "related.sqlite3"), \
             patch.object(semantic_search, "embed", side_effect=fake_embed):
            semantic_search.index_files("all", max_files=30)
            results = file_relationships.find_similar(first)["results"]
            self.assertIn(close.name, [file["name"] for file in results])
            self.assertNotIn(first.name, [file["name"] for file in results])

    def test_duplicate_job_singleton_and_stop(self):
        entered = threading.Event()
        finished = threading.Event()

        def slow_scan(scope, cancel_event=None):
            entered.set()
            self.assertTrue(cancel_event.wait(3))
            finished.set()
            raise file_tools.SearchCancelled()

        with patch.object(file_relationships, "find_duplicates", side_effect=slow_scan) as scan:
            try:
                self.assertEqual(app.start_duplicates("workspace")["status"], "running")
                self.assertTrue(entered.wait(2))
                self.assertEqual(app.start_duplicates("workspace")["status"], "running")
                self.assertEqual(scan.call_count, 1)
                app.cancel_duplicates()
                self.assertTrue(finished.wait(2))
                for _ in range(200):
                    with app.DUPLICATE_LOCK:
                        if app.DUPLICATE_JOB["status"] == "cancelled":
                            break
                    threading.Event().wait(.005)
                self.assertEqual(app.DUPLICATE_JOB["status"], "cancelled")
            finally:
                app.cancel_duplicates()

    def test_duplicate_cleanup_requires_selection_confirmation_and_fresh_hashes(self):
        first = self.workspace / "same A.txt"
        second = self.workspace / "same B.txt"
        outside = self.root / "outside.txt"
        for file in (first, second, outside):
            file.write_text("identical")
        group = [[file_tools.file_info(first), file_tools.file_info(second)]]
        with self.assertRaises(ValueError):
            file_relationships.plan_duplicate_cleanup(group, [])
        with self.assertRaises(ValueError):
            file_relationships.plan_duplicate_cleanup(group, [str(outside)])
        with self.assertRaises(ValueError):
            file_relationships.plan_duplicate_cleanup(group, [str(first), str(second)])
        plan = file_relationships.plan_duplicate_cleanup(group, [str(second)])
        with patch.object(file_relationships, "trash_file", side_effect=lambda path: path.unlink()) as trash:
            second.write_text("changed")
            with self.assertRaises(ValueError):
                file_relationships.execute_duplicate_cleanup(plan)
            trash.assert_not_called()
            second.write_text("identical")
            plan = file_relationships.plan_duplicate_cleanup(group, [str(second)])
            result = file_relationships.execute_duplicate_cleanup(plan)
            self.assertEqual(result["moved"], [str(second)])
            self.assertTrue(first.exists())
            self.assertFalse(second.exists())

    def test_duplicate_cleanup_one_use_token_and_clear_chat_state(self):
        first = self.workspace / "duplicate 1.txt"
        second = self.workspace / "duplicate 2.txt"
        first.write_text("same")
        second.write_text("same")
        result = {"groups": [[file_tools.file_info(first), file_tools.file_info(second)]], "total": 1}
        with app.DUPLICATE_LOCK:
            app.DUPLICATE_JOB.update(status="completed", result=result, cancel=None)
        proposal = app.prepare_duplicate_cleanup([str(second)])
        self.assertEqual(proposal["keep"], [str(first)])
        with patch.object(file_relationships, "trash_file", side_effect=lambda path: path.unlink()):
            self.assertEqual(app.confirm_duplicate_cleanup(proposal["token"])["moved"], [str(second)])
        with self.assertRaises(ValueError):
            app.confirm_duplicate_cleanup(proposal["token"])
        key, state = app.session_for(None)
        state.history.append({"role": "user", "content": "old"})
        state.last_search_query = {"keyword": "old"}
        fresh = app.clear_chat(key)
        self.assertEqual(app.SESSIONS[fresh].history, [])
        self.assertEqual(app.SESSIONS[fresh].last_search_query, {})
        self.assertNotIn(key, app.SESSIONS)

    def test_desktop_pet_compiles_and_opens_existing_local_service(self):
        with patch.object(desktop_pet.sys, "platform", "darwin"), \
             patch.object(desktop_pet.fcntl, "flock"), \
             patch.object(desktop_pet.shutil, "which", return_value="/usr/bin/swiftc"), \
             patch.object(Path, "exists", return_value=False), \
             patch.object(desktop_pet, "compile_helper") as compile_helper, \
             patch.object(desktop_pet.subprocess, "run") as run, \
             patch.object(sys, "argv", ["desktop_pet.py"]):
            desktop_pet.main()
        compile_helper.assert_called_once()
        self.assertEqual(run.call_args_list[0].args[0][-1], f"http://{settings.HOST}:{settings.PORT}")

    def test_http_cleanup_requires_plan_and_clear_creates_empty_session(self):
        first = self.workspace / "keep.txt"
        second = self.workspace / "trash.txt"
        first.write_text("identical")
        second.write_text("identical")
        with app.DUPLICATE_LOCK:
            app.DUPLICATE_JOB.update(status="completed", result={
                "groups": [[file_tools.file_info(first), file_tools.file_info(second)]], "total": 1}, cancel=None)
        server = app.ThreadingHTTPServer((settings.HOST, 0), app.Handler)
        old_port = settings.PORT
        settings.PORT = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(setattr, settings, "PORT", old_port)
        url = f"http://{settings.HOST}:{settings.PORT}"

        def post(route, payload):
            req = urllib.request.Request(url + route, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json", "Origin": url})
            return json.load(urllib.request.urlopen(req))

        with self.assertRaises(urllib.error.HTTPError):
            post("/api/duplicates/plan", {"selected": [str(first), str(second)]})
        plan = post("/api/duplicates/plan", {"selected": [str(second)]})
        self.assertTrue(second.exists())
        with patch.object(file_relationships, "trash_file", side_effect=lambda path: path.unlink()):
            result = post("/api/duplicates/confirm", {"token": plan["token"]})
        self.assertEqual(result["moved"], [str(second)])
        self.assertTrue(first.exists())
        self.assertFalse(second.exists())
        with self.assertRaises(urllib.error.HTTPError):
            post("/api/duplicates/confirm", {"token": plan["token"]})
        key, state = app.session_for(None)
        state.history.append({"role": "user", "content": "previous"})
        fresh = post("/api/chat/clear", {"session_id": key})["session_id"]
        self.assertEqual(app.SESSIONS[fresh].history, [])
        self.assertNotIn(key, app.SESSIONS)

    def test_chat_cancel_forks_session_and_next_request_proceeds(self):
        server = app.ThreadingHTTPServer((settings.HOST, 0), app.Handler)
        old_port = settings.PORT
        settings.PORT = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(setattr, settings, "PORT", old_port)
        url = f"http://{settings.HOST}:{settings.PORT}"
        entered, release, first_done = threading.Event(), threading.Event(), threading.Event()
        responses = []

        def post(route, payload):
            req = urllib.request.Request(url + route, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json", "Origin": url})
            return json.load(urllib.request.urlopen(req, timeout=5))

        def model(**kwargs):
            if "第一次" in kwargs["messages"][-1].get("content", ""):
                entered.set()
                release.wait(4)
            return SimpleNamespace(message=SimpleNamespace(content="完成", tool_calls=[]))

        def first():
            try:
                responses.append(post("/api/chat", {"question": "第一次", "scope": "workspace",
                                                    "request_id": "cancel-test"}))
            finally:
                first_done.set()

        with patch.object(app, "chat", side_effect=model):
            thread = threading.Thread(target=first, daemon=True)
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                new_session = post("/api/chat/cancel", {"request_id": "cancel-test"})["session_id"]
                self.assertNotIn(new_session, (None, ""))
                following = post("/api/chat", {"question": "下一项任务", "scope": "workspace",
                                               "session_id": new_session, "request_id": "next-test"})
                self.assertEqual(following["answer"], "完成")
                self.assertEqual(following["session_id"], new_session)
            finally:
                release.set()
                self.assertTrue(first_done.wait(3))
                thread.join(timeout=3)
            self.assertTrue(responses[0]["cancelled"])

    def test_existing_sqlite_index_migration(self):
        db_path = self.root / "old.sqlite3"
        with sqlite3.connect(db_path) as db:
            db.execute("CREATE TABLE documents(path TEXT PRIMARY KEY,mtime_ns INTEGER,size INTEGER,model TEXT,indexed_at REAL)")
            db.execute("CREATE TABLE chunks(path TEXT,position INTEGER,text TEXT,vector BLOB,model TEXT,PRIMARY KEY(path,position))")
            db.execute("INSERT INTO documents VALUES (?,?,?,?,?)", (str(self.workspace / "old.pdf"), 1, 1, settings.EMBED_MODEL, 1))
            db.execute("INSERT INTO chunks VALUES (?,?,?,?,?)", (str(self.workspace / "old.pdf"), 0, "old lecture", b'\x00', settings.EMBED_MODEL))
        with patch.object(settings, "SEMANTIC_DB", db_path), semantic_search.connect() as db:
            self.assertIn("page", [row[1] for row in db.execute("PRAGMA table_info(chunks)")])
            self.assertIn("extractor_version", [row[1] for row in db.execute("PRAGMA table_info(documents)")])
            self.assertEqual(db.execute("SELECT count(*) FROM chunks_fts").fetchone()[0], 1)

    def test_preview_and_saved_search_http_permissions(self):
        with patch.object(settings, "PREFERENCES_PATH", self.root / "http-prefs.json"):
            server = app.ThreadingHTTPServer((settings.HOST, 0), app.Handler)
            old_port = settings.PORT
            settings.PORT = server.server_port
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            self.addCleanup(setattr, settings, "PORT", old_port)
            url = f"http://{settings.HOST}:{settings.PORT}"

            def post(route, payload):
                req = urllib.request.Request(url + route, data=json.dumps(payload).encode(),
                                             headers={"Content-Type": "application/json", "Origin": url})
                return json.load(urllib.request.urlopen(req))

            def model(**kwargs):
                if not hasattr(model, "used"):
                    model.used = True
                    call = SimpleNamespace(function=SimpleNamespace(name="find_files", arguments={"keyword": "4418"}))
                    return SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[call]))
                return SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=[]))

            with patch.object(app, "chat", side_effect=model):
                response = post("/api/chat", {"question": "找4418"})
            session = response["session_id"]
            path = response["events"][0]["results"][0]["path"]
            preview = post("/api/preview", {"session_id": session, "path": path})
            self.assertEqual(preview["file"]["path"], path)
            saved = post("/api/saved-search", {"session_id": session, "operation": "save", "name": "4418"})
            rerun = post("/api/saved-search", {"session_id": session, "operation": "run", "key": saved["key"]})
            self.assertEqual(rerun["events"][0]["total"], 3)
            fresh = post("/api/saved-search", {"operation": "run", "key": saved["key"]})
            self.assertTrue(fresh["session_id"])
            self.assertEqual(fresh["events"][0]["total"], 3)
            self.assertIn(saved["key"], json.load(urllib.request.urlopen(url + "/api/preferences"))["searches"])
            with self.assertRaises(urllib.error.HTTPError):
                post("/api/preview", {"session_id": session, "path": str(self.root / "outside.txt")})
            with self.assertRaises(urllib.error.HTTPError):
                post("/api/preferences", {"operation": "add_scope", "name": "outside", "path": str(self.root)})

    def test_auto_index_only_when_enabled(self):
        with patch.object(preferences, "load", side_effect=[{"auto_index": False}, {"auto_index": True}]), \
             patch.object(app.time, "sleep", side_effect=[None, None, StopIteration]), \
             patch.object(app, "start_index") as start:
            with self.assertRaises(StopIteration):
                app.automatic_index_loop()
            start.assert_called_once_with("all")


if __name__ == "__main__":
    unittest.main()
