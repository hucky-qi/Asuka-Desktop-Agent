"""Local incremental embedding index and bounded semantic retrieval."""

import math
import re
import sqlite3
import time
import threading
import os
import hashlib
from array import array
from contextlib import closing
from pathlib import Path

from ollama import Client
embed = Client(timeout=60).embed

import file_reader
import file_tools
import settings
import retrieval_cache
import diagnostics
import concept_review
from retrieval_evidence import SemanticEvidence


DB_LOCK = threading.RLock()


def connect(read_only=False):
    if read_only:
        db = sqlite3.connect(settings.SEMANTIC_DB.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
        columns = {row[1] for row in db.execute("PRAGMA table_info(chunks)")}
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "page" in columns and "chunks_fts" in tables:
            return db
        db.close()
        with closing(connect()) as migrated:
            pass
        return sqlite3.connect(settings.SEMANTIC_DB.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    with DB_LOCK:
        return _connect_writable()


def _connect_writable():
    db = sqlite3.connect(settings.SEMANTIC_DB, timeout=30)
    db.execute("PRAGMA foreign_keys=ON")
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    migrate = (bool(tables) and ("chunks_fts" not in tables or
               "page" not in {row[1] for row in db.execute("PRAGMA table_info(chunks)")} or
               "extractor_version" not in {row[1] for row in db.execute("PRAGMA table_info(documents)")}))
    if migrate:
        folder = settings.SEMANTIC_DB.parent / ".index-backups"
        folder.mkdir(exist_ok=True)
        with closing(sqlite3.connect(folder / f"before-migration-{time.time_ns()}.sqlite3")) as backup:
            db.backup(backup)
    try:
        db.execute("BEGIN IMMEDIATE")
        _ensure_schema(db)
        db.commit()
    except Exception:
        db.rollback()
        db.close()
        raise
    return db


def _ensure_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS documents (
        path TEXT PRIMARY KEY, mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL,
        model TEXT NOT NULL, indexed_at REAL NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS chunks (
        path TEXT NOT NULL, position INTEGER NOT NULL, text TEXT NOT NULL,
        vector BLOB NOT NULL, model TEXT NOT NULL,
        PRIMARY KEY(path, position),
        FOREIGN KEY(path) REFERENCES documents(path) ON DELETE CASCADE)""")
    db.execute("CREATE INDEX IF NOT EXISTS chunks_model ON chunks(model)")
    if "page" not in {row[1] for row in db.execute("PRAGMA table_info(chunks)")}:
        db.execute("ALTER TABLE chunks ADD COLUMN page INTEGER")
    if "extractor_version" not in {row[1] for row in db.execute("PRAGMA table_info(documents)")}:
        db.execute("ALTER TABLE documents ADD COLUMN extractor_version INTEGER NOT NULL DEFAULT 1")
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(path UNINDEXED, position UNINDEXED, text)")
    # Upgrade an existing embedding database without forcing the user to rebuild it.
    if db.execute("SELECT count(*) FROM chunks_fts").fetchone()[0] == 0:
        db.execute("INSERT INTO chunks_fts(path,position,text) SELECT path,position,text FROM chunks")


def unit_vector(values):
    vector = array("f", (float(x) for x in values))
    norm = math.sqrt(sum(x * x for x in vector))
    if not vector or not norm:
        raise ValueError("embedding 模型返回了空向量")
    return array("f", (x / norm for x in vector))


def embedding_vectors(texts):
    try:
        response = embed(model=settings.EMBED_MODEL, input=texts)
    except Exception as exc:
        raise RuntimeError(f"无法调用 embedding 模型 {settings.EMBED_MODEL}；请确认 Ollama 已启动并已下载模型") from exc
    vectors = response["embeddings"]
    if len(vectors) != len(texts):
        raise RuntimeError("embedding 返回的向量数与输入数不一致")
    return [unit_vector(v) for v in vectors]


def index_files(scope="all", max_files=None, progress=None, cancel_event=None):
    report = {"seen": 0, "indexed": 0, "new": 0, "updated": 0, "unchanged": 0,
              "skipped": 0, "failed": 0, "indexed_total": 0, "chunks": 0,
              "removed": 0, "partial": False, "truncated_files": 0,
              "model": settings.EMBED_MODEL, "phase": "preparing", "current_file": None}
    old_cancel = getattr(file_tools._current_search, "cancel_event", None)
    if cancel_event is not None:
        file_tools._current_search.cancel_event = cancel_event
    try:
        return _index_files(scope, max_files, progress, report)
    except file_tools.SearchCancelled:
        report.update(cancelled=True, partial=True, phase="cancelled")
        if settings.SEMANTIC_DB.exists():
            with closing(connect(read_only=True)) as db:
                report["indexed_total"] = db.execute("SELECT count(*) FROM documents").fetchone()[0]
        return report
    finally:
        file_tools._current_search.cancel_event = old_cancel


def _index_files(scope="all", max_files=None, progress=None, report=None):
    """Incrementally index supported local files; never index outside allowed roots."""
    file_tools.roots_for(scope)
    maximum = settings.SEMANTIC_MAX_FILES if max_files is None else max(1, int(max_files))
    coverage = {}
    file_tools.check_cancelled()
    with closing(connect()) as db:
        existing = {row[0]: row[1:] for row in db.execute(
            "SELECT path, mtime_ns, size, model, extractor_version FROM documents")}
        # A small batch probe provides an actionable failure before any database edits.
        embedding_vectors(["语义索引连接测试"])
        extensions = [ext.lstrip(".") for ext in settings.CONTENT_EXTENSIONS]
        for path in file_tools.walk_files(scope, extensions, max_files=maximum + 1, stats=coverage):
            report.update(phase="parsing", current_file=str(path))
            if progress:
                progress(dict(report))
            report["seen"] += 1
            if report["seen"] > maximum:
                report["partial"] = True
                report["scan_limit"] = maximum
                break
            try:
                safe = file_tools.checked_file(path)
                stat = safe.stat()
                old = existing.get(str(safe))
                if old is not None and old[:3] == (stat.st_mtime_ns, stat.st_size, settings.EMBED_MODEL):
                    report["unchanged"] += 1
                    continue
                parts = list(file_reader.document_chunks(safe))
                if len(parts) > settings.SEMANTIC_MAX_CHUNKS_PER_FILE:
                    report["truncated_files"] += 1
                parts = parts[:settings.SEMANTIC_MAX_CHUNKS_PER_FILE]
                if not parts:
                    report["skipped"] += 1
                    continue
                records = []
                for start in range(0, len(parts), settings.SEMANTIC_BATCH_SIZE):
                    batch = parts[start:start + settings.SEMANTIC_BATCH_SIZE]
                    report["phase"] = "embedding"
                    if progress:
                        progress(dict(report))
                    vectors = embedding_vectors([part for _, part, _ in batch])
                    file_tools.check_cancelled()
                    records.extend((str(safe), offset, chunk, vector.tobytes(), settings.EMBED_MODEL, page)
                                   for (offset, chunk, page), vector in zip(batch, vectors))
                # Replace an individual document atomically; interrupted indexing keeps older data.
                with db:
                    db.execute("DELETE FROM chunks_fts WHERE path=?", (str(safe),))
                    db.execute("DELETE FROM documents WHERE path=?", (str(safe),))
                    db.execute("INSERT INTO documents(path,mtime_ns,size,model,indexed_at,extractor_version) "
                               "VALUES (?,?,?,?,?,2)",
                               (str(safe), stat.st_mtime_ns, stat.st_size, settings.EMBED_MODEL, time.time()))
                    db.executemany("INSERT INTO chunks(path,position,text,vector,model,page) VALUES (?,?,?,?,?,?)", records)
                    db.executemany("INSERT INTO chunks_fts(path,position,text) VALUES (?,?,?)",
                                   ((path, position, chunk) for path, position, chunk, _, _, _ in records))
                report["indexed"] += 1
                report["updated" if old is not None else "new"] += 1
                report["chunks"] += len(records)
            except (OSError, RuntimeError, ValueError) as exc:
                report["skipped"] += 1
                report["failed"] += 1
                if progress:
                    progress(f"跳过 {path.name}: {exc}")
            if progress and report["seen"] % 10 == 0:
                progress(f"已检查 {report['seen']} 个文件，新索引 {report['indexed']} 个，"
                         f"未变 {report['unchanged']} 个，跳过 {report['skipped']} 个")
        partial = report["partial"]
        report.update(coverage)
        report["partial"] = partial or report["partial"] or report["skipped"] > 0 or bool(report.get("missing_roots")) or report["truncated_files"] > 0
        file_tools.check_cancelled()
        # Prune only proven missing files in the completed scope, after a successful
        # parent listing. Missing volumes, permissions, cancellation and partial
        # scans never imply deletion; inaccessible old records retain their vectors.
        if not report["partial"]:
            roots = [root.resolve() for root in file_tools.roots_for(scope)]
            for path in existing:
                file_tools.check_cancelled()
                candidate = Path(path)
                if not any(candidate.is_relative_to(root) for root in roots):
                    continue
                try:
                    if candidate.parent.resolve(strict=True) != candidate.parent:
                        continue
                    with os.scandir(candidate.parent) as entries:
                        missing = all(entry.name != candidate.name for entry in entries)
                    if missing:
                        with db:
                            db.execute("DELETE FROM chunks_fts WHERE path=?", (path,))
                            db.execute("DELETE FROM documents WHERE path=?", (path,))
                        report["removed"] += 1
                except OSError:
                    pass
        report.update(phase="finished", current_file=None)
        report["indexed_total"] = db.execute(
            "SELECT count(*) FROM documents WHERE model=?", (settings.EMBED_MODEL,)).fetchone()[0]
    return report


def search_semantic(query, scope="all", extensions=None, limit=10, hard_filters=None, required_identifiers=None, identifier_groups=None, review_uncertain=True):
    """Combine semantic, full-text and filename evidence; reject weak guesses."""
    query = str(query or "").strip()
    if not query or len(query) > 500:
        raise ValueError("语义查询需为 1 至 500 个字符")
    roots = file_tools.roots_for(scope)
    allowed_exts = file_tools.normalize_extensions(extensions)
    limit = max(1, min(int(limit), settings.MAX_LIMIT))
    if not settings.SEMANTIC_DB.exists():
        return {"results": [], "total": 0, "needs_index": True,
                "note": "尚未建立语义索引，请先运行 python build_index.py"}
    with closing(connect(read_only=True)) as db:
        count = db.execute("SELECT count(*) FROM chunks WHERE model=?", (settings.EMBED_MODEL,)).fetchone()[0]
        if count == 0:
            return {"results": [], "total": 0, "needs_index": True,
                    "note": "语义索引为空或 embedding 模型已改变，请运行 python build_index.py"}
        query_vector = retrieval_cache.query_vector(query, embedding_vectors)
        file_tools.check_cancelled()
        words = file_tools.tokens(query)
        # Quoted tokens avoid interpreting arbitrary user text as FTS syntax.
        expression = " OR ".join('"' + word.replace('"', '""') + '"'
                                 for word in words if len(word) >= 2)[:800]
        lexical = {}
        if expression:
            for path, position, text, rank in db.execute(
                    "SELECT path,position,text,bm25(chunks_fts) FROM chunks_fts "
                    "WHERE chunks_fts MATCH ? ORDER BY rank LIMIT 2000", (expression,)):
                file_tools.check_cancelled()
                if path not in lexical or rank < lexical[path][0]:
                    lexical[path] = (rank, position, text)
        best = {}
        evidence_windows = {}
        matched_identifiers = {}
        groups = identifier_groups if identifier_groups is not None else (tuple(required_identifiers),) if required_identifiers else ()
        from identifier_intent import satisfied
        valid = {}
        stale = 0
        compared = 0
        rows = retrieval_cache.rows(db)
        vector_started = time.perf_counter()
        for path, offset, chunk, vector, page, mtime_ns, size in rows:
            file_tools.check_cancelled()
            if path not in valid:
                try:
                    file = file_tools.checked_file(path)
                    stat = file.stat()
                    valid[path] = (any(file.is_relative_to(root.resolve()) for root in roots)
                                   and (not allowed_exts or file.suffix.lower() in allowed_exts)
                                   and stat.st_mtime_ns == mtime_ns and stat.st_size == size
                                   and file_tools.stat_matches(stat, hard_filters))
                    if not valid[path] and (stat.st_mtime_ns != mtime_ns or stat.st_size != size):
                        stale += 1
                except (OSError, RuntimeError, ValueError):
                    valid[path] = False
            if not valid[path]:
                continue
            matched_identifiers.setdefault(path,set()).update(value for value in required_identifiers or ()
                if file_tools.search_identifier_evidence(path,chunk,[value]))
            if len(vector) != len(query_vector):
                continue
            score = retrieval_cache.dot(query_vector, vector)
            compared += 1
            windows=evidence_windows.setdefault(path,[])
            windows.append((score,offset,chunk))
            windows.sort(key=lambda item:-item[0])
            del windows[2:]
            if path not in best or score > best[path][0]:
                best[path] = (score, offset, chunk, page)
        trace=getattr(diagnostics.local,"trace",None)
        if trace:trace.timings["vector_retrieval"]=time.perf_counter()-vector_started
        diagnostics.record('vector_comparison',model=settings.EMBED_MODEL,compared_chunks=compared,
                           eligible_files=sum(valid.values()),stale_files=stale)
        peak=max((value[0] for path,value in best.items() if satisfied(matched_identifiers.get(path,()),groups)),default=0)
        # Cross-language concepts may have a modest cosine score. Retrieve
        # weak candidates for bounded local verification instead of silently
        # excluding them or declaring every nearest neighbour relevant.
        vector_floor=max(settings.SEMANTIC_CANDIDATE_MIN_SCORE,peak-settings.SEMANTIC_CANDIDATE_MARGIN)
        uncertain={}
        ordered = []
        for path, (similarity, offset, excerpt, page) in best.items():
            file_tools.check_cancelled()
            if not satisfied(matched_identifiers.get(path,()),groups):continue
            root=next((root.resolve() for root in roots if Path(path).is_relative_to(root.resolve())),None)
            name_score = file_tools.relevance(query, Path(path),root)
            word_match = path in lexical
            if word_match:
                full_text=lexical[path][2].casefold()
                # An OR FTS candidate is not itself proof of a multi-word topic.
                # Check the full retrieved text before constructing its excerpt.
                word_match=bool(words) and all(word in full_text if any('\u4e00'<=c<='\u9fff' for c in word)
                    else re.search(r'(?<![a-z0-9])'+re.escape(word)+r'(?![a-z0-9])',full_text) for word in words if len(word)>=2)
            if similarity < vector_floor and not word_match and name_score < 32:
                continue
            # Lexical matches are evidence, not an invitation to return all weak vectors.
            if word_match:
                _, offset, excerpt = lexical[path]
                if required_identifiers and not any(file_tools.search_identifier_evidence(path,excerpt,[value]) for value in required_identifiers):
                    # An unrelated lexical hit must not replace the valid chunk.
                    offset,excerpt,page=best[path][1:]
                    word_match=False
                page_row = db.execute("SELECT page FROM chunks WHERE path=? AND position=?",
                                      (path, offset)).fetchone()
                page = page_row[0] if page_row else None
            score = max(0.0, similarity) * .70 + min(name_score / 120, 1) * .20
            if word_match:
                score += .25
            if required_identifiers and file_tools.path_identifier_evidence(path,required_identifiers):score += .25
            source = ("名称/路径与内容" if name_score >= 32 and word_match else
                      "内容关键词" if word_match else "名称/路径" if name_score >= 32 else "语义相似")
            if required_identifiers:source+='；标识匹配 '+','.join(sorted(matched_identifiers.get(path,())))
            if not required_identifiers and name_score<32 and similarity<settings.SEMANTIC_TRUSTED_SCORE and (not word_match or len(words)>1):
                texts=list(dict.fromkeys(item[2] for item in evidence_windows[path]))
                if word_match and lexical[path][2] not in texts:texts.append(lexical[path][2])
                uncertain[path]={'path':path,'name':Path(path).name,'text':'\n'.join(texts)}
            ordered.append((path, score, similarity, offset, excerpt, source, page))
        ordered.sort(key=lambda item: (-item[1], -item[2], item[0]))
        review_partial=False
        if uncertain:
            clusters={}
            for item in ordered:
                if item[0] in uncertain:
                    record=uncertain[item[0]]
                    key=hashlib.sha256(record['text'].encode()).digest()
                    clusters.setdefault(key,[]).append(record)
            groups_to_review=list(clusters.values())[:settings.SEMANTIC_REVIEW_LIMIT]
            candidates=[group[0] for group in groups_to_review]
            try:
                accepted=concept_review.review(query,candidates) if review_uncertain else set()
                accepted={record['path'] for group in groups_to_review if group[0]['path'] in accepted for record in group}
            except (OSError,RuntimeError,ValueError):
                file_tools.check_cancelled();accepted=set();review_partial=True
            if len(clusters)>len(candidates) or not review_uncertain:review_partial=True
            ordered=[item for item in ordered if item[0] not in uncertain or item[0] in accepted]
        results = []
        evidence_records = []
        for path, score, similarity, offset, excerpt, source, page in ordered[:limit]:
            # Qualification was checked against the full selected chunk/path,
            # before any display window is chosen. Keep no private text here.
            evidence_records.append(SemanticEvidence(str(Path(path).resolve()),tuple(sorted(matched_identifiers.get(path,())))))
            item = file_tools.file_info(path, score)
            item["score"] = round(score, 3)
            item["similarity"] = round(similarity, 3)
            item["evidence_source"] = source
            snippet_start=0
            for identifier in required_identifiers or ():
                if not file_tools.path_identifier_evidence(path,[identifier]):
                    hit=re.search(r'(?<!\d)'+re.escape(identifier)+r'(?!\d)',excerpt)
                    if hit:snippet_start=max(0,hit.start()-120);break
            item["excerpt"] = re.sub(r"\s+", " ", excerpt[snippet_start:snippet_start+600]).strip()
            item["offset"] = offset
            item["page"] = page
            results.append(item)
        return {"results": results, "total": len(ordered), "needs_index": False,
                "_retrieval_evidence":tuple(evidence_records),
                "stale_files": stale, "model": settings.EMBED_MODEL,
                "indexed_scope_files": sum(valid.values()),
                "partial":review_partial,
                "retrieval": {"mode":"semantic","model":settings.EMBED_MODEL,"compared_chunks":compared,
                              "qualified_files":len(ordered),"vector_only_floor":round(vector_floor,3)},
                "note": ("仅展示已核验候选；部分概念候选超过复核上限或复核未完成，覆盖不完整" if review_partial else
                         "当前范围没有有效索引覆盖；文件名和类型搜索仍可用" if not any(valid.values()) else
                         "没有可靠匹配；可换关键词或检查索引范围" if not ordered else
                         f"已排除 {stale} 个过期文件；索引覆盖可能不完整" if stale else "")}


def relevant_file_chunks(path, question, limit=6):
    """Return indexed passages for one unchanged allowed file; None means use lexical fallback."""
    file = file_tools.checked_file(path)
    if not question.strip() or not settings.SEMANTIC_DB.exists():
        return None
    stat = file.stat()
    with closing(connect(read_only=True)) as db:
        row = db.execute("SELECT mtime_ns, size, model FROM documents WHERE path=?",
                         (str(file),)).fetchone()
        if row != (stat.st_mtime_ns, stat.st_size, settings.EMBED_MODEL):
            return None
        vector = retrieval_cache.query_vector(question, embedding_vectors)
        scored = []
        for offset, text, blob, page in db.execute(
                "SELECT position, text, vector, page FROM chunks WHERE path=? AND model=?",
                (str(file), settings.EMBED_MODEL)):
            chunk_vector = array("f")
            chunk_vector.frombytes(blob)
            if len(chunk_vector) != len(vector):
                continue
            scored.append((sum(a * b for a, b in zip(vector, chunk_vector)), offset, text, page))
        if not scored:
            return None
        scored.sort(reverse=True)
        return [{"offset": offset, "text": text, "page": page, "score": round(score, 4)}
                for score, offset, text, page in sorted(scored[:limit], key=lambda item: item[1])]
