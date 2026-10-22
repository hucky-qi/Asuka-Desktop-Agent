"""Bounded query/vector caches, invalidated by SQLite and WAL revision; read only."""
import ctypes
import ctypes.util
import threading
from array import array
from collections import OrderedDict
from pathlib import Path
import backend.settings as settings
import backend.diagnostics as diagnostics
import time

lock=threading.RLock()
queries=OrderedDict()
snapshot=None
embedding_slots=threading.BoundedSemaphore(2)

def cancellable_embedding(query, embed_fn):
    """Bound outstanding local inference; cancellation never waits for HTTP.

    An already sent Ollama request may finish in the background, but its result
    is discarded, cannot publish cards, and cannot write the index or session.
    """
    import backend.files.file_tools as file_tools
    until=time.monotonic()+settings.QUERY_EMBED_TIMEOUT
    while not embedding_slots.acquire(timeout=.1):
        file_tools.check_cancelled()
        if time.monotonic()>=until:raise RuntimeError('embedding 排队超时，语义检索未完成')
    done=threading.Event();result={}
    def worker():
        try:result['value']=embed_fn([query])[0]
        except Exception as exc:result['error']=exc
        finally:embedding_slots.release();done.set()
    threading.Thread(target=worker,daemon=True).start()
    while not done.wait(.1):
        file_tools.check_cancelled()
        if time.monotonic()>=until:raise RuntimeError('查询 embedding 超过15秒，语义检索未完成；可取消或稍后重试')
    file_tools.check_cancelled()
    if 'error' in result:raise result['error']
    return result['value']
try:
    _native=ctypes.CDLL('/System/Library/Frameworks/Accelerate.framework/Accelerate')
    _dot=_native.cblas_sdot
    _dot.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_int]
    _dot.restype=ctypes.c_float
except (OSError,AttributeError):_dot=None

def revision():
    p=settings.SEMANTIC_DB.resolve()
    def signature(file):
        try:
            s=file.stat();return s.st_ino,s.st_size,s.st_mtime_ns
        except OSError:return None
    return str(p),settings.EMBED_MODEL,signature(p),signature(Path(str(p)+'-wal'))

def query_vector(query, embed_fn):
    key=(revision(),settings.EMBED_QUERY_INSTRUCTION,query)
    with lock:
        value=queries.get(key)
        if value is not None:
            queries.move_to_end(key)
            diagnostics.record('query_embedding',source='cache',model=settings.EMBED_MODEL,instruction=True)
            return value
    diagnostics.record('query_embedding',source='model',model=settings.EMBED_MODEL,instruction=True)
    instructed='Instruct: '+settings.EMBED_QUERY_INSTRUCTION+'\nQuery: '+query
    with diagnostics.span('query_embedding','searching'):value=cancellable_embedding(instructed,embed_fn)
    with lock:
        queries[key]=value
        while len(queries)>32:queries.popitem(last=False)
    return value

def rows(db):
    global snapshot
    key=revision()
    with lock:
        if snapshot and snapshot[0]==key:
            diagnostics.record('index_vectors',source='cache',chunks=len(snapshot[1]))
            return snapshot[1]
    with diagnostics.span('vector_data_load'):
        data=[]
        for path,position,text,blob,page,mtime,size in db.execute('''SELECT c.path,c.position,c.text,c.vector,c.page,d.mtime_ns,d.size
             FROM chunks c JOIN documents d ON d.path=c.path WHERE c.model=?''',(settings.EMBED_MODEL,)):
            import backend.files.file_tools as file_tools
            file_tools.check_cancelled()
            vector=array('f');vector.frombytes(blob)
            data.append((path,position,text,vector,page,mtime,size))
    with lock:
        # Never cache an inconsistent concurrent revision. Next request reloads.
        if revision()==key:snapshot=(key,data)
    diagnostics.record('index_vectors',source='sqlite',chunks=len(data))
    return data

def dot(a,b):
    if _dot:return float(_dot(len(a),a.buffer_info()[0],1,b.buffer_info()[0],1))
    return sum(x*y for x,y in zip(a,b))
