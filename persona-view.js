/* Code-owned state templates. No generated facts, event IDs, permission or action. */
window.PersonaView={
 enabled:()=>document.documentElement.dataset.personaEnabled!=='false',
 set(info){document.documentElement.dataset.personaEnabled=info?.enabled===false?'false':'true';document.documentElement.dataset.personaTone=info?.tone||'default';if(!this.enabled())document.querySelectorAll('.persona-intro').forEach(e=>e.remove())},
 text(state,original=''){
  if(!this.enabled()||document.documentElement.dataset.personaTone==='serious')return original;
  const phrases={files_ready:'结果摆好了，先看卡片。',files_partial:'先看覆盖范围，别急着把部分结果当成全部。',files_empty:'这次没有可靠匹配；已查范围外仍不能下结论。',calendar_preview:'先核对预览，确认后才执行。',calendar_delete:'请确认具体删除对象与范围。',verified:'结果已读回核对。',unknown:'先核对最终状态，不要重复执行。',failed:'这次尚未完成，先看具体原因。',candidate:'目前只是候选，尚未生效。',reminder:'该准备了。',saved:'结果已保存。'};
  return (phrases[state]||'')+(original?' '+original:'');
 },
 welcome(parent){
  parent.replaceChildren();parent.classList.add('welcome');
  for(const [kind,rows] of [['persona-welcome',[['喂，今天有什么事要交给我？',''],['聊天、找文件，还是安排日程？说清楚，我来帮你理顺。','']]],['neutral-welcome',[['你好，想聊点什么？',''],['可以聊天、找文件或安排日程。','']]]]){
   const group=document.createElement('div');group.className=kind;
   for(const [text,cls] of rows){const p=document.createElement('p');p.textContent=text;p.className=cls;group.append(p)}parent.append(group)
  }
 },
 search(parent,data){
  const files=data.files||[],c=data.counts||{},v=data.coverage||{};
  const line=(text,where=parent)=>{const p=document.createElement('p');p.textContent=text;where.append(p);return p};
  line(files.length?(this.text('files_ready')||'结果已列出。'):'当前范围没有可靠匹配；不能据此判断文件不存在。');
  if(v.partial||v.truncated||v.needs_index){const p=line('覆盖不完整，以下仅为已验证结果。');p.className='search-warning'}
  if(data.calendar_notice)line(data.calendar_notice);
  const details=document.createElement('details');details.className='search-details';const title=document.createElement('summary');title.textContent='搜索详情';details.append(title);
  line('候选 '+(c.candidates??files.length)+' · 已验证 '+(c.verified??files.length)+' · 展示 '+(c.displayed??files.length),details);
  if(v.note)line(v.note,details);
  if(v.scanned_files!==undefined)line('扫描文件：'+v.scanned_files,details);
  if(v.indexed_scope_files!==undefined)line('范围内索引文件：'+v.indexed_scope_files,details);
  if(v.retrieval){const p=line('检索与复核统计',details);const code=document.createElement('pre');code.style.whiteSpace='pre-wrap';code.textContent=JSON.stringify(v.retrieval,null,2);p.after(code)}
  parent.append(details);return details;
 },
 intro(parent,state){const text=this.text(state);if(!text)return;const p=document.createElement('p');p.className='persona-intro';p.textContent=text;p.style.cssText='font-size:10px;margin:2px 0;color:#79462f';parent.prepend(p)}
};

{const s=document.createElement('style');s.textContent='html:not([data-persona-enabled="false"]) .neutral-welcome{display:none}html[data-persona-enabled="false"] .persona-welcome{display:none}.welcome p{overflow-wrap:anywhere}.search-details{font-size:11px;color:#79462f}.search-warning{color:#8a491e}.search-details pre{overflow-wrap:anywhere}';document.head.append(s)}
