"""Exact duplicate discovery, confirmed trash cleanup, and indexed similarity."""

import hashlib
import sqlite3
import sys
from array import array
from collections import defaultdict
from contextlib import closing
from pathlib import Path

import file_tools
import settings


def digest(path, cancel_event=None):
    file = file_tools.checked_file(path)
    value = hashlib.sha256()
    with file.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            file_tools.check_cancelled()
            if cancel_event is not None and cancel_event.is_set():
                raise file_tools.SearchCancelled()
            value.update(chunk)
    return value.hexdigest()


def find_duplicates(scope="all", limit=10, cancel_event=None):
    """Hash only same-sized files; never compare path names as proof of duplication."""
    limit = max(1, min(int(limit), settings.MAX_LIMIT))
    by_size = defaultdict(list)
    seen = 0
    for file in file_tools.walk_files(scope, max_files=settings.MAX_FILES_SCANNED + 1):
        if cancel_event is not None and cancel_event.is_set():
            raise file_tools.SearchCancelled()
        seen += 1
        if seen > settings.MAX_FILES_SCANNED:
            break
        try:
            size = file.stat().st_size
            if size <= 200 * 1024 * 1024:
                by_size[size].append(file)
        except OSError:
            continue
    groups = defaultdict(list)
    for size, paths in by_size.items():
        if cancel_event is not None and cancel_event.is_set():
            raise file_tools.SearchCancelled()
        if len(paths) < 2:
            continue
        for file in paths:
            if cancel_event is not None and cancel_event.is_set():
                raise file_tools.SearchCancelled()
            try:
                groups[(size, digest(file, cancel_event))].append(file_tools.file_info(file))
            except (OSError, ValueError):
                continue
    repeated = [items for items in groups.values() if len(items) > 1]
    repeated.sort(key=lambda group: (-sum(item["size"] for item in group), group[0]["path"]))
    return {"groups": [group[:20] for group in repeated[:limit]], "total": len(repeated), "scanned": seen,
            "truncated_groups": sum(len(group) > 20 for group in repeated[:limit]),
            "partial": seen > settings.MAX_FILES_SCANNED}


def file_snapshot(path):
    """Identity and content stamp used to reject stale cleanup plans."""
    file = file_tools.checked_file(path)
    stat = file.stat()
    return {"path": str(file), "device": stat.st_dev, "inode": stat.st_ino,
            "size": stat.st_size, "modified_ns": stat.st_mtime_ns, "sha256": digest(file)}


def plan_duplicate_cleanup(groups, selected_paths):
    """Select only displayed, verified duplicates and retain a copy in each group."""
    if not isinstance(selected_paths, list) or not selected_paths or len(selected_paths) > 200:
        raise ValueError("请先勾选要移入废纸篓的文件")
    if any(not isinstance(path, str) for path in selected_paths):
        raise ValueError("文件路径不正确")
    selected = set(selected_paths)
    if len(selected) != len(selected_paths):
        raise ValueError("不能重复选择同一个文件")
    all_displayed = {item["path"] for group in groups for item in group}
    if not selected <= all_displayed:
        raise ValueError("只能选择本次检查显示的重复文件")
    checked = []
    for group in groups:
        chosen = [item["path"] for item in group if item["path"] in selected]
        if not chosen:
            continue
        keep = [item["path"] for item in group if item["path"] not in selected]
        if not keep:
            raise ValueError("每组重复文件至少要保留一份")
        snapshots = [file_snapshot(path) for path in [*keep, *chosen]]
        if len({item["sha256"] for item in snapshots}) != 1:
            raise ValueError("文件内容已变化，请重新检查重复文件")
        checked.append({"keep": snapshots[:len(keep)], "delete": snapshots[len(keep):]})
    return checked


def trash_file(path):
    if sys.platform != "darwin":
        raise RuntimeError("移入废纸篓目前只支持 macOS")
    try:
        from send2trash import send2trash
    except ImportError as exc:
        raise RuntimeError("请先安装 requirements.txt 中的 Send2Trash") from exc
    send2trash(str(path))


def execute_duplicate_cleanup(plan):
    """Revalidate every file before moving any selected copy to the Trash."""
    for group in plan:
        for expected in [*group["keep"], *group["delete"]]:
            if file_snapshot(expected["path"]) != expected:
                raise ValueError("重复文件已变化，请重新检查后再选择")
        if not group["keep"] or len({item["sha256"] for item in [*group["keep"], *group["delete"]]}) != 1:
            raise ValueError("每组必须保留至少一份内容相同的文件")
    moved = []
    for group in plan:
        for item in group["delete"]:
            try:
                # Resolve again immediately before handing the path to the OS.
                file = file_tools.checked_file(item["path"])
                stat = file.stat()
                if (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns) != (
                        item["device"], item["inode"], item["size"], item["modified_ns"]):
                    raise ValueError("文件已变化")
                trash_file(file)
                moved.append(item["path"])
            except (OSError, RuntimeError, ValueError) as exc:
                return {"moved": moved, "failed": item["path"], "error": str(exc)}
    return {"moved": moved, "failed": None}


def find_similar(path, limit=10, threshold=0.75):
    """Find indexed files with a close passage; results are suggestions, not duplicates."""
    file = file_tools.checked_file(path)
    limit = max(1, min(int(limit), settings.MAX_LIMIT))
    if not settings.SEMANTIC_DB.exists():
        return {"results": [], "total": 0, "note": "请先建立语义索引"}
    with closing(sqlite3.connect(settings.SEMANTIC_DB)) as db:
        stat = file.stat()
        indexed = db.execute("SELECT mtime_ns,size,model FROM documents WHERE path=?",
                             (str(file),)).fetchone()
        if indexed != (stat.st_mtime_ns, stat.st_size, settings.EMBED_MODEL):
            return {"results": [], "total": 0, "note": "此文件的索引已过期，请更新索引"}
        own = []
        for (blob,) in db.execute("SELECT vector FROM chunks WHERE path=? AND model=? LIMIT 32",
                                  (str(file), settings.EMBED_MODEL)):
            vector = array("f")
            vector.frombytes(blob)
            own.append(vector)
        if not own:
            return {"results": [], "total": 0, "note": "此文件尚未建立语义索引"}
        best = {}
        rows = db.execute("SELECT c.path,c.vector,c.text,c.page,d.mtime_ns,d.size "
                          "FROM chunks c JOIN documents d ON d.path=c.path WHERE c.model=? AND c.path<>?",
                          (settings.EMBED_MODEL, str(file)))
        for other, blob, text, page, mtime, size in rows:
            file_tools.check_cancelled()
            vector = array("f")
            vector.frombytes(blob)
            if any(len(vector) != len(item) for item in own):
                continue
            score = max(sum(a * b for a, b in zip(item, vector)) for item in own)
            if score >= threshold and (other not in best or score > best[other][0]):
                best[other] = (score, text, page, mtime, size)
        results = []
        for other, (score, text, page, mtime, size) in sorted(best.items(), key=lambda item: -item[1][0]):
            try:
                safe = file_tools.checked_file(other)
                current = safe.stat()
                if current.st_mtime_ns != mtime or current.st_size != size:
                    continue
                info = file_tools.file_info(safe, score)
                info.update(similarity=round(score, 3), excerpt=text[:400], page=page,
                            evidence_source="相似内容")
                results.append(info)
            except (OSError, ValueError):
                continue
        return {"results": results[:limit], "total": len(results)}
