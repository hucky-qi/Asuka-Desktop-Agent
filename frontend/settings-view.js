/* Settings is a sibling view. The live task DOM never leaves the document. */
window.createSettingsView=function(target,buttons,options={}){
 const pane=document.createElement('section');pane.className='settings-pane';pane.id='chat-settings-pane';pane.hidden=true;pane.setAttribute('aria-label','聊天设置');pane.style.cssText='flex:1;min-height:0;overflow:auto;color:#583e35;font-size:11px;line-height:1.5;overflow-wrap:anywhere';target.before(pane);
 let open=false,scroll=null,display='';let revision=0;
 const label=()=>buttons.forEach(b=>{b.setAttribute('aria-expanded',String(open));b.setAttribute('aria-controls',pane.id);b.title=open?'返回聊天':'模型与记忆设置';b.setAttribute('aria-label',open?'返回聊天':'聊天设置')});label();
 function close(){if(!open)return;open=false;revision++;pane.hidden=true;target.style.display=display;label();
  if(scroll&&scroll.nodes.length===target.childNodes.length&&scroll.nodes.every((n,i)=>target.childNodes[i]===n)){target.scrollTop=scroll.y;target.scrollLeft=scroll.x}else{target.scrollTop=options.latestAtEnd?target.scrollHeight:0}scroll=null;
 }
 function show(){if(open){close();return false}open=true;revision++;display=target.style.display;scroll={nodes:[...target.childNodes],y:target.scrollTop,x:target.scrollLeft};target.style.display='none';pane.replaceChildren();pane.hidden=false;label();return true}
 return {get open(){return open},get revision(){return revision},content:pane,show,close};
};
