from pathlib import Path
"""Local HTTP chat, session state, and Ollama tool dispatch."""

import json
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import model_providers
import diagnostics
chat = model_providers.configured_provider().chat

import file_reader
import file_relationships
import file_tools
import preferences
import semantic_search
import re
import search_retrieval
import settings
import search_intent
import bubble_chat
import task_status


@dataclass
class Session:
    history: list = field(default_factory=list)
    last_search_query: dict = field(default_factory=dict)
    last_search_results: list = field(default_factory=list)
    current_filtered_results: list = field(default_factory=list)
    filters: dict = field(default_factory=dict)
    search_coverage: dict = field(default_factory=dict)
    selected_file: str | None = None
    display_limit: int = settings.DEFAULT_LIMIT
    touched: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)


SESSIONS = {}
SESSIONS_LOCK = threading.Lock()
INDEX_STATUS = {"status": "idle", "message": "尚未更新语义索引"}
INDEX_LOCK = threading.Lock()
INDEX_CANCEL = None
CHAT_JOBS = {}
CHAT_JOBS_LOCK = threading.Lock()
CHAT_CANCELLED = {}
DUPLICATE_LOCK = threading.Lock()
DUPLICATE_JOB = {"status": "idle", "message": "尚未检查重复文件", "result": None, "cancel": None}
DUPLICATE_PLAN = {}


def start_duplicates(scope):
    file_tools.roots_for(scope)
    with DUPLICATE_LOCK:
        if DUPLICATE_JOB["status"] == "running":
            return {key: value for key, value in DUPLICATE_JOB.items() if key != "cancel"}
        cancel = threading.Event()
        DUPLICATE_JOB.update(status="running", message="正在核对同大小文件的 SHA-256…",
                             result=None, cancel=cancel)
        DUPLICATE_PLAN.clear()

    def worker():
        try:
            result = file_relationships.find_duplicates(scope, cancel_event=cancel)
            status, message = "completed", "重复文件检查完成"
        except file_tools.SearchCancelled:
            result, status, message = None, "cancelled", "已终止重复文件检查"
        except Exception as exc:
            result, status, message = None, "error", f"检查失败：{exc}"
        with DUPLICATE_LOCK:
            DUPLICATE_JOB.update(status=status, message=message, result=result, cancel=None)

    threading.Thread(target=worker, daemon=True).start()
    return {"status": "running", "message": "正在核对同大小文件的 SHA-256…", "result": None}


def cancel_duplicates():
    with DUPLICATE_LOCK:
        if DUPLICATE_JOB["status"] == "running":
            DUPLICATE_JOB["cancel"].set()
        return {key: value for key, value in DUPLICATE_JOB.items() if key != "cancel"}


def prepare_duplicate_cleanup(selected_paths):
    with DUPLICATE_LOCK:
        if DUPLICATE_JOB["status"] != "completed" or not DUPLICATE_JOB["result"]:
            raise ValueError("请先完成重复文件检查")
        groups = DUPLICATE_JOB["result"]["groups"]
        plan = file_relationships.plan_duplicate_cleanup(groups, selected_paths)
        token = uuid.uuid4().hex
        DUPLICATE_PLAN.clear()
        DUPLICATE_PLAN.update(token=token, plan=plan, expires=time.time() + 120)
        return {"token": token, "keep": [item["path"] for group in plan for item in group["keep"]],
                "delete": [item["path"] for group in plan for item in group["delete"]]}


def confirm_duplicate_cleanup(token):
    with DUPLICATE_LOCK:
        if not isinstance(token, str) or token != DUPLICATE_PLAN.get("token") or time.time() > DUPLICATE_PLAN.get("expires", 0):
            raise ValueError("确认已过期，请重新选择文件")
        plan = DUPLICATE_PLAN["plan"]
        DUPLICATE_PLAN.clear()  # A confirmation token is single use.
        result = file_relationships.execute_duplicate_cleanup(plan)
        DUPLICATE_JOB.update(status="idle", message="文件已移动，请重新检查重复文件", result=None, cancel=None)
        return result


def fork_session(state):
    """Give the next request a separate lock while an old Ollama call winds down."""
    return Session(history=list(state.history), last_search_query=dict(state.last_search_query),
                   last_search_results=list(state.last_search_results),
                   current_filtered_results=list(state.current_filtered_results),
                   filters=dict(state.filters), search_coverage=dict(state.search_coverage), selected_file=state.selected_file,
                   display_limit=state.display_limit)


def cancel_chat(request_id, session_id=None):
    task_status.finish_request(request_id,"file")
    with CHAT_JOBS_LOCK:
        job = CHAT_JOBS.get(request_id)
        if job is None:
            # The stop request can arrive before the chat worker registers.
            if session_id is not None and session_id not in SESSIONS:
                raise ValueError("未知会话")
            CHAT_CANCELLED[request_id] = time.time()
            old = SESSIONS[session_id] if session_id else Session()
        else:
            job["cancel"].set()
            old = job["state"]
        with SESSIONS_LOCK:
            key = uuid.uuid4().hex
            SESSIONS[key] = fork_session(old)
        return key


def clear_chat(session_id=None, request_id=None):
    """Discard this browser conversation without touching saved searches or the index."""
    if session_id is not None and (not isinstance(session_id, str) or len(session_id) > 64):
        raise ValueError("会话标识不正确")
    if session_id is not None and session_id not in SESSIONS:
        raise ValueError("未知会话")
    if request_id is not None:
        task_status.finish_request(request_id,"file")
        if not isinstance(request_id, str) or len(request_id) > 64:
            raise ValueError("任务标识不正确")
        with CHAT_JOBS_LOCK:
            job = CHAT_JOBS.get(request_id)
            if job:
                job["cancel"].set()
                session_id = session_id or job.get("session_id")
            else:
                CHAT_CANCELLED[request_id] = time.time()
    with SESSIONS_LOCK:
        if session_id is not None:
            SESSIONS.pop(session_id, None)
        key = uuid.uuid4().hex
        SESSIONS[key] = Session()
    return key


def start_index(scope):
    global INDEX_CANCEL
    file_tools.roots_for(scope)
    status_path = settings.SEMANTIC_DB.parent / ".index-status.json"
    with INDEX_LOCK:
        if INDEX_STATUS["status"] == "running":
            return dict(INDEX_STATUS)
        INDEX_CANCEL = threading.Event()
        cancel = INDEX_CANCEL
        INDEX_STATUS.update(status="running", message="正在检查文件…", result=None)
        INDEX_STATUS.update(started_at=time.time(), finished_at=None, phase="preparing")

    def report(message):
        with INDEX_LOCK:
            if isinstance(message, dict):
                INDEX_STATUS.update(result=message, phase=message.get("phase"), current_file=message.get("current_file"))
            else:
                INDEX_STATUS["message"] = message

    def worker():
        timer = threading.Timer(30 * 60, cancel.set)
        timer.daemon = True
        timer.start()
        try:
            with INDEX_LOCK:
                snapshot = dict(INDEX_STATUS)
            target = status_path
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
            temporary.replace(target)
        except OSError:
            pass
        try:
            result = semantic_search.index_files(scope=scope, progress=report, cancel_event=cancel)
            with INDEX_LOCK:
                status = "cancelled" if result.get("cancelled") else "partial" if result.get("partial") else "completed"
                message = {"cancelled": "索引已中断，已提交数据保留，可继续增量更新", "partial": "索引部分完成，请查看权限、失败及解析/扫描限制", "completed": "索引更新完成"}[status]
                INDEX_STATUS.update(status=status, message=message, result=result, phase=result.get("phase"), current_file=None, finished_at=time.time())
        except Exception as exc:
            with INDEX_LOCK:
                INDEX_STATUS.update(status="error", message=f"索引失败：{exc}", finished_at=time.time())
        finally:
            timer.cancel()
            with INDEX_LOCK:
                snapshot = dict(INDEX_STATUS)
            try:
                target = status_path
                temporary = target.with_suffix(".tmp")
                temporary.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
                temporary.replace(target)
            except OSError:
                pass

    threading.Thread(target=worker, daemon=True).start()
    with INDEX_LOCK:
        return dict(INDEX_STATUS)


def cancel_index():
    with INDEX_LOCK:
        if INDEX_STATUS["status"] == "running" and INDEX_CANCEL:
            INDEX_CANCEL.set()
            INDEX_STATUS["message"] = "正在取消，等待当前有超时限制的模型/解析请求返回…"
        return dict(INDEX_STATUS)


