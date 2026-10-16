"""Extract bounded text and retrieve relevant chunks, ready for a future index."""

import subprocess
import sys
import threading
import time
from zipfile import BadZipFile
from datetime import datetime
from pathlib import Path

import settings
from swift_build import compile_helper
from file_tools import stat_matches, roots_for, SearchCancelled, checked_file, check_cancelled, file_info, normalize_extensions, tokens, walk_files


OCR_BUILD_LOCK = threading.Lock()


def ocr_command():
    source = Path(__file__).with_name("ocr_vision.swift")
    binary = Path(__file__).with_name(".ocr_vision")
    if not source.exists():
        raise RuntimeError("缺少 ocr_vision.swift")
    with OCR_BUILD_LOCK:
        if not binary.exists() or binary.stat().st_mtime_ns < source.stat().st_mtime_ns:
            try:
                compile_helper(source, binary)
            except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
                raise RuntimeError("编译本机 OCR 工具失败，请确认已安装 Xcode Command Line Tools") from exc
    return [str(binary)]


def ocr_text(path, page=None):
    """Recognize an image or one scanned PDF page with Apple's on-device Vision."""
    file = checked_file(path)
    if sys.platform != "darwin":
        raise ValueError("OCR 需要在 macOS 上运行")
    args = [*ocr_command(), str(file)]
    if page is not None:
        args.append(str(page))
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=90, check=True)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise ValueError("OCR 无法识别此文件或执行超时") from exc
    return result.stdout.strip()[:180_000]


def extract_sections(path):
    """Return (page number or None, text) while preserving PDF references."""
    file = checked_file(path)
    if file.suffix.lower() not in settings.CONTENT_EXTENSIONS:
        raise ValueError("暂不支持读取该文件类型")
    suffix = file.suffix.lower()
    maximum = (settings.MAX_OCR_BYTES if suffix in settings.IMAGE_EXTENSIONS else
               settings.MAX_PDF_BYTES if suffix == ".pdf" else
               settings.MAX_DOCX_BYTES if suffix == ".docx" else settings.MAX_READ_BYTES)
    if file.stat().st_size > maximum:
        raise ValueError(f"文件过大，超过当前读取上限 {maximum // (1024 * 1024)} MB")
    if file.suffix.lower() in settings.IMAGE_EXTENSIONS:
        return [(None, ocr_text(file))]
    if file.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("请先安装 requirements.txt 中的 pypdf") from exc
        try:
            reader = PdfReader(file)
            if reader.is_encrypted:
                raise ValueError("暂不支持加密 PDF")
            sections = []
            remaining = 180_000
            for number, page in enumerate(reader.pages[:settings.MAX_PDF_PAGES], 1):
                check_cancelled()
                content = (page.extract_text() or "").strip()
                if not content:
                    content = ocr_text(file, number)
                if remaining <= 0:
                    break
                sections.append((number, content[:remaining]))
                remaining -= len(content)
            return sections
        except ValueError:
            raise
        except SearchCancelled:
            raise
        except Exception as exc:
            raise ValueError("PDF 无法提取文字或文件已损坏") from exc
    if file.suffix.lower() == ".docx":
        try:
            from docx import Document
            from docx.opc.exceptions import PackageNotFoundError
        except ImportError as exc:
            raise RuntimeError("请先安装 requirements.txt 中的 python-docx") from exc
        try:
            document = Document(file)
            paragraphs = [p.text for p in document.paragraphs]
            for table in document.tables:
                paragraphs.extend(" | ".join(cell.text for cell in row.cells) for row in table.rows)
            return [(None, "\n".join(paragraphs)[:180_000])]
        except (BadZipFile, PackageNotFoundError, KeyError, OSError, ValueError) as exc:
            raise ValueError("DOCX 文件已损坏或格式不正确") from exc
    raw = file.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return [(None, raw.decode(encoding)[:180_000])]
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别文件编码")


def extract_text(path):
    return "\n".join(text for _, text in extract_sections(path))


def document_chunks(path):
    position = 0
    for page, content in extract_sections(path):
        for offset, piece in chunks(content):
            if piece.strip():
                yield position + offset, piece, page
        position += len(content) + 1


def chunks(text, size=1400, overlap=180):
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        yield start, text[start:end]
        if end == len(text):
            break
        start = end - overlap


