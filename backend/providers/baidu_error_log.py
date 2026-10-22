"""Local error diagnostics only, redacts credentials; no network or budget effects."""
import json,os,re,tempfile
from pathlib import Path
import backend.settings as settings
PATH=settings.PROJECT/'.baidu-web-error.json'

def record(detail,http_status,headers=None):
    if not isinstance(detail,dict):detail={}
    message=str(detail.get('message',detail.get('error_msg','')))
    redacted=False
    for key in ('BAIDU_APPBUILDER_API_KEY','BAIDU_API_KEY'):
        value=os.environ.get(key)
        if value and value in message:message=message.replace(value,'[REDACTED]');redacted=True
    request_id=detail.get('request_id',detail.get('requestId'))
    if not request_id and headers:request_id=headers.get('x-bce-request-id') or headers.get('x-request-id')
    result={'http_status':http_status,'code':detail.get('code',detail.get('error_code')),'message':message,'request_id':request_id,'credentials_redacted':redacted}
    # One unique temp per error avoids corruption between concurrent requests.
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=PATH.parent,prefix='.baidu-web-error-',suffix='.tmp',delete=False) as f:
        json.dump(result,f,ensure_ascii=False,indent=2);temporary=f.name
    os.chmod(temporary,0o600);os.replace(temporary,PATH)
    return result
