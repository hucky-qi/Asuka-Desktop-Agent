/* Explicit native microphone -> editable draft only. Never sends a chat/tool. */
window.LocalSpeechView={install(h){
 const input=h.input,send=h.send,form=input.closest('form');if(!form)return;
 const mic=document.createElement('button');mic.type='button';mic.id='speech-mic';drawIcon();mic.setAttribute('aria-label','开始本地录音');mic.title='本机中文转写；结果追加到草稿，仍需手动发送';mic.hidden=true;send.before(mic);
 function drawIcon(stopping=false){mic.innerHTML='<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'+(stopping?'<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none"/>':'<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 10v1a7 7 0 0 0 14 0v-1M12 18v3M8 21h8"/>')+'</svg>'}
 const cancel=document.createElement('button');cancel.type='button';cancel.id='speech-cancel';cancel.textContent='取消录音';cancel.hidden=true;
 const note=document.createElement('small');note.id='speech-note';note.setAttribute('role','status');note.hidden=true;form.before(note);note.after(cancel);
 const style=document.createElement('style');style.textContent='#speech-mic{flex:0 0 24px;width:24px;height:26px;padding:0;background:#f8e1c7;color:#79462f;border-radius:7px;font-size:13px}#speech-note{font-size:10px;color:#79462f;overflow-wrap:anywhere}#speech-cancel{font-size:10px;padding:2px 5px;align-self:flex-end}';document.head.append(style);
 let serial=0,current=null,composing=false,ready=false,probe=false;
 const hint=t=>{note.textContent=t;note.hidden=!t};
 const request=(action,sid,token)=>h.post('/api/bubble/speech',{session_id:sid,action,token,explicit:action==='start'});
 function resetUI(){drawIcon();mic.setAttribute('aria-label','开始本地录音');mic.disabled=false;cancel.hidden=true}
 async function abort(message='录音已取消；原草稿保留。'){
  serial++;const t=current;current=null;resetUI();hint(message);if(t?.token)try{await request('cancel',t.sid,t.token)}catch{}
 }
 function valid(t){return current===t&&t.serial===serial&&t.sid===h.sid&&t.epoch===h.epoch&&!document.hidden&&!input.readOnly}
 async function poll(t){
  while(valid(t)){
   let r;try{r=await request('poll',t.sid,t.token)}catch(e){if(valid(t))await abort(e.message);return}
   if(!valid(t)){if(t.token)request('cancel',t.sid,t.token).catch(()=>{});return}
   if(r.status==='complete'){
    current=null;resetUI();const text=String(r.text||'').trim();
    if(!text){hint('未识别到清晰语音，草稿未改动。');return}
    if(composing){hint('中文选词中，转写未回填，请结束选词后重录。');return}
    // Always append to the latest draft; never restore an old saved draft.
    input.value+=(input.value&&!/\s$/.test(input.value)?(input.tagName==='TEXTAREA'?'\n':' '):'')+text;
    input.dispatchEvent(new Event('input',{bubbles:true}));hint('已追加到草稿，请检查后手动发送。');return
   }
   if(['error','cancelled','unavailable','unknown','idle'].includes(r.status)){await abort(r.message||'转写未完成，草稿未改动。');return}
   hint(r.status==='recording'?'录音中，再点麦克风停止；最多2分钟。':r.status==='requesting'?'请在系统提示中允许麦克风。':'正在本机转写，可取消。');
   if(r.status==='recording'){mic.disabled=false;drawIcon(true);mic.setAttribute('aria-label','停止录音并转写')}
   await new Promise(resolve=>setTimeout(resolve,300));
  }
  if(current===t)await abort();
 }
 mic.onclick=async()=>{
  if(composing||input.readOnly){hint('请先结束中文选词或返回聊天，再录音。');return}
  if(current){const t=current;mic.disabled=true;hint('正在停止录音并本机转写…');try{await request('stop',t.sid,t.token)}catch(e){await abort(e.message)}return}
  if(h.busy()){hint('请等待当前任务结束后录音，草稿仍可编辑。');return}
  await h.ensure();if(!ready||h.busy()||composing)return;
  const t={sid:h.sid,epoch:h.epoch,serial:++serial,token:null};current=t;mic.disabled=true;cancel.hidden=false;hint('正在准备录音…');
  try{const r=await request('start',t.sid);t.token=r.token;if(!valid(t)){if(t.token)request('cancel',t.sid,t.token).catch(()=>{});return}if(!['requesting','recording'].includes(r.status)){await abort(r.message||'本地录音不可用。');return}poll(t)}catch(e){if(current===t)await abort(e.message)}
 };
 cancel.onclick=()=>abort();input.addEventListener('compositionstart',()=>{composing=true});input.addEventListener('compositionend',()=>{composing=false});
 form.addEventListener('submit',()=>{if(current)abort()},true);send.addEventListener('click',()=>{if(current)abort()},true);
 h.reset?.addEventListener('click',()=>{if(current)abort()},true);
 const stopHidden=()=>{if(current)abort('已停止录音，草稿保留。')};window.addEventListener('pagehide',stopHidden);window.addEventListener('local-file-chat-hidden',stopHidden);document.addEventListener('visibilitychange',()=>{if(document.hidden)stopHidden()});
 new MutationObserver(()=>{if(current&&(input.readOnly||input.closest('[aria-hidden=true]')))stopHidden()}).observe(document.body,{subtree:true,attributes:true,attributeFilter:['readonly','aria-hidden']});
 async function capability(){if(probe||current)return;probe=true;try{await h.ensure();const r=await request('capability',h.sid);ready=r.status==='ready';mic.hidden=!ready;if(!ready)input.title='本机中文语音未就绪；仍可使用文字输入。'}catch{mic.hidden=true}finally{probe=false}}
 window.addEventListener('focus',capability);capability();return {cancel:abort};
}};
