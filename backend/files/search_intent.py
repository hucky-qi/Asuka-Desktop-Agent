"""Conservative hard constraints shared by filename, content and semantic tools."""
import re
from pathlib import Path
from datetime import datetime, timedelta

import backend.files.file_tools as file_tools
import backend.preferences as preferences
import backend.settings as settings
from backend.files.retrieval_evidence import SemanticEvidence
import backend.files.identifier_intent as identifier_intent


class Constraints:
    def __init__(self, question, selected_scope):
        text = question.casefold()
        self._semantic_evidence = {}
        self.identifier_plan = identifier_intent.parse(question)
        self.question_text = re.sub(r'\d{4}-\d{2}-\d{2}', '', text)
        self.question_text = re.sub(r'\d+(?:\.\d+)?\s*(?:gb|mb|kb|字节|天)', '', self.question_text)
        self.scope = selected_scope if selected_scope != 'all' else None
        for key, labels in {'desktop': ('桌面', 'desktop'), 'downloads': ('下载', 'downloads'),
                            'documents': ('文稿', 'documents'), 'workspace': ('workspace',)}.items():
            if self.scope is None and any(label in text for label in labels):
                self.scope = key
        type_text = re.sub(r'[“"\']([^“”"\']+)[”"\']\s*(?:文件夹|目录)', '', text)
        type_text = re.sub(r'(?:/Users/|~/|(?:desktop|downloads|documents|workspace)/)[^\s“”"\']+', '', type_text, flags=re.I)
        self.types = set()
        for label, extensions in settings.FILE_TYPE_ALIASES.items():
            if (label in type_text if any('\u4e00'<=c<='\u9fff' for c in label) else re.search(r'(?<![a-z])'+re.escape(label)+r'(?![a-z])',type_text)):
                self.types.update('.'+ext for ext in extensions)
        explicit_exts={'.'+ext for ext in set().union(*[set(v) for v in settings.FILE_TYPE_ALIASES.values()])|{'pdf','docx','txt','md','swift','json'}
                       if re.search(r'(?<![a-z])'+ext+r'(?![a-z])',type_text)}
        if explicit_exts:
            self.types = self.types & explicit_exts if self.types else explicit_exts
            if not self.types:raise ValueError('明确的文件类型条件互相冲突')
        self.images = bool(self.types) and self.types <= file_tools.normalize_extensions('图片')
        self.media = bool(self.types) and self.types <= (file_tools.normalize_extensions('图片') | settings.VIDEO_EXTENSIONS)
        self.bounds = {}
        now=datetime.now().astimezone()
        recent=re.search(r'最近(?:一周|([一二三四五六七八九十]|\d+)\s*天)',text)
        if recent:
            n=7 if recent[0]=='最近一周' else int(recent[1]) if recent[1].isdigit() else {'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}[recent[1]]
            self.bounds['modified_after']=(now-timedelta(days=n)).isoformat()
        for date,relation in re.findall(r'(\d{4}-\d{2}-\d{2})(?:\s*)(之后|以后|起|之前|以前)',text):
            self.bounds['modified_after' if relation in ('之后','以后','起') else 'modified_before']=date
        for relation,value,unit in re.findall(r'(大于|超过|至少|小于|低于|不超过)\s*(\d+(?:\.\d+)?)\s*(gb|mb|kb|字节|b)',text):
            size=int(float(value)*{'gb':1024**3,'mb':1024**2,'kb':1024,'b':1,'字节':1}[unit])
            self.bounds['min_size' if relation in ('大于','超过','至少') else 'max_size']=size+(1 if relation in ('大于','超过') else -1 if relation in ('小于','低于') else 0)
        self.topic = next((word for word in settings.SEARCH_SYNONYMS if word in text), None)
        self.directory = None
        # Quoted folder names and explicit paths are hard boundaries, never hints.
        path = re.search(r'(?:/Users/|~/|(?:desktop|downloads|documents|workspace)/)[^\s“”"\']+', question, re.I)
        folder = re.search(r'[“"\']([^“”"\']+)[”"\']\s*(?:文件夹|目录)', question)
        if not folder:
            folder = re.search(r'(?:在|从)\s*(?:桌面(?:上)?的?)?([^\s，。]+?)\s*(?:文件夹|目录)', question)
        self.folder_requested = bool(folder or path)
        if path:
            self.directory = path.group(0).rstrip('，。')
        elif folder:
            self.directory = folder.group(1)
        for key, item in preferences.custom_scopes().items():
            if item['name'] in question:
                self.scope = key

    def scoped(self, model_scope='all', directory=None):
        scope = self.scope or model_scope
        requested = self.directory or directory
        if requested:
            candidate = Path(requested).expanduser()
            if not candidate.is_absolute() and '/' not in requested:
                roots = file_tools.roots_for(scope)
                matches = [root / requested for root in roots if (root / requested).is_dir()]
                if len(matches) != 1:
                    raise ValueError('指定文件夹不存在或不唯一，请提供允许目录内的完整路径；未扩大搜索范围')
                candidate = matches[0]
            else:
                candidate = requested
            safe = preferences.allowed_subfolder(candidate)
            if self.scope and not any(safe.is_relative_to(root.resolve()) for root in file_tools.roots_for(self.scope)):
                raise ValueError('指定目录超出本轮选定范围')
            return 'folder:' + str(safe)
        return scope

    def filename(self, keyword, extensions):
        if self.types:
            requested = file_tools.normalize_extensions(extensions) if extensions else self.types
            chosen = requested & self.types
            if not chosen:
                raise ValueError('工具文件类型与本轮明确类型冲突')
            extensions = sorted(ext.lstrip('.') for ext in chosen)
        if self.media:
            if self.topic:keyword=self.topic
            elif file_tools.category_query(keyword) or not keyword:keyword=''
            else:
                for label in sorted(settings.FILE_TYPE_ALIASES,key=len,reverse=True):
                    pattern=re.escape(label) if any('\u4e00'<=c<='\u9fff' for c in label) else r'(?<![a-z])'+re.escape(label)+r'(?![a-z])'
                    keyword=re.sub(pattern,'',keyword,flags=re.I)
                keyword=re.sub(r'有关|相关|的|文件','',keyword).strip()
        return keyword, extensions

    def metadata(self, values=None):
        result={k:v for k,v in (values or {}).items() if v is not None}
        for key,value in self.bounds.items():
            if key not in result:result[key]=value;continue
            if key.startswith('modified_'):
                compare=lambda x:file_tools.parse_date(x,key=='modified_before')
            else:compare=int
            result[key]=(max if key in ('modified_after','min_size') else min)(result[key],value,key=compare)
        if 'min_size' in result and 'max_size' in result and int(result['min_size'])>int(result['max_size']):raise ValueError('文件大小条件互相冲突')
        if 'modified_after' in result and 'modified_before' in result and file_tools.parse_date(result['modified_after'])>file_tools.parse_date(result['modified_before'],True):raise ValueError('文件时间条件互相冲突')
        return result

    def media_topic(self):
        return self.topic if self.media and self.topic in settings.TOPIC_MEDIA_EVIDENCE else None

    def semantic(self, query):
        # Type predicates are handled by extensions, never lexical OR evidence.
        text=str(query).strip()
        if self.types:
            for ext in sorted(self.types,key=len,reverse=True):
                text=re.sub(r'(?<![a-z])'+re.escape(ext.lstrip('.'))+r'(?![a-z])','',text,flags=re.I)
            text=re.sub(r'有关|相关|的文件|的资料','',text).strip(' 的，。')
        identifiers=self.identifiers()
        for identifier in identifiers:
            if not identifier_intent.identifier_matches(text,identifier):text=identifier+' '+text
        if not text:raise ValueError('请提供需要查找的主题；文件类型本身不是语义主题')
        return text

    def identifiers(self):
        return list(self.identifier_plan.identifiers)

    def identifier_groups(self):
        return self.identifier_plan.resolved()

    def accept_semantic_evidence(self, records):
        for record in records:
            if not isinstance(record, SemanticEvidence):raise ValueError('检索证据来源无效')
            self._semantic_evidence[record.path] = record

    def check_event(self,event):
        if 'results' not in event:return
        roots=file_tools.roots_for(self.scoped(event.get('scope','all')))
        for item in event['results']:
            path=Path(item['path']).resolve()
            if not any(path.is_relative_to(root.resolve()) for root in roots):raise ValueError('结果不满足明确目录条件，未展示')
            if self.types and path.suffix.lower() not in self.types:raise ValueError('结果不满足明确文件类型，未展示')
            if self.media_topic() and not any(file_tools.topic_evidence(path,self.topic,root.resolve()) for root in roots if path.is_relative_to(root.resolve())):raise ValueError('结果缺少可靠主题名称依据，未展示')
            if self.identifiers() and event.get('tool')=='find_files':
                matched=[value for value in self.identifiers() if file_tools.path_identifier_evidence(path,[value])]
                if not identifier_intent.satisfied(matched,self.identifier_groups()):raise ValueError('文件名/路径结果缺少完整标识依据，未展示')
            if event.get('tool') in ('semantic_search_files','search_file_content') and self.identifiers():
                evidence=self._semantic_evidence.get(str(path))
                if not evidence or not evidence.matches(path,self.identifiers(),self.identifier_groups()):raise ValueError('语义结果缺少完整检索标识依据，未展示')
            if not file_tools.filter_results([item],**self.metadata()):raise ValueError('结果不满足明确时间或大小条件，未展示')



def fast_plan(question, has_results=False):
    """Only unambiguous short commands. Rich constraints/questions stay with the LLM."""
    q = question.strip().strip('，。！？?!').casefold()
    if has_results:
        if re.fullmatch(r'(?:只看|仅看|只要)\s*(pdf|docx|txt|md|图片|照片|视频)', q):
            return 'filter_results', {'extensions':[re.sub(r'^(?:只看|仅看|只要)\s*','',q)]}
        if q in ('按最近修改排序','按修改时间排序','最近修改的是哪个','按最近修改时间排序'):
            return 'sort_results', {'sort_by':'modified_desc'}
        m=re.fullmatch(r'(打开|定位)(?:第)?\s*(\d+|一|二|三|四|五|它)(?:个)?(?:文件)?',q)
        if m:
            i={'它':0,'一':1,'二':2,'三':3,'四':4,'五':5}.get(m[2])
            return {'打开':'open_file','定位':'reveal_in_finder','读取':'read_file'}[m[1]], {'index':int(m[2]) if i is None else i}
    m=re.fullmatch(r'(?:帮我)?(?:找|查找|搜索)(?:一下)?\s*(?:包含|含有)\s*[“"\']?([\w -]{1,100})[”"\']?\s*的文件',q)
    if m:return 'search_file_content', {'keyword':m[1].strip()}
    m=re.fullmatch(r'(?:语义搜索|按语义搜索)\s*(.{1,100})',q)
    if m:return 'semantic_search_files', {'query':m[1].strip()}
    m=re.fullmatch(r'(?:帮我)?(?:找|查找|搜索)(?:一下)?\s*(?:(桌面|下载|文稿|workspace|desktop|downloads|documents)(?:上)?(?:的)?\s*)?([\w. -]{1,60})',q)
    if not m:return None
    keyword=m[2].strip()
    # No guess at rich predicates or compound instructions.
    if any(w in keyword for w in ('最近','大小','大于','小于','至少','超过','之前','之后','包含','含有','然后','打开','为什么','讲什么','文件夹','目录','以及','并且')):return None
    scope={'桌面':'desktop','下载':'downloads','文稿':'documents'}.get(m[1],m[1])
    args={'keyword':keyword}
    if scope:args['scope']=scope
    return 'find_files',args
