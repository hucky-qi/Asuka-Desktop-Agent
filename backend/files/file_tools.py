"""Bounded, read-only filesystem search and user-requested open/reveal actions."""

import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import backend.settings as settings
import backend.preferences as preferences


class SearchCancelled(Exception):
    """A user stopped the current read-only search."""


_current_search = threading.local()


def check_cancelled():
    deadline = getattr(_current_search, "deadline", None)
    if deadline is not None and time.monotonic() > deadline:
        raise SearchCancelled("任务达到时间上限")
    event = getattr(_current_search, "cancel_event", None)
    if event is not None and event.is_set():
        raise SearchCancelled()


TYPE_ALIASES = settings.FILE_TYPE_ALIASES

def category_query(text):
    """Recognize a broad file-type request without treating the type as a filename."""
    words = str(text or "").casefold().strip()
    words = re.sub(r"[\s，。！？?!]+|帮我|请|给我|找一下|查找|找|搜索|一下|看看|所有|全部|本地|文件|的", "", words)
    return words if words in TYPE_ALIASES else None


def spread_directories(results, roots):
    """Avoid filling a broad type search's first page from just one folder."""
    buckets = {}
    for item in results:
        path = Path(item["path"])
        root = next((r for r in roots if str(path).startswith(str(r)+os.sep)), None)
        if root is None:
            key = str(path.parent)
        else:
            relative = path.relative_to(root)
            key = str(root / relative.parts[0]) if len(relative.parts) > 1 else str(root)
        buckets.setdefault(key, deque()).append(item)
    ordered = []
    while buckets:
        for key in list(buckets):
            ordered.append(buckets[key].popleft())
            if not buckets[key]:
                del buckets[key]
    return ordered


def normalize_extensions(extensions=None):
    if not extensions:
        return set()
    if isinstance(extensions, str):
        extensions = re.split(r"[,，/\s]+", extensions)
    if not isinstance(extensions, (list, tuple, set)):
        raise ValueError("extensions 必须是字符串或列表")
    result = set()
    for value in extensions:
        name = str(value).strip().lower().lstrip(".")
        if not name:
            continue
        result.update("." + ext for ext in TYPE_ALIASES.get(name, [name]))
    return result


def roots_for(scope="all"):
    if isinstance(scope, str) and scope.startswith("folder:"):
        return [preferences.allowed_subfolder(scope[7:])]
    scope = (scope or "all").lower().strip()
    if scope == "all":
        return list(settings.SEARCH_ROOTS.values())
    custom = preferences.custom_scopes()
    if scope in custom:
        return [Path(custom[scope]["path"])]
    if scope not in settings.SEARCH_ROOTS:
        raise ValueError("未知搜索范围：" + scope)
    return [settings.SEARCH_ROOTS[scope]]


def is_path_allowed(path):
    """Resolve symlinks first; reject traversal and ignored directories."""
    try:
        candidate = Path(path).expanduser().resolve(strict=True)
        if not candidate.is_file() or Path(path).is_symlink():
            return False
        for root in settings.SEARCH_ROOTS.values():
            try:
                real_root = root.resolve(strict=True)
                prefix = str(real_root) + os.sep
                if not str(candidate).startswith(prefix):continue
                relative = Path(str(candidate)[len(prefix):])
            except (ValueError, OSError):
                continue
            if any(part.startswith(".") or part in settings.IGNORE_DIRS for part in relative.parts):
                return False
            return True
    except (OSError, RuntimeError, ValueError):
        pass
    return False


def checked_file(path):
    if not is_path_allowed(path):
        raise ValueError("文件不在允许访问的目录中，或文件已移动")
    return Path(path).expanduser().resolve(strict=True)


