"""Optional Baidu search snippets only; never sends memory/history/local data."""
import os,re,json,html,ipaddress,ssl
import certifi
from urllib.parse import urlparse
from urllib.error import HTTPError
import task_status
import baidu_config
baidu_config.load()
from urllib.request import Request,build_opener,HTTPRedirectHandler,HTTPSHandler

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

_transport=build_opener(NoRedirect(),HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())))
def urlopen(request,timeout):return _transport.open(request,timeout=timeout)

SOURCE='百度搜索摘要'
def configured():
    return (os.environ.get('BUBBLE_WEB_ENABLED','false').lower()=='true' and
            bool(os.environ.get('BAIDU_APPBUILDER_API_KEY')) and
            os.environ.get('BAIDU_FREE_RESOURCES_CONFIRMED','false').lower()=='true')
def wants_search(text):return bool(re.search(r'搜索|查一下|查查|查找|最新|今日|今天|现在|目前|天气|新闻|search|latest|today|current|news|weather',text,re.I))
def query(text):
    # No local paths, credentials or file body are ever transmitted as query text.
    if re.search(r'/Users/|/home/|file://|[A-Z]:[\\/]|(?:api[_ -]?key|password|密码|密钥)\s*[:=]',text,re.I):
        raise ValueError('联网搜索词含本地路径或凭据信息，请改用不含私人信息的搜索词')
    value=re.sub(r'^(?:请|帮我|麻烦)?\s*(?:联网|上网)?\s*(?:搜索一下|搜索|查一下|查查|search)\s*[:：]?\s*','',text,flags=re.I).strip()
    if not 1<=len(value)<=200:raise ValueError('联网搜索词需要1～200字，请缩小问题范围')
    return value

def safe_url(value):
    p=urlparse(value)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:return False
    if p.hostname.lower() in ('localhost','localhost.localdomain') or p.hostname.endswith('.local'):return False
    try:return ipaddress.ip_address(p.hostname).is_global
    except ValueError:return '.' in p.hostname

def search(text,cancel,opener=None):
    import web_usage
    if not configured():raise ValueError('百度联网未配置或免费资源未确认；未发送搜索请求')
    q=query(text)
    if cancel.is_set():raise InterruptedError('已取消')
    payload={'messages':[{'role':'user','content':q}],'search_source':'baidu_search_v2','edition':'standard','resource_type_filter':[{'type':'web','top_k':5}]}
    req=Request('https://qianfan.baidubce.com/v2/ai_search/web_search',data=json.dumps(payload,ensure_ascii=False).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+os.environ['BAIDU_APPBUILDER_API_KEY']})
    def send():
        task_status.stage_current('external_search')
        return (opener or urlopen)(req,timeout=10)
    try:
        response=web_usage.dispatch(send,cancel)
        with response:
            raw=response.read(256_001)
        if len(raw)>256_000:raise ValueError('搜索结果过大')
        data=json.loads(raw)
        web_usage.record_round(getattr(response,"status",200),data,getattr(response,"headers",None))
    except HTTPError as error:
        try:
            detail=json.loads(error.read(256_000))
            from baidu_error_log import record
            record(detail,error.code,error.headers)
            web_usage.record_round(error.code,detail,error.headers)
            code=str(detail.get('code',detail.get('error_code','')))
            code=code if re.fullmatch(r'[A-Za-z0-9_.-]{1,40}',code) else '未提供'
        except Exception:code='未提供'
        reason={401:'鉴权失败',403:'权限或试用资源不可用',429:'服务限流或额度不足'}.get(error.code,'服务调用失败')
        raise RuntimeError(f'百度搜索{reason}：HTTP {error.code}，code {code}（已计数，不自动重试）') from None
    except (ValueError,InterruptedError) as error:
        if isinstance(error,ValueError):web_usage.record_round(error_type=type(error).__name__)
        raise
    except Exception as error:
        web_usage.record_round(error_type=type(error).__name__)
        raise RuntimeError('百度搜索请求失败或超时，未获得可核实结果（本次已计数，不自动重试）') from error
    finally:task_status.stage_current('generating')
    if cancel.is_set():raise InterruptedError('已取消')
    if data.get('code') not in (None,0,'0'):
        from baidu_error_log import record
        record(data,200)
        raise RuntimeError('百度接口返回错误，请核对鉴权和服务开通状态（已计数）')
    results=[]
    for item in data.get('references',[])[:5]:
        url=item.get('url','')
        if not isinstance(url,str) or not safe_url(url):continue
        clean=lambda x:html.unescape(re.sub('<[^>]+>','',str(x)))
        results.append({'title':clean(item.get('title',''))[:120],'url':url,'snippet':clean(item.get('snippet') or item.get('content',''))[:600]})
    if not results:raise ValueError('百度搜索没有返回可核实摘要，未读取网页正文')
    return results
