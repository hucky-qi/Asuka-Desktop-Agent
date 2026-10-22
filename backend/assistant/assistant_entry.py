"""Phase-one entry routing and explicit access boundaries, not an autonomous planner."""
import json,re
import backend.files.search_intent as search_intent, backend.settings as settings, backend.memory.bubble_memory as bubble_memory
from backend.assistant.assistant_tools import TOOLS
import backend.assistant.assistant_files as assistant_files
from backend.memory.assistant_memory import MemoryAccess

def route(text,provider,cancel,files=None,selected_calendar=False,pending=None,calendar_pending=None):
 plan=search_intent.fast_plan(text,bool(files and files.state.last_search_query))
 # Cloud mode keeps only code-owned refinements/selection fast paths. New discovery fields
 # come from the chosen understanding provider, not the old local filename parser.
 if plan and getattr(provider,'memory_destination',None)=='cloud' and plan[0] in ('find_files','semantic_search_files','search_file_content'):plan=None
 if plan and plan[0]=='read_file':return {'route':'files_qa','selection_index':plan[1]['index']}
 if plan:
  if plan[0]=='find_files' and not re.sub(r'(?:一下|帮忙|帮我|请|文件|资料|看看|所有|全部|的|\s)+','',plan[1].get('keyword','')) and not plan[1].get('extensions'):return {'route':'clarify'}
  return {'route':'files','file_action':'discover' if plan[0] in ('find_files','semantic_search_files','search_file_content') else 'refine'}
 # A reliable chosen calendar stays on its proven path, except explicit file commands above.
 # An event selection is trusted task data, not a mandate to interpret all future chat as an edit.
 # Preserve existing clock/calendar entry; explicit file words win the file route classification.
 if not re.search(r'文件|资料|pdf|docx|\bfile|\bfind\b|\bsearch\b|找|搜索|筛选|排序',text,re.I):
  import backend.native_context as native_context
  if not getattr(provider,'daily_understanding',False) and pending is None and (native_context.CANDIDATE.search(text) or calendar_pending):return {'route':'calendar'}
 from backend.assistant.assistant_understanding import extract
 return extract(text,provider,cancel,bool(files and files.state.last_search_query),selected_calendar,pending,selected_file=bool(files and files.state.selected_file),calendar_pending=calendar_pending)

def files(question,scope,snapshot_id,provider,emit,task,context,discovery=False,plan=None):
 return TOOLS.run('files.query',{'question':question,'scope':scope,'snapshot_id':snapshot_id,'provider':provider,'emit':emit,'discovery':discovery,'plan':plan},task,context,{'read_files'})