def walk_files(scope="all", extensions=None, max_files=None, stats=None):
    stats = stats if stats is not None else {}
    stats.setdefault("permission_errors", [])
    stats.setdefault("missing_roots", [])
    stats.setdefault("partial", False)
    allowed_exts = normalize_extensions(extensions)
    seen = set()
    count = 0
    for root in roots_for(scope):
        check_cancelled()
        if not root.is_dir():
            stats["missing_roots"].append(str(root))
            continue
        def failed(error):
            stats["permission_errors"].append(str(error.filename))
            stats["partial"] = True
        for current, dirs, files in os.walk(root, followlinks=False, onerror=failed):
            check_cancelled()
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in settings.IGNORE_DIRS
                       and not (Path(current) / d).is_symlink()]
            for name in files:
                check_cancelled()
                if name.startswith("."):
                    continue
                path = Path(current) / name
                if path.is_symlink() or (allowed_exts and path.suffix.lower() not in allowed_exts):
                    continue
                count += 1
                stats["scanned_files"] = count
                if count > (max_files or settings.MAX_FILES_SCANNED):
                    stats["partial"] = True
                    stats["scan_limit"] = max_files or settings.MAX_FILES_SCANNED
                    return
                absolute = str(path)
                if absolute not in seen and is_path_allowed(path):
                    seen.add(absolute)
                    yield path


def file_info(path, score=0):
    file = checked_file(path)
    stat = file.stat()
    # On macOS st_birthtime is creation time; on other platforms ctime may be metadata change.
    created = getattr(stat, "st_birthtime", None)
    return {
        "name": file.name, "path": str(file), "extension": file.suffix.lower().lstrip("."),
        "size": stat.st_size, "modified_time": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
        "created_time": datetime.fromtimestamp(created).astimezone().isoformat(timespec="seconds") if created else None,
        "score": round(float(score), 2),
    }


def tokens(text):
    return re.findall(r"[a-z]+|\d+|[\u4e00-\u9fff]+", text.casefold())


def _relevance_basic(keyword, path, root=None):
    keyword = keyword.strip().casefold()
    if not keyword:
        return 1.0
    stem = path.stem.casefold()
    name = path.name.casefold()
    compact_name = "".join(tokens(stem))
    compact_query = "".join(tokens(keyword))
    query_tokens = [token for token in tokens(keyword) if len(token) > 1 or token.isdigit()]
    file_tokens = tokens(stem)
    parent_string=str(path.parent)
    root_string=str(root) if root else ""
    parent = (parent_string[len(root_string)+1:] if root and parent_string.startswith(root_string+os.sep) else "" if root and parent_string==root_string else parent_string).casefold()
    if name == keyword or stem == keyword:
        return 120.0
    if compact_query and compact_name.startswith(compact_query):
        return 105.0
    if query_tokens and all(t in file_tokens for t in query_tokens):
        if any(t.isdigit() and re.match(r"^[a-z]{2,8}[-_ ]?" + re.escape(t) + r"(?:\D|$)", stem)
               for t in query_tokens):
            return round(101 + 1 / (1 + len(name)), 2)
        return round(98 + 1 / (1 + len(name)), 2)
    if keyword in name or (compact_query and compact_query in compact_name):
        return 90.0
    if query_tokens and all(any(t in ft for ft in file_tokens) for t in query_tokens):
        return 82.0
    ratio = SequenceMatcher(None, compact_query, compact_name).ratio() if compact_query else 0
    # Typo tolerance is not thematic evidence. Require a single insertion,
    # deletion or substitution in a whole token, rather than visual similarity
    # to an arbitrary filename (e.g. shared suffixes).
    def one_edit(a, b):
        if abs(len(a)-len(b)) > 1:return False
        if len(a)==len(b):return sum(x!=y for x,y in zip(a,b))<=1
        if len(a)>len(b):a,b=b,a
        for i in range(len(b)):
            if b[:i]+b[i+1:]==a:return True
        return False
    typo = len(query_tokens)==1 and len(compact_query)>=5 and any(
        one_edit(compact_query, token) for token in file_tokens)
    if ratio >= .85 and typo:
        return round(35 + ratio * 40, 2)
    parent_tokens = tokens(parent)
    aliases = {"assignment": ("assignment", "assign", "assgn", "asgn", "ass1", "ass2")}
    if query_tokens and all(any(variant in token for variant in aliases.get(t, (t,))
                               for token in [*file_tokens, *parent_tokens]) for t in query_tokens):
        return 68.0
    if query_tokens and all(any(t in pt for pt in parent_tokens) for t in query_tokens):
        return 32.0
    if keyword in str(path).casefold():
        return 15.0
    return 0.0


