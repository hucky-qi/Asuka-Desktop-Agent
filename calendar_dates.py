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
 source=plain(text)
 duration=bool(re.search(NUM+r'\s*(?:分钟|小时|刻钟)|半小时|半个小时',source))
 ending=bool(re.search(r'(?:到|至|[-—–~～])\s*(?:(?:次日|明天|后天|上午|下午|晚上|早上|凌晨|中午)\s*)*'+NUM+r'\s*(?:[:：]\d|点|时)|(?:结束|截止)\s*(?:时间)?\s*(?:是|为|在)?\s*(?:上午|下午|晚上)?\s*'+NUM,source))
 return duration or ending
