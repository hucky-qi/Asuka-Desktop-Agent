/* Shared IME-safe submit arbitration. No timers and no input/change submission. */
(function(root){
 function install(input,form,{enterSends=false}={}){
  let composing=false,commitEnter=false,imeEnterHeld=false;
  input.addEventListener('compositionstart',()=>{composing=true});
  input.addEventListener('compositionend',()=>{composing=false;commitEnter=imeEnterHeld});
  input.addEventListener('keydown',event=>{
   const ime=composing||event.isComposing||event.keyCode===229;
   if(ime){if(event.key==='Enter'||event.keyCode===229){imeEnterHeld=true;commitEnter=true}return}
   if(event.key!=='Enter')return;
   if(commitEnter||event.repeat){event.preventDefault();return}
   if(enterSends&&!event.shiftKey){event.preventDefault();form.requestSubmit()}
  });
  input.addEventListener('keyup',()=>{imeEnterHeld=false;commitEnter=false});
  input.addEventListener('blur',()=>{composing=false;imeEnterHeld=false;commitEnter=false});
  form.addEventListener('submit',event=>{
   if(composing||commitEnter){event.preventDefault();event.stopImmediatePropagation()}
  },true);
  form.addEventListener('click',event=>{
   if(event.detail>0&&event.target.closest('button')){commitEnter=false;imeEnterHeld=false}
  },true);
  return {isComposing:()=>composing};
 }
 root.LocalFileChatInputGuard={install};
 if(typeof module!=='undefined')module.exports={install};
})(typeof window==='undefined'?globalThis:window);
