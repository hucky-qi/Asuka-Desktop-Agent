/* Presentation only. Trusted identity and persistence remain in native/backend.
   Never snapshot task HTML or replay task requests. No model/network calls here. */
(function(){
 window.localFileChatSharedReminderReady=true;
 const bubble=document.querySelector('.bubble');if(!bubble)return;
 const task=document.createElement('div');task.id='chat-task-view';
 for(const node of [...bubble.children])if(!node.classList.contains('outline'))task.append(node);
 bubble.append(task);
 const style=document.createElement('style');style.textContent=`
 #chat-task-view{display:flex;flex-direction:column;flex:1;min-height:0}
 #chat-task-view.reminder-covered{opacity:0;pointer-events:none}
 #reminder-layer{position:absolute;inset:12px 30px 30px 12px;z-index:3;overflow:auto;font-size:11px;line-height:1.45;overflow-wrap:anywhere}
 html[data-tail-side=left] #reminder-layer{left:30px;right:12px}
 #reminder-layer[hidden]{display:none}#reminder-layer p{margin:3px 0}#reminder-layer button{font-size:10px;padding:4px;margin:3px 3px 0 0}
 #reminder-layer input{max-width:100%;font:11px -apple-system,sans-serif;color:#583e35;background:#fffaf4;border:1px solid #d9b590;border-radius:5px}
 #reminder-count,#reminder-note{font-size:9px;color:#8c7060}#reminder-inbox{position:absolute;right:30px;top:0;font-size:9px;padding:1px 4px;z-index:4}
 `;document.head.append(style);
 const layer=document.createElement('section');layer.id='reminder-layer';layer.hidden=true;layer.setAttribute('aria-label','日程提醒');
 const make=(tag,id,text)=>{const e=document.createElement(tag);e.id=id;if(text)e.textContent=text;return e};
 const count=make('p','reminder-count'),title=make('p','reminder-title'),time=make('p','reminder-time'),note=make('p','reminder-note');
 const button=(id,text,fn)=>{const b=make('button',id,text);b.type='button';b.onclick=fn;return b};
 const ten=button('reminder-ten','10分钟后',()=>act('snooze',{minutes:10}));
 const custom=button('reminder-custom','自定义',()=>{picker.hidden=save.hidden=false});
 const picker=make('input','reminder-until');picker.type='datetime-local';picker.setAttribute('aria-label','再次提醒时间');picker.hidden=true;
 const save=button('reminder-save','保存时间',()=>{const date=new Date(picker.value);if(!picker.value||!Number.isFinite(+date)){note.textContent='请选择有效的再次提醒时间。';return}act('snooze',{until:date.toISOString()})});save.hidden=true;
 const close=button('reminder-close','关闭本次提醒',()=>act('dismiss'));
 const back=button('reminder-return','返回聊天',()=>leave(true));
 const next=button('reminder-next','查看其他提醒',()=>{const rows=available();if(rows.length&&(rows.length>1||key(rows[0])!==activeKey))show(rows[(rows.findIndex(r=>key(r)===activeKey)+1)%rows.length])});
 const reconcile=button('reminder-reconcile','核对保存结果',()=>{if(pending)send('reminder_reconcile',{operation_id:pending.op})});reconcile.hidden=true;
 const expression=make('p','reminder-expression');expression.textContent=window.PersonaView?.text('reminder')||'';
 layer.append(count,title,time,expression,ten,custom,picker,save,close,back,next,note,reconcile);bubble.append(layer);
 const inbox=button('reminder-inbox','',()=>{autoPaused=false;shelved.clear();pump()});inbox.hidden=true;bubble.append(inbox);
 let gesture=null;
 let rows=[],activeKey=null,composing=false,menu=false,pointer=false,keys=new Set(),frame=0,saved=null,pending=null,lastFocus='',receipts={};
 let autoPaused=false,requestedFocus=null;
 const shelved=new Set(),completed=new Set();const key=r=>`${r.id}:${r.version}:${r.delivery_id}`;
 const send=(kind,data={})=>window.webkit?.messageHandlers?.bubblePanel?.postMessage({kind,page_id:window.localFileChatPanelPageID,...data});
 function available(){return rows.filter(r=>!completed.has(key(r)))}
 function blocked(){return composing||pointer||keys.size>0||menu||!!document.querySelector('dialog[open], [aria-modal=true]')}
 function pump(){cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>{
  inbox.hidden=!available().length||!layer.hidden;inbox.textContent=`提醒 ${available().length}`;
  if(requestedFocus&&!pending&&!blocked()){const focused=rows.find(r=>r.delivery_id===requestedFocus);requestedFocus=null;if(focused){autoPaused=false;shelved.delete(key(focused));show(focused);return}}
  if(activeKey){count.textContent=`${available().length}项提醒`;next.disabled=!!pending||!available().some(r=>key(r)!==activeKey);return}
  if(requestedFocus&&!pending&&!blocked()){const focused=rows.find(r=>r.delivery_id===requestedFocus);requestedFocus=null;if(focused){autoPaused=false;shelved.delete(key(focused));show(focused);return}}
  const row=(!autoPaused&&available().find(r=>!shelved.has(key(r)))) || (pending&&!shelved.has(pending.key)?pending.row:null);if(row&&!blocked())show(row);
 })}
 function show(row){
  if(blocked())return;
  if(!activeKey){const input=document.querySelector('#input'),sel=window.getSelection();saved={input,inputStart:input.selectionStart,inputEnd:input.selectionEnd,inputDirection:input.selectionDirection,readOnly:input.readOnly,scroll:[...task.querySelectorAll('*')].filter(e=>e.scrollTop||e.scrollLeft).map(e=>[e,e.scrollTop,e.scrollLeft,[...e.childNodes]]),range:sel?.rangeCount?sel.getRangeAt(0).cloneRange():null};input.readOnly=true;task.setAttribute('aria-hidden','true');task.classList.add('reminder-covered');send('reminder_show')}
  activeKey=key(row);expression.textContent=window.PersonaView?.text('reminder')||'';title.textContent=row.title||'日程提醒';time.textContent=`还有约${Math.max(0,Math.ceil((Date.parse(row.start)-Date.now())/60000))}分钟 · ${new Date(row.start).toLocaleString()}`;
  note.textContent='稍后提醒保存后才生效；不会修改日历。';picker.hidden=save.hidden=true;picker.value='';layer.hidden=false;inbox.hidden=true;
  ten.disabled=custom.disabled=close.disabled=save.disabled=!!pending;reconcile.hidden=!pending;
  count.textContent=`${available().length}项提醒`;next.disabled=!available().some(r=>key(r)!==activeKey)||!!pending;
 }
 function leave(explicit){
  if(explicit)autoPaused=true;
  // Shelf the whole currently known batch: no immediate interruption loop.
  for(const r of rows)shelved.add(key(r));activeKey=null;layer.hidden=true;
  task.classList.remove('reminder-covered');task.removeAttribute('aria-hidden');
  if(saved){saved.input.readOnly=saved.readOnly;
   // Keep live task DOM; restore scroll only for still-connected original nodes.
   for(const [e,y,x,nodes] of saved.scroll)if(e.isConnected&&e.childNodes.length===nodes.length&&nodes.every((n,i)=>e.childNodes[i]===n)){e.scrollTop=y;e.scrollLeft=x}
   saved.input.setSelectionRange(saved.inputStart,saved.inputEnd,saved.inputDirection);
   const range=saved.range;if(document.activeElement!==saved.input&&range?.startContainer.isConnected&&range.endContainer.isConnected&&task.contains(range.commonAncestorContainer)){const s=window.getSelection();s.removeAllRanges();s.addRange(range)}saved=null;
  }
  send('reminder_end',{explicit});pump();
 }
 function act(action,args={}){
  if(pending||!activeKey)return;const row=rows.find(r=>key(r)===activeKey);if(!row){note.textContent='提醒已失效，请返回聊天。';return}
  pending={op:crypto.randomUUID().replaceAll('-',''),key:activeKey,action,row};
  try{sessionStorage.setItem('reminder-pending',JSON.stringify(pending))}catch{}
  ten.disabled=custom.disabled=close.disabled=save.disabled=next.disabled=true;reconcile.hidden=false;note.textContent='正在核对保存结果，尚未确认完成。';
  send('reminder_action',{...args,id:row.id,version:row.version,delivery_id:row.delivery_id,operation_id:pending.op,action,clicked_at:new Date().toISOString()});
 }
 // Capture whole input transactions, not delays. compositionend alone isn't a
 // safe boundary: the same Enter may still be down. Pointerup precedes click.
 document.addEventListener('compositionstart',()=>{composing=true},true);
 document.addEventListener('compositionend',()=>{composing=false;pump()},true);
 document.addEventListener('keydown',e=>{keys.add(e.code||e.key);if(activeKey&&task.contains(e.target)){e.preventDefault();e.stopImmediatePropagation()}},true);
 document.addEventListener('keyup',e=>{keys.delete(e.code||e.key);if(activeKey&&task.contains(e.target)){e.preventDefault();e.stopImmediatePropagation()}pump()},true);
 document.addEventListener('pointerdown',e=>{pointer=true;gesture={key:activeKey,target:e.target.closest('button'),pointerId:e.pointerId}},true);
 document.addEventListener('pointerup',e=>{
  const transaction=gesture;
  // Default click dispatch completes before the next rendering opportunity.
  // Two-stage pump also handles drag/text-selection sequences with no click.
  requestAnimationFrame(()=>{if(gesture===transaction){pointer=false;pump()}});
 },true);
 document.addEventListener('pointercancel',()=>{pointer=false;pump()},true);
 document.addEventListener('click',e=>{
  const button=e.target.closest('button');
  if(e.isTrusted&&e.detail>0&&layer.contains(e.target)&&(!gesture||gesture.key!==activeKey||gesture.target!==button)){e.preventDefault();e.stopImmediatePropagation()}
  pointer=false;gesture=null;pump();
 },true);
 document.addEventListener('contextmenu',()=>{menu=true},true);
 window.addEventListener('blur',()=>{keys.clear();pointer=false;menu=true});
 window.addEventListener('focus',()=>{menu=false;pump()});
 window.addEventListener('native-reminder-menu',e=>{menu=!!e.detail;pump()});
 // Native validates snapshots from fixed private stores; this event is not an
 // execution API. Native validates every action again before queuing it.
 window.addEventListener('native-reminder-snapshot',e=>{
  const d=e.detail;if(d?.protocol!==2||!Array.isArray(d.rows))return;
  rows=d.rows;receipts=d.receipts||{};
  const live=new Set(rows.map(key));for(const k of shelved)if(!live.has(k))shelved.delete(k);for(const k of completed)if(!live.has(k)&&k!==activeKey)completed.delete(k);
  if(pending&&receipts[pending.op]){
   if(!activeKey&&pending.row)show(pending.row);
   const r=receipts[pending.op];if(r.status==='saved'){
    completed.add(pending.key);note.textContent=r.action==='snooze'&&r.due?'再次提醒：'+new Date(r.due).toLocaleString():'已关闭本次提醒，日历未变。';
   }else{note.textContent=r.message||'保存失败，尚未安排。';ten.disabled=custom.disabled=close.disabled=save.disabled=false}
   pending=null;reconcile.hidden=true;try{sessionStorage.removeItem('reminder-pending')}catch{}
  }
  const still=rows.some(r=>key(r)===activeKey);
  // A successful receipt stays visible until user returns; never revive stale actions.
  if(activeKey&&!still&&!completed.has(activeKey)&&!pending){leave(false)}
  if(d.focus&&d.focus!==lastFocus){lastFocus=d.focus;requestedFocus=d.focus.split(':')[0]}
  pump();
 });
 document.addEventListener('local-file-chat-task',e=>{if(e.detail?.state==='queued'){autoPaused=false;pump()}});
 try{pending=JSON.parse(sessionStorage.getItem('reminder-pending')||'null');if(pending)send('reminder_reconcile',{operation_id:pending.op})}catch{}
})();