def relevance(keyword, path, root=None):
    score = _relevance_basic(keyword, path, root)
    for term, variants in settings.SEARCH_SYNONYMS.items():
        if term in keyword.casefold():
            score = max(score, *(_relevance_basic(variant, path, root) * .9 for variant in variants))
    return score


def topic_evidence(path, topic, root=None):
    words=settings.TOPIC_MEDIA_EVIDENCE.get(topic,())
    relative=path.relative_to(root) if root else path
    label=str(relative).casefold()
    lexical=any(word in label if any('\u4e00'<=c<='\u9fff' for c in word)
               else re.search(r'(?<![a-z])'+re.escape(word)+r'(?![a-z])',label) for word in words)
    if lexical:return True
    if root:
        categories=settings.TOPIC_MEDIA_CATEGORY_DIRS.get(topic,())
        # Exact directory component, never a filename substring such as
        # animation_project.mp4. Nested project/src/animation is not a category.
        if len(relative.parts)>1 and relative.parts[0].casefold() in categories:return True
        # Explicit folder scopes may start at/below the category directory.
        for authorized in roots_for('all'):
            try: scoped=Path(root).relative_to(authorized)
            except ValueError:continue
            scoped_label=str(scoped).casefold()
            if any(word in scoped_label if any('\u4e00'<=c<='\u9fff' for c in word)
                   else re.search(r'(?<![a-z])'+re.escape(word)+r'(?![a-z])',scoped_label) for word in words):return True
            if scoped.parts and scoped.parts[0].casefold() in categories:return True
    return False


def identifier_evidence(text, identifiers):
    from backend.files.identifier_intent import identifier_matches
    return all(identifier_matches(text,value) for value in identifiers or ())


def path_identifier_evidence(path, identifiers):
    path=Path(path)
    from backend.files.identifier_intent import identifier_matches
    return all(identifier_matches(path.stem,value) or any(
        re.fullmatch((r'(?:[a-z]+)?' if str(value).isdigit() else '')+re.escape(str(value)),part,re.I)
        for part in path.parent.parts) for value in identifiers or ())


def search_identifier_evidence(path, text, identifiers):
    return all(path_identifier_evidence(path,[value]) or identifier_evidence(text,[value]) for value in identifiers or ())



def parse_date(value, end_of_day=False):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if end_of_day and len(str(value)) == 10:
            parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed.timestamp()
    except ValueError as exc:
        raise ValueError("日期需使用 ISO 格式，例如 2026-09-23") from exc


def stat_matches(stat, filters=None):
    f=filters or {}
    after,before=parse_date(f.get('modified_after')),parse_date(f.get('modified_before'),True)
    return not ((after is not None and stat.st_mtime<after) or (before is not None and stat.st_mtime>before)
                or (f.get('min_size') is not None and stat.st_size<int(f['min_size']))
                or (f.get('max_size') is not None and stat.st_size>int(f['max_size'])))


def filter_results(results, extensions=None, modified_after=None, modified_before=None,
                   min_size=None, max_size=None, sort_by="relevance"):
    exts = normalize_extensions(extensions)
    after, before = parse_date(modified_after), parse_date(modified_before, end_of_day=True)
    lo, hi = int(min_size) if min_size is not None else None, int(max_size) if max_size is not None else None
    if lo is not None and lo < 0 or hi is not None and hi < 0:
        raise ValueError("文件大小不能为负数")
    filtered = []
    for item in results:
        if not is_path_allowed(item["path"]):
            continue
        stamp = datetime.fromisoformat(item["modified_time"]).timestamp()
        if exts and "." + item["extension"] not in exts:
            continue
        if after is not None and stamp < after or before is not None and stamp > before:
            continue
        if lo is not None and item["size"] < lo or hi is not None and item["size"] > hi:
            continue
        filtered.append(item)
    if sort_by == "modified_desc":
        filtered.sort(key=lambda x: (x["modified_time"], x["score"]), reverse=True)
    elif sort_by == "modified_asc":
        filtered.sort(key=lambda x: (x["modified_time"], x["score"]))
    elif sort_by == "size_desc":
        filtered.sort(key=lambda x: (x["size"], x["score"]), reverse=True)
    elif sort_by == "relevance":
        filtered.sort(key=lambda x: (-x["score"], -datetime.fromisoformat(x["modified_time"]).timestamp(), x["path"]))
    else:
        raise ValueError("未知排序方式")
    return filtered


