"""Read only explicitly authorized dotenv path; never execute/interpolate contents."""
import json,os,re,shlex
from pathlib import Path
from project_paths import PROJECT
ENV_PATH=PROJECT/'.env'
FLAGS_PATH=PROJECT/'.baidu-search-config.json'
ALLOWED={'BAIDU_API_KEY','BAIDU_APPBUILDER_API_KEY','BUBBLE_WEB_ENABLED','BAIDU_FREE_RESOURCES_CONFIRMED','BAIDU_LOCAL_DAILY_LIMIT','BAIDU_TEST_MODE','BAIDU_TEST_LIMIT'}

def read_env(path):
    values={}
    if not path.exists():return values
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        match=re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*=\s*(.*)$',line)
        if not match or match[1] not in ALLOWED:continue
        tokens=shlex.split(match[2],comments=True,posix=True)
        if len(tokens)>1:raise ValueError('百度配置值格式不正确；请使用单个值或引号，不执行shell表达式')
        values[match[1]]=tokens[0] if tokens else ''
    return values

def load(path=ENV_PATH,flags_path=FLAGS_PATH,environ=None):
    env=os.environ if environ is None else environ
    existing=dict(env);file=read_env(Path(path));flags={}
    if Path(flags_path).exists():
        flags=json.loads(Path(flags_path).read_text())
        if not isinstance(flags,dict):raise ValueError('百度启用配置格式不正确')
    for k in ALLOWED-{'BAIDU_API_KEY','BAIDU_APPBUILDER_API_KEY'}:
        if k not in env and k in file:env[k]=file[k]
        elif k not in env and k in flags:env[k]=str(flags[k])
    # Both aliases in the process outrank either alias in the file.
    key=next((existing[k] for k in ('BAIDU_APPBUILDER_API_KEY','BAIDU_API_KEY') if k in existing),None)
    if key is None:key=file.get('BAIDU_APPBUILDER_API_KEY',file.get('BAIDU_API_KEY',''))
    env['BAIDU_APPBUILDER_API_KEY']=key
    return {'key_loaded':bool(key)}
