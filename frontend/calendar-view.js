// Only a bounded, field-only draft is kept; no event identity, grants or confirmation.
window.CalendarDraftStore={
 storage:()=>location.pathname==='/bubble'?localStorage:sessionStorage,
 key:'calendar-permission-draft-v1',
 read(){try{const d=JSON.parse(this.storage().getItem(this.key)||'null');if(d&&Date.now()/1000-d.saved_at<86400&&JSON.stringify(d).length<8192)return d;this.clear()}catch{}return null},
 save(d){try{this.storage().setItem(this.key,JSON.stringify(d))}catch{}},
 clear(){try{this.storage().removeItem(this.key)}catch{}}
};
// Shared existing calendar cards and confirmations; no model or business logic.
window.createCalendarView=function(host){
function offerCalendarRecovery(d){
 if(host.answer.querySelector('.calendar-recovery-resume')||host.answer.querySelector('.calendar-preview button'))return;
 const row=document.createElement('div');row.className='calendar-recovery-resume';const button=document.createElement('button');button.type='button';button.textContent='继续未完成的日历请求';button.onclick=()=>{if(!host.active&&row.isConnected)renderCalendarRecovery(d)};row.append(button);host.answer.append(row);
}
function renderCalendarRecovery(d){
 window.CalendarDraftStore.save(d);host.answer.replaceChildren();const box=document.createElement('div');box.className='calendar-preview';host.answer.append(box);
 const line=t=>{const p=document.createElement('p');p.textContent=t;box.append(p)};
 const permission=['authorization_required','authorization_pending','denied','revoked','restricted','write_only'].includes(d.status);
 line(permission?'日历权限尚未就绪；尚未读取或写入。':'日历服务暂未完成请求；尚未读取或写入。');
 const fields=d.plan.create||{},source=d.plan.source||{};line([fields.title||source.title,fields.date||source.date,fields.start].filter(Boolean).join(' · '));
 const rem=d.plan.reminder;if(rem)line('提醒提前量：'+(rem.timing==='default'?'默认30分钟':rem.lead_minutes!=null?rem.lead_minutes+'分钟':'待补充')+'；没有保存或开启提醒。');
 if(['denied','revoked','write_only','restricted'].includes(d.status))line(d.status==='restricted'?'系统限制了访问，请检查系统管理限制，再重新检查。':'系统设置 → 隐私与安全性 → 日历 → 明日香小助手 → 完全访问。当前拒绝状态不一定再次弹窗。');
 else if(permission)line('点击继续授权后，由你处理系统窗口；完成后重新检查。');
 line('字段已保留；恢复只重新读取和预览，仍须具体确认。恢复草稿保留最多24小时，重置会清除。');
 const retry=document.createElement('button');retry.type='button';retry.textContent=['authorization_required','authorization_pending'].includes(d.status)?'继续授权':permission?'重新检查权限':'重试日历服务';box.append(retry);
 retry.onclick=async()=>{if(host.active||!box.isConnected||retry.disabled)return;retry.disabled=true;let task;
  try{if(host.ensureSession)await host.ensureSession();if(!box.isConnected||host.active)return;task={id:crypto.randomUUID(),generation:++host.epoch,controller:new AbortController()};host.active=task;host.controls(true);host.beginState(task.id);host.panelEvent('begin',task);
   const r=await host.post('/api/bubble/calendar/refresh',{session_id:host.sid,draft:d,request_id:task.id},task.controller.signal);if(host.active!==task||host.epoch!==task.generation)return;
   host.state('idle',task.id);if(r.calendar_recovery)renderCalendarRecovery(r.calendar_recovery);else{window.CalendarDraftStore.clear();if(r.calendar_preview||r.calendar_draft)renderCalendarPreview(r.calendar_preview||r.calendar_draft);else if(r.calendar_events)renderCalendarEvents(r.calendar_events);else host.renderAnswer(r.answer||'请核对当前结果。')}host.panelEvent('ready',task);
  }catch(e){if(!task||(host.active===task&&host.epoch===task.generation)){line(host.safeMessage(e.message));host.state('idle',task?.id);if(task)host.panelEvent('error',task)}}finally{if(!task||host.active===task){host.active=null;host.controls(false)}if(box.isConnected)retry.disabled=false}
 };
}
function renderCalendarEvents(meta){
 host.lastCalendarQuery=meta;host.selectedEdit=null;host.answer.replaceChildren();const box=document.createElement('div');box.className='calendar-preview';box.dataset.snapshotId=meta.snapshot_id;const version=host.epoch;
 const el=(tag,text,cls,parent=box)=>{const e=document.createElement(tag);e.className=cls||'';e.textContent=text;parent.append(e);return e};
 // All dates use the calendar response timezone, independent of the browser's timezone.
 const parts=value=>Object.fromEntries(new Intl.DateTimeFormat('zh-CN',{timeZone:meta.timezone,year:'numeric',month:'numeric',day:'numeric',weekday:'long',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(value)).map(p=>[p.type,p.value]));
 const day=p=>p.year+'-'+p.month+'-'+p.day, clock=p=>p.hour+':'+p.minute;
 const query=parts(meta.start),shortDate=p=>(p.year!==query.year?p.year+'年':'')+p.month+'月'+p.day+'日';
 el('div',query.year+'年'+query.month+'月'+query.day+'日 · '+query.weekday,'calendar-date');
 el('div','共'+meta.total+'项安排','calendar-count');
 if(meta.partial)el('p','覆盖不完整；未读取的内容不代表没有安排，请缩小查询范围。','calendar-warning');
 if(!meta.total&&!meta.partial)el('p','该范围内没有安排。');
 for(const event of meta.events){
  const row=el('div','','calendar-event');row.dataset.eventId=event.event_id||'';
  const start=parts(event.start),end=parts(event.end);let time;
  if(event.all_day){
   // EventKit all-day end is exclusive. Subtracting 1ms identifies the last covered local date, including DST days.
   const last=parts(new Date(new Date(event.end).getTime()-1));
   time='全天'+(day(start)!==day(last)?' · '+shortDate(start)+'–'+shortDate(last):'');
  }else if(day(start)!==day(end)){
   time='跨日 · 至'+shortDate(end)+clock(end);
   if(start.year!==end.year&&end.year===query.year)time='跨日 · '+start.year+'年'+start.month+'月'+start.day+'日'+clock(start)+' 至'+shortDate(end)+clock(end);
  }else time=clock(start)+'–'+clock(end);
  const stamp=el('div',time,'event-time',row);stamp.title=event.start+' → '+event.end+' · '+meta.timezone;
  el('div',event.title,'event-title',row);el('div',event.calendar_title||'','event-calendar',row);
  if(event.event_id){
   const panel=el('details','','event-reminder',row);el('summary','助手提醒','',panel);const label=el('label','提前分钟：','',panel),lead=document.createElement('input');lead.type='number';lead.min='0';lead.max='10080';lead.value=String(meta.reminder_request?.lead_minutes??30);if(meta.reminder_request)panel.open=true;lead.style.width='55px';label.append(lead);
   const set=el('button',meta.reminder_request?'确认设置助手提醒':'设置本机提醒','',panel);set.type='button';const note=el('p','默认30分钟，只设置本机助手提醒，不改变日历。需先在设置中启用。','event-calendar',panel);let op=null;
   if(meta.reminder_request){const q=meta.reminder_request;note.textContent=(q.enabled?'':'当前关闭；可从齿轮→本机日程提醒启用后返回。')+(q.timing==='default'?'默认':'')+'提前'+q.lead_minutes+'分钟；'+(q.due?'提醒时间：'+new Date(q.due).toLocaleString('zh-CN',{timeZone:meta.timezone})+'。':'请先选定具体事件。')+'仅设置助手提醒，不添加系统日历提醒；尚未保存。'}
   set.onclick=async()=>{if(host.active||!box.isConnected||host.epoch!==version)return;set.disabled=true;let submitting=false;try{
    if(!/^(0|[1-9][0-9]*)$/.test(lead.value)||Number(lead.value)>10080)throw Error('请填写0至10080的整数分钟。');
    const selected=await host.post('/api/bubble/reminders',{session_id:host.sid,action:'select',snapshot_id:meta.snapshot_id,card_token:event.card_token});
    if(!box.isConnected||host.epoch!==version)return;
    op=op||crypto.randomUUID().replaceAll('-','');submitting=true;const r=await host.post('/api/bubble/reminders',{session_id:host.sid,action:'set',selection_token:selected.selection_token,minutes:Number(lead.value),operation_id:op});
    if(box.isConnected&&host.epoch===version){note.textContent=r.message;set.textContent='已保存';}
   }catch(e){if(box.isConnected&&host.epoch===version)note.textContent=submitting&&!e.http_status?'提醒保存结果待核对，请查看已有计划，勿重复设置。':host.safeMessage(e.message)}finally{if(box.isConnected&&host.epoch===version)set.disabled=false}};lead.onchange=()=>{op=null;set.textContent='设置本机提醒'};
  }
  if(event.event_id){const edit=el('button','修改','calendar-event-edit',row);edit.type='button';edit.setAttribute('aria-label','修改 '+event.title+' '+time);edit.onclick=()=>{if(box.isConnected&&!host.active&&host.epoch===version)runCalendarUpdate('/choose',{snapshot_id:meta.snapshot_id,event_id:event.event_id,card_token:event.card_token},false,box)};const remove=el('button','删除','calendar-event-delete',row);remove.type='button';remove.onclick=()=>{if(box.isConnected&&!host.active&&host.epoch===version)runCalendarDelete('/preview',{snapshot_id:meta.snapshot_id,event_id:event.event_id,card_token:event.card_token},false,box)}}
 }
 if(meta.events.length<meta.retrieved){const more=el('button','查看更多（还有'+(meta.retrieved-meta.events.length)+'项）','calendar-more');more.type='button';more.id='calendar-more';more.onclick=async()=>{more.disabled=true;try{const r=await host.post('/api/bubble/calendar/more',{session_id:host.sid,snapshot_id:meta.snapshot_id});if(box.isConnected&&host.epoch===version)renderCalendarEvents(r)}catch(e){if(box.isConnected&&host.epoch===version){el('p',e.message,'calendar-warning');more.disabled=false}}}}
 const details=el('details','','query-details');el('summary','查询详情','',details);
 el('p','查询范围：'+meta.start+' → '+meta.end+'（结束不含）','',details);
 el('p','时区：'+meta.timezone+' · 所有已授权日历','',details);
 el('p','共'+meta.total+'项；已读取'+meta.retrieved+'项；显示'+meta.events.length+'项。','',details);
 if(meta.fetched_at)el('p','读取时间：'+meta.fetched_at,'',details);
 host.answer.appendChild(box);
}
function renderSelectedEdit(edit){
 host.selectedEdit=edit;host.answer.replaceChildren();const box=document.createElement('div');box.className='calendar-preview';box.dataset.calendarEditId=edit.id;
 for(const text of ['正在修改 · '+edit.original.title,calendarUpdateStamp(edit.original),'请输入目标时间，例如：改到下周三晚上八点。保留原时长，预览确认后才修改。']){const line=document.createElement('p');line.textContent=text;box.append(line)}
 if(Object.keys(edit.changes||{}).length){const line=document.createElement('p');line.textContent='已保留的修改字段：'+Object.entries(edit.changes).map(([k,v])=>({date:'目标日期',start:'开始',end:'结束',end_date:'结束日期',duration_minutes:'时长（分钟）',shift_minutes:'平移（分钟）',title:'标题'}[k]||k)+' '+v).join(' · ');box.append(line)}
 const cancel=document.createElement('button');cancel.type='button';cancel.id='calendar-edit-cancel';cancel.textContent='取消修改';box.append(cancel);
 const change=document.createElement('button');change.type='button';change.id='calendar-edit-change';change.textContent='换选事件';change.disabled=!host.lastCalendarQuery;box.append(change);host.answer.append(box);host.web.checked=false;host.web.dispatchEvent(new Event('change'));
 const at=host.epoch;
 const leave=async(switching)=>{if(host.active||host.epoch!==at||!box.isConnected)return;cancel.disabled=change.disabled=true;try{await host.post('/api/bubble/calendar/update/deselect',{session_id:host.sid,calendar_edit_id:edit.id});if(host.epoch!==at||!box.isConnected)return;host.selectedEdit=null;if(switching&&host.lastCalendarQuery)renderCalendarEvents(host.lastCalendarQuery);else host.renderAnswer('已取消修改，没有更新日历。')}catch(e){if(host.epoch===at&&box.isConnected){host.notice(e.message);cancel.disabled=false;change.disabled=!host.lastCalendarQuery}}};
 cancel.onclick=()=>leave(false);change.onclick=()=>leave(true);
}
function calendarUpdateStamp(e){
 const f=new Intl.DateTimeFormat('zh-CN',{timeZone:e.timezone||Intl.DateTimeFormat().resolvedOptions().timeZone,year:'numeric',month:'2-digit',day:'2-digit',weekday:'long',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
 return (e.all_day?'全天 · ':'')+f.format(new Date(e.start))+' → '+f.format(new Date(e.end))+' · '+(e.calendar_title||'');
}
function renderCalendarUpdateSelection(p){
 host.answer.replaceChildren();const box=document.createElement('div');box.className='calendar-preview';box.dataset.selectionId=p.id;const at=host.epoch;
 const heading=document.createElement('p');heading.textContent='有多个匹配，请选择原事件，再查看修改预览：';box.append(heading);
 const select=document.createElement('select');select.setAttribute('aria-label','选择原日程');select.style.width='100%';
 for(const event of p.candidates){const option=document.createElement('option');option.value=event.event_id;option.textContent=calendarUpdateStamp(event)+' · '+event.title;select.append(option)}
 const yes=document.createElement('button'),no=document.createElement('button');yes.type=no.type='button';yes.id='calendar-update-select';yes.textContent='先看修改预览';no.textContent='取消';box.append(select,yes,no);host.answer.append(box);
 no.onclick=async()=>{try{await host.post('/api/bubble/calendar/update/cancel',{session_id:host.sid,preview_id:p.id});if(box.isConnected&&host.epoch===at)host.answer.textContent='已取消选择，没有修改日历。'}catch(e){if(box.isConnected&&host.epoch===at)host.notice(e.message)}};
 yes.onclick=()=>{if(box.isConnected&&host.epoch===at)runCalendarUpdate('/select',{selection_id:p.id,event_id:select.value},false,box)};
}
async function runCalendarUpdate(path,body,writing,box){
 if(host.active)return;const task={id:crypto.randomUUID(),generation:++host.epoch,controller:new AbortController(),calendarWrite:writing};host.active=task;for(const b of box.querySelectorAll('button,select'))b.disabled=true;
 host.panelEvent('begin',task);host.beginState(task.id);host.controls(true);
 try{
  const result=await host.post('/api/bubble/calendar/update'+path,{session_id:host.sid,request_id:task.id,...body},task.controller.signal);
  if(host.active!==task||host.epoch!==task.generation)return;host.state('idle',task.id);
  if(result.calendar_edit){host.selectedEdit=result.calendar_edit;renderSelectedEdit(host.selectedEdit)}
  else if(result.calendar_update_preview){renderCalendarUpdatePreview(result.calendar_update_preview);if(result.message)host.notice(result.message)}
  else if(result.status==='verified'){host.selectedEdit=null;host.renderAnswer('已修改原事件并读回核对：\n'+result.event.title+'\n'+calendarUpdateStamp(result.event)+'\n'+result.event.timezone+'\n事件身份：'+result.event_id)}
  else {const e=document.createElement('p');e.textContent=result.message||result.status;box.append(e);const yes=box.querySelector('#calendar-update-confirm');if(yes&&['uncertain','writing'].includes(result.status)){yes.disabled=false;yes.textContent='核对原操作'} }
  host.panelEvent('ready',task);
 }catch(e){if(host.active===task&&host.epoch===task.generation){host.state('idle',task.id);host.notice(e.message);const line=document.createElement('p');line.textContent=writing?'结果未确认；不要另建事件或盲目重试。可核对原操作。':'读取未完成，没有修改。';box.append(line);if(writing){const yes=box.querySelector('#calendar-update-confirm');if(yes){yes.disabled=false;yes.textContent='核对原操作'}}host.panelEvent('error',task)}}
 finally{if(host.active===task){host.active=null;host.controls(false)}}
}
async function runCalendarDelete(path,body,writing,box){
 if(host.active)return;const task={id:crypto.randomUUID(),generation:++host.epoch,controller:new AbortController(),calendarWrite:writing};host.active=task;
 for(const b of box.querySelectorAll('button'))b.disabled=true;host.panelEvent('begin',task);host.beginState(task.id);host.controls(true);
 try{const r=await host.post('/api/bubble/calendar/delete'+path,{session_id:host.sid,request_id:task.id,...body},task.controller.signal);if(host.active!==task||host.epoch!==task.generation)return;host.state('idle',task.id);host.selectedEdit=null;
  if(r.calendar_delete_preview){renderCalendarDeletePreview(r.calendar_delete_preview);if(r.message)host.notice(r.message)}
  else if(r.status==='verified')host.renderAnswer('已核对删除1项，原事件已不可读取。');
  else if(r.status==='cancelled')host.renderAnswer('已取消本次删除操作，没有删除日历事件。');
  else {host.notice(r.message||r.status);if(['uncertain','writing'].includes(r.status)){const yes=box.querySelector('#calendar-delete-confirm');if(yes){yes.disabled=false;yes.textContent='核对原删除操作'}}}
  host.panelEvent('ready',task);
 }catch(e){if(host.active===task&&host.epoch===task.generation){host.state('idle',task.id);host.notice(e.message);host.panelEvent('error',task)}}finally{if(host.active===task){host.active=null;host.controls(false)}}
}
function renderCalendarDeletePreview(p){
 host.answer.replaceChildren();const box=document.createElement('div');box.className='calendar-preview';box.dataset.deletePreviewId=p.id;window.PersonaView?.intro(box,'calendar_delete');
 for(const text of ['待确认 · 将删除1项',p.original.title,calendarUpdateStamp(p.original),'仅删除这项普通单次事件；取消本次操作不会删除事件。']){const line=document.createElement('p');line.textContent=text;box.append(line)}
 const yes=document.createElement('button'),no=document.createElement('button');yes.type=no.type='button';yes.id='calendar-delete-confirm';yes.textContent='确认删除';no.id='calendar-delete-cancel';no.textContent='取消本次操作';box.append(yes,no);host.answer.append(box);
 yes.onclick=()=>{if(box.isConnected&&!host.active)runCalendarDelete('/confirm',{preview_id:p.id,confirm:true,action:'delete'},true,box)};
 no.onclick=()=>{if(box.isConnected&&!host.active)runCalendarDelete('/cancel',{preview_id:p.id},false,box)};
}
function renderCalendarUpdatePreview(p){
 host.answer.replaceChildren();const box=document.createElement('div');box.className='calendar-preview';box.dataset.updatePreviewId=p.id;window.PersonaView?.intro(box,'calendar_preview');const at=host.epoch;
 const target=document.createElement('div');target.className='calendar-update-target';target.dataset.eventId=p.original.event_id;box.append(target);
 const line=(text,parent=target)=>{const el=document.createElement('p');el.textContent=text;parent.append(el)};
 line('将修改1项（以下其他安排仅为冲突参考）');line('修改前');line(p.original.title);line(calendarUpdateStamp({...p.original,timezone:p.timezone}));
 line('修改后');line(p.proposed.title);line(calendarUpdateStamp({...p.proposed,timezone:p.timezone}));line('时区：'+p.timezone);line(p.note);
 if(p.conflicts.length){const refs=document.createElement('details');refs.className='calendar-conflicts';const heading=document.createElement('summary');heading.textContent='发现'+p.conflicts.length+'项冲突（这些安排不会被修改）';refs.append(heading);box.append(refs);for(const event of p.conflicts){line((event.advisory?'全天安排（参考提示） · ':'')+event.title+' · '+calendarUpdateStamp({...event,timezone:p.timezone}),refs);line('重叠：'+event.overlap_start+' → '+event.overlap_end,refs)}}else line('最新可读日历未发现重叠；确认时会再次核对。');
 const yes=document.createElement('button'),adjust=document.createElement('button'),no=document.createElement('button');yes.type=adjust.type=no.type='button';yes.id='calendar-update-confirm';yes.textContent=p.conflicts.length?'仍然修改':'确认修改';adjust.textContent='重新调整';adjust.id='calendar-update-adjust';no.textContent='取消';no.id='calendar-update-cancel';box.append(yes,adjust,no);host.answer.append(box);
 let ownedEpoch=at;
 yes.onclick=()=>{if(!box.isConnected)return;ownedEpoch=host.epoch+1;runCalendarUpdate('/confirm',{preview_id:p.id,confirm:true,accept_conflicts:p.conflicts.length>0},true,box)};
 const abandon=async(revise)=>{if(!box.isConnected||host.active||host.epoch!==ownedEpoch)return;yes.disabled=no.disabled=adjust.disabled=true;try{const r=await host.post('/api/bubble/calendar/update/cancel',{session_id:host.sid,preview_id:p.id});if(box.isConnected&&host.epoch===ownedEpoch){if(r.status==='cancelled'){if(host.selectedEdit&&!revise){await host.post('/api/bubble/calendar/update/deselect',{session_id:host.sid,calendar_edit_id:host.selectedEdit.id});host.selectedEdit=null}host.answer.textContent=revise?'请重新输入要调整的字段，将生成新预览并再次确认；尚未修改。':'已取消预览，没有修改日历。';if(revise)host.input.focus()}else host.notice(r.message||r.status)}}catch(e){if(box.isConnected&&host.epoch===ownedEpoch)host.notice(e.message)}};
 adjust.onclick=()=>abandon(true);no.onclick=()=>abandon(false);
}

function renderCalendarOffer(offer){
 const box=document.createElement('div');box.className='calendar-preview';const version=host.epoch;const yes=document.createElement('button'),no=document.createElement('button');yes.type=no.type='button';yes.textContent='需要，先看预览';no.textContent='不用';yes.id='calendar-offer-yes';no.id='calendar-offer-no';box.append(yes,no);host.answer.append(box);
 no.onclick=async()=>{yes.disabled=no.disabled=true;try{await host.post('/api/bubble/calendar/offer',{session_id:host.sid,offer_id:offer.id,accept:false});if(box.isConnected&&host.epoch===version)box.replaceChildren(document.createTextNode('没有加入日历。'))}catch(e){if(box.isConnected&&host.epoch===version)host.notice(e.message)}};
 yes.onclick=async()=>{
  if(host.active)return;const task={id:crypto.randomUUID(),generation:++host.epoch,controller:new AbortController()};host.active=task;yes.disabled=no.disabled=true;host.panelEvent('begin',task);host.beginState(task.id);host.controls(true);
  try{const r=await host.post('/api/bubble/calendar/offer',{session_id:host.sid,offer_id:offer.id,request_id:task.id,accept:true},task.controller.signal);if(host.active!==task||host.epoch!==task.generation)return;if(r.error)throw Error(r.error);host.state('idle',task.id);host.renderAnswer(r.host.answer||'请核对预览');if(r.calendar_preview||r.calendar_draft)renderCalendarPreview(r.calendar_preview||r.calendar_draft);host.panelEvent('ready',task)}catch(e){if(host.active===task&&host.epoch===task.generation){host.state('idle',task.id);host.answer.textContent=host.safeMessage(e.message);host.panelEvent('error',task)}}finally{if(host.active===task){host.active=null;host.controls(false)}}
 };
}
function renderReminderBinding(target,result,previewId){
 const at=host.epoch;let current=result.assistant_reminder||{status:'not_requested',message:'本次未设置助手提醒。'};
 function paint(receipt){
  current=receipt;target.replaceChildren();const p=document.createElement('p');p.textContent=receipt.status==='saved'?'助手提醒计划已保存'+(receipt.lead_minutes!=null?'：'+(receipt.lead_minutes===0?'开始时':('提前'+receipt.lead_minutes+'分钟')):'')+'。':receipt.message||'日程已保存；提醒尚未设置。';target.append(p);
  const details=document.createElement('details'),summary=document.createElement('summary'),text=document.createElement('p');summary.textContent='日程详情 · 提醒';text.textContent=receipt.detail||(receipt.due?'提醒时间：'+new Date(receipt.due).toLocaleString():'日历和助手提醒分别保存。')+(receipt.status==='saved'?' 计划保存不等于系统通知已送达；需助手运行。':'');details.append(summary,text);target.append(details);
  if(receipt.status==='needs_confirmation'){const changes=document.createElement('p');changes.textContent='原：'+receipt.original.title+' '+receipt.original.start+'；现：'+receipt.current.title+' '+receipt.current.start+'；提前'+receipt.lead_minutes+'分钟。仅设置提醒。';target.append(changes)}
  if(receipt.status==='saved'||receipt.status==='not_requested'||receipt.retryable===false)return;
  const button=document.createElement('button');button.type='button';const requestPermission=receipt.code==='permission'&&['authorization_required','authorization_pending'].includes(receipt.permission_status);button.textContent=receipt.status==='needs_confirmation'?'确认这项提醒':requestPermission?'继续日历授权':receipt.code==='permission'?'重新检查权限':'重新检查提醒';target.append(button);if(receipt.code==='permission'&&!requestPermission){const guide=document.createElement('p');guide.textContent=receipt.permission_status==='restricted'?'请检查系统管理限制，再重新检查。':'系统设置 → 隐私与安全性 → 日历 → 明日香小助手 → 完全访问。返回后重新检查；不保证再次弹出授权窗口。';target.append(guide)}
  button.onclick=async()=>{if(host.active||host.epoch!==at||!target.isConnected)return;button.disabled=true;const task={id:crypto.randomUUID(),generation:at,controller:new AbortController()};host.active=task;host.controls(true);
   try{const r=await host.post('/api/bubble/calendar/reminder/recheck',{session_id:host.sid,preview_id:previewId,request_id:task.id,action:receipt.status==='needs_confirmation'?'confirm':requestPermission?'authorize':'retry',recovery_id:receipt.recovery_id,explicit:true},task.controller.signal);if(host.active!==task||host.epoch!==at)return;window.dispatchEvent(new CustomEvent('calendar-reminder-receipt',{detail:{previewId,receipt:r.assistant_reminder}}));}
   catch(e){if(host.epoch===at&&target.isConnected){p.textContent=host.safeMessage(e.message);button.disabled=false}}
   finally{if(host.active===task){host.active=null;host.controls(false)}}
  };
 }
 paint(current);const listener=e=>{if(!target.isConnected){window.removeEventListener('calendar-reminder-receipt',listener);return}if(host.epoch===at&&e.detail?.previewId===previewId)paint(e.detail.receipt)};window.addEventListener('calendar-reminder-receipt',listener);
}
async function renderPendingReminders(target){
 const at=host.epoch;try{const r=await host.post('/api/bubble/calendar/reminder/status',{session_id:host.sid});if(!target.isConnected||at!==host.epoch)return;
 for(const item of r.items||[]){const box=document.createElement('section'),title=document.createElement('p');title.textContent=item.title+' · '+new Date(item.start).toLocaleString()+' · 提前'+item.minutes+'分钟';box.append(title);const controls=document.createElement('div');box.append(controls);target.append(box);renderReminderBinding(controls,{assistant_reminder:item.assistant_reminder},item.preview_id)}}catch(e){if(target.isConnected){const p=document.createElement('p');p.textContent=host.safeMessage(e.message);target.append(p)}}
}
function renderCreated(r,p){
 const e=r.event;
 if(!e?.title||!e.start||!e.end||!e.timezone){host.answer.textContent='创建回执不完整，结果待核对；请勿重复创建。';return}
 let dates;
 try{
  const options={timeZone:e.timezone},date=new Intl.DateTimeFormat('zh-CN',{...options,year:'numeric',month:'long',day:'numeric'}),time=new Intl.DateTimeFormat('zh-CN',{...options,hour:'2-digit',minute:'2-digit',hourCycle:'h23'}),start=new Date(e.start),end=new Date(e.end);
  dates=e.all_day?date.format(start)+'（全天；结束日期见详情）':date.format(start)+' '+time.format(start)+'–'+(date.format(start)===date.format(end)?'':date.format(end)+' ')+time.format(end);
 }catch{host.answer.textContent='创建回执时间待核对，请勿重复创建。';return}
 if(r.calendar_events)renderCalendarEvents(r.calendar_events);else host.answer.replaceChildren();
 const box=document.createElement('section');box.className='calendar-created';
 const line=t=>{const p=document.createElement('p');p.textContent=t;box.append(p)};
 const role=window.PersonaView?.enabled()&&document.documentElement.dataset.personaTone!=='serious';
 const variants=['安排好了，已经保存到日历。','这件事已经安排进日历了。','日程建好了，时间也核对过了。'];
 const index=[...String(r.event_id||e.title)].reduce((n,c)=>n+c.charCodeAt(0),0)%variants.length;
 line(role?variants[index]:'日程创建成功。');
 line(e.title);line(dates);
 for(const w of p.warnings||[])line(w);
 const binding=document.createElement('section');box.append(binding);renderReminderBinding(binding,r,p.id);
 const details=document.createElement('details'),summary=document.createElement('summary'),receipt=document.createElement('pre');summary.textContent='日程详情';receipt.style.whiteSpace='pre-wrap';receipt.textContent='时区：'+e.timezone+'\n开始：'+e.start+'\n结束：'+e.end+'\n事件身份：'+r.event_id+'\n读回核对：成功'+(r.message?'\n'+r.message:'');details.append(summary,receipt);box.append(details);host.answer.prepend(box);
}
function renderCalendarPreview(p,draft=null){
 host.answer.replaceChildren();const box=document.createElement('div');const previewEpoch=host.epoch;const stillCurrent=()=>box.isConnected&&host.epoch===previewEpoch;box.className='calendar-preview';box.dataset.previewId=p.id;window.PersonaView?.intro(box,'calendar_preview');
 const line=text=>{const e=document.createElement('p');e.textContent=text;box.appendChild(e)};
 line(p.title);
 const fmt=new Intl.DateTimeFormat('zh-CN',{timeZone:p.timezone,year:'numeric',month:'long',day:'numeric'}),tm=new Intl.DateTimeFormat('zh-CN',{timeZone:p.timezone,hour:'2-digit',minute:'2-digit',hourCycle:'h23'}),start=new Date(p.start),end=new Date(p.end);
 line(p.all_day?fmt.format(start)+'（全天）':fmt.format(start)+' '+tm.format(start)+'–'+(fmt.format(start)===fmt.format(end)?'':fmt.format(end)+' ')+tm.format(end));
 for(const w of p.warnings||[])line(w);
 const details=document.createElement('details'),summary=document.createElement('summary');summary.textContent='日程详情';details.append(summary);box.append(details);
 const detail=t=>{const e=document.createElement('p');e.textContent=t;details.append(e)};
 detail('时区：'+p.timezone);detail('开始：'+p.start+'；结束：'+p.end);detail(p.assistant_reminder?'会创建日历事件承载这项助手提醒；日历与提醒分别保存。':'仅创建日历事件，不设置系统日历闹钟。');detail('事件链接保存本机防重复标记。修改时间或标题后需重新确认。');
 const select=document.createElement('select');select.setAttribute('aria-label','目标日历');for(const c of p.calendars){const o=new Option(c.title,c.id);o.selected=c.id===p.calendar_id;select.add(o)}details.appendChild(select);
 const option=p.reminder_options||{},remLabel=document.createElement('label'),remCheck=document.createElement('input'),lead=document.createElement('input');remCheck.type='checkbox';remCheck.checked=draft?draft.enabled:option.selected===true;remCheck.id='create-reminder';remLabel.append(remCheck,document.createTextNode('同时提醒我'));box.append(remLabel);
 lead.type='text';lead.inputMode='numeric';lead.pattern='[0-9]+';lead.setAttribute('aria-label','提前分钟');lead.value=draft?draft.minutes:(option.minutes==null?'30':String(option.minutes));lead.style.width='56px';box.append(lead,document.createTextNode(' 分钟前（0＝开始时）'));
 const feedback=document.createElement('p');feedback.setAttribute('role','status');box.append(feedback);const enable=document.createElement('button');enable.type='button';enable.textContent='打开提醒设置';enable.hidden=true;box.append(enable);enable.onclick=()=>document.querySelector('#settings,#assistantModel')?.click();
 const offset=Date.parse(option.server_now||new Date().toISOString())-Date.now();
 const reminderError=()=>{if(!remCheck.checked)return '';if(p.all_day)return '全天事件暂不支持助手提醒。';if(!/^(0|[1-9][0-9]*)$/.test(lead.value)||Number(lead.value)>10080)return '请填写0至10080的整数分钟，不能留空、小数或指数。';const remain=(start.getTime()-(Date.now()+offset))/60000;if(remain<=0||Number(lead.value)>remain)return '这个提前量已不适用，请选更短时间；日程时间不会改变。';return ''};
 let expiryTimer;const updateRange=()=>{if(!box.isConnected){clearInterval(expiryTimer);return}lead.disabled=!remCheck.checked;const error=reminderError(),max=Math.max(0,Math.min(10080,Math.floor((start.getTime()-(Date.now()+offset))/60000)));enable.hidden=!remCheck.checked||option.enabled===true;feedback.textContent=error||(remCheck.checked?(option.enabled?'':'助手提醒关闭，需在设置开启；不会自动开启。')+'当前最多提前'+max+'分钟；默认30分钟，可修改。':'');yes.disabled=!!host.active?.calendarWrite||!p.calendars.length||!!error;};remCheck.onchange=lead.oninput=updateRange;
 const duplicate=document.createElement('input');duplicate.type='checkbox';duplicate.id='calendar-duplicate';
 if(p.duplicates?.length){line('可能重复：'+p.duplicates.map(d=>d.title+' '+d.start).join('；'));const label=document.createElement('label');label.append(duplicate,document.createTextNode('仍要创建一份新事件'));box.appendChild(label)}
 const yes=document.createElement('button'),no=document.createElement('button');yes.textContent='确认创建';yes.type=no.type='button';no.textContent='取消';yes.id='calendar-confirm';no.id='calendar-cancel';box.append(yes,no);host.answer.appendChild(box);updateRange();expiryTimer=setInterval(updateRange,10000);
 if(!p.calendars.length){
  yes.disabled=select.disabled=true;const status=p.calendar_status;
  const pending=['authorization_required','authorization_pending'].includes(status),denied=['denied','revoked','restricted','write_only'].includes(status);
  line(p.message||'本机日历服务暂不可用；尚未创建。');
  if(denied)line(status==='restricted'?'系统限制了日历访问，请检查系统管理限制。恢复后返回这里重新检查。':'请到系统设置 → 隐私与安全性 → 日历，允许原“明日香小助手”完全访问；返回后重新检查。');
  const refresh=document.createElement('button');refresh.id='calendar-permission-recheck';refresh.textContent=pending?'继续授权':denied?'重新检查':'重试日历服务';refresh.type='button';box.appendChild(refresh);
  const retry=async()=>{if(!stillCurrent()||host.active||refresh.disabled)return;refresh.disabled=true;
   try{const r=await host.post('/api/bubble/calendar/refresh',{session_id:host.sid,preview_id:p.id});if(stillCurrent())renderCalendarPreview(r.calendar_preview,{enabled:remCheck.checked,minutes:lead.value})}
   catch(e){if(stillCurrent())host.notice(e.message)}finally{refresh.disabled=false}};
  refresh.onclick=retry;
  // Permission changes in Settings are read fresh on return; undecided permission
  // requests are only issued by the user's explicit Continue button.
  if(denied){const recheck=()=>{if(!box.isConnected){window.removeEventListener('focus',recheck);return}if(stillCurrent()&&!host.active)retry()};window.addEventListener('focus',recheck)}
 }

 select.onchange=async()=>{yes.disabled=no.disabled=select.disabled=true;try{const r=await host.post('/api/bubble/calendar/revise',{session_id:host.sid,preview_id:p.id,calendar_id:select.value});if(stillCurrent())renderCalendarPreview(r.calendar_preview,{enabled:remCheck.checked,minutes:lead.value})}catch(e){host.notice(e.message);yes.disabled=no.disabled=select.disabled=false}};
 no.onclick=async()=>{yes.disabled=no.disabled=true;try{const r=await host.post('/api/bubble/calendar/cancel',{session_id:host.sid,preview_id:p.id});if(!stillCurrent())return;if(r.status==='cancelled')host.answer.textContent='已取消预览，没有写入日历。';else host.notice(r.message||r.status)}catch(e){host.notice(e.message)}};
 yes.onclick=async()=>{
  if(host.active)return;const error=reminderError();if(error){feedback.textContent=error;return}const task={id:crypto.randomUUID(),generation:++host.epoch,controller:new AbortController(),calendarWrite:true};host.active=task;yes.disabled=no.disabled=select.disabled=true;host.panelEvent('begin',task);host.beginState(task.id);host.controls(true);
  try{const r=await host.post('/api/bubble/calendar/confirm',{session_id:host.sid,preview_id:p.id,request_id:task.id,confirm:true,accept_duplicate:duplicate.checked,assistant_reminder:{enabled:remCheck.checked,minutes:remCheck.checked?Number(lead.value):null}});if(host.active!==task||host.epoch!==task.generation)return;
   host.state('idle',task.id);
   if(r.status==='verified'){renderCreated(r,p)}
   else if(r.calendar_preview){renderCalendarPreview(r.calendar_preview,{enabled:remCheck.checked,minutes:lead.value})}
   else if(r.status==='uncertain'||r.status==='writing'){line(r.message||'结果不确定，请核对原操作');yes.textContent='核对原操作';yes.disabled=false;no.disabled=true;void 0}
   else {line(r.message||'未能创建：'+r.status);if(r.status==='duplicate_confirmation_required'){yes.disabled=no.disabled=false}}
   host.panelEvent('ready',task);
  }catch(e){if(host.active===task&&host.epoch===task.generation){host.state('idle',task.id);host.notice(e.message);line('请保留当前预览；若请求已发出但结果不明，只核对原操作，不要另建相同事件。');yes.textContent='核对原操作';yes.disabled=false;host.panelEvent('error',task)}}finally{if(host.active===task){host.active=null;host.controls(false)}}
 };
}

return {renderPendingReminders,offerCalendarRecovery,renderCalendarRecovery,renderCalendarEvents,renderSelectedEdit,renderCalendarUpdateSelection,renderCalendarUpdatePreview,renderCalendarDeletePreview,renderCalendarOffer,renderCalendarPreview};
};