def read_file(path, question=""):
    """Read a text/PDF/DOCX file and return bounded relevant excerpts."""
    file = checked_file(path)
    parts = list(document_chunks(file))
    content = "\n".join(part for _, part, _ in parts)
    if not content.strip():
        return {"file": file_info(file), "text": "", "note": "没有可提取的文字，扫描 PDF 可能需要 OCR"}
    query = [t for t in tokens(question) if len(t) > 1]
    if query:
        ranked = sorted(parts, key=lambda part: (sum(part[1].casefold().count(t) for t in query), -part[0]), reverse=True)
        chosen = sorted(ranked[:6], key=lambda part: part[0])
    else:
        chosen = parts[:6]
    snippets = [{"offset": start, "text": piece, "page": page} for start, piece, page in chosen]
    return {"file": file_info(file), "snippets": snippets,
            "truncated": len(parts) > len(chosen), "characters_extracted": len(content)}


def preview_file(path):
    """A short local preview, suitable for disambiguating identically named files."""
    file = checked_file(path)
    if file.suffix.lower() in settings.CONTENT_EXTENSIONS:
        maximum = (settings.MAX_PDF_BYTES if file.suffix.lower() == ".pdf" else
                   settings.MAX_OCR_BYTES if file.suffix.lower() in settings.IMAGE_EXTENSIONS else
                   settings.MAX_DOCX_BYTES if file.suffix.lower() == ".docx" else settings.MAX_READ_BYTES)
        if file.stat().st_size > maximum:
            raise ValueError("文件超过预览大小上限")
    info = file_info(file)
    if file.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        document = PdfReader(file)
        if document.is_encrypted:
            raise ValueError("暂不支持加密 PDF")
        info["pages"] = len(document.pages)
        first = document.pages[0].extract_text() if document.pages else ""
        if not first and document.pages:
            first = ocr_text(file, 1)
        excerpt, page = first[:1200], 1 if document.pages else None
    else:
        excerpt, page = None, None
    thumbnail = None
    if file.suffix.lower() in settings.IMAGE_EXTENSIONS | {".pdf"} and sys.platform == "darwin":
        args = [*ocr_command(), "--thumbnail", str(file)]
        if file.suffix.lower() == ".pdf":
            args.append("1")
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=90, check=True)
            if len(result.stdout) <= 2_000_000:
                thumbnail = "data:image/png;base64," + result.stdout.strip()
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
            pass
    if file.suffix.lower() not in settings.CONTENT_EXTENSIONS:
        return {"file": info, "excerpt": "此格式暂无文字预览", "page": None}
    if excerpt is None:
        parts = document_chunks(file)
        try:
            _, excerpt, page = next(parts)
        except StopIteration:
            return {"file": info, "excerpt": "没有可提取的文字", "page": None, "thumbnail": thumbnail}
    return {"file": info, "excerpt": excerpt[:1200], "page": page, "thumbnail": thumbnail}


