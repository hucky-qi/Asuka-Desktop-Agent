"""All filesystem and performance settings in one place."""

from pathlib import Path
import os

MODEL = "qwen3.5:9b"
EMBED_MODEL = "qwen3-embedding:0.6b"
HOST = "127.0.0.1"
def _port(name, default):
    try:value=int(os.environ.get(name,str(default)))
    except ValueError:raise ValueError(name+' must be a port from 1 to 65535') from None
    if not 1<=value<=65535:raise ValueError(name+' must be a port from 1 to 65535')
    return value

PORT = _port('LOCAL_FILE_CHAT_PORT', 8765)
LIVE2D_PORT = _port('LOCAL_FILE_CHAT_LIVE2D_PORT', 8766)
PROJECT = Path(__file__).resolve().parent
WORKSPACE = PROJECT.parent / "workspace" if (PROJECT.parent / "workspace").is_dir() else PROJECT / "workspace"
SEARCH_ROOTS = {
    "workspace": WORKSPACE,
    "desktop": Path.home() / "Desktop",
    "documents": Path.home() / "Documents",
    "downloads": Path.home() / "Downloads",
}
IGNORE_DIRS = frozenset({
    ".venv", "venv", "env", "node_modules", ".git", "__pycache__",
    ".cache", ".idea", ".vscode", "dist", "build", "Library",
    "site-packages", "Caches", "cache", "tmp", ".Trash", "acceptance", "index-recovery", "local-file-chat_orign",
})
DEFAULT_LIMIT = 10
MAX_LIMIT = 20
MAX_STORED_RESULTS = 500
MAX_FILES_SCANNED = 100_000
MAX_CONTENT_FILES = 3_000
MAX_READ_BYTES = 3 * 1024 * 1024
MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_DOCX_BYTES = 10 * 1024 * 1024
MAX_READ_CHARS = 9_000
CONTENT_EXTENSIONS = frozenset({
    ".txt", ".md", ".py", ".java", ".js", ".html", ".css",
    ".json", ".csv", ".pdf", ".docx", ".png", ".jpg", ".jpeg", ".heic", ".tiff",
})
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".heic", ".tiff"})
# Filename/folder vocabulary expansion. These are search hints, not image recognition.
SEARCH_SYNONYMS = {"动漫": ("anime", "animation", "cartoon"), "简历": ("resume", "résumé", "curriculum vitae")}
MAX_OCR_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 200
AUTO_INDEX_INTERVAL = 15 * 60
PREFERENCES_PATH = PROJECT / ".assistant_preferences.json"
SEMANTIC_DB = PROJECT / ".semantic_index.sqlite3"
SEMANTIC_MAX_FILES = MAX_FILES_SCANNED  # Safety ceiling; the UI scans all allowed files up to this bound.
SEMANTIC_MIN_SCORE = 0.48  # Cosine score below this needs independent lexical evidence.
QUERY_EMBED_TIMEOUT = 15
EMBED_QUERY_INSTRUCTION = 'Given a search query, retrieve relevant passages about the complete concept, including equivalent wording in other languages.'
SEMANTIC_CANDIDATE_MIN_SCORE = .42  # Recall only; weak candidates require local verification.
SEMANTIC_CANDIDATE_MARGIN = .12
SEMANTIC_TRUSTED_SCORE = .85
SEMANTIC_REVIEW_LIMIT = 4  # Distinct evidence groups; full vector scan remains unchanged.
SEMANTIC_REVIEW_TIMEOUT = 30  # 15s is a progress target, not a reason to discard good results.
SEMANTIC_MAX_CHUNKS_PER_FILE = 128
SEMANTIC_BATCH_SIZE = 8

# Central file-type vocabulary: explicit categories are hard filters.
VIDEO_EXTENSIONS = frozenset({'.mp4','.mkv','.mov','.avi','.webm','.m4v','.mpeg','.mpg','.wmv','.flv','.ts'})
FILE_TYPE_ALIASES = {
 '图片': ['jpg','jpeg','png','gif','webp','heic','svg','tif','tiff','bmp','avif'],
 '视频': sorted(x[1:] for x in VIDEO_EXTENSIONS),
 'word':['doc','docx'], 'ppt':['ppt','pptx'], 'excel':['xls','xlsx'],
}
for aliases,canonical in [(['照片','相片','图像','photo','photos','image','images'],'图片'),(['影片','video','videos','movie','movies'],'视频')]:
 for alias in aliases:FILE_TYPE_ALIASES[alias]=FILE_TYPE_ALIASES[canonical]
# Generic animation alone does not establish an anime subject for media files.
TOPIC_MEDIA_EVIDENCE = {'动漫': ('动漫','anime','cartoon')}
# A category directory directly under an authorized root is stronger evidence
# than a generic animation word in a filename or inside a software project.
TOPIC_MEDIA_CATEGORY_DIRS = {'动漫': ('animation', 'animations')}
