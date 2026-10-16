// Display adapter only. Task identities and authoritative phases stay in the existing bridge.
export const WAITING_SIZE={width:76,height:44,tailX:72,tailY:40,angle:-8};
export function waitingPosition(body,viewport){
  const {width,height,tailX,tailY,angle}=WAITING_SIZE,rad=angle*Math.PI/180,c=Math.cos(rad),s=Math.sin(rad);
  const corners=[[0,0],[width,0],[0,height],[width,height]].map(([x,y])=>({x:tailX+(x-tailX)*c-(y-tailY)*s,y:tailY+(x-tailX)*s+(y-tailY)*c}));
  const minX=Math.min(...corners.map(p=>p.x)),maxX=Math.max(...corners.map(p=>p.x)),minY=Math.min(...corners.map(p=>p.y)),maxY=Math.max(...corners.map(p=>p.y));
  const availableW=Math.max(1,viewport.right-viewport.left-6),availableH=Math.max(1,viewport.bottom-viewport.top-6);
  const scale=Math.min(1,availableW/(maxX-minX),availableH/(maxY-minY));
  const anchor={x:body.left+body.width*.28-8,y:body.top-8};
  const clamp=(v,lo,hi)=>Math.max(lo,Math.min(v,hi));
  const left=clamp(anchor.x-tailX,viewport.left+3-tailX-(minX-tailX)*scale,viewport.right-3-tailX-(maxX-tailX)*scale);
  let top=clamp(anchor.y-tailY,viewport.top+3-tailY-(minY-tailY)*scale,viewport.bottom-3-tailY-(maxY-tailY)*scale);
  // At the top screen corner there may be no space above the head. Prefer
  // a compact position below the face rather than covering it after clamping.
  const face={left:body.left+body.width*.22,right:body.left+body.width*.8,top:body.top+body.height*.22,bottom:body.top+body.height*.51};
  const box={left:left+tailX+(minX-tailX)*scale,right:left+tailX+(maxX-tailX)*scale,top:top+tailY+(minY-tailY)*scale,bottom:top+tailY+(maxY-tailY)*scale};
  const overlap=Math.max(0,Math.min(box.right,face.right)-Math.max(box.left,face.left))*Math.max(0,Math.min(box.bottom,face.bottom)-Math.max(box.top,face.top));
  if(overlap>80)top=clamp(face.bottom+5-tailY-(minY-tailY)*scale,viewport.top+3-tailY-(minY-tailY)*scale,viewport.bottom-3-tailY-(maxY-tailY)*scale);
  return {left,top,scale,angle,rotated:{left:left+tailX+(minX-tailX)*scale,top:top+tailY+(minY-tailY)*scale,right:left+tailX+(maxX-tailX)*scale,bottom:top+tailY+(maxY-tailY)*scale}};
}
export class WaitingBubble {
  constructor(element){this.element=element;this.mode='none';this.body={left:50,top:32,width:154,height:192};this.viewport={left:0,top:0,right:innerWidth,bottom:innerHeight};this.key='';addEventListener('resize',()=>{this.viewport={left:0,top:0,right:innerWidth,bottom:innerHeight};this.layout()});this.layout()}
  setMode(mode){
    if(!['none','dots','wifi'].includes(mode))return false;
    this.mode=mode;this.element.dataset.mode=mode;this.element.hidden=mode==='none';
    this.element.setAttribute('aria-label',mode==='wifi'?'正在请求外部搜索摘要（不是进度或信号强度）':'正在处理');
    // CSS opacity transitions are interruptible. No completion callback can revive an old icon.
    return !this.element.hidden;
  }
  setCharacter(body,key){if(key===this.key)return;this.key=key;this.body=body;this.layout()}
  setViewport(bounds){
    if(!bounds||!['left','right','top','bottom'].every(k=>Number.isFinite(bounds[k])))return false;
    if(bounds.left<0||bounds.top<0||bounds.right>1||bounds.bottom>1||bounds.left>=bounds.right||bounds.top>=bounds.bottom)return false;
    this.viewport={left:bounds.left*innerWidth,right:bounds.right*innerWidth,top:bounds.top*innerHeight,bottom:bounds.bottom*innerHeight};return this.layout();
  }
  layout(){const p=waitingPosition(this.body,this.viewport);this.element.style.left=p.left+'px';this.element.style.top=p.top+'px';this.element.style.transform=`rotate(${p.angle}deg) scale(${p.scale})`;return p}
}