def search_file_content(keyword, scope="all", extensions=None, limit=10, progress=None, fallback_seconds=None, hard_filters=None, required_identifiers=None, identifier_groups=None):
    """Find files containing a literal phrase in allowed text/PDF/DOCX contents."""
    keyword = str(keyword or "").strip()
    if not keyword or len(keyword) > 100:
        raise ValueError("请输入 1 至 100 个字符的内容关键词")
    requested = normalize_extensions(extensions)
    supported = settings.CONTENT_EXTENSIONS
    chosen = requested & supported if requested else supported
    if requested and not chosen:
        return {"results": [], "scanned": 0, "partial": False, "total": 0, "note": "指定类型不支持内容搜索"}
    from identifier_intent import satisfied
    from file_tools import search_identifier_evidence
    from retrieval_evidence import SemanticEvidence
    groups=identifier_groups if identifier_groups is not None else (tuple(required_identifiers),) if required_identifiers else ()
    matched_identifiers={}
    def collect(path,text):
        matched_identifiers.setdefault(str(path),set()).update(value for value in required_identifiers or () if search_identifier_evidence(path,text,[value]))
    def proof(items):
        return tuple(SemanticEvidence(str(Path(x['path']).resolve()),tuple(sorted(matched_identifiers.get(x['path'],())))) for x in items)
    matches = []
    # Reuse unchanged indexed text without parsing or embedding. Uncovered files
    # still follow the original bounded parser path; report that coverage explicitly.
    indexed_valid = set()
    indexed_partial = False
    if settings.SEMANTIC_DB.exists():
        import semantic_search
        from contextlib import closing
        try:
            with closing(semantic_search.connect(read_only=True)) as db:
                roots = roots_for(scope)
                for path, mtime, size, model in db.execute("SELECT path,mtime_ns,size,model FROM documents"):
                    check_cancelled()
                    try:
                        file=checked_file(path); stat=file.stat()
                        if model==settings.EMBED_MODEL and file.suffix.lower() in chosen and any(file.is_relative_to(r.resolve()) for r in roots) and stat.st_mtime_ns==mtime and stat.st_size==size and stat_matches(stat,hard_filters):
                            indexed_valid.add(str(file))
                    except (OSError,ValueError):pass
                if required_identifiers:
                    for path,text in db.execute("SELECT path,text FROM chunks"):
                        check_cancelled()
                        if path in indexed_valid:collect(path,text)
                for path, text, page in db.execute("SELECT path,text,page FROM chunks WHERE instr(lower(text),lower(?))>0",(keyword,)):
                    check_cancelled()
                    if path not in indexed_valid or any(x['path']==path for x in matches):continue
                    if not satisfied(matched_identifiers.get(path,()),groups):continue
                    pos=text.casefold().find(keyword.casefold())
                    if pos<0:continue
                    item=file_info(path,score=70 if keyword.casefold() in Path(path).name.casefold() else 50)
                    item.update(excerpt=text[max(0,pos-90):pos+180].replace("\n"," "),page=page,evidence_source="已有索引的内容关键词")
                    matches.append(item)
        except (OSError,ValueError,RuntimeError):indexed_partial=True
    if progress and indexed_valid:
        progress({"_retrieval_evidence":proof(matches),"results":matches[:max(1,min(int(limit),settings.MAX_LIMIT))],"total":len(matches),"partial":True,"indexed_scope_files":len(indexed_valid),"note":"已有索引命中；正在核对未索引文件，结果尚未完整。"})
    fallback_start=time.monotonic()
    scanned = 0
    parsed = 0
    partial = False
    skipped = 0
    coverage = {}
    for path in walk_files(scope, [e.lstrip(".") for e in chosen], max_files=settings.MAX_CONTENT_FILES + 1, stats=coverage):
        scanned += 1
        if scanned > settings.MAX_CONTENT_FILES:
            partial = True
            break
        try:
            if not stat_matches(path.stat(),hard_filters):continue
        except OSError:continue
        if str(path.resolve()) in indexed_valid:
            continue
        if fallback_seconds is not None and time.monotonic()-fallback_start >= fallback_seconds:
            partial=True
            break
        parsed += 1
        try:
            match=None
            for page,content in extract_sections(path):
                collect(path,content)
                pos=content.casefold().find(keyword.casefold())
                if match is None and pos>=0:match=(page,pos,content)
                if match and satisfied(matched_identifiers.get(str(path),()),groups):break
            if not satisfied(matched_identifiers.get(str(path),()),groups):continue
            if not match:
                continue
            page, pos, text = match
            data = file_info(path, score=70 if keyword.casefold() in path.name.casefold() else 50)
            data["excerpt"] = text[max(0, pos-90):pos+180].replace("\n", " ")
            data["page"] = page
            matches.append(data)
        except (OSError, ValueError, RuntimeError):
            skipped += 1
            continue
    matches.sort(key=lambda x: (-x["score"], -datetime.fromisoformat(x["modified_time"]).timestamp()))
    return {"_retrieval_evidence":proof(matches),"results": matches[:max(1, min(int(limit), settings.MAX_LIMIT))], "scanned": scanned,
            **coverage, "partial": partial or indexed_partial or skipped > 0 or bool(indexed_valid) or coverage.get("partial", False) or bool(coverage.get("missing_roots")),
            "skipped": skipped, "total": len(matches), "indexed_scope_files":len(indexed_valid), "parsed_files":parsed,
            "unindexed_files":parsed,
            "note": (f"未索引回退解析达到 {fallback_seconds} 秒时限，覆盖不完整。" if fallback_seconds is not None and partial else "") + ("复用已有索引文本，索引解析上限可能遗漏长文件内容；未索引文件受原扫描/解析上限约束。" if indexed_valid else "") + ("部分目录无权限，内容搜索不完整" if coverage.get("permission_errors") else
                    "部分文件未解析或扫描范围不完整" if partial or skipped or coverage.get("missing_roots") else "")}
