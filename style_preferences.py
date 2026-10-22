"""Finite explicit response styles; same memory store, no inference or tool authority."""
import json,uuid,fcntl,os,threading
from pathlib import Path
import settings
LOCK=threading.RLock()
from memory_learning import VALUES as ORIGINAL_VALUES
VALUES={k:dict(v) for k,v in ORIGINAL_VALUES.items()}
VALUES['tone'].update(tsundere='语气自信俏皮、嘴硬心软；不贬低用户，事实与严肃要求优先。',warm='语气温和、平静且清楚。',playful='语气俏皮自然，不靠重复口头禅，不牺牲准确性。')
PROMPT=' Explicit continuing/temporary RESPONSE styles may additionally use tone=tsundere|warm|playful. Do not infer these as habits. For an explicit bounded number of upcoming replies use duration=next_turns, turns=1..10; otherwise turns=null. Correcting the QUESTION is not a style preference. Reply immediately in the requested style, with useful content; do not generate memory-save claims. Program adds transaction receipt. Temporary style overrides old long-term style; OFF/serious and safety win over tone.'
SAFE={'tone','language','response_length','formatting','answer_structure','comparison_format','explanation_style'}
def policy(slot='style_cloud'):
 if slot not in ('style_cloud','record_cloud'):raise ValueError('未知许可范围。')
 p=settings.PROJECT/'.runtime/daily-chat-policy.json';d=json.loads(p.read_text()) if p.exists() else {};s=d.get(slot,{'enabled':False,'revision':0,'id':None})
 if not isinstance(s,dict) or type(s.get('enabled')) is not bool or type(s.get('revision')) is not int:raise ValueError('风格云端许可配置损坏；未发送。')
 return s
def configure(enabled,revision,slot='style_cloud'):
 if slot not in ('style_cloud','record_cloud'):raise ValueError('未知许可范围。')
 if type(enabled) is not bool:raise ValueError('请明确选择风格许可。')
 p=settings.PROJECT/'.runtime/daily-chat-policy.json';p.parent.mkdir(exist_ok=True)
 with LOCK,p.with_suffix('.style.lock').open('a') as gate:
  os.chmod(gate.name,0o600);fcntl.flock(gate,fcntl.LOCK_EX);old=policy(slot)
  if type(revision) is not int or old['revision']!=revision:raise ValueError('风格许可已变化，请刷新后再操作。')
  d=json.loads(p.read_text()) if p.exists() else {};d[slot]={'enabled':enabled,'revision':revision+1,'id':uuid.uuid4().hex}
  t=p.with_name(p.name+'.'+uuid.uuid4().hex+'.tmp')
  with t.open('x') as f:os.chmod(t,0o600);json.dump(d,f);f.flush();os.fsync(f.fileno())
  os.replace(t,p)
 return policy(slot)
def typed(r):
 return r.get('kind')=='preference' and r.get('source')=='user_explicit' and r.get('preference_key') in SAFE and r.get('preference_value') in VALUES.get(r.get('preference_key'),{})
def permitted(r):
 if r.get('cloud_allowed') is True:return True
 p=policy();return bool(typed(r) and p['enabled'] and p['id'] and r.get('style_cloud_policy')==p['id'] and not r.get('style_cloud_denied'))
def stamp(r):
 p=policy()
 if typed(r) and p['enabled'] and not r.get('style_cloud_denied'):r['style_cloud_policy']=p['id']
def canonical(r):
 return VALUES[r['preference_key']][r['preference_value']]
def temporary(c,proposal,text,rid):
 from memory_learning import SCENARIOS
 if not proposal or proposal.get('action') not in ('save','update'):return
 duration=proposal.get('duration',proposal.get('scope'));key=proposal.get('key');value=proposal.get('value');e=proposal.get('evidence','');scenario=proposal.get('scenario')
 if duration not in ('session','next_turns') or key not in SAFE or value not in VALUES.get(key,{}) or proposal.get('sensitive') or not e.strip() or e not in text or scenario not in SCENARIOS:return
 count=proposal.get('turns')
 if duration=='next_turns' and (type(count) is not int or not 1<=count<=10):raise ValueError('请明确临时要求适用几次答复（1至10）。')
 c.explicit_styles[key]={'key':key,'value':value,'scenario':scenario,'remaining':count if duration=='next_turns' else None,'origin':rid}
def message(c):
 rows=[{k:r[k] for k in ('key','value','scenario','remaining')} for r in c.explicit_styles.values()]
 if not rows:return ''
 if not getattr(c,'persona_enabled',True) or getattr(c,'persona_tone','default')=='serious':rows=[r for r in rows if r['key']!='tone']
 return json.dumps({'temporary_response_style':rows,'priority':'Current request and safety/persona off/serious override; style only, not permission.'},ensure_ascii=False,separators=(',',':')) if rows else ''
def finish(c,rid):
 for k,r in list(c.explicit_styles.items()):
  if r['origin']!=rid and r['remaining'] is not None:
   r['remaining']-=1
   if r['remaining']<=0:c.explicit_styles.pop(k,None)
def explicit_lifetime(p,text):
 # Validate lifetime framing in the exact user evidence, never a topic/phrase rule.
 import re
 e=p.get('evidence','')
 return bool(e and e in text and p.get('duration')=='long_term' and
  re.search(r'以后|今后|往后|从现在起|from now on|going forward|always',e,re.I) and
  not re.search(r'这次|本次|本会话|当前对话|这一段|接下来.{0,6}(?:条|次)|this (?:turn|session|conversation)|next \d+',e,re.I))
def receipt(message,c,proposal,context=None):
 # Translate only code-owned transaction receipts, never model prose/fact slots.
 if not message.startswith(('行，这项长期偏好已记住。','行，这项长期偏好已更新。','已忘记这项长期偏好')):return message
 lang=None
 for r in (context or {}).get('requirements',[]):
  if r.get('key')=='language' and r.get('value') in ('zh','en'):lang=r['value']
 if proposal.get('key')=='language' and proposal.get('value') in ('zh','en'):lang=proposal['value']
 if lang is None and 'language' in c.explicit_styles:lang=c.explicit_styles['language']['value']
 if lang is None and __import__('daily_chat').controls(c,__import__('cloud_access').config())['memory']:
  from assistant_memory import MemoryAccess
  values={r.get('preference_value') for r in MemoryAccess.list_for(c)['items'] if r.get('preference_key')=='language' and r.get('scenario','general')=='general' and permitted(r)}
  if len(values)==1:lang=next(iter(values))
 if lang!='en':return message
 if message.startswith('行，这项长期偏好已记住。'):return 'That long-term reply preference is saved. Cloud use follows the permissions in Memory.'
 if message.startswith('行，这项长期偏好已更新。'):return 'That long-term reply preference is updated. Cloud use follows the permissions in Memory.'
 if message.startswith('已忘记这项长期偏好'):return 'That preference is forgotten for future recall. Chat history and backups were not deleted.'
 return message
