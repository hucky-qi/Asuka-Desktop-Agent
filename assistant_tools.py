"""Code-owned tool registry. No shell, arbitrary path execution or cloud fallback."""
from dataclasses import dataclass
@dataclass
class Outcome:
 kind:str
 data:dict
 context:object=None
 status:str='completed'
class Registry:
 def __init__(self):self.tools={}
 def register(self,name,permission,validate,run):
  if name in self.tools:raise ValueError('工具重复注册')
  self.tools[name]=(permission,validate,run)
 def run(self,name,args,task,context,grants):
  task.check()
  if name not in self.tools:raise ValueError('工具未注册')
  permission,validate,run=self.tools[name]
  if permission not in grants:raise ValueError('工具权限不足')
  validate(args);task.stage('executing','searching');out=run(args,task,context);task.check();return out
TOOLS=Registry()
