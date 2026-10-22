"""One stable result snapshot: hard filters first, lexical + indexed concepts.

No index writes, no LLM ranking, no asynchronous replacement of numbered cards.
Explicit filenames/identifiers and media metadata retain a direct path.
"""
from pathlib import Path
import re
import time
import backend.files.file_tools as file_tools
import backend.files.semantic_search as semantic_search
import backend.diagnostics as diagnostics
import backend.settings as settings


def topic_query(query, extensions=None):
    text=str(query or '').strip()
    # Remove intent/type syntax, never expand thematic vocabulary here.
    for label in sorted(settings.FILE_TYPE_ALIASES,key=len,reverse=True):
        pattern=re.escape(label) if any('\u4e00'<=c<='\u9fff' for c in label) else r'(?<![a-z])'+re.escape(label)+r'(?![a-z])'
        text=re.sub(pattern,'',text,flags=re.I)
    for ext in file_tools.normalize_extensions(extensions):
        text=re.sub(r'(?<![a-z])'+re.escape(ext.lstrip('.'))+r'(?![a-z])','',text,flags=re.I)
    return re.sub(r'有关|相关|的文件|的资料','',text).strip(' 的，。')


def strategy(keyword, extensions=None, required_identifiers=None, topic=None):
    if required_identifiers:return 'direct', 'explicit_identifier'
    if not str(keyword).strip() or file_tools.category_query(keyword):return 'direct', 'type_or_metadata_only'
    # File suffix in the keyword explicitly denotes a filename, not a topic.
    if re.search(r'\.[a-z0-9]{1,8}$',str(keyword).strip(),re.I):return 'direct', 'explicit_filename'
    exts=file_tools.normalize_extensions(extensions)
    media=file_tools.normalize_extensions('图片')|settings.VIDEO_EXTENSIONS
    if exts and exts <= media:return 'direct', 'media_metadata_no_visual_index'
    return 'hybrid', 'document_topic_requires_vectors'


def find_files(**params):
    mode,reason=strategy(params.get('keyword',''),params.get('extensions'),params.get('required_identifiers'),params.get('topic'))
    started=time.perf_counter()
    with diagnostics.span('filename_retrieval','searching'):
        names=file_tools.find_files(**params)
    if mode=='direct':
        names['retrieval']={'mode':mode,'semantic_skipped':reason}
        diagnostics.record('retrieval_plan',mode=mode,reason=reason)
        return names
    query=topic_query(params.get('keyword',''),params.get('extensions'))
    if not query:return names
    diagnostics.record('retrieval_plan',mode=mode,reason=reason,budget_seconds=15)
    filters={k:params[k] for k in ('modified_after','modified_before','min_size','max_size') if params.get(k) is not None}
    diagnostics.stage('searching')
    # The existing SQL/FTS/vector retriever ranks all eligible indexed files,
    # not just the first filename matches. Both branches are completed before
    # publishing a numbered result set.
    try:
        # Clear name evidence already supplies a trustworthy answer. Still
        # compare the index, but do not delay simple cards to certify weaker
        # concept-only extras. Expose that limited verification explicitly.
        strong_names=any(item.get('score',0)>=90 for item in names['results'])
        meaning=semantic_search.search_semantic(query,params.get('scope','all'),params.get('extensions'),limit=settings.MAX_LIMIT,hard_filters=filters,review_uncertain=not strong_names)
    except (OSError,RuntimeError,ValueError) as exc:
        file_tools.check_cancelled()
        names.update(partial=True,note=(names.get('note','')+'\n语义检索未完成，仅有名称候选：'+str(exc)).strip())
        names['retrieval']={'mode':'partial_hybrid','semantic_completed':False}
        return names
    by_path={};ranks={}
    # Reciprocal-rank fusion avoids mixing the two differently scaled scores.
    # A missing branch contributes zero. Qualifications remain upstream.
    for source,items in [('name',names['results']),('semantic',meaning['results'])]:
        for rank,item in enumerate(items,1):
            path=item['path'];ranks[path]=ranks.get(path,0)+1/(60+rank)
            sources=by_path.get(path,{}).get('candidate_sources',[])
            if path not in by_path or source=='semantic':by_path[path]=dict(item)
            by_path[path]['candidate_sources']=[*sources,source]
    merged=[]
    for path,item in by_path.items():
        item['score']=round(ranks[path],6)
        if not item.get('evidence_source'):item['evidence_source']='名称/目录匹配'
        merged.append(item)
    merged.sort(key=lambda item:(-item['score'],item['path']))
    merged=file_tools.filter_results(merged,params.get('extensions'),**filters,sort_by=params.get('sort_by','relevance'))
    notes=[x for x in (names.get('note'),meaning.get('note')) if x]
    semantic_truncated=meaning['total']>len(meaning['results'])
    if semantic_truncated:notes.append('语义候选仅融合前20项；多轮筛选基于这份稳定结果快照，不代表全范围穷尽')
    if not meaning.get('indexed_scope_files'):notes.append('当前范围无有效正文索引覆盖；仅名称检索，未扩大范围')
    elif meaning.get('indexed_scope_files',0)<names.get('scanned_files',0):notes.append('语义仅覆盖已有有效索引；未索引文件仅按名称检索，未解析或更新索引')
    elapsed=time.perf_counter()-started
    if elapsed>15:notes.append('检索超过15秒目标预算，已等待必要检索完成')
    result={**names,'results':merged,'total':len(merged),'note':'\n'.join(dict.fromkeys(notes)),
            'indexed_scope_files':meaning.get('indexed_scope_files',0),'stale_files':meaning.get('stale_files',0),
            '_retrieval_evidence':meaning.get('_retrieval_evidence',()),
            'partial':bool(names.get('partial') or meaning.get('partial') or meaning.get('needs_index') or semantic_truncated),
            'retrieval':{'mode':mode,'semantic_completed':not meaning.get('needs_index'),'model':settings.EMBED_MODEL,
                         'filename_candidates':names['total'],'semantic_candidates':meaning['total'],
                         'fusion_candidates':len(merged),'stable_snapshot':True}}
    diagnostics.record('fusion',**result['retrieval'])
    return result
