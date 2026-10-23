"""Shared local calendar date grammar. Monday begins a week; no I/O/model calls."""
import datetime as dt,re
DIGITS={'零':0,'〇':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
NUM=r'[0-9零〇一二两三四五六七八九十]+'
WEEK=r'(?:下下周|下周|本周|这周|下下(?:个)?星期|下(?:个)?星期|本星期|这(?:个)?星期)'
DAY=r'[一二三四五六日天1234567]'
DATE_TOKEN=re.compile(r'\d{4}(?:年\d{1,2}月\d{1,2}日?|[-/]\d{1,2}[-/]\d{1,2})|'+WEEK+'(?:'+DAY+')?|(?:本月|这个月|下月|下个月)'+NUM+'[日号]|前天|昨天|昨日|大后天|后天|明天|明早|明晚|今天|今早|今晚')
def plain(text):return re.sub(r'“[^”]*”|「[^」]*」|《[^》]*》|"[^"\n]*"','',text)
def number(value):
 if value.isdigit():return int(value)
 if '十' in value:
  a,b=value.split('十',1)
  if (a and a not in DIGITS) or (b and b not in DIGITS):raise ValueError('日期不合法，请检查日期数字')
  return (DIGITS.get(a,1)*10)+DIGITS.get(b,0)
 if value in DIGITS:return DIGITS[value]
 raise ValueError('日期不合法，请检查日期数字')
def resolve(value,clock):
 today=dt.date.fromisoformat(clock['date'])
 offsets={'yesterday':-1,'day_before_yesterday':-2,'昨天':-1,'昨日':-1,'前天':-2,'today':0,'tomorrow':1,'day_after_tomorrow':2,'今天':0,'今早':0,'今晚':0,'明天':1,'明早':1,'明晚':1,'后天':2,'大后天':3}
 if not isinstance(value,str) or not value.strip():raise ValueError('请补充日程日期，例如下周二或完整年月日')
 value=value.strip()
 if value in offsets:return today+dt.timedelta(days=offsets[value])
 m=re.fullmatch('('+WEEK+')('+DAY+')?',value)
 if m:
  if not m[2]:raise ValueError('请明确这一周的哪一天，例如下周二')
  week=2 if m[1].startswith('下下') else 1 if m[1].startswith('下') else 0
  weekday='一二三四五六日'.find(m[2]) if m[2] in '一二三四五六日' else 6 if m[2]=='天' else int(m[2])-1
  return today-dt.timedelta(days=today.weekday())+dt.timedelta(days=7*week+weekday)
 m=re.fullmatch(r'(本月|这个月|下月|下个月)('+NUM+r')[日号]',value)
 if m:
  month=today.month+(1 if m[1].startswith('下') else 0);year=today.year+(month==13);month=1 if month==13 else month
  try:return dt.date(year,month,number(m[2]))
  except ValueError:raise ValueError('日期不合法，请检查该月份的实际天数') from None
 value=re.sub(r'^(\d{4})年(\d{1,2})月(\d{1,2})日?$',lambda m:f'{int(m[1]):04}-{int(m[2]):02}-{int(m[3]):02}',value)
 try:
  if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise ValueError()
  return dt.date.fromisoformat(value)
 except ValueError:raise ValueError('日期不明确，请补充具体星期或完整年月日') from None

def from_source(text,clock):
 """Only a unique date outside quoted titles may override model date arithmetic."""
 tokens=list(DATE_TOKEN.finditer(plain(text)))
 if not tokens:return None
 dates=[]
 for token in tokens:
  try:dates.append(resolve(token[0],clock))
  except ValueError:
   # A complete malformed date/week-without-day is actionable, not a guess.
   if len(tokens)==1:raise
   return None
 if len(set(dates))==1:return dates[0].isoformat()
 # Start/end dates and alternative dates remain distinct; never silently choose.
 return None

def check_start(text,start):
 original=plain(text);source=DATE_TOKEN.sub('',original)
 if not start:raise ValueError('请补充开始时间；尚未创建')
 qualified=re.search(r'上午|下午|晚上|早上|早晨|凌晨|中午|傍晚|午后|明早|明晚|今早|今晚|a\.?m\.?|p\.?m\.?',original,re.I)
 hours=re.findall(r'('+NUM+r')(?:点|时)(?!区|长)',source)
 if hours and not qualified and 1<=number(hours[0])<=12:
  raise ValueError('尚未创建：请确认开始时间是上午还是下午，例如下午2点或14:00。')

def explicit_length(text):
 source=_RELATIVE.sub('',plain(text))
 source=re.sub(r'(?:提前|in advance)\s*'+_DURATION,'',source,flags=re.I)
 duration=bool(re.search(NUM+r'\s*(?:分钟|小时|刻钟)|半小时|半个小时',source))
 ending=bool(re.search(r'(?:到|至|[-—–~～])\s*(?:(?:次日|明天|后天|上午|下午|晚上|早上|凌晨|中午)\s*)*'+NUM+r'\s*(?:[:：]\d|点|时)|(?:结束|截止)\s*(?:时间)?\s*(?:是|为|在)?\s*(?:上午|下午|晚上)?\s*'+NUM,source))
 return duration or ending


# Elapsed durations describe start offsets, NOT event length or reminder advance.
# Role selection remains the shared semantic contract; code owns all arithmetic.
_WORDS={'a':1,'an':1,'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10,'eleven':11,'twelve':12,'fifteen':15,'twenty':20,'thirty':30,'forty':40,'forty-five':45,'sixty':60,'ninety':90,'half':.5}
_Q=r'(?:[0-9]+(?:\.[0-9]+)?|[零〇一二两三四五六七八九十百千]+(?:个)?半?|半(?:个)?|(?:forty-five|fifteen|ninety|sixty|thirty|twenty|eleven|twelve|three|seven|eight|four|five|nine|half|one|two|six|ten|an|a)(?: and a half)?)\s*(?:个)?'
_U=r'(?:minutes?|mins?|分钟|分|hours?|hrs?|小时|钟头|h|seconds?|secs?|秒钟|秒|s|days?|天|d)'
_PART=_Q+r'\s*'+_U
_DURATION=_PART+r'(?:\s*(?:and|又)?\s*'+_PART+r')*'
_RELATIVE=re.compile(r'(?:(?:再)?过\s*'+_DURATION+r'|(?:in|after)\s+'+_DURATION+r'|'+_DURATION+r'\s*(?:之后|以后|之前|以前|后|前|from now|later|ago))',re.I)
_COMPONENT=re.compile('('+_Q+r')\s*('+_U+')',re.I)
def _quantity(value):
 value=value.strip().lower().replace('个','')
 if value in _WORDS:return _WORDS[value]
 if value.endswith(' and a half'):return _quantity(value[:-11])+.5
 if value=='半':return .5
 if value.endswith('半'):return _quantity(value[:-1])+.5
 try:return float(value)
 except ValueError:pass
 if '百' not in value and '千' not in value:return number(value)
 total=current=0
 for char in value:
  if char in DIGITS:current=DIGITS[char]
  elif char in ('十','百','千'):total+=(current or 1)*{'十':10,'百':100,'千':1000}[char];current=0
  else:raise ValueError('相对时间数字不明确，请补充明确时长。')
 return total+current

def relative_seconds(expression):
 import unicodedata,math
 if not isinstance(expression,str):raise ValueError('相对开始时间格式不正确。')
 value=unicodedata.normalize('NFKC',expression).strip().lower()
 if not _RELATIVE.fullmatch(value):raise ValueError('请说明明确的相对时长，例如半小时后；“晚点”无法计算。')
 seconds=0
 for match in _COMPONENT.finditer(value):
  unit=match[2].lower();scale=60 if unit.startswith(('min','分')) else 3600 if unit.startswith(('hour','hr','小时','钟头')) or unit=='h' else 86400 if unit.startswith(('day','天')) or unit=='d' else 1
  seconds+=_quantity(match[1])*scale
 if not math.isfinite(seconds) or not 0<seconds<=366*86400 or int(seconds)!=seconds:raise ValueError('请提供1秒至366天以内、可精确到秒的相对时长。')
 return -int(seconds) if re.search(r'之前$|以前$|前$|ago$',value) else int(seconds)

def relative_target(anchor):
 from zoneinfo import ZoneInfo
 fields={'version','basis','timezone','target','offset_seconds','expression','section'}
 if not isinstance(anchor,dict) or set(anchor)!=fields or type(anchor['version']) is not int or anchor['version']!=1 or anchor['section'] not in ('create','changes'):raise ValueError('相对时间基准无效，请重新明确目标时间。')
 base=dt.datetime.fromisoformat(anchor['basis']);target=dt.datetime.fromisoformat(anchor['target']);zone=ZoneInfo(anchor['timezone'])
 if not base.tzinfo or not target.tzinfo or type(anchor['offset_seconds']) is not int or relative_seconds(anchor['expression'])!=anchor['offset_seconds']:raise ValueError('相对时间记录不一致，未重新起算。')
 expected=(base.astimezone(dt.timezone.utc)+dt.timedelta(seconds=anchor['offset_seconds'])).astimezone(zone)
 if expected!=target or target.isoformat()!=expected.isoformat():raise ValueError('相对目标与原基准不一致，未执行。')
 return expected

def bind_start(current,merged,pending,text,clock,restored=None):
 """Bind only CURRENT start evidence; trusted pending anchors survive supplements."""
 import copy
 from zoneinfo import ZoneInfo
 if merged['operation'] not in ('create','reminder','update','offer'):return merged
 section='changes' if merged['operation']=='update' else 'create'
 old=(pending or {}).get('_relative_time') if current.get('resume') else None
 if old and old['section']!=section:old=None
 if old:merged['_relative_time']=copy.deepcopy(old)
 claim=current.get('start_time');matches=list(_RELATIVE.finditer(plain(text)));expression=None
 if pending and pending.get('_relative_time') and not current.get('resume') and not claim and not matches:
  prior=pending.get(section,{})
  if any(current.get(section,{}).get(k) is not None for k in ('date','start')) and all(current.get(section,{}).get(k) in (None,prior.get(k)) for k in ('date','start')):
   merged['_relative_time']=copy.deepcopy(pending['_relative_time']);merged[section]['date']=prior.get('date');merged[section]['start']=prior.get('start')
   raise ValueError('时间字段来自原待补任务，但继续关系未明确；请确认是否继续该安排，原目标未重新起算。')
 if restored:
  anchor=copy.deepcopy(restored);relative_target(anchor)
 elif claim:
  if claim['section']!=section:raise ValueError('相对时间属于不同字段，请明确事件开始时间，不与时长或提醒提前量混用。')
  expression=claim['expression']
  if expression not in plain(text):
   if old and expression==old['expression'] and not matches:claim=None;expression=None
   else:raise ValueError('时间表达不是当前用户原文，未重算目标。')
  if claim and claim['kind']=='unknown':raise ValueError('目标时间尚不明确，请说明具体时长或时刻；其他字段已保留。')
  if claim and claim['kind']=='absolute':
   if _RELATIVE.search(expression):raise ValueError('相对开始时间不能当作绝对时刻，尚未执行。')
   if not any(current.get(section,{}).get(k) for k in ('date','start')):raise ValueError('新的绝对时间字段不完整，已保留原目标；请补充新日期或时刻。')
   merged.pop('_relative_time',None);return merged
  if expression:relative_seconds(expression)
 if not restored:
  if not expression and matches:
   if len(matches)!=1:raise ValueError('存在多个相对时间，请明确采用哪一个；没有自动选择。')
   expression=matches[0][0]
  if expression:
   offset=relative_seconds(expression);base=dt.datetime.fromisoformat(clock['now']);target=(base.astimezone(dt.timezone.utc)+dt.timedelta(seconds=offset)).astimezone(ZoneInfo(clock['timezone']))
   anchor={'version':1,'basis':clock['now'],'timezone':clock['timezone'],'target':target.isoformat(),'offset_seconds':offset,'expression':expression,'section':section}
  else:anchor=copy.deepcopy(old)
 if not anchor:return merged
 target=relative_target(anchor);merged['_relative_time']=anchor
 # A repeated model-calculated date/start never moves a code-held target.
 merged[section]['date']=target.date().isoformat();merged[section]['start']=target.strftime('%H:%M')
 if expression and not explicit_length(text):
  for key in ('duration_minutes','end','end_date'):
   if (pending or {}).get(section,{}).get(key) is not None and current.get('resume'):merged[section][key]=pending[section][key]
   else:merged[section].pop(key,None)
 return merged
