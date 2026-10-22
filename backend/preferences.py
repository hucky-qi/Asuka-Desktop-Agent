"""Small local JSON store for user chosen scopes, saved searches and indexing policy."""

import json
import os
import threading
import uuid
from pathlib import Path

import backend.settings as settings


LOCK = threading.RLock()


def load():
    with LOCK:
        try:
            data = json.loads(settings.PREFERENCES_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {"scopes": data.get("scopes", {}), "searches": data.get("searches", {}),
                        "auto_index": bool(data.get("auto_index", False))}
        except (OSError, ValueError, TypeError):
            pass
        return {"scopes": {}, "searches": {}, "auto_index": False}


def save(data):
    with LOCK:
        target = settings.PREFERENCES_PATH
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)


def allowed_subfolder(path):
    if not isinstance(path, (str, os.PathLike)):
        raise ValueError("请输入目录路径")
    raw = os.fspath(path).strip()
    if not raw:
        raise ValueError("请输入目录路径")
    # UI-friendly aliases, e.g. desktop/resume or /desktop/resume. Absolute
    # macOS paths (/Users/name/Desktop/resume) continue to work unchanged.
    parts = raw.lstrip("/").split("/")
    aliases = {"workspace": "workspace", "desktop": "desktop", "documents": "documents", "downloads": "downloads"}
    if parts[0].lower() in aliases:
        segments = parts[1:]
        if not segments or any(segment in ("", ".", "..") or segment.startswith(".") for segment in segments):
            raise ValueError("请输入允许目录下的子文件夹，不能包含 .. 或隐藏目录")
        candidate = settings.SEARCH_ROOTS[aliases[parts[0].lower()]].joinpath(*segments)
    else:
        candidate = Path(raw).expanduser()
    if not candidate.is_dir() or candidate.is_symlink():
        raise ValueError("请选择现有的普通文件夹")
    real = candidate.resolve(strict=True)
    for root in settings.SEARCH_ROOTS.values():
        try:
            relative = real.relative_to(root.resolve(strict=True))
        except (ValueError, OSError):
            continue
        if any(part.startswith(".") or part in settings.IGNORE_DIRS for part in relative.parts):
            break
        if any((root.resolve() / Path(*relative.parts[:index])).is_symlink()
               for index in range(1, len(relative.parts) + 1)):
            break
        return real
    raise ValueError("自定义范围必须在 workspace、桌面、文稿或下载目录内")


def custom_scopes():
    valid = {}
    for key, item in load()["scopes"].items():
        try:
            valid[key] = {"name": item["name"], "path": str(allowed_subfolder(item["path"]))}
        except (ValueError, KeyError, TypeError):
            continue
    return valid


def add_scope(name, path):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
        raise ValueError("范围名称需为 1 至 40 个字符")
    real = allowed_subfolder(path)
    with LOCK:
        data = load()
        if len(data["scopes"]) >= 20:
            raise ValueError("最多保存 20 个自定义范围")
        key = "custom_" + uuid.uuid4().hex[:12]
        data["scopes"][key] = {"name": name.strip(), "path": str(real)}
        save(data)
        return key


def remove_scope(key):
    with LOCK:
        data = load()
        if key not in data["scopes"]:
            raise ValueError("范围不存在")
        del data["scopes"][key]
        save(data)


def save_search(name, query):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
        raise ValueError("搜索名称需为 1 至 60 个字符")
    if not isinstance(query, dict) or not query:
        raise ValueError("请先搜索，再保存")
    with LOCK:
        data = load()
        if len(data["searches"]) >= 30:
            raise ValueError("最多保存 30 个搜索")
        key = uuid.uuid4().hex[:12]
        data["searches"][key] = {"name": name.strip(), "query": query}
        save(data)
        return key


def remove_search(key):
    with LOCK:
        data = load()
        if key not in data["searches"]:
            raise ValueError("搜索不存在")
        del data["searches"][key]
        save(data)


def set_auto_index(enabled):
    if not isinstance(enabled, bool):
        raise ValueError("自动更新开关格式不正确")
    with LOCK:
        data = load()
        data["auto_index"] = enabled
        save(data)
