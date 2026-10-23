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

import backend.providers.model_providers as model_providers
import backend.diagnostics as diagnostics
chat = model_providers.configured_provider().chat

import backend.files.file_reader as file_reader
import backend.files.file_relationships as file_relationships
import backend.files.file_tools as file_tools
import backend.preferences as preferences
import backend.files.semantic_search as semantic_search
import re
import backend.files.search_retrieval as search_retrieval
import backend.settings as settings
import backend.files.search_intent as search_intent
import backend.assistant.bubble_chat as bubble_chat
import backend.assistant.task_status as task_status


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
            response=(model_providers.CURRENT.get() or chat)(cancel=getattr(file_tools._current_search,'cancel_event',None),**kwargs)
        except __import__('backend.providers.model_dispatch', fromlist=['*']).Cancelled:
            raise file_tools.SearchCancelled() from None
    trace=getattr(diagnostics.local,'trace',None)
    if trace:
        trace.model_calls.append({"stage":stage, **{k:getattr(response,k,0)/1e9 for k in ('load_duration','prompt_eval_duration','eval_duration') if isinstance(getattr(response,k,None),(int,float))},
                                  'thinking_chars':len(getattr(response.message,'thinking','') or '')})
    return response


def answer(question, state, search_scope="all", fast=False, allowed_tools=None, hard_directory=None, discovery=False, context_messages=None, provided_plan=None):
    """Keep structured state in Python; LLM chooses tools and phrases answers."""
    file_tools.check_cancelled()
    tool_errors = []
    with diagnostics.span("condition_extraction"):
        constraints = search_intent.Constraints(question, search_scope)
        if hard_directory is not None:constraints.directory=hard_directory
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
        params = dict(keyword=keyword, scope=scoped(scope, directory), extensions=extensions,
                      modified_after=modified_after, modified_before=modified_before,
                      min_size=min_size, max_size=max_size, sort_by=sort_by, limit=limit)
        params.update(constraints.metadata({k:params[k] for k in ('modified_after','modified_before','min_size','max_size')}))
        if constraints.identifiers():params.update(required_identifiers=constraints.identifiers(),identifier_groups=constraints.identifier_groups())
        if constraints.media_topic():params['topic']=constraints.media_topic()
        with diagnostics.span("file_retrieval", "searching"):
            data = search_retrieval.find_files(**params)
        # The validated unified contract uses find_files for names/IDs. Keep
        # hybrid ranking/embedding, but body-only candidates cannot satisfy it.
        if provided_plan and provided_plan[0]=='find_files' and keyword and data.get('retrieval',{}).get('mode')=='hybrid':
            results=data['results']
            data['results']=[item for item in results if 'name' in item.get('candidate_sources',[])]
            data['total']=len(data['results'])
            if len(results)!=len(data['results']):
                data['note']=(data.get('note','')+'\n名称条件已保留；仅正文相关而无名称/目录依据的候选未展示。').strip()
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
                       max_size: int | None = None, sort_by: str | None = None,
                       clear_fields: list[str] | None = None) -> dict:
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
        clear_fields = clear_fields or []
        mutable={'extensions','modified_after','modified_before','min_size','max_size','sort_by'}
        if not isinstance(clear_fields,list) or any(k not in mutable for k in clear_fields):
            raise ValueError("只能清除明确的后续筛选条件，原搜索硬约束保持不变")
        if any(k!='sort_by' and k in state.last_search_query and state.last_search_query[k] is not None for k in clear_fields):
            raise ValueError("该条件属于原搜索范围；请发起新的搜索，旧结果保持不变")
        for key in clear_fields:state.filters.pop(key,None)
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
    if allowed_tools is not None:tools={k:v for k,v in tools.items() if k in allowed_tools}
    def execute_tool(name, args):
        if name not in tools:raise ValueError("当前工具入口不允许该操作，请在完整文件助手明确操作")
        trace=getattr(diagnostics.local,"trace",None)
        if trace:trace.tool_calls += 1
        with diagnostics.span("tool_execution", "searching"):
            return tools[name](**args)
    plan = provided_plan or (search_intent.fast_plan(question, bool(state.last_search_query)) if fast else None)
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
    if fast and (discovery or (not state.last_search_query and not re.search(r'时间|日期|大小|大于|小于|最近|之前|之后|before|after|size|modified|larger|smaller|读取|打开|定位|讲什么|写了什么|总结',question,re.I) and re.search(r'找|查找|搜索|寻找|\bsearch\b|\bfind\b',question,re.I))):
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
    messages = [{"role": "system", "content": system}, *(context_messages or []), *state.history[-6:],
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


import backend.assistant.assistant_files as assistant_files
assistant_files.bind(session=Session,answer=answer,snapshot=search_snapshot,visible=visible,clone=fork_session,session_for=session_for,sessions=SESSIONS,lock=SESSIONS_LOCK)

HTML = (settings.FRONTEND / "index.html").read_text()


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
        if self.path=="/reminder-notifications":
            raw=(settings.FRONTEND/"reminder-notifications.html").read_bytes();self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.send_header("Cache-Control","no-store");self.send_header("Referrer-Policy","no-referrer");self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'");self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw);return
        if self.path in ("/speech-view.js","/persona-view.js","/shared-reminder.js","/daily-settings.js","/unified-web.js","/calendar-view.js","/file-qa-view.js", "/memory-view.js"):
            raw=(settings.FRONTEND/self.path[1:]).read_bytes();self.send_response(200);self.send_header("Content-Type","application/javascript; charset=utf-8");self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw);return
        if self.path == "/input-guard.js":
            raw=(settings.FRONTEND/"input-guard.js").read_bytes()
            self.send_response(200);self.send_header("Content-Type","application/javascript; charset=utf-8");self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw);return
        if self.path == "/api/health":
            self.send_json(200, {"service": "local-file-chat", "project": str(settings.PROJECT), "database": str(settings.SEMANTIC_DB), "model": settings.MODEL, "chat_protocol":2, "assistant_protocol":3, "file_qa_protocol":1,"web_summary_protocol":1,"memory_contract_protocol":4, "desktop_model_protocol":1, "provider":"local-default-or-explicit-config"})
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
            self.send_json(200, {'web_configured': bubble_chat.bubble_web.configured(), 'web_source': '百度搜索摘要', 'usage': __import__('backend.providers.web_usage', fromlist=['*']).status() if bubble_chat.bubble_web.configured() else None})
            return
        if self.path == "/api/preferences":
            data = preferences.load()
            data["scopes"] = preferences.custom_scopes()
            self.send_json(200, data)
            return
        page_path=self.path.split("?",1)[0]
        if page_path not in ("/", "/compact", "/bubble"):
            self.send_error(404)
            return
        payload = ((settings.FRONTEND/'bubble.html').read_text() if page_path=='/bubble' else HTML).encode("utf-8")
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
        import backend.native_context as native_context
        self.received_clock = native_context.system_clock()
        if self.path in ('/api/bubble/speech','/api/assistant/speech'):
            if self.headers.get('Host')!=f'{settings.HOST}:{settings.PORT}' or self.headers.get('Origin')!=f'http://{settings.HOST}:{settings.PORT}':self.send_json(403,{'error':'语音仅允许本机同源页面主动操作。'});return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=1024 or self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('语音请求格式不正确。')
                body=json.loads(self.rfile.read(size))
                if not isinstance(body,dict):raise ValueError('语音请求格式不正确。')
                import macos.speech_native as speech_native
                self.send_json(200,speech_native.handle(body,'assistant' if '/assistant/' in self.path else 'bubble'))
            except (ValueError,TypeError,KeyError):self.send_json(400,{'error':str(__import__('sys').exc_info()[1])[:250]})
            except Exception:self.send_json(503,{'error':'本地语音暂不可用，草稿未改动。'})
            return
        if self.path in ('/api/bubble/reminders','/api/assistant/reminders'):
            origin=self.headers.get('Origin')
            if origin and origin!=f'http://{settings.HOST}:{settings.PORT}':self.send_json(403,{'error':'仅允许同源本地请求'});return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=8192 or self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('提醒请求格式不正确')
                body=json.loads(self.rfile.read(size))
                if not isinstance(body,dict):raise ValueError('提醒请求格式不正确')
                import backend.calendar.reminder_api as reminder_api
                self.send_json(200,reminder_api.handle(body,'assistant' if '/assistant/' in self.path else 'bubble'))
            except (ValueError,TypeError,KeyError):self.send_json(400,{'error':str(__import__('sys').exc_info()[1])[:250]})
            except Exception:self.send_json(503,{'error':'本机提醒保存或读取失败；没有宣称已完成。'})
            return

        if self.path=="/api/notifications/test-channel":
            if self.headers.get('Host')!=f'{settings.HOST}:{settings.PORT}' or self.headers.get('Origin')!=f'http://{settings.HOST}:{settings.PORT}':self.send_json(403,{'error':'仅允许原应用打开的本地同源验收页。'});return
            import backend.calendar.notification_test_api as channel
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=1024 or self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('测试请求格式不正确。')
                body=json.loads(self.rfile.read(size))
                if not isinstance(body,dict):raise ValueError('测试请求格式不正确。')
                self.send_json(200,channel.handle(body,self.headers.get('X-Reminder-Test')))
            except channel.Refused as e:self.send_json(403,{'error':str(e)})
            except channel.Limited as e:self.send_json(429,{'error':str(e)})
            except (ValueError,TypeError):self.send_json(400,{'error':'测试操作未执行，请检查入口和操作。'})
            except Exception:self.send_json(503,{'error':'通知操作未确认，没有自动重试。'})
            return

        if not self.path.startswith("/api/assistant/") and self.path not in ("/api/chat", "/api/action", "/api/index", "/api/preferences",
                             "/api/saved-search", "/api/preview", "/api/related", "/api/duplicates",
                             "/api/duplicates/cancel", "/api/duplicates/plan", "/api/duplicates/confirm",
                             "/api/chat/cancel", "/api/chat/clear", "/api/index/cancel",
                             "/api/bubble/calendar/delete/preview", "/api/bubble/calendar/delete/confirm", "/api/bubble/calendar/delete/cancel", "/api/bubble/calendar/update/choose", "/api/bubble/calendar/update/deselect", "/api/bubble/calendar/update/select", "/api/bubble/calendar/update/confirm", "/api/bubble/calendar/update/cancel", "/api/bubble/calendar/offer", "/api/bubble/calendar/more", "/api/bubble/calendar/refresh", "/api/bubble/calendar/confirm", "/api/bubble/calendar/cancel", "/api/bubble/calendar/revise", "/api/bubble/session", "/api/bubble/chat", "/api/bubble/cancel", "/api/bubble/reset", "/api/files/search", "/api/files/current", "/api/bubble/files/select", "/api/bubble/files/qa", "/api/bubble/files/handoff", "/api/bubble/memory", "/api/bubble/model", "/api/bubble/model/compare"):
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
            if self.path=="/api/chat" and not getattr(settings,"LEGACY_WEB_CHAT_ENABLED",False):
                import backend.assistant.unified_web as unified_web
                key=body.get('session_id')
                if key not in unified_web.SESSIONS or unified_web.resolve(key)[2].selected_provider!='ollama':
                    raise ValueError('旧文件正文入口只用于主动选择的本地Qwen网页会话；默认请使用统一助手入口。')
            if self.path.startswith("/api/assistant/"):
                __import__('backend.assistant.unified_web', fromlist=['*']).handle(self,body);return
            if self.path in ('/api/bubble/memory','/api/bubble/model','/api/bubble/model/compare'):
                bubble_chat.handle(self,body);return
            if self.path.startswith('/api/bubble/files/'):
                bubble_chat.handle(self,body);return
            if self.path=='/api/files/current':
                key=body.get('session_id')
                if key not in SESSIONS:raise ValueError('文件会话已失效')
                state=SESSIONS[key]
                with state.lock:event=search_snapshot(state,{'tool':'find_files','results':visible(state),'total':len(state.current_filtered_results),**state.search_coverage},fresh=False)
                self.send_json(200,{'event':event,'answer':listing_reply(event)});return
            if self.path in ('/api/bubble/calendar/delete/preview','/api/bubble/calendar/delete/confirm','/api/bubble/calendar/delete/cancel','/api/bubble/calendar/update/choose','/api/bubble/calendar/update/deselect','/api/bubble/calendar/update/select','/api/bubble/calendar/update/confirm','/api/bubble/calendar/update/cancel','/api/bubble/session','/api/bubble/chat','/api/bubble/cancel','/api/bubble/reset','/api/bubble/calendar/offer','/api/bubble/calendar/more','/api/bubble/calendar/refresh','/api/bubble/calendar/confirm','/api/bubble/calendar/cancel','/api/bubble/calendar/revise'):
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
                __import__('backend.assistant.unified_web', fromlist=['*']).import_files(key)
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
                            __import__('backend.assistant.unified_web', fromlist=['*']).import_files(session_id)
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
                        preview=file_reader.preview_file(path)
                        state.selected_file=path
                        __import__('backend.assistant.unified_web', fromlist=['*']).import_files(session_id,selected=True)
                        self.send_json(200,preview)
                    elif self.path == "/api/related":
                        data = file_relationships.find_similar(path)
                        state.last_search_query = {"similar_to": path}
                        state.search_coverage={k:data[k] for k in ("partial","truncated","note","indexed_scope_files","scanned_files") if k in data}
                        state.last_search_results = data["results"]
                        state.current_filtered_results = list(data["results"])
                        state.filters.clear()
                        state.selected_file = None
                        __import__('backend.assistant.unified_web', fromlist=['*']).import_files(session_id)
                        self.send_json(200, {"events": [{"tool": "find_similar_files", **data}],
                                             "answer": f"找到 {data['total']} 个相似文件。"})
                    else:
                        op = file_tools.open_file if body["action"] == "open_file" else file_tools.reveal_in_finder
                        result = op(path)
                        state.selected_file = path
                        __import__('backend.assistant.unified_web', fromlist=['*']).import_files(session_id,selected=True)
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


import backend.calendar.calendar_reminders as calendar_reminders
calendar_reminders.start()
import backend.calendar.reminder_test_service as reminder_test_service
reminder_test_service.resume()

def main():
    settings.SEARCH_ROOTS["workspace"].mkdir(exist_ok=True)
    threading.Thread(target=automatic_index_loop, daemon=True).start()
    server = ThreadingHTTPServer((settings.HOST, settings.PORT), Handler)
    print(f"打开浏览器访问 http://{settings.HOST}:{settings.PORT} （Ctrl+C 退出）")
    server.serve_forever()


if __name__ == "__main__":
    main()
