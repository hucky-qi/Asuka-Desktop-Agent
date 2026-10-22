/* Shared safe rendering for the existing desktop/web answer regions. No HTML from files/models. */
function createFileQAView(env){
 const line=(box,text)=>{const p=document.createElement('p');p.textContent=text;box.append(p);};
 function render(result){
  if(result?.kind==='file_answer'){env.renderAnswer(result.data.message);if(env.returnFiles){const b=document.createElement('button');b.type='button';b.textContent='返回文件列表';b.onclick=()=>{if(!env.active)env.returnFiles()};env.answer.append(b)}return;}
  if(result?.kind!=='file_qa_authorization')return;
  const p=result.data.preview,at=env.epoch,box=document.createElement('div');box.className='calendar-preview file-qa-authorization';env.answer.replaceChildren(box);
  line(box,'正文片段授权 · '+p.service+' / '+p.model);p.files.forEach(f=>line(box,f.name));line(box,'用途：'+p.purpose);line(box,'将发送：'+p.data);line(box,p.cost);line(box,'仅当前所选版本；不上传路径、整份文件、记忆或日历。不授权任何写入。');
  const button=(text,fn)=>{const b=document.createElement('button');b.type='button';b.textContent=text;b.style.cssText='background:#f8e1c7;color:#50392f;border:0;border-radius:7px;padding:5px;margin:3px';b.onclick=async()=>{if(env.active||env.epoch!==at||!box.isConnected)return;const buttons=box.querySelectorAll('button');buttons.forEach(x=>x.disabled=true);try{await fn()}catch(e){line(box,env.safeMessage(e.message));buttons.forEach(x=>x.disabled=false)}};box.append(b);};
  button('授权片段并回答',async()=>{const a=await env.post('/api/bubble/files/qa',{session_id:env.sid,action:'authorize',preview_id:p.id});if(env.epoch===at&&box.isConnected)await env.resubmit(p.question,a.qa_continue)});
  button('仅本地Qwen',async()=>{const m=await env.post('/api/bubble/model',{session_id:env.sid,action:'select',provider:'ollama'});env.applyModel(m);await env.resubmit(p.question,null)});
  if(env.returnFiles)button('返回文件列表',async()=>{await env.post('/api/bubble/files/qa',{session_id:env.sid,action:'cancel'});env.returnFiles()});
  button('取消正文授权',async()=>{await env.post('/api/bubble/files/qa',{session_id:env.sid,action:'cancel'});box.replaceChildren();line(box,'已取消，未发送正文；所选文件保留。')});
 }
 return {render};
}
