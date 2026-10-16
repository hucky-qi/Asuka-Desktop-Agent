"""Explicit small-talk memory, independent of assistant preferences/searches."""
import json, os, threading
from pathlib import Path
import settings
PATH = settings.PROJECT / '.bubble-memory.json'
LOCK = threading.RLock()
LIMIT = 30

def load():
    with LOCK:
        if not PATH.exists(): return []
        data = json.loads(PATH.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('namespace') != 'desktop-smalltalk-v1':
            raise ValueError('长期记忆文件格式不正确，未覆盖原文件')
        values=data.get('items')
        if not isinstance(values,list) or len(values)>LIMIT or any(not isinstance(x,str) or len(x)>300 for x in values):
            raise ValueError('长期记忆内容不正确，未覆盖原文件')
        return values

def save(items):
    with LOCK:
        temporary=PATH.with_suffix('.tmp')
        temporary.write_text(json.dumps({'namespace':'desktop-smalltalk-v1','items':items},ensure_ascii=False,indent=2),encoding='utf-8')
        os.chmod(temporary,0o600);os.replace(temporary,PATH)

def command(text):
    """Only explicit user commands mutate the store; no model-generated writes."""
    with LOCK:
        if text in ('查看记忆','查看长期记忆'):
            items=load();return '长期记忆：\n'+'\n'.join(f'{i+1}. {x}' for i,x in enumerate(items)) if items else '暂无长期记忆。'
        if text in ('清空长期记忆','彻底清除记忆'):
            save([]);return '长期记忆已全部清空。当前对话可用重置清除。'
        if text.startswith(('记住：','记住:','记住 ')):
            value=text[3:].strip()
        elif text.startswith('记住'):
            value=text[2:].lstrip('，,：: ').strip()
        elif text.startswith(('忘记：','忘记:','忘记 ')):
            value=text[3:].strip();items=load()
            if value.isdecimal() and 1<=int(value)<=len(items):items.pop(int(value)-1)
            elif value in items:items.remove(value)
            else:return '没有找到该记忆。请先输入“查看长期记忆”，再用“忘记：编号”或完整内容。'
            save(items);return '指定长期记忆已忘记。'
        else:return None
        if not value or len(value)>300:return '请只保存1～300字的明确偏好或事实，不保存整份文件或网页。'
        items=load()
        if value not in items:
            if len(items)>=LIMIT:return '长期记忆已满（30项），请先忘记部分内容。'
            items.append(value);save(items)
        return '已保存长期记忆：'+value+'\n普通重置保留长期记忆；彻底清除请输入“清空长期记忆”。'