def index_status():
    import sqlite3
    with INDEX_LOCK:
        data = dict(INDEX_STATUS)
    if data["status"] == "idle":
        try:
            saved = json.loads((settings.SEMANTIC_DB.parent / ".index-status.json").read_text())
            if isinstance(saved, dict):
                if saved.get("status") == "running":
                    saved.update(status="interrupted", message="上次更新未结束；已提交数据保留，可增量继续")
                data.update(saved)
        except (OSError, ValueError):
            pass
    data.update(path=str(settings.SEMANTIC_DB.resolve()), model=settings.EMBED_MODEL,
                indexed_files=0, indexed_chunks=0, scan_limit=settings.SEMANTIC_MAX_FILES,
                parsing_limits={"pdf_pages": settings.MAX_PDF_PAGES, "chunks_per_file": settings.SEMANTIC_MAX_CHUNKS_PER_FILE})
    if settings.SEMANTIC_DB.exists():
        try:
            with closing(sqlite3.connect(settings.SEMANTIC_DB.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
                data["indexed_files"] = db.execute("SELECT count(*) FROM documents").fetchone()[0]
                data["indexed_chunks"] = db.execute("SELECT count(*) FROM chunks").fetchone()[0]
                data["last_indexed_at"] = db.execute("SELECT max(indexed_at) FROM documents").fetchone()[0]
        except sqlite3.Error:
            data.update(indexed_files=None, indexed_chunks=None, coverage_note="索引暂时无法读取")
    else:
        data["coverage_note"] = "尚无索引；文件名和类型搜索仍可用"
    return data


def automatic_index_loop():
    """The user explicitly enables this policy; no Ollama calls at startup by default."""
    while True:
        time.sleep(settings.AUTO_INDEX_INTERVAL)
        if preferences.load()["auto_index"]:
            try:
                start_index("all")
            except (OSError, RuntimeError, ValueError):
                pass


def saved_search(state, key):
    item = preferences.load()["searches"].get(key)
    if item is None:
        raise ValueError("保存的搜索不存在")
    query = item["query"]
    if "semantic_query" in query:
        data = semantic_search.search_semantic(query["semantic_query"], query.get("scope", "all"),
                                               query.get("extensions"),required_identifiers=query.get("required_identifiers"),identifier_groups=query.get("identifier_groups"))
    elif "similar_to" in query:
        data = file_relationships.find_similar(query["similar_to"])
    elif "content_keyword" in query:
        data = file_reader.search_file_content(query["content_keyword"], query.get("scope", "all"),
                                               query.get("extensions"),required_identifiers=query.get("required_identifiers"),identifier_groups=query.get("identifier_groups"))
    else:
        data = search_retrieval.find_files(**query)
    data.pop('_retrieval_evidence', None)
    state.last_search_query = query
    state.last_search_results = data["results"]
    state.current_filtered_results = list(data["results"])
    state.filters.clear()
    state.selected_file = None
    state.display_limit = settings.DEFAULT_LIMIT
    state.search_coverage={k:data[k] for k in ("partial","truncated","note") if k in data}
    return search_snapshot(state, {"tool":"saved_search", **data})


def session_for(key):
    with SESSIONS_LOCK:
        now = time.time()
        for old in list(SESSIONS):
            if now - SESSIONS[old].touched > 3600:
                del SESSIONS[old]
        if key not in SESSIONS:
            if len(SESSIONS) >= 100:
                del SESSIONS[min(SESSIONS, key=lambda item: SESSIONS[item].touched)]
            key = uuid.uuid4().hex
            SESSIONS[key] = Session()
        SESSIONS[key].touched = now
        return key, SESSIONS[key]


def visible(state):
    return state.current_filtered_results[:state.display_limit]


def search_snapshot(state, event, fresh=True):
    """Publish counts and conditions from the one actionable session snapshot.

    Source totals can exceed retained rows. They are candidates, never the
    number of cards or the number available to subsequent ordinal actions.
    """
    if 'results' not in event:
        return event
    if event.get('needs_index'):
        event.update(results=[], total=0, counts={'candidates':0,'verified':0,'displayed':0})
        return event
    if fresh:
        state.search_coverage['candidate_total'] = max(int(event.get('total', 0)), len(state.last_search_results))
    verified = len(state.current_filtered_results)
    event.update(results=visible(state), total=verified,
                 counts={'candidates':state.search_coverage.get('candidate_total', len(state.last_search_results)),
                         'verified':verified, 'displayed':len(visible(state))},
                 search_state={'query':dict(state.last_search_query), 'filters':dict(state.filters),
                               'display_limit':state.display_limit})
    event.setdefault('scope',state.last_search_query.get('scope','all'))
    return event


def select(state, index=0):
    if not state.current_filtered_results:
        raise ValueError("还没有可选择的搜索结果")
    if index in (None, 0, "0") and state.selected_file:
        return file_tools.checked_file(state.selected_file)
    position = int(index) - 1
    if position < 0 or position >= len(visible(state)):
        raise ValueError("序号超出当前展示的搜索结果")
    path = visible(state)[position]["path"]
    state.selected_file = path
    return file_tools.checked_file(path)


def remember(state, question, response):
    state.history.extend([{"role":"user","content":question[:4000]}, {"role":"assistant","content":response[:4000]}])
    state.history=state.history[-12:]


def listing_reply(event):
    if event.get("needs_index"):
        return "当前没有可用语义索引；文件名和类型搜索仍可用。"
    text = (f"找到 {event['total']} 个匹配文件（当前已验证），展示 {len(event.get('results',[]))} 个及匹配依据。"
            if event.get("total") else "没有找到可靠匹配的文件。")
    if event.get("note"):text += "\n" + event["note"]
    elif event.get("partial") or event.get("truncated"):text += "\n结果覆盖或展示不完整，请查看扫描范围。"
    return text


def model_call(stage, **kwargs):
    with diagnostics.span(stage, "generating"):
        try:
            response=chat(cancel=getattr(file_tools._current_search,'cancel_event',None),**kwargs)
        except __import__('model_dispatch').Cancelled:
            raise file_tools.SearchCancelled() from None
    trace=getattr(diagnostics.local,'trace',None)
    if trace:
        trace.model_calls.append({"stage":stage, **{k:getattr(response,k,0)/1e9 for k in ('load_duration','prompt_eval_duration','eval_duration') if isinstance(getattr(response,k,None),(int,float))},
                                  'thinking_chars':len(getattr(response.message,'thinking','') or '')})
    return response


def answer(question, state, search_scope="all", fast=False):
    """Keep structured state in Python; LLM chooses tools and phrases answers."""
    file_tools.check_cancelled()
    tool_errors = []
    with diagnostics.span("condition_extraction"):
        constraints = search_intent.Constraints(question, search_scope)
    def validate_event(event):
        search_snapshot(state, event, fresh=event.get('tool') not in ('filter_results','sort_results'))
        constraints.check_event(event)
    events = diagnostics.Events(validate_event)
    file_tools.roots_for(search_scope)  # Validate UI scope before any model/tool calls.
    explicit = [key for key, labels in {
        "desktop": ("桌面", "desktop"), "documents": ("文稿", "文档", "documents"),
        "downloads": ("下载", "downloads"), "workspace": ("workspace",),
    }.items() if any(label in question.casefold() for label in labels)]
    requested_scope = search_scope if search_scope != "all" else explicit[0] if len(explicit) == 1 else None
    image_request = any(word in question.casefold() for word in ("图片", "照片", "相片", "图像", "png", "jpg", "jpeg", "heic"))
    topic_hint = next((word for word in settings.SEARCH_SYNONYMS if word in question.casefold()), None)

    def scoped(value, directory=None):
        return constraints.scoped(value, directory)

    def filename_intent(keyword, extensions):
        return constraints.filename(keyword, extensions)

    def find_files(keyword: str = "", scope: str = "all", extensions: list[str] | None = None,
                   modified_after: str | None = None, modified_before: str | None = None,
                   min_size: int | None = None, max_size: int | None = None,
                   sort_by: str = "relevance", limit: int = 10, directory: str | None = None) -> dict:
        """Search local filenames with optional scope, types, ISO dates, byte sizes, sorting and limit.

        Args:
            keyword: Filename keywords; empty for recent files or other filters.
            scope: workspace, desktop, documents, downloads, or all.
            extensions: File suffixes, for example ["pdf", "docx"], or ["图片"].
            modified_after: ISO date/time lower bound, for example 2026-09-23.
            modified_before: ISO date/time upper bound.
            min_size: Minimum file size in bytes.
            max_size: Maximum file size in bytes.
            sort_by: relevance, modified_desc, modified_asc, or size_desc.
            limit: Number of results to display, at most 20.
        """
        keyword, extensions = filename_intent(keyword, extensions)
        if constraints.literal:
            return search_file_content(constraints.literal, scope, extensions, limit, directory)
        params = dict(keyword=keyword, scope=scoped(scope, directory), extensions=extensions,
                      modified_after=modified_after, modified_before=modified_before,
                      min_size=min_size, max_size=max_size, sort_by=sort_by, limit=limit)
        params.update(constraints.metadata({k:params[k] for k in ('modified_after','modified_before','min_size','max_size')}))
        if constraints.identifiers():params.update(required_identifiers=constraints.identifiers(),identifier_groups=constraints.identifier_groups())
        if constraints.media_topic():params['topic']=constraints.media_topic()
        with diagnostics.span("file_retrieval", "searching"):
            data = search_retrieval.find_files(**params)
        constraints.accept_semantic_evidence(data.pop('_retrieval_evidence', ()))
        constraints.check_event({"scope":params["scope"],"results":data["results"]})
        state.last_search_query = params
        state.search_coverage={k:data[k] for k in ("partial","truncated","note","indexed_scope_files","scanned_files") if k in data}
        state.last_search_results = data["results"]
        state.current_filtered_results = list(data["results"])
        state.filters.clear()
        state.selected_file = None
        state.display_limit = data["limit"]
        result = {**data, "results": visible(state)}
        if constraints.media and topic_hint:
            result["note"] = "依据文件名或目录名查找，未识别图片或视频画面"
        events.append({"tool": "find_files", "scope": params["scope"], **result})
        return result

    def filter_results(extensions: list[str] | None = None, modified_after: str | None = None,
                       modified_before: str | None = None, min_size: int | None = None,
                       max_size: int | None = None, sort_by: str | None = None) -> dict:
        """Filter the previous search result set; do not search for the filter words as filenames.

        Args:
            extensions: Types to keep, e.g. ["pdf"].
            modified_after: ISO date/time inclusive lower bound.
            modified_before: ISO date/time inclusive upper bound.
            min_size: Minimum bytes.
            max_size: Maximum bytes.
            sort_by: relevance, modified_desc, modified_asc, or size_desc.
        """
        if not state.last_search_query:
            raise ValueError("请先搜索文件")
        _, extensions = constraints.filename("", extensions)
        updates = constraints.metadata({key: value for key, value in dict(
            extensions=extensions, modified_after=modified_after, modified_before=modified_before,
            min_size=min_size, max_size=max_size, sort_by=sort_by).items() if value is not None})
        state.filters.update(updates)
        state.current_filtered_results = file_tools.filter_results(state.last_search_results, **state.filters)
        state.selected_file = None
        result = {**state.search_coverage, "results": visible(state), "total": len(state.current_filtered_results)}
        if result.get("truncated"):result["note"]="仅筛选已保存的最多500条结果；原搜索展示/保存被截断，覆盖不完整。"
        events.append({"tool": "filter_results", **result})
        return result

    def sort_results(sort_by: str = "modified_desc") -> dict:
        """Sort current search results by modified_desc, modified_asc, size_desc, or relevance."""
        if not state.last_search_query:
            raise ValueError("请先搜索文件")
        state.filters["sort_by"] = sort_by
        state.current_filtered_results = file_tools.filter_results(state.last_search_results, **state.filters)
        state.selected_file = None
        result = {**state.search_coverage, "results": visible(state), "total": len(state.current_filtered_results)}
        if result.get("truncated"):result["note"]="仅筛选已保存的最多500条结果；原搜索展示/保存被截断，覆盖不完整。"
        events.append({"tool": "sort_results", **result})
        return result

    def get_file_info(index: int = 0) -> dict:
        """Get metadata of the numbered current result; index 0 means selected file."""
        return file_tools.file_info(select(state, index))

    def open_file(index: int = 0) -> dict:
        """Open a current search result in its default macOS app; index starts at 1, 0 means selected file."""
        result = file_tools.open_file(select(state, index))
        events.append({"tool": "open_file", "file": result})
        return result

    def reveal_in_finder(index: int = 0) -> dict:
        """Show and select a current search result in Finder; index starts at 1, 0 means selected file."""
        result = file_tools.reveal_in_finder(select(state, index))
        events.append({"tool": "reveal_in_finder", "file": result})
        return result

    def read_file(index: int = 0, question: str = "") -> dict:
        """Read relevant, bounded excerpts of a numbered current file for answering a question.

        Args:
            index: Current visible result number, starting at 1; 0 means selected file.
            question: The user's question to select relevant text chunks.
        """
        path = select(state, index)
        try:
            snippets = semantic_search.relevant_file_chunks(path, question) if question else None
        except Exception:
            snippets = None  # Optional semantic retrieval must not disable ordinary reading.
        data = ({"file": file_tools.file_info(path), "snippets": snippets, "retrieval": "semantic_index"}
                if snippets else file_reader.read_file(path, question))
        events.append({"tool": "read_file", "file": data["file"]})
        return data

    def search_file_content(keyword: str, scope: str = "all",
                            extensions: list[str] | None = None, limit: int = 10, directory: str | None = None) -> dict:
        """Search literal text inside supported files, even when filenames do not match.

        Args:
            keyword: Literal words or phrase to find inside files.
            scope: workspace, desktop, documents, downloads, or all.
            extensions: Optional types; TXT, code, CSV, PDF, DOCX supported.
            limit: Number of results, at most 20.
        """
        scope = scoped(scope, directory)
        keyword = constraints.literal or keyword
        _, extensions = constraints.filename("", extensions)
        def partial_cards(data):
            constraints.accept_semantic_evidence(data.pop('_retrieval_evidence', ()))
            constraints.check_event({"scope":scope,"results":data["results"]})
            state.last_search_query = {"content_keyword":keyword,"scope":scope,"extensions":extensions,"required_identifiers":constraints.identifiers(),"identifier_groups":constraints.identifier_groups()}
            state.search_coverage={k:data[k] for k in ("partial","truncated","note","indexed_scope_files","scanned_files") if k in data}
            state.last_search_results = data["results"]
            state.current_filtered_results = list(data["results"])
            state.filters.clear();state.selected_file=None
            state.display_limit=max(1,min(int(limit),settings.MAX_LIMIT))
            events.append({"tool":"search_file_content","scope":scope,"preliminary":True,**data})
        with diagnostics.span("content_retrieval", "searching"):
            data = file_reader.search_file_content(keyword, scope, extensions, limit, progress=partial_cards if fast else None, fallback_seconds=15 if fast else None, hard_filters=constraints.metadata(), **({"required_identifiers":constraints.identifiers(),"identifier_groups":constraints.identifier_groups()} if constraints.identifiers() else {}))
        constraints.accept_semantic_evidence(data.pop("_retrieval_evidence", ()))
        constraints.check_event({"scope":scope,"results":data["results"]})
        state.last_search_query = {"content_keyword": keyword, "scope": scope, "extensions": extensions,"required_identifiers":constraints.identifiers(),"identifier_groups":constraints.identifier_groups()}
        state.search_coverage={k:data[k] for k in ("partial","truncated","note","indexed_scope_files","scanned_files") if k in data}
        state.last_search_results = data["results"]
        state.current_filtered_results = list(data["results"])
        state.filters.clear()
        state.selected_file = None
        state.display_limit = max(1, min(int(limit), settings.MAX_LIMIT))
        result = {**data, "results": visible(state)}
        events.append({"tool": "search_file_content", "scope": scope, **result})
        return result

    def find_similar_files(index: int, limit: int = 10) -> dict:
        """Find other indexed files with similar content to the numbered result."""
        data = file_relationships.find_similar(select(state, index), limit)
        state.last_search_query = {"similar_to": state.selected_file}
        state.search_coverage={k:data[k] for k in ("partial","truncated","note","indexed_scope_files","scanned_files") if k in data}
        state.last_search_results = data["results"]
        state.current_filtered_results = list(data["results"])
        state.filters.clear()
        state.selected_file = None
        state.display_limit = min(int(limit), settings.MAX_LIMIT)
        events.append({"tool": "find_similar_files", **data})
        return data

    def find_duplicate_files(scope: str = "all", limit: int = 10) -> dict:
        """Find byte-identical files under an allowed scope using SHA-256 hashes."""
        data = file_relationships.find_duplicates(scope, limit)
        events.append({"tool": "find_duplicate_files", **data})
        return data

    def semantic_search_files(query: str, scope: str = "all",
                              extensions: list[str] | None = None, limit: int = 10, directory: str | None = None) -> dict:
        """Find files by meaning, including related wording absent from filenames and contents.

        Args:
            query: A concept or question to search semantically, e.g. unfair housing allocation.
            scope: workspace, desktop, documents, downloads, or all.
            extensions: Optional file types such as ["pdf", "docx"].
            limit: Number of results to display, at most 20.
        """
        if constraints.literal:
            return search_file_content(constraints.literal, scope, extensions, limit, directory)
        scope = scoped(scope, directory)
        query, extensions = filename_intent(query, extensions)
        if constraints.media:
            query, extensions = filename_intent(query, extensions)
            category = True
        else:
            query = constraints.semantic(query)
            category = file_tools.category_query(query)
        # A photo without readable text is absent from the text embedding index.
        # A broad image request must scan filenames/extensions, including PNGs.
        with diagnostics.span("semantic_retrieval", "searching"):
            data = (file_tools.find_files(query, scope, extensions, limit=limit, topic=constraints.media_topic(), **constraints.metadata(), **({"required_identifiers":constraints.identifiers(),"identifier_groups":constraints.identifier_groups()} if constraints.identifiers() else {}))
                    if category else semantic_search.search_semantic(query, scope, extensions, limit, **({"hard_filters":constraints.metadata()} if constraints.metadata() else {}), **({"required_identifiers":constraints.identifiers(),"identifier_groups":constraints.identifier_groups()} if constraints.identifiers() else {})))
        if category:
            if constraints.identifiers():
                constraints.accept_semantic_evidence(tuple(search_intent.SemanticEvidence(str(Path(item["path"]).resolve()),tuple(value for value in constraints.identifiers() if file_tools.path_identifier_evidence(item["path"],[value]))) for item in data["results"]))
            data = {**data, "needs_index": False, "note": "依据文件名/目录名和类型查找；未识别图片或视频画面内容"}
        if not data.get("needs_index"):
            constraints.accept_semantic_evidence(data.get('_retrieval_evidence', ()))
            constraints.check_event({"scope":scope,"results":data["results"]})
            state.last_search_query = ({"keyword": query, "scope": scope, "extensions": extensions}
                                       if category else {"semantic_query": query, "scope": scope, "extensions": extensions, "required_identifiers":constraints.identifiers(),"identifier_groups":constraints.identifier_groups()})
            state.search_coverage={k:data[k] for k in ("partial","truncated","note","indexed_scope_files","scanned_files") if k in data}
            state.last_search_results = data["results"]
            state.current_filtered_results = list(data["results"])
            state.filters.clear()
            state.selected_file = None
            state.display_limit = max(1, min(int(limit), settings.MAX_LIMIT))
        result = {"results": data["results"], "total": data["total"],
                  "needs_index": data["needs_index"], "note": data.get("note", ""),
                  "stale_files": data.get("stale_files", 0),
                  **{k:data[k] for k in ("partial","truncated","indexed_scope_files","scanned") if k in data}}
        events.append({"tool": "semantic_search_files", "scope": scope, **result})
        return result

    tools = {function.__name__: function for function in (
        find_files, filter_results, sort_results, get_file_info, open_file,
        reveal_in_finder, read_file, search_file_content, semantic_search_files,
        find_similar_files, find_duplicate_files,
    )}
    def execute_tool(name, args):
        trace=getattr(diagnostics.local,"trace",None)
        if trace:trace.tool_calls += 1
        with diagnostics.span("tool_execution", "searching"):
            return tools[name](**args)
    plan = search_intent.fast_plan(question, bool(state.last_search_query)) if fast else None
    if plan:
        name, args = plan
        try:
            diagnostics.stage("searching")
            execute_tool(name,args)
            latest = events[-1] if events else {}
            if name=="find_files" and not latest.get("note"):latest["note"]="按文件名/目录名及类型查找；如需按内容概念检索，可明确说‘语义搜索…’。"
            response_text = listing_reply(latest) if "results" in latest else "操作已完成。"
            remember(state, question, response_text)
            return response_text, events
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            file_tools.check_cancelled()
            trace=getattr(diagnostics.local,"trace",None)
            if trace:trace.had_error=True
            response_text = "未执行成功：" + str(exc)
            remember(state, question, response_text)
            return response_text, events
    # First discovery requests need only a small intent schema, not every
    # file-operation tool and a second model call for a discarded listing.
    # Followups/file QA retain the full structured-state/tool path below.
    first_response = None
    if fast and not state.last_search_query and not re.search(r'时间|日期|大小|大于|小于|最近|之前|之后|before|after|size|modified|larger|smaller|读取|打开|定位|讲什么|写了什么|总结',question,re.I) and re.search(r'找|查找|搜索|寻找|\bsearch\b|\bfind\b',question,re.I):
        intent_schema={"type":"object","properties":{
            "method":{"type":"string","enum":["find_files","semantic_search_files","search_file_content"]},
            "query":{"type":"string"},"directory":{"type":["string","null"]},
            "extensions":{"type":["array","null"],"items":{"type":"string"}},
            "scope":{"type":"string","enum":["all","desktop","documents","downloads","workspace"]}},
            "required":["method","query","directory","extensions","scope"]}
        first_response=model_call('condition_extraction',model=settings.MODEL,think=False,format=intent_schema,
            options={"temperature":0,"num_predict":160},messages=[
                {"role":"system","content":"提取本地文件检索意图为JSON。明确文件名/编号用find_files，内容精确包含词句用search_file_content，主题、用途、同义概念用semantic_search_files。query只写用户原文的核心概念，去掉查找/讨论/介绍等任务动词；不翻译、不改写、不泛化，保留概念完整措辞及逻辑关系。目录/文件类型分开。不得新增或放宽条件。"},
                {"role":"user","content":question}])
        file_tools.check_cancelled()
        try:
            intent=json.loads(first_response.message.content)
            name=intent['method'];query=intent['query']
            if name not in ('find_files','semantic_search_files','search_file_content') or not isinstance(query,str):raise ValueError('无效检索意图')
            args={k:intent[k] for k in ('scope','extensions','directory') if intent.get(k) is not None}
            args['query' if name=='semantic_search_files' else 'keyword']=query
        except (ValueError,KeyError,TypeError):
            # Compatibility with older/non-JSON model responses: reuse any
            # existing tool call instead of requesting the intent twice.
            if not getattr(first_response.message,'tool_calls',None):first_response=None
        else:
            try:
                execute_tool(name,args)
                response_text=listing_reply(events[-1]);remember(state,question,response_text)
                return response_text,events
            except (OSError,RuntimeError,ValueError,TypeError) as exc:
                file_tools.check_cancelled()
                trace=getattr(diagnostics.local,'trace',None)
                if trace:trace.had_error=True
                response_text='未执行成功：'+str(exc);remember(state,question,response_text)
                return response_text,events
    custom_labels = {key: item["name"] for key, item in preferences.custom_scopes().items()}
    system = ("你是本机文件助手。今天的本地日期是 " + datetime.now().astimezone().date().isoformat() + "。"
              "自定义目录名称及 scope 编号：" + json.dumps(custom_labels, ensure_ascii=False)[:1000] + "。"
              "按文件名或文件属性查找用 find_files；查找内容包含精确词句用 search_file_content；"
              "找照片、图片、相片等泛文件类型时，用 find_files(keyword='', extensions=['图片'])；不要把‘照片’作为文件名搜索。"
              "按主题/用途找文件（如求职材料、课程资料），或要求语义、近义概念搜索时用 semantic_search_files。"
              "找内容相似的文件用 find_similar_files；找完全相同的副本用 find_duplicate_files。"
              "语义索引缺失时告诉用户点击页面的‘更新语义索引’或运行 python build_index.py，不要编造结果。"
              "‘只看 PDF’、‘最近一周的’是对上次结果调用 filter_results；"
              "‘最近修改的是哪个’调用 sort_results(modified_desc)，据其结果回答。"
              "打开、定位、读取或问答文件，必须调用对应工具并传当前显示序号（从 1 开始）；‘它’可沿用已选文件。"
              "指定文件夹时传 directory（完整路径或允许目录内的子目录名），找不到不得扩大范围。"
              "工具处理路径和排序，你只提取参数并描述真实返回的结果。不能编造文件或内容。"
              "针对 PDF 回答时标明工具片段中的页码，没有页码时不猜测。"
              "结果可能被截断；如没有抽取到相关段落，请说明局限。用简短中文回答。")
    messages = [{"role": "system", "content": system}, *state.history[-6:],
                {"role": "user", "content": question}]
    summary = {"last_search_query": state.last_search_query,
               "current_result_count": len(state.current_filtered_results),
               "visible_files": [{"index": i, "name": f["name"]} for i, f in enumerate(visible(state), 1)],
               "selected_file": state.selected_file}
    messages.insert(1, {"role": "system", "content": "当前结构化状态：" + json.dumps(summary, ensure_ascii=False)[:2000]})
    response_text = "模型没有返回文字。"
    for _ in range(4):
        file_tools.check_cancelled()
        response = first_response or model_call("condition_extraction" if not events else "final_generation", model=settings.MODEL, messages=messages, tools=list(tools.values()), think=False, options={"num_predict": 512})
        first_response=None
        file_tools.check_cancelled()
        messages.append(response.message)
        if not response.message.tool_calls:
            response_text = response.message.content or response_text
            break
        for call in response.message.tool_calls:
            file_tools.check_cancelled()
            name = call.function.name
            args = call.function.arguments or {}
            try:
                result = execute_tool(name,args) if name in tools and isinstance(args, dict) else {"error": "未知工具或参数"}
            except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
                result = {"error": str(exc)}
            if result.get("error"):
                tool_errors.append(result["error"])
            tool_message = {"role":"tool", "tool_name":name, "content":json.dumps(result,ensure_ascii=False,default=str)[:5000]}
            if getattr(call,"id",None):tool_message["tool_call_id"]=call.id
            messages.append(tool_message)
    else:
        file_tools.check_cancelled()
        final = model_call("final_generation", model=settings.MODEL, messages=messages, think=False, options={"num_predict": 512})
        file_tools.check_cancelled()
        response_text = final.message.content or response_text
    # File listings must reflect tool results exactly, even when a small model hallucinates.
    search_events = [event for event in events if event["tool"] in
                     ("find_files", "search_file_content", "semantic_search_files", "filter_results", "sort_results", "find_similar_files")]
    if search_events and not any(event["tool"] == "read_file" for event in events):
        latest = search_events[-1]
        response_text = listing_reply(latest)
    if tool_errors and not search_events:
        trace=getattr(diagnostics.local,"trace",None)
        if trace:trace.had_error=True
        response_text = "未执行成功：" + "；".join(dict.fromkeys(tool_errors))[:1000]
    remember(state, question, response_text)
    return response_text, events


def structured_search(body, state):
    """Explicit UI conditions go directly to existing file retrieval, no LLM."""
    params={k:body[k] for k in ('keyword','scope','extensions','modified_after','modified_before','min_size','max_size','sort_by') if body.get(k) is not None}
    if not isinstance(params.get('keyword',''),str) or not isinstance(params.get('scope','all'),str):raise ValueError('搜索条件不正确')
    if 'extensions' in params and (not isinstance(params['extensions'],list) or any(not isinstance(x,str) for x in params['extensions'])):raise ValueError('文件类型不正确')
    for key in ('min_size','max_size'):
        if key in params and (type(params[key]) is not int or params[key]<0):raise ValueError('文件大小不正确')
    if params.get('sort_by','relevance') not in ('relevance','modified_desc','modified_asc','size_desc'):raise ValueError('排序不正确')
    params['limit']=20
    with state.lock:
        query = state.last_search_query
        current_keyword = query.get('keyword', query.get('semantic_query', query.get('content_keyword', '')))
        if body.get('reuse_current'):
            if not query or params.get('keyword','') != current_keyword or params.get('scope','all') != query.get('scope','all'):
                raise ValueError('当前搜索条件已变化，请重新搜索')
            # Base retrieval constraints remain hard; the form narrows the
            # retained snapshot, without scanning or dropping conversation.
            state.filters={k:params[k] for k in ('extensions','modified_after','modified_before','min_size','max_size','sort_by') if k in params}
            state.current_filtered_results=file_tools.filter_results(state.last_search_results, **state.filters)
            state.selected_file=None
            return search_snapshot(state, {'tool':'filter_results','scope':query.get('scope','all'),
                                           **state.search_coverage,'total':len(state.current_filtered_results),'results':visible(state)}, fresh=False)
        data=search_retrieval.find_files(**params)
        data.pop('_retrieval_evidence',None)
        state.last_search_query=params
        state.last_search_results=data['results'];state.current_filtered_results=list(data['results'])
        state.filters.clear();state.selected_file=None;state.display_limit=data['limit']
        state.search_coverage={k:data[k] for k in ('partial','truncated','note','indexed_scope_files','scanned_files') if k in data}
        return search_snapshot(state, {'tool':'find_files','scope':params.get('scope','all'),**data})


HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>本地文件助手</title>
<script>if(location.hash==="#theme=blue")document.documentElement.dataset.theme="blue";</script><style>:root{--tone-172b4d:#503b32;--tone-f7f9fd:#fff8ef;--tone-fff:#fffdf9;--tone-1f45910c:#804b300c;--tone-f9fbff:#fdfcfb;--tone-e4eaf5:#f5ece4;--tone-2459c7:#bd5147;--tone-61728c:#8c6f61;--tone-243751:#513324;--tone-e8f0ff:#f8e1c7;--tone-17469f:#973c34;--tone-e0e7f3:#f3e9e0;--tone-163f7b08:#693d2808;--tone-64748b:#8b7164;--tone-d8e1f0:#f0e3d8;--tone-edf3ff:#f8e1c7;--tone-253a58:#583625;--tone-e9eef7:#f7efe9;--tone-6b7c98:#987a6b;--tone-f6f8fc:#fcf9f6;--tone-667997:#977666;--tone-425b7e:#7e5642;--tone-e2e9f4:#f4eae2;--tone-234a8208:#78462d08;--tone-687b98:#987868;--tone-eaf1ff:#f8e1c7;--tone-d5dfed:#ede0d5;--tone-2459c71c:#aa64411c;--tone-fbfcff:#fefdfc;--tone-e7edf7:#f6eee8;--tone-62738e:#8e7162;--tone-60718a:#8a6e60;--tone-526a92:#926752;--tone-a33b3b:#a12838;--tone-d9e4f6:#f2e6dd;--tone-172b4d33:#482a1c33;--tone-132d5270:#492b1c70;--tone-dfe8f7:#f4eae2;--focus-ring:#d78b4c;--warning:#8b5c16;--success:#287255;}
:root[data-theme="blue"]{--tone-172b4d:#172b4d;--tone-f7f9fd:#f7f9fd;--tone-fff:#fff;--tone-1f45910c:#1f45910c;--tone-f9fbff:#f9fbff;--tone-e4eaf5:#e4eaf5;--tone-2459c7:#2459c7;--tone-61728c:#61728c;--tone-243751:#243751;--tone-e8f0ff:#e8f0ff;--tone-17469f:#17469f;--tone-e0e7f3:#e0e7f3;--tone-163f7b08:#163f7b08;--tone-64748b:#64748b;--tone-d8e1f0:#d8e1f0;--tone-edf3ff:#edf3ff;--tone-253a58:#253a58;--tone-e9eef7:#e9eef7;--tone-6b7c98:#6b7c98;--tone-f6f8fc:#f6f8fc;--tone-667997:#667997;--tone-425b7e:#425b7e;--tone-e2e9f4:#e2e9f4;--tone-234a8208:#234a8208;--tone-687b98:#687b98;--tone-eaf1ff:#eaf1ff;--tone-d5dfed:#d5dfed;--tone-2459c71c:#2459c71c;--tone-fbfcff:#fbfcff;--tone-e7edf7:#e7edf7;--tone-62738e:#62738e;--tone-60718a:#60718a;--tone-526a92:#526a92;--tone-a33b3b:#a33b3b;--tone-d9e4f6:#d9e4f6;--tone-172b4d33:#172b4d33;--tone-132d5270:#132d5270;--tone-dfe8f7:#dfe8f7;--focus-ring:#2459c7;}

:root{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--tone-172b4d);background:var(--tone-f7f9fd);font-synthesis:none}
*{box-sizing:border-box}body{margin:0}button,input,select{font:inherit}button{cursor:pointer}button:disabled{opacity:.55;cursor:wait}
.shell{height:100dvh;display:grid;grid-template-columns:260px minmax(0,1fr) 248px;max-width:1720px;margin:auto;background:var(--tone-fff);box-shadow:0 12px 60px var(--tone-1f45910c)}
.sidebar{background:var(--tone-f9fbff);border-right:1px solid var(--tone-e4eaf5);padding:28px 18px;display:flex;flex-direction:column;gap:22px;min-width:0;overflow:auto}
.brand{display:flex;align-items:center;gap:11px;padding:3px 10px}.brand-icon{display:grid;place-items:center;background:var(--tone-2459c7);color:var(--tone-fff);width:42px;height:42px;border-radius:12px;font-size:23px;font-weight:700}.brand strong{display:block;font-size:17px}.model{font-size:12px;color:var(--tone-61728c);margin-top:3px}.model:before{content:'●';color:var(--tone-2459c7);margin-right:5px}
.nav{display:grid;gap:5px}.nav button{border:0;background:transparent;color:var(--tone-243751);text-align:left;border-radius:12px;padding:12px 15px;font-weight:550}.nav button.active,.nav button:hover{background:var(--tone-e8f0ff);color:var(--tone-17469f)}
.settings-card,.side-card{border:1px solid var(--tone-e0e7f3);border-radius:16px;background:var(--tone-fff);padding:16px;box-shadow:0 4px 18px var(--tone-163f7b08)}.settings-card h2,.side-card h2{font-size:15px;margin:0 0 16px}.settings-card label,.side-card label{display:block;color:var(--tone-64748b);font-size:12px;margin:10px 0 7px}.settings-card select,.saved-card select{width:100%;border:1px solid var(--tone-d8e1f0);border-radius:9px;background:var(--tone-fff);color:var(--tone-172b4d);padding:9px;min-width:0}
.settings-card button,.saved-card button{width:100%;min-height:36px;margin-top:8px;border-radius:9px;background:var(--tone-2459c7);color:var(--tone-fff);border:0}.settings-card button.secondary,.saved-card button.secondary{background:var(--tone-edf3ff);color:var(--tone-17469f)}.settings-card .pair,.saved-card .pair{display:flex;gap:7px}.settings-card .pair button,.saved-card .pair button{flex:1}.settings-card .toggle{display:flex;gap:8px;align-items:center;color:var(--tone-253a58);font-size:12px;margin:12px 0 0}.settings-card .toggle input{accent-color:var(--tone-2459c7)}.saved-card{margin-top:0}.helper{font-size:12px;color:var(--tone-61728c);line-height:1.5;margin:8px 0}
.chat{display:flex;flex-direction:column;min-width:0;min-height:0;background:var(--tone-fff)}.chat-head{padding:25px 32px 17px;border-bottom:1px solid var(--tone-e9eef7)}.chat-head h1{font-size:24px;margin:0 0 4px}.chat-head p{font-size:13px;color:var(--tone-6b7c98);margin:0}
#log{flex:1;overflow:auto;padding:26px 32px;display:flex;flex-direction:column;gap:18px;min-height:0}.message{max-width:90%;padding:14px 17px;border-radius:16px;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6}.me{align-self:flex-end;background:var(--tone-e8f0ff)}.bot{align-self:flex-start;background:var(--tone-f6f8fc)}.status{font-size:12px;color:var(--tone-667997);margin:7px 0}.paths{font-size:12px;color:var(--tone-425b7e);margin-top:8px;white-space:normal}.paths div{margin:5px 0;overflow-wrap:anywhere}
.card{position:relative;background:var(--tone-fff);border:1px solid var(--tone-e2e9f4);border-radius:13px;padding:12px 14px;margin:10px 0;white-space:normal;box-shadow:0 3px 12px var(--tone-234a8208)}.card strong{display:block;overflow-wrap:anywhere;margin-bottom:5px}.card strong:before{content:'▣';color:var(--tone-2459c7);margin-right:8px}.meta{font-size:12px;color:var(--tone-687b98);margin:4px 0;overflow-wrap:anywhere}.card button{background:var(--tone-eaf1ff);color:var(--tone-17469f);border:0;border-radius:8px;padding:6px 10px;font-size:12px;margin:7px 6px 0 0}.card button:first-of-type{background:var(--tone-2459c7);color:var(--tone-fff)}
#form{display:flex;gap:10px;padding:15px 28px 22px;border-top:1px solid var(--tone-e9eef7);background:var(--tone-fff)}#question{flex:1;min-width:0;padding:13px 15px;border:1px solid var(--tone-d5dfed);border-radius:12px;outline:0}#question:focus{border-color:var(--tone-2459c7);box-shadow:0 0 0 3px var(--tone-2459c71c)}#send{border:0;border-radius:12px;background:var(--tone-2459c7);color:var(--tone-fff);padding:0 22px;font-weight:600}
#stopChat,#stopDuplicates{border:0;border-radius:10px;background:var(--tone-eaf1ff);color:var(--tone-17469f);padding:8px 12px}#stopChat[hidden],#stopDuplicates[hidden]{display:none}
.right{background:var(--tone-fbfcff);border-left:1px solid var(--tone-e7edf7);padding:28px 14px;display:flex;flex-direction:column;gap:14px;overflow:auto}.side-card{box-shadow:none}.side-card p{font-size:13px;color:var(--tone-62738e);line-height:1.5;margin:0}.side-card strong{color:var(--tone-172b4d)}.summary-row{display:flex;justify-content:space-between;gap:8px;padding:6px 0;font-size:12px;color:var(--tone-60718a)}.summary-row b{font-weight:600;color:var(--tone-172b4d);text-align:right}.index-state{font-size:12px;line-height:1.5;overflow-wrap:anywhere;color:var(--tone-526a92)}.index-state.error{color:var(--tone-a33b3b)}.index-state.completed{color:var(--tone-2459c7)}.pill{display:inline-block;background:var(--tone-eaf1ff);color:var(--tone-17469f);border-radius:999px;padding:5px 9px;font-size:12px;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.folder-dialog{border:1px solid var(--tone-d9e4f6);border-radius:18px;padding:24px;width:min(430px,calc(100vw - 32px));box-shadow:0 24px 70px var(--tone-172b4d33);color:var(--tone-172b4d)}.folder-dialog::backdrop{background:var(--tone-132d5270)}.folder-dialog h2{margin:0 0 7px;font-size:20px}.folder-dialog p{font-size:13px;color:var(--tone-60718a);line-height:1.55}.folder-dialog label{display:block;font-size:13px;font-weight:600;margin:16px 0 5px}.folder-dialog input,.folder-dialog select{display:block;width:100%;border:1px solid var(--tone-d5dfed);border-radius:10px;padding:10px;background:var(--tone-fff);color:var(--tone-172b4d)}.folder-dialog .dialog-actions{display:flex;justify-content:flex-end;gap:9px;margin-top:20px}.folder-dialog button{border:0;border-radius:10px;padding:10px 15px;background:var(--tone-2459c7);color:white}.folder-dialog button.secondary{background:var(--tone-edf3ff);color:var(--tone-17469f)}.folder-dialog .error{color:var(--tone-a33b3b)}
.chat-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.chat-head button{border:1px solid var(--tone-d9e4f6);border-radius:9px;background:var(--tone-fff);color:var(--tone-2459c7);padding:8px 11px;font-size:12px}.duplicate-group{border:1px solid var(--tone-dfe8f7);border-radius:11px;padding:10px;margin-top:10px;background:var(--tone-fff)}.duplicate-group strong{display:block;margin-bottom:7px}.duplicate-choice{display:flex;align-items:flex-start;gap:8px;font-size:12px;margin:7px 0;overflow-wrap:anywhere}.duplicate-choice input{margin-top:3px;accent-color:var(--tone-2459c7)}.duplicate-action{margin-top:12px;border:0;border-radius:9px;background:var(--tone-2459c7);color:var(--tone-fff);padding:8px 12px}.cleanup-dialog{width:min(650px,calc(100vw - 32px));max-height:80vh;overflow:auto}.cleanup-paths{max-height:24vh;overflow:auto;padding:9px;background:var(--tone-f6f8fc);border-radius:9px;font-size:12px;overflow-wrap:anywhere}.cleanup-paths div{padding:3px 0}
@media(max-width:1050px){.shell{grid-template-columns:225px minmax(0,1fr)}.right{display:none}.sidebar{padding:20px 12px}.chat-head{padding:20px 24px}.message{max-width:98%}}
@media(max-width:680px){.shell{height:100dvh;display:flex;flex-direction:column}.sidebar{flex:0 0 auto;max-height:40vh;padding:12px;gap:8px}.brand{padding:0}.nav{display:flex}.nav button{padding:7px 9px;font-size:12px}.settings-card,.saved-card{padding:10px}.settings-card h2,.saved-card h2{margin-bottom:5px}.chat{flex:1;min-height:0}.chat-head{padding:12px 18px}.chat-head h1{font-size:19px}#log{padding:15px;gap:10px}#form{padding:10px}#send{padding:0 13px}}
.compact .shell{display:flex;height:100dvh}.compact .sidebar,.compact .right{display:none}.compact .chat{width:100%}.compact .chat-head{padding:10px;flex-wrap:wrap}.compact .chat-head h1{font-size:17px}.compact .chat-head p{display:none}.compact #scope{max-width:130px;border:1px solid var(--tone-d5dfed);border-radius:8px;padding:5px}.compact #log{padding:10px;gap:10px}.compact .message{max-width:100%;padding:10px}.compact #form{padding:8px;gap:6px}.compact #question{padding:10px}.compact .card{padding:8px}.compact #send{padding:0 10px}.compact #stopChat{font-size:11px;padding:5px}.compact .full-web{font-size:12px;color:var(--tone-2459c7)}

button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--focus-ring);outline-offset:2px}.index-state.cancelled,.index-state.partial{color:var(--warning)}.index-state.completed{color:var(--success)}button:disabled{cursor:not-allowed}.status{border-left:2px solid var(--focus-ring);padding-left:6px}
#fileSearchForm{padding:16px 28px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;border-bottom:1px solid var(--tone-e9eef7)}#fileSearchForm[hidden]{display:none}#fileSearchForm label{font-size:12px;color:var(--tone-61728c)}#fileSearchForm input,#fileSearchForm select{display:block;width:100%;margin-top:4px;padding:8px;border:1px solid var(--tone-d5dfed);border-radius:8px;background:var(--tone-fff);color:var(--tone-172b4d)}#fileSearchForm button{border:0;border-radius:8px;background:var(--tone-2459c7);color:var(--tone-fff)}#fileSearchForm p{font-size:11px;margin:0}#form[hidden]{display:none}
</style></head><body><main class="shell">
<aside class="sidebar" aria-label="文件助手侧栏"><div class="brand"><span class="brand-icon" aria-hidden="true">⌕</span><div><strong>本地文件助手</strong><div class="model">qwen3.5:9b · 本地运行</div></div></div>
<nav class="nav" aria-label="快捷入口"><button id="navChat" class="active" type="button">☏ &nbsp;文件助手</button><button id="navFiles" type="button">⌕ &nbsp;高级筛选</button><button id="navSaved" type="button">▤ &nbsp;已保存搜索</button></nav>
<section class="settings-card" aria-label="索引与目录"><h2>索引与目录</h2><label for="scope">搜索范围</label><select id="scope" aria-label="索引范围"><option value="all">全部目录</option><option value="workspace">workspace</option><option value="desktop">桌面</option><option value="documents">文稿</option><option value="downloads">下载</option></select><button id="build" type="button">↻ &nbsp;更新语义索引</button><div class="pair"><button id="addScope" class="secondary" type="button">添加目录</button><button id="removeScope" class="secondary" type="button">移除当前目录</button></div><label class="toggle"><input id="autoIndex" type="checkbox"> 每 15 分钟自动更新</label></section>
<section class="settings-card saved-card" id="savedPanel" aria-label="保存的搜索"><h2>保存的搜索</h2><select id="savedSearches" aria-label="保存的搜索"><option value="">选择保存的搜索</option></select><div class="pair"><button id="saveSearch" class="secondary" type="button">保存当前</button><button id="runSearch" type="button">运行</button></div><button id="removeSearch" class="secondary" type="button">删除所选搜索</button></section>
<button id="duplicates" type="button" style="border:1px solid #dce6f4;background:#fff;color:#17469f;padding:10px;border-radius:10px;text-align:left">查找重复文件 →</button><button id="stopDuplicates" type="button" hidden>终止重复文件检查</button></aside>
<section class="chat" aria-label="文件助手"><header class="chat-head"><div><h1>文件助手</h1><p>搜索、阅读并理解你的本地文件</p></div><button id="clearChat" type="button">清空聊天</button></header>
<form id="fileSearchForm" hidden aria-label="结构化文件搜索"><label>文件名关键词<input id="fileKeyword" maxlength="100" placeholder="例如：简历"></label><label>文件类型<input id="fileTypes" placeholder="例如：pdf,docx"></label><label>修改时间从<input id="fileAfter" type="date"></label><label>至<input id="fileBefore" type="date"></label><label>最小大小（字节）<input id="fileMin" type="number" min="0"></label><label>最大大小（字节）<input id="fileMax" type="number" min="0"></label><label>排序<select id="fileSort"><option value="relevance">相关性</option><option value="modified_desc">最近修改</option><option value="modified_asc">最早修改</option><option value="size_desc">大小降序</option></select></label><button id="fileSearchSubmit" type="submit">搜索文件</button><p>条件不变时筛选当前结果快照；修改关键词或范围后重新搜索。可切回“文件助手”继续打开或问答。</p></form><div id="log"><div class="message bot">你好！试试“帮我找一下简历”，找到后还可以说“只看 PDF”或“打开第一个”。也可以按文件内容搜索与提问。</div></div>
<form id="form"><input id="question" autocomplete="off" placeholder="搜索文件，或询问文件内容…" maxlength="4000" required><button id="stopChat" type="button" hidden>终止搜索</button><button id="send">发送</button></form></section>
<aside class="right" aria-label="搜索与索引状态"><section class="side-card"><h2>当前搜索</h2><p id="searchSummary">搜索后会在这里显示当前结果。</p><div class="summary-row"><span>搜索范围</span><b id="currentScope">全部目录</b></div><div class="summary-row"><span>实际展示 / 已验证</span><b id="resultCount">—</b></div></section><section class="side-card"><h2>索引状态</h2><div id="indexRightStatus" class="index-state"></div><div id="indexSummary" class="index-state">正在读取索引状态</div><details id="indexDetails"><summary>索引技术详情</summary><div id="indexStatus" class="index-state"></div></details></section></aside></main>
<dialog id="folderDialog" class="folder-dialog"><form id="folderForm"><h2>添加搜索目录</h2><p>选择一个允许的基础目录，再填写已有的子文件夹。也可以输入完整 Mac 路径。</p><label for="folderBase">基础目录</label><select id="folderBase"><option value="desktop">桌面 Desktop</option><option value="documents">文稿 Documents</option><option value="downloads">下载 Downloads</option><option value="workspace">workspace</option></select><label for="folderPath">子目录名称或路径</label><input id="folderPath" required placeholder="例如 resume 或 项目/resume"><p>将使用类似 <code>desktop/resume</code> 的路径；也接受 <code>/Users/你的用户名/Desktop/resume</code>。</p><label for="folderName">显示名称（可选）</label><input id="folderName" maxlength="40" placeholder="例如 简历"><p id="folderError" class="error" role="alert" hidden></p><div class="dialog-actions"><button id="folderCancel" class="secondary" type="button">取消</button><button id="folderSubmit" type="submit">添加目录</button></div></form></dialog>
<dialog id="cleanupDialog" class="folder-dialog cleanup-dialog"><h2>确认移入废纸篓</h2><p>请核对保留和移走的文件。确认后，所选文件会进入系统废纸篓。</p><strong>保留</strong><div id="cleanupKeep" class="cleanup-paths"></div><strong>移入废纸篓</strong><div id="cleanupDelete" class="cleanup-paths"></div><div class="dialog-actions"><button id="cleanupCancel" class="secondary" type="button">取消</button><button id="cleanupConfirm" type="button">确认移入废纸篓</button></div></dialog>
<script src="/input-guard.js"></script>
<script>
const log=document.getElementById('log'), form=document.getElementById('form'), input=document.getElementById('question'), send=document.getElementById('send');
LocalFileChatInputGuard.install(input,form);
for(const field of document.querySelectorAll('#fileSearchForm input,#folderForm input'))LocalFileChatInputGuard.install(field,field.form);
let sessionId=null;
window.localFileChatTaskState={state:'idle',request_id:null,sequence:0};
function taskState(state,task,sequence=0){
  if(task && chatTask!==task)return;
  const detail={state,request_id:task?.id||null,session_id:sessionId,sequence,at:Date.now()};
  window.localFileChatTaskState=detail;
  document.documentElement.dataset.taskState=state;
  document.documentElement.dataset.taskRequest=detail.request_id||'';
  document.dispatchEvent(new CustomEvent('local-file-chat-task',{detail}));
}

let chatTask=null, duplicatePending=null, duplicatePoll=null, cleanupToken=null, duplicateHidden=false, clearingChat=false, viewEpoch=0;
const stopChat=document.getElementById('stopChat'), duplicates=document.getElementById('duplicates'), stopDuplicates=document.getElementById('stopDuplicates');
const cleanupDialog=document.getElementById('cleanupDialog'), cleanupConfirm=document.getElementById('cleanupConfirm');
const build=document.getElementById('build'), scope=document.getElementById('scope'), indexStatus=document.getElementById('indexStatus');
const savedSearches=document.getElementById('savedSearches');
const currentScope=document.getElementById('currentScope'), resultCount=document.getElementById('resultCount'), searchSummary=document.getElementById('searchSummary');
const stopIndex=document.createElement('button');stopIndex.type='button';stopIndex.textContent='取消索引更新';stopIndex.hidden=true;build.after(stopIndex);stopIndex.addEventListener('click',async()=>{try{await postJSON('/api/index/cancel',{});await refreshIndex()}catch(error){indexStatus.textContent=error.message}});
build.parentElement.appendChild(document.getElementById('indexSummary'));build.parentElement.appendChild(document.getElementById('indexDetails'));
if(location.pathname==='/compact'){document.body.classList.add('compact');const header=document.querySelector('.chat-head');header.appendChild(scope);const full=document.createElement('a');full.href='/';full.target='_blank';full.className='full-web';full.textContent='打开完整网页';header.appendChild(full)}
scope.addEventListener('change',()=>{currentScope.textContent=scope.selectedOptions[0]?.textContent||'全部目录'});
let fileMode='assistant', currentSearchState=null;
function syncFilterForm(){if(!currentSearchState)return;const q=currentSearchState.query,f={...q,...currentSearchState.filters};document.getElementById('fileKeyword').value=q.keyword??q.semantic_query??q.content_keyword??'';document.getElementById('fileTypes').value=(f.extensions||[]).join(',');for(const [id,key] of [['fileAfter','modified_after'],['fileBefore','modified_before'],['fileMin','min_size'],['fileMax','max_size']])document.getElementById(id).value=f[key]??'';document.getElementById('fileSort').value=f.sort_by||'relevance';}
function setFileMode(mode){fileMode=mode;const structured=mode==='files';document.getElementById('fileSearchForm').hidden=!structured;form.hidden=structured;document.querySelector('.chat-head h1').textContent=structured?'高级筛选':'文件助手';for(const id of ['navChat','navFiles','navSaved']){const chosen=id===(structured?'navFiles':mode==='saved'?'navSaved':'navChat');document.getElementById(id).classList.toggle('active',chosen);document.getElementById(id).setAttribute('aria-current',chosen?'page':'false')}document.body.dataset.fileMode=mode;if(structured){syncFilterForm();document.getElementById('fileKeyword').focus();}else input.focus();}
document.getElementById('navChat').addEventListener('click',()=>setFileMode('assistant'));
document.getElementById('navFiles').addEventListener('click',()=>setFileMode('files'));
document.getElementById('navSaved').addEventListener('click',()=>{setFileMode('saved');document.getElementById('savedPanel').scrollIntoView({behavior:'smooth',block:'nearest'});savedSearches.focus()});
document.getElementById('fileSearchForm').onsubmit=async event=>{event.preventDefault();if(chatTask||clearingChat)return;const button=document.getElementById('fileSearchSubmit');if(button.disabled)return;button.disabled=true;const epoch=viewEpoch;try{const payload={session_id:sessionId,scope:scope.value,keyword:document.getElementById('fileKeyword').value.trim(),sort_by:document.getElementById('fileSort').value};const q=currentSearchState?.query;payload.reuse_current=!!q&&payload.keyword===(q.keyword??q.semantic_query??q.content_keyword??'')&&payload.scope===(q.scope||'all');const types=document.getElementById('fileTypes').value.trim();if(types)payload.extensions=types.split(/[,，\s]+/).filter(Boolean);for(const [id,key] of [['fileAfter','modified_after'],['fileBefore','modified_before']]){const v=document.getElementById(id).value;if(v)payload[key]=v}for(const [id,key] of [['fileMin','min_size'],['fileMax','max_size']]){const v=document.getElementById(id).value;if(v)payload[key]=Number(v)}const data=await postJSON('/api/files/search',payload);if(epoch!==viewEpoch)return;sessionId=data.session_id;add('assistant',data.answer,data.events);}catch(error){if(epoch===viewEpoch)add('assistant','搜索失败：'+error.message)}finally{button.disabled=false}};
async function postJSON(url,payload){const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:AbortSignal.timeout(20000)});const data=await response.json();if(!response.ok)throw new Error(data.error||'请求失败');return data}
async function refreshPreferences(){
  const response=await fetch('/api/preferences');const data=await response.json();
  const selected=scope.value;scope.querySelectorAll('option[data-custom]').forEach(option=>option.remove());
  for(const [key,item] of Object.entries(data.scopes)){const option=new Option(item.name+' · '+item.path,key);option.dataset.custom='true';scope.add(option)}
  if([...scope.options].some(option=>option.value===selected))scope.value=selected;
  const saved=savedSearches.value;savedSearches.replaceChildren(new Option('选择保存的搜索',''));
  for(const [key,item] of Object.entries(data.searches))savedSearches.add(new Option(item.name,key));
  if(data.searches[saved])savedSearches.value=saved;
  document.getElementById('autoIndex').checked=data.auto_index;
}
refreshPreferences().catch(error=>add('assistant','读取设置失败：'+error.message));
document.getElementById('autoIndex').addEventListener('change',async event=>{try{await postJSON('/api/preferences',{operation:'auto_index',enabled:event.target.checked})}catch(error){event.target.checked=!event.target.checked;add('assistant',error.message)}});
const folderDialog=document.getElementById('folderDialog'), folderForm=document.getElementById('folderForm');
document.getElementById('addScope').addEventListener('click',()=>{
  folderForm.reset();document.getElementById('folderError').hidden=true;
  if(['desktop','documents','downloads','workspace'].includes(scope.value))document.getElementById('folderBase').value=scope.value;
  folderDialog.showModal();document.getElementById('folderPath').focus();
});
document.getElementById('folderCancel').addEventListener('click',()=>folderDialog.close());
folderForm.addEventListener('submit',async event=>{
  event.preventDefault();
  const entry=document.getElementById('folderPath').value.trim();
  const path=entry.startsWith('/')||entry.startsWith('~')?entry:document.getElementById('folderBase').value+'/'+entry;
  const name=document.getElementById('folderName').value.trim()||entry.split('/').filter(Boolean).at(-1)||entry;
  const error=document.getElementById('folderError'), submit=document.getElementById('folderSubmit');
  error.hidden=true;submit.disabled=true;
  try{const data=await postJSON('/api/preferences',{operation:'add_scope',name,path});await refreshPreferences();scope.value=data.key;scope.dispatchEvent(new Event('change'));folderDialog.close()}
  catch(exc){error.textContent=exc.message;error.hidden=false}
  finally{submit.disabled=false}
});
document.getElementById('removeScope').addEventListener('click',async()=>{if(!scope.value.startsWith('custom_'))return add('assistant','请先选择自定义目录');try{await postJSON('/api/preferences',{operation:'remove_scope',key:scope.value});scope.value='all';await refreshPreferences()}catch(error){add('assistant',error.message)}});
document.getElementById('saveSearch').addEventListener('click',async()=>{if(!sessionId)return add('assistant','请先搜索文件');const name=prompt('给当前搜索起一个名称');if(!name)return;try{await postJSON('/api/saved-search',{operation:'save',name,session_id:sessionId});await refreshPreferences()}catch(error){add('assistant',error.message)}});
document.getElementById('runSearch').addEventListener('click',async()=>{const epoch=viewEpoch;if(!savedSearches.value)return;try{const data=await postJSON('/api/saved-search',{operation:'run',key:savedSearches.value,session_id:sessionId});if(epoch!==viewEpoch)return;sessionId=data.session_id;add('assistant',data.answer,data.events)}catch(error){add('assistant',error.message)}});
document.getElementById('removeSearch').addEventListener('click',async()=>{if(!savedSearches.value)return;try{await postJSON('/api/preferences',{operation:'remove_search',key:savedSearches.value});await refreshPreferences()}catch(error){add('assistant',error.message)}});
function renderDuplicateResults(box,found){
  box.textContent=found.total?'找到 '+found.total+' 组完全相同的文件。勾选要移入废纸篓的副本；每组至少留一份。':'没有找到内容完全相同的文件。';
  if(!found.total)return;
  for(const [index,group] of found.groups.entries()){
    const section=document.createElement('div');section.className='duplicate-group';
    const heading=document.createElement('strong');heading.textContent='第 '+(index+1)+' 组 · '+group.length+' 个文件';section.appendChild(heading);
    for(const file of group){
      const label=document.createElement('label');label.className='duplicate-choice';
      const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.value=file.path;
      const detail=document.createElement('span');detail.textContent=file.name+' · '+size(file.size)+'\n'+file.path;
      label.append(checkbox,detail);section.appendChild(label);
    }
    box.appendChild(section);
  }
  const button=document.createElement('button');button.type='button';button.className='duplicate-action';button.textContent='核对所选文件';
  button.addEventListener('click',async()=>{
    const epoch=viewEpoch;const selected=[...box.querySelectorAll('.duplicate-choice input:checked')].map(item=>item.value);
    if(!selected.length)return add('assistant','请先勾选要移入废纸篓的文件。');
    button.disabled=true;
    try{
      const plan=await postJSON('/api/duplicates/plan',{selected});if(epoch!==viewEpoch)return;cleanupToken=plan.token;
      for(const [id,paths] of [['cleanupKeep',plan.keep],['cleanupDelete',plan.delete]]){
        const target=document.getElementById(id);target.replaceChildren();
        for(const path of paths){const row=document.createElement('div');row.textContent=path;target.appendChild(row)}
      }
      cleanupDialog.showModal();
    }catch(error){add('assistant','无法准备清理：'+error.message)}finally{button.disabled=false}
  });
  box.appendChild(button);
}
document.getElementById('cleanupCancel').addEventListener('click',()=>{cleanupToken=null;cleanupDialog.close()});
cleanupConfirm.addEventListener('click',async()=>{
  if(!cleanupToken)return;cleanupConfirm.disabled=true;
  const token=cleanupToken;cleanupToken=null;
  try{
    const result=await postJSON('/api/duplicates/confirm',{token});cleanupDialog.close();
    add('assistant',result.moved.length+' 个文件已移入废纸篓。'+(result.failed?'\n未完成：'+result.failed+'\n'+result.error:''));
    if(duplicatePending)duplicatePending=null;
  }catch(error){cleanupDialog.close();add('assistant','清理未执行：'+error.message)}
  finally{cleanupConfirm.disabled=false}
});
async function updateDuplicates(){
  const response=await fetch('/api/duplicates/status');const data=await response.json();
  const running=data.status==='running';duplicates.disabled=running;stopDuplicates.hidden=!running;
  if(running){if(!duplicatePending&&!duplicateHidden)duplicatePending=add('assistant',data.message);if(!duplicatePoll)duplicatePoll=setTimeout(()=>{duplicatePoll=null;updateDuplicates().catch(error=>{if(duplicatePending)duplicatePending.textContent=error.message})},750);return}
  if(duplicatePoll){clearTimeout(duplicatePoll);duplicatePoll=null}
  if(duplicatePending){
    if(data.status==='completed'){
      renderDuplicateResults(duplicatePending,data.result);
    }else duplicatePending.textContent=data.message;
    duplicatePending=null;
  }
}
duplicates.addEventListener('click',async()=>{
  if(duplicates.disabled)return;duplicates.disabled=true;
  duplicateHidden=false;
  duplicatePending=add('assistant','正在核对同大小文件的 SHA-256…');
  try{await postJSON('/api/duplicates',{scope:scope.value});await updateDuplicates()}
  catch(error){duplicates.disabled=false;if(duplicatePending)duplicatePending.remove();duplicatePending=null;add('assistant',error.message)}
});
stopDuplicates.addEventListener('click',async()=>{stopDuplicates.disabled=true;try{await postJSON('/api/duplicates/cancel',{});await updateDuplicates()}catch(error){add('assistant',error.message)}finally{stopDuplicates.disabled=false}});
updateDuplicates().catch(()=>{});
async function refreshIndex(){
  const response=await fetch('/api/index/status',{signal:AbortSignal.timeout(20000)});const data=await response.json();
  document.getElementById('indexRightStatus').textContent=data.message;
  document.getElementById('indexSummary').textContent=data.message+' · '+(data.indexed_files??'未知')+' 个文件 / '+(data.indexed_chunks??'未知')+' 个分块';
  indexStatus.textContent=data.message+'\n'+data.path+'\n'+data.model+' · '+(data.indexed_files??'未知')+' 个文件 / '+(data.indexed_chunks??'未知')+' 个分块\n扫描上限 '+data.scan_limit+(data.coverage_note?' · '+data.coverage_note:'')+(data.phase?'\n阶段 '+data.phase:'')+(data.current_file?' · '+data.current_file:'')+(data.finished_at?'\n结束于 '+new Date(data.finished_at*1000).toLocaleString():'')+(data.result?'（本次检查 '+data.result.seen+' 个，新建 '+(data.result.new??data.result.indexed)+' 个，更新 '+(data.result.updated??0)+' 个，失败 '+(data.result.failed??0)+' 个，未变 '+data.result.unchanged+' 个，跳过 '+data.result.skipped+' 个；索引库共 '+data.result.indexed_total+' 个）':'');
  if(data.parsing_limits)indexStatus.textContent+='\n解析上限：PDF '+data.parsing_limits.pdf_pages+' 页，每文件 '+data.parsing_limits.chunks_per_file+' 块';
  if(data.result){const r=data.result;if(r.partial)indexStatus.textContent+='\n覆盖不完整：权限错误 '+(r.permission_errors??[]).length+'；不可访问目录 '+(r.missing_roots??[]).join('、')+'；分块截断 '+(r.truncated_files??0)+'；扫描达到上限 '+(r.scan_limit?'是':'否')+'；跳过/失败 '+(r.skipped??0)+'/'+(r.failed??0)}
  if(!data.finished_at&&data.last_indexed_at)indexStatus.textContent+='\n最近入库 '+new Date(data.last_indexed_at*1000).toLocaleString();
  indexStatus.className='index-state '+(data.status==='error'?'error':data.status==='completed'?'completed':'');
  build.disabled=data.status==='running';stopIndex.hidden=data.status!=='running';
  if(data.status==='running')setTimeout(()=>refreshIndex().catch(error=>{indexStatus.textContent=error.message}),2000);
}
build.addEventListener('click',async()=>{
  if(build.disabled)return;build.disabled=true;
  try{const response=await fetch('/api/index',{signal:AbortSignal.timeout(20000),method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scope:scope.value})});
      const data=await response.json();if(!response.ok)throw new Error(data.error||'启动失败');refreshIndex();
  }catch(error){build.disabled=false;indexStatus.textContent=error.message}
});
refreshIndex().catch(()=>{});
function size(bytes){return bytes>=1048576?(bytes/1048576).toFixed(1)+' MB':bytes>=1024?(bytes/1024).toFixed(1)+' KB':bytes+' B'}
async function action(kind,path){
  const epoch=viewEpoch;try{const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sessionId,action:kind,path}),signal:AbortSignal.timeout(20000)});
    const data=await response.json();if(epoch!==viewEpoch)return;if(!response.ok)throw new Error(data.error||'操作失败');add('assistant',kind==='open_file'?'已打开：'+data.file.name:'已在 Finder 中定位：'+data.file.name);
  }catch(error){if(epoch===viewEpoch)add('assistant','操作失败：'+error.message)}
}
async function inspect(kind,path){const epoch=viewEpoch;try{const data=await postJSON(kind==='preview'?'/api/preview':'/api/related',{session_id:sessionId,path});if(epoch!==viewEpoch)return;if(kind==='preview'){const box=add('assistant',data.file.name+(data.file.pages?' · 共 '+data.file.pages+' 页':'')+(data.page?' · 预览第 '+data.page+' 页':'')+'\n'+data.excerpt);if(data.thumbnail){const picture=document.createElement('img');picture.src=data.thumbnail;picture.alt=data.file.name+' 预览';picture.style='max-width:100%;max-height:440px;display:block;margin-top:8px';box.appendChild(picture)}}else add('assistant',data.answer,data.events)}catch(error){if(epoch===viewEpoch)add('assistant','操作失败：'+error.message)}}
function add(role, content, events=[]){
  const box=document.createElement('div'); box.className='message '+(role==='user'?'me':'bot'); box.textContent=content;
  for(const event of events){
    if(Array.isArray(event.results)){
      const c=event.counts||{candidates:event.total??event.results.length,verified:event.total??event.results.length,displayed:event.results.length};
      resultCount.textContent=c.displayed+' / '+c.verified+' 个文件';
      searchSummary.textContent='检索候选 '+c.candidates+' 个；'+(c.verified?'当前快照已验证 '+c.verified+' 个，可继续筛选或打开。':'当前条件下没有可靠匹配。')+(event.partial||event.truncated?' 覆盖不完整，见结果说明。':'');
      if(event.search_state)currentSearchState=event.search_state;
      if(event.scope){let selected=[...scope.options].find(option=>option.value===event.scope);if(!selected){selected=new Option('当前目录 · '+event.scope,event.scope);selected.dataset.transient='true';scope.add(selected)}currentScope.textContent=selected.textContent;scope.value=event.scope}
    }
    const note=document.createElement('div');note.className='status';note.textContent='已调用 '+event.tool+(event.total===undefined?'':' · 已验证 '+event.total+' 个 · 展示 '+(event.results||[]).length+' 个')+(event.note?' · '+event.note:'');box.appendChild(note);
    for(const [position,file] of (event.results||[]).entries()){
      const card=document.createElement('div');card.className='card';
      const name=document.createElement('strong');name.textContent=(position+1)+'. '+file.name;card.appendChild(name);
      const details=document.createElement('div');details.className='meta';details.textContent=(file.extension||'文件').toUpperCase()+' · '+size(file.size)+' · 修改于 '+file.modified_time.slice(0,16)+(file.page?' · 第 '+file.page+' 页':'')+(file.evidence_source?' · 匹配依据 '+file.evidence_source:'')+(typeof file.similarity==='number'?' · 相似度 '+file.similarity.toFixed(3):'');card.appendChild(details);
      const path=document.createElement('div');path.className='meta';path.textContent=file.path;card.appendChild(path);
      if(file.excerpt){const excerpt=document.createElement('div');excerpt.className='meta';excerpt.textContent='内容：'+file.excerpt;card.appendChild(excerpt)}
      for(const [label,kind] of [['打开','open_file'],['Finder 定位','reveal_in_finder']]){
        const button=document.createElement('button');button.type='button';button.textContent=label;button.addEventListener('click',()=>action(kind,file.path));card.appendChild(button)
      }
      for(const [label,kind] of [['预览','preview'],['相似文件','related']]){const button=document.createElement('button');button.type='button';button.textContent=label;button.addEventListener('click',()=>inspect(kind,file.path));card.appendChild(button)}
      box.appendChild(card)
    }
  }
  log.appendChild(box);log.scrollTop=log.scrollHeight;return box;
}
form.addEventListener('submit', async e=>{
  e.preventDefault();const question=input.value.trim();if(!question||chatTask||clearingChat)return;
  input.value='';add('user',question);send.disabled=true;stopChat.hidden=false;const pending=add('assistant','正在思考…');
  const task={id:crypto.randomUUID(),controller:new AbortController(),pending,epoch:viewEpoch};chatTask=task;task.timeout=setTimeout(()=>{if(chatTask===task)stopChat.click()},180000);
  try{
    taskState('queued',task);
    const response=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},signal:task.controller.signal,body:JSON.stringify({question,session_id:sessionId,scope:scope.value,request_id:task.id,stream:true})});
    if(!response.ok){const error=await response.json();throw new Error(error.error||'请求失败')}
    const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',doneReceived=false;
    task.boxes=new Map();task.sequence=0;
    function accept(data){
      if(chatTask!==task || clearingChat || task.epoch!==viewEpoch || data.request_id!==task.id)return;
      if(data.sequence<=task.sequence)return;task.sequence=data.sequence;
      if(data.type==='session'){sessionId=data.session_id;return}
      if(data.type==='stage'){taskState(data.state,task,data.sequence);if(data.state==='queued')pending.textContent='正在排队等待本地模型，可终止当前请求…';else if(data.state==='searching')pending.textContent='正在检索/核验相关性…';else if(data.state==='generating')pending.textContent='本地模型正在处理…';return}
      if(data.type==='progress'){pending.textContent=data.message+'（'+data.elapsed_seconds+'秒）';return}
      if(data.type==='tool'){
        const event=data.event;task.boxes.get(event.tool)?.remove();
        const box=add('assistant',event.preliminary?'索引首批结果，继续核对覆盖范围…':'工具结果已返回。',[event]);
        task.boxes.set(event.tool,box);pending.textContent='正在完成当前请求…';return;
      }
      if(data.type==='error')throw new Error(data.error||'请求失败');
      if(data.type==='done'){
        doneReceived=true;pending.remove();
        if(data.cancelled){taskState('idle',task,data.sequence);return}
        sessionId=data.session_id;
        add('assistant',data.answer,task.boxes.size?[]:data.events||[]);
        taskState(data.task_state||'complete',task,data.sequence);
      }
    }
    while(true){
      const {value,done}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});
      let cut;while((cut=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,cut);buffer=buffer.slice(cut+1);if(line.trim())accept(JSON.parse(line))}
      if(done)break;
    }
    if(buffer.trim())accept(JSON.parse(buffer));
    if(!doneReceived && chatTask===task)throw new Error('连接中断，请重新发送；旧请求不会回填。');
  }catch(error){if(chatTask===task){taskState('idle',task,1000001);pending.remove();add('assistant','出错了：'+error.message)}}
  finally{clearTimeout(task.timeout);if(chatTask===task){chatTask=null;send.disabled=false;stopChat.hidden=true;input.focus()}}
});
stopChat.addEventListener('click',async()=>{
  const task=chatTask;if(!task)return;stopChat.disabled=true;taskState('idle',task,1000002);
  try{
    const data=await postJSON('/api/chat/cancel',{request_id:task.id,session_id:sessionId});
    if(chatTask!==task)return;
    sessionId=data.session_id;task.pending.textContent='已终止搜索，可以继续输入下一项任务。';
    chatTask=null;task.controller.abort();send.disabled=false;stopChat.hidden=true;input.focus();
  }catch(error){add('assistant','终止失败：'+error.message)}finally{stopChat.disabled=false}
});
document.getElementById('clearChat').addEventListener('click',async event=>{
  const button=event.currentTarget;button.disabled=true;clearingChat=true;viewEpoch++;send.disabled=true;taskState('idle',chatTask,1000002);
  try{
    const active=chatTask;
    const data=await postJSON('/api/chat/clear',{session_id:sessionId,request_id:active?.id||null});
    if(active){clearTimeout(active.timeout);active.controller.abort();chatTask=null}
    sessionId=data.session_id;duplicatePending=null;duplicateHidden=true;
    cleanupToken=null;cleanupDialog.close();
    log.replaceChildren();add('assistant','聊天已清空。可以开始新的搜索或提问。');
    currentSearchState=null;searchSummary.textContent='搜索后会在这里显示当前结果。';resultCount.textContent='—';
    currentScope.textContent=scope.selectedOptions[0]?.textContent||'全部目录';
    send.disabled=false;stopChat.hidden=true;input.value='';input.focus();
  }catch(error){add('assistant','清空失败：'+error.message)}finally{button.disabled=false;clearingChat=false;send.disabled=!!chatTask}
});
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def send_json(self, status, obj):
        if getattr(self,"streaming",False):
            self.emit_stream({"type":"error" if status>=400 else "done","request_id":self.chat_request,"sequence":1000000,**obj})
            return
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass  # A cancelled browser fetch may have closed its socket.

    def do_GET(self):
        if self.path == "/input-guard.js":
            raw=(settings.PROJECT/"input-guard.js").read_bytes()
            self.send_response(200);self.send_header("Content-Type","application/javascript; charset=utf-8");self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw);return
        if self.path == "/api/health":
            self.send_json(200, {"service": "local-file-chat", "project": str(settings.PROJECT), "database": str(settings.SEMANTIC_DB), "model": settings.MODEL, "chat_protocol":2, "provider":"local-default-or-explicit-config"})
            return
        if self.path == "/api/duplicates/status":
            with DUPLICATE_LOCK:
                self.send_json(200, {key: value for key, value in DUPLICATE_JOB.items() if key != "cancel"})
            return
        if self.path == "/api/index/status":
            self.send_json(200, index_status())
            return
        if self.path == "/api/tasks/status":
            self.send_json(200, task_status.snapshot())
            return
        if self.path == "/api/bubble/config":
            self.send_json(200, {'web_configured': bubble_chat.bubble_web.configured(), 'web_source': '百度搜索摘要', 'usage': __import__('web_usage').status() if bubble_chat.bubble_web.configured() else None})
            return
        if self.path == "/api/preferences":
            data = preferences.load()
            data["scopes"] = preferences.custom_scopes()
            self.send_json(200, data)
            return
        if self.path not in ("/", "/compact", "/bubble"):
            self.send_error(404)
            return
        payload = ((settings.PROJECT/'bubble.html').read_text() if self.path=='/bubble' else HTML).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def emit_stream(self, obj):
        cancel = getattr(self, "chat_cancel", None)
        if cancel and cancel.is_set() and obj.get("type") not in ("done","error"):
            raise file_tools.SearchCancelled()
        try:
            self.wfile.write((json.dumps(obj, ensure_ascii=False, default=str)+"\n").encode())
            self.wfile.flush()
        except (BrokenPipeError,ConnectionResetError,OSError):
            if cancel:cancel.set()
            raise file_tools.SearchCancelled()

    def do_POST(self):
        if self.path not in ("/api/chat", "/api/action", "/api/index", "/api/preferences",
                             "/api/saved-search", "/api/preview", "/api/related", "/api/duplicates",
                             "/api/duplicates/cancel", "/api/duplicates/plan", "/api/duplicates/confirm",
                             "/api/chat/cancel", "/api/chat/clear", "/api/index/cancel",
                             "/api/bubble/calendar/update/select", "/api/bubble/calendar/update/confirm", "/api/bubble/calendar/update/cancel", "/api/bubble/calendar/offer", "/api/bubble/calendar/more", "/api/bubble/calendar/refresh", "/api/bubble/calendar/confirm", "/api/bubble/calendar/cancel", "/api/bubble/calendar/revise", "/api/bubble/session", "/api/bubble/chat", "/api/bubble/cancel", "/api/bubble/reset", "/api/files/search"):
            self.send_error(404)
            return
        if self.headers.get("Host") != f"{settings.HOST}:{settings.PORT}":
            self.send_json(403, {"error": "请通过本地地址访问页面"})
            return
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{settings.HOST}:{settings.PORT}":
            self.send_json(403, {"error": "仅允许从本地页面发送请求"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 65536 or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self.send_json(400, {"error": "请求格式不正确"})
                return
            body = json.loads(self.rfile.read(size))
            if self.path in ('/api/bubble/calendar/update/select','/api/bubble/calendar/update/confirm','/api/bubble/calendar/update/cancel','/api/bubble/session','/api/bubble/chat','/api/bubble/cancel','/api/bubble/reset','/api/bubble/calendar/offer','/api/bubble/calendar/more','/api/bubble/calendar/refresh','/api/bubble/calendar/confirm','/api/bubble/calendar/cancel','/api/bubble/calendar/revise'):
                bubble_chat.handle(self,body)
                return
            if self.path == "/api/files/search":
                if str(body.get('session_id','')).startswith('bubble-'):raise ValueError('闲聊与文件助手会话隔离')
                key, state = session_for(body.get('session_id'))
                token=task_status.begin(uuid.uuid4().hex,"file")
                try:
                    task_status.stage(token,"searching")
                    event = structured_search(body, state)
                finally:task_status.finish(token)
                self.send_json(200, {'session_id':key,'events':[event],'answer':listing_reply(event)})
                return
            if self.path == "/api/index/cancel":
                self.send_json(200, cancel_index())
                return
            if self.path == "/api/index":
                self.send_json(200, start_index(body.get("scope", "all")))
                return
            if self.path == "/api/duplicates":
                self.send_json(200, start_duplicates(body.get("scope", "all")))
                return
            if self.path == "/api/duplicates/cancel":
                self.send_json(200, cancel_duplicates())
                return
            if self.path == "/api/duplicates/plan":
                self.send_json(200, prepare_duplicate_cleanup(body.get("selected")))
                return
            if self.path == "/api/duplicates/confirm":
                self.send_json(200, confirm_duplicate_cleanup(body.get("token")))
                return
            if self.path == "/api/chat/cancel":
                request_id = body.get("request_id")
                if not isinstance(request_id, str) or len(request_id) > 64:
                    raise ValueError("任务标识不正确")
                self.send_json(200, {"session_id": cancel_chat(request_id, body.get("session_id"))})
                return
            if self.path == "/api/chat/clear":
                self.send_json(200, {"session_id": clear_chat(body.get("session_id"), body.get("request_id"))})
                return
            if self.path == "/api/preferences":
                operation = body.get("operation")
                if operation == "add_scope":
                    key = preferences.add_scope(body.get("name"), body.get("path"))
                elif operation == "remove_scope":
                    preferences.remove_scope(body.get("key"))
                    key = None
                elif operation == "remove_search":
                    preferences.remove_search(body.get("key"))
                    key = None
                elif operation == "auto_index":
                    preferences.set_auto_index(body.get("enabled"))
                    key = None
                else:
                    raise ValueError("未知设置操作")
                self.send_json(200, {"key": key, "preferences": preferences.load()})
                return
            session_id = body.get("session_id")
            if session_id is not None and (not isinstance(session_id, str) or len(session_id) > 64):
                self.send_json(400, {"error": "会话标识不正确"})
                return
            if self.path in ("/api/action", "/api/preview", "/api/related", "/api/saved-search"):
                if self.path == "/api/saved-search" and body.get("operation") == "run" and session_id is None:
                    session_id, _ = session_for(None)
                if session_id not in SESSIONS:
                    raise ValueError("未知会话")
                state = SESSIONS[session_id]
                if self.path == "/api/saved-search":
                    with state.lock:
                        if body.get("operation") == "save":
                            key = preferences.save_search(body.get("name"), state.last_search_query)
                            self.send_json(200, {"key": key})
                        elif body.get("operation") == "run":
                            event = saved_search(state, body.get("key"))
                            self.send_json(200, {"events": [event], "answer": f"找到 {event['total']} 个匹配文件。",
                                                 "session_id": session_id})
                        else:
                            raise ValueError("未知搜索操作")
                    return
                if session_id not in SESSIONS or body.get("action") not in ("open_file", "reveal_in_finder"):
                    if self.path == "/api/action":
                        raise ValueError("未知操作")
                path = body.get("path")
                if not isinstance(path, str):
                    raise ValueError("文件路径不正确")
                with state.lock:
                    # Only a file returned by a prior search in this session is actionable.
                    if path not in {item["path"] for item in state.last_search_results}:
                        raise ValueError("文件不属于当前搜索结果")
                    if self.path == "/api/preview":
                        self.send_json(200, file_reader.preview_file(path))
                    elif self.path == "/api/related":
                        data = file_relationships.find_similar(path)
                        state.last_search_query = {"similar_to": path}
                        state.search_coverage={k:data[k] for k in ("partial","truncated","note","indexed_scope_files","scanned_files") if k in data}
                        state.last_search_results = data["results"]
                        state.current_filtered_results = list(data["results"])
                        state.filters.clear()
                        state.selected_file = None
                        self.send_json(200, {"events": [{"tool": "find_similar_files", **data}],
                                             "answer": f"找到 {data['total']} 个相似文件。"})
                    else:
                        op = file_tools.open_file if body["action"] == "open_file" else file_tools.reveal_in_finder
                        result = op(path)
                        state.selected_file = path
                        self.send_json(200, {"file": result})
                return
            question = body.get("question")
            if not isinstance(question, str) or not question.strip() or len(question) > 4000:
                self.send_json(400, {"error": "问题格式不正确"})
                return
            search_scope = body.get("scope", "all")
            if not isinstance(search_scope, str):
                raise ValueError("搜索范围不正确")
            file_tools.roots_for(search_scope)
            key, state = session_for(session_id)
            request_id = body.get("request_id")
            if request_id is not None and (not isinstance(request_id, str) or len(request_id) > 64):
                raise ValueError("任务标识不正确")
            cancel = threading.Event()
            if request_id is not None:
                with CHAT_JOBS_LOCK:
                    for expired, when in list(CHAT_CANCELLED.items()):
                        if time.time() - when > 60:
                            del CHAT_CANCELLED[expired]
                    if request_id in CHAT_JOBS:
                        raise ValueError("任务正在进行")
                    if CHAT_CANCELLED.pop(request_id, None) is not None:
                        cancel.set()
                    for previous in CHAT_JOBS.values():
                        if previous["session_id"] == key:
                            previous["cancel"].set()
                            state=fork_session(state)
                            with SESSIONS_LOCK:SESSIONS[key]=state
                            break
                    CHAT_JOBS[request_id] = {"cancel": cancel, "state": state, "session_id": key}
            self.chat_request=request_id or uuid.uuid4().hex
            self.chat_cancel=cancel
            self.streaming=body.get("stream") is True
            if self.streaming:
                self.send_response(200)
                self.send_header("Content-Type","application/x-ndjson; charset=utf-8")
                self.send_header("Cache-Control","no-store")
                self.send_header("Connection","close")
                self.end_headers()
                self.close_connection=True
            trace=diagnostics.Trace(self.chat_request,self.emit_stream if self.streaming else None)
            diagnostics.local.trace=trace
            queued=time.perf_counter()
            outcome="error"
            try:
                trace.emit("session",session_id=key)
                with state.lock, diagnostics.progress(trace):
                    trace.timings["queue"]=time.perf_counter()-queued
                    file_tools._current_search.cancel_event = cancel
                    file_tools._current_search.deadline = time.monotonic() + 180
                    try:
                        result, events = answer(question.strip(), state, search_scope, fast=True)
                        file_tools.check_cancelled()
                        outcome="error" if getattr(trace,"had_error",False) else "complete"
                        trace.stage("idle" if outcome=="error" else "complete")
                    finally:
                        file_tools._current_search.cancel_event = None
                        file_tools._current_search.deadline = None
            finally:
                trace.save("cancelled" if cancel.is_set() else outcome)
                diagnostics.local.trace=None
                if request_id is not None:
                    with CHAT_JOBS_LOCK:
                        CHAT_JOBS.pop(request_id, None)
                if cancel.is_set():
                    with SESSIONS_LOCK:
                        if SESSIONS.get(key) is state:
                            SESSIONS.pop(key, None)
            self.send_json(200, {"answer": result, "events": events, "session_id": key, "metrics":trace.summary(), "task_state":"idle" if outcome=="error" else "complete"})
        except file_tools.SearchCancelled:
            self.send_json(200, {"cancelled": True})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})
        except (OSError, RuntimeError) as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            print(f"模型调用失败：{exc}")
            self.send_json(500, {"error": "模型调用失败，请确认 Ollama 已启动并已下载 qwen3.5:9b；详情见终端。"})


if __name__ == "__main__":
    settings.SEARCH_ROOTS["workspace"].mkdir(exist_ok=True)
    threading.Thread(target=automatic_index_loop, daemon=True).start()
    server = ThreadingHTTPServer((settings.HOST, settings.PORT), Handler)
    print(f"打开浏览器访问 http://{settings.HOST}:{settings.PORT} （Ctrl+C 退出）")
    server.serve_forever()
