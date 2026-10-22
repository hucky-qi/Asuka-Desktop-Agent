"""Desktop task envelope; activity/queue animation still owned by task_status."""
import backend.assistant.task_status as task_status
class StaleTask(RuntimeError):pass
class Task:
 def __init__(self,sid,rid,version,cancel,token,current):
  self.sid,self.rid,self.version,self.cancel,self.token,self.current=sid,rid,version,cancel,token,current;self.state='queued'
 def check(self):
  if self.cancel.is_set() or not self.current():raise StaleTask('任务已取消或失效')
 def stage(self,state,phase=None):
  self.check();self.state=state
  if phase:task_status.stage(self.token,phase)
 def identity(self):return {'session_id':self.sid,'request_id':self.rid,'version':self.version,'state':self.state}
 def result(self,kind,data,status='completed'):
  self.check();self.state={'needs_input':'waiting_user','failed':'failed','cancelled':'cancelled','writing':'executing'}.get(status,'completed');return {'task':self.identity(),'kind':kind,'status':status,'data':data}