def find_files(keyword="", scope="all", extensions=None, modified_after=None,
               modified_before=None, min_size=None, max_size=None, sort_by="relevance", limit=10, topic=None, required_identifiers=None, identifier_groups=None):
    """Search local filenames, rank matches, and filter by metadata."""
    keyword = str(keyword or "").strip()
    if len(keyword) > 100:
        raise ValueError("关键词过长")
    if required_identifiers is None:
        from backend.files.identifier_intent import parse
        plan=parse(keyword)
        required_identifiers=list(plan.identifiers)
        identifier_groups=plan.resolved()
    category = category_query(keyword)
    if category:
        # An explicit extension filter further narrows this category.
        selected = normalize_extensions(category)
        selected &= normalize_extensions(extensions) if extensions else selected
        if not selected:
            return {"results": [], "total": 0, "limit": max(1, min(int(limit), settings.MAX_LIMIT)), "truncated": False}
        extensions = [ext.lstrip(".") for ext in selected]
        keyword = ""
    limit = max(1, min(int(limit), settings.MAX_LIMIT))
    results = []
    coverage = {}
    roots = roots_for(scope)
    for path in walk_files(scope, extensions, stats=coverage):
        check_cancelled()
        try:
            root = next((r for r in roots if str(path).startswith(str(r)+os.sep)), None)
            if topic and not topic_evidence(path, topic, root):continue
            if required_identifiers:
                from backend.files.identifier_intent import satisfied
                matched=[value for value in required_identifiers if path_identifier_evidence(path,[value])]
                if not satisfied(matched,identifier_groups if identifier_groups is not None else (tuple(required_identifiers),)):continue
            score = relevance(keyword, path, root)
            if required_identifiers:score=max(score,35)
            if score:
                if root == settings.SEARCH_ROOTS.get("workspace"):
                    score += 0.2
                item = file_info(path, score)
                if keyword and _relevance_basic(keyword, path, root) == 0:
                    item["evidence_source"] = "文件名或目录名的同义词匹配"
                if topic:
                    item["evidence_source"] = "类型符合；文件名或分类目录提供主题线索，未识别图片或视频画面"
                results.append(item)
        except (OSError, ValueError):
            continue
    results = filter_results(results, extensions, modified_after, modified_before, min_size, max_size, sort_by)
    image_types = normalize_extensions("图片")
    chosen_types = normalize_extensions(extensions)
    if not keyword and chosen_types and chosen_types <= image_types and sort_by == "relevance":
        results = spread_directories(results, roots)
    return {"results": results[:settings.MAX_STORED_RESULTS], "total": len(results),
            "limit": limit, "truncated": len(results) > settings.MAX_STORED_RESULTS,
            **coverage, "note": ("部分目录无权限，结果不完整" if coverage.get("permission_errors") else
                                  "达到扫描上限，结果不完整" if coverage.get("partial") else
                                  "部分目录不存在" if coverage.get("missing_roots") else "")}


def open_file(path):
    """Open a validated file in the macOS default app."""
    file = checked_file(path)
    if sys.platform != "darwin":
        raise RuntimeError("打开文件目前只支持 macOS")
    subprocess.run(["open", str(file)], check=True, timeout=15)
    return file_info(file)


def reveal_in_finder(path):
    """Select a validated file in Finder."""
    file = checked_file(path)
    if sys.platform != "darwin":
        raise RuntimeError("Finder 定位目前只支持 macOS")
    subprocess.run(["open", "-R", str(file)], check=True, timeout=15)
    return file_info(file)
