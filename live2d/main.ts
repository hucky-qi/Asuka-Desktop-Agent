// Uses only the locally supplied Cubism Web R5 Framework/Core; no CDN or upload.
import {CubismFramework, Option, LogLevel} from './.sdk/Framework/live2dcubismframework';
import {CubismUserModel} from './.sdk/Framework/model/cubismusermodel';
import {CubismMoc} from './.sdk/Framework/model/cubismmoc';
import {CubismMatrix44} from './.sdk/Framework/math/cubismmatrix44';
import {StateController, parameterMap, applyState, applyEyeOpen, TiltController, boostTailOutput} from './state-controller.mjs';
import {StaticBody} from './static-body';
import {WaitingBubble} from './waiting-bubble.mjs';

const status = document.querySelector('#status') as HTMLElement;
const canvas = document.querySelector('#model') as HTMLCanvasElement;
const shadowCanvas=document.querySelector('#footShadow') as HTMLCanvasElement;
let shadowScale=-1;
let hairGain=1.6,showFootShadow=true;
function drawFootShadow(pixels:Uint8Array,width:number,height:number){
  if(!shadowCanvas)return;
  shadowCanvas.width=width;shadowCanvas.height=height;
  const ctx=shadowCanvas.getContext('2d')!;ctx.clearRect(0,0,width,height);
  shadowScale=diagnostics.scale;
  if(!showFootShadow)return;
  let floor=-1,left=width,right=0;
  for(let y=0;y<height && floor<0;y++)for(let x=0;x<width;x++)if(pixels[(y*width+x)*4+3]>32){floor=y;break;}
  if(floor<0)return;
  for(let y=floor;y<Math.min(height,floor+height*.08);y++)for(let x=0;x<width;x++)if(pixels[(y*width+x)*4+3]>32){left=Math.min(left,x);right=Math.max(right,x);}
  const dpr=width/canvas.getBoundingClientRect().width;
  const rx=(right-left)*.75,ry=4.5*dpr*diagnostics.scale/.88;
  const cy=Math.min(height-ry-1,height-floor-2*dpr),cx=(left+right)/2;
  ctx.save();ctx.translate(cx,cy);ctx.scale(rx,ry);
  const gradient=ctx.createRadialGradient(0,0,0,0,0,1);gradient.addColorStop(0,'rgba(90,90,90,.23)');gradient.addColorStop(.55,'rgba(90,90,90,.10)');gradient.addColorStop(1,'rgba(90,90,90,0)');
  ctx.fillStyle=gradient;ctx.beginPath();ctx.arc(0,0,1,0,Math.PI*2);ctx.fill();ctx.restore();shadowScale=diagnostics.scale;
}
const motionButton = document.querySelector('#motion') as HTMLButtonElement;
const pauseButton = document.querySelector('#pause') as HTMLButtonElement;
const reviewMode=new URLSearchParams(location.search).get('review')==='1';
if(reviewMode)document.body.classList.add('review');
if(reviewMode || new URLSearchParams(location.search).get('pet')==='1') document.body.classList.add('pet');
const diagnostics = {waitingUIVersion:'shared-waiting-v1',ready:false,frames:0,motions:0,physics:false,mocVersion:0,latestMoc:0,coreVersion:0,
  resources:[] as string[], errors:[] as string[],warnings:[] as string[],scale:1,paused:false,alphaCorner:0,visiblePixels:0,
  state:'idle',stateParameters:[] as string[],parameterValues:{} as Record<string,number>,parameterRanges:{} as Record<string,{min:number,max:number}>,fallback:false,staticBody:false};
const states=new StateController();
const waitingBubble=new WaitingBubble(document.querySelector('#searchBubble'));
let waitingOverride:string|undefined;
function renderWaiting(){
  const mode=waitingOverride ?? (diagnostics.state==='searching'?'dots':'none');
  waitingBubble.setMode(mode);
}
let eyeReview:number|null=null;
let headReview:number|null=null;
let breathReview:number|null=null;
let hairReview:number|null=null;
let eyeSide='both';
let activeModel:PrototypeModel|null=null;
function setState(value:string){
  if(!states.set(value))return false;
  if(value==='idle')activeModel?.tilt.resetEpisode();
  diagnostics.state=states.current();
  renderWaiting();
  publish();
  return true;
}
(window as any).localFileChatLive2D={setState,setVisibleViewport(bounds:any){
  return waitingBubble.setViewport(bounds);
},setTaskState(value:string,waiting:string){
  if(!['none','dots','wifi'].includes(waiting))return false;
  waitingOverride=waiting;setState(value);renderWaiting();
  return {state:diagnostics.state,waiting,visible:!(document.querySelector('#searchBubble') as HTMLElement).hidden};
}};
function publish(){canvas.dataset.diagnostics=JSON.stringify(diagnostics)}
function native(type:string){(window as any).webkit?.messageHandlers?.live2d?.postMessage({type,diagnostics})}
function fail(error:any){diagnostics.errors.push(String(error));status.textContent='显示失败：'+error;publish();native('error');console.error(error)}
window.addEventListener('error',event=>fail(event.message));
window.addEventListener('unhandledrejection',event=>fail(event.reason));

async function bytes(url:string){
  const resolved=new URL(url,location.href);
  if(resolved.origin!==location.origin)throw new Error('只允许本机同源资源');
  const response=await fetch(resolved,{signal:AbortSignal.timeout(15000)});
  if(!response.ok)throw new Error(`${resolved.pathname}: HTTP ${response.status}`);
  diagnostics.resources.push(resolved.pathname);
  return response.arrayBuffer();
}
class PrototypeModel extends CubismUserModel {
  body:StaticBody|null=null;
  motions:any[]=[];idle:any[]=[];tilt=new TiltController();
  parameters:Map<string,number>=new Map();elapsed=0;
  async initialize(url:string,gl:WebGL2RenderingContext){
    const setting=JSON.parse(new TextDecoder().decode(await bytes(url)));
    if(setting.Version!==3)throw new Error('需要 model3.json Version 3');
    const refs=setting.FileReferences;
    if(!refs||typeof refs.Moc!=='string'||!refs.Moc||!Array.isArray(refs.Textures)||!refs.Textures.length)
      throw new Error('model3.json 必须包含 moc3 和至少一张纹理');
    const base=new URL('.',new URL(url,location.href));
    const resource=(name:string)=>{
      if(typeof name!=='string'||!name)throw new Error('模型资源名称无效');
      const target=new URL(name,base);
      const decoded=decodeURIComponent(target.pathname);
      if(target.origin!==base.origin||!decoded.startsWith(decodeURIComponent(base.pathname))||decoded.split('/').includes('..'))throw new Error('模型资源引用越界');
      return target.href;
    };
    const moc=await bytes(resource(refs.Moc));
    diagnostics.mocVersion=CubismMoc.getMocVersionFromBuffer(moc);
    diagnostics.latestMoc=(window as any).Live2DCubismCore.Version.csmGetLatestMocVersion();
    diagnostics.coreVersion=(window as any).Live2DCubismCore.Version.csmGetVersion();
    if(diagnostics.mocVersion>diagnostics.latestMoc)throw new Error('moc3 版本高于当前 Core');
    if(!CubismMoc.hasMocConsistency(moc))throw new Error('moc3 一致性检查失败');
    this.loadModel(moc,true);
    if(!this.getModel())throw new Error('Core 未创建模型');
    this.createRenderer(canvas.width,canvas.height);
    this.getRenderer().startUp(gl);
    this.getRenderer().loadShaders('./shaders/');
    this.getRenderer().setIsPremultipliedAlpha(true);
    for(let i=0;i<refs.Textures.length;i++){
      const data=await bytes(resource(refs.Textures[i]));
      const texture=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,texture);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL,true);
      // ImageBitmap ignores UNPACK_PREMULTIPLY_ALPHA_WEBGL: explicitly request premultiplication instead.
      const premultiplied=await createImageBitmap(new Blob([data],{type:'image/png'}),{premultiplyAlpha:'premultiply'});
      gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,premultiplied);
      gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
      this.getRenderer().bindTexture(i,texture);premultiplied.close();
    }
    if(refs.Physics){const data=await bytes(resource(refs.Physics));this.loadPhysics(data,data.byteLength);diagnostics.physics=true}
    if(refs.Pose){const data=await bytes(resource(refs.Pose));this.loadPose(data,data.byteLength)}
    const effectIds=(name:string)=>{const result:any[]=[];for(const id of setting.Groups?.find((g:any)=>g.Name===name)?.Ids||[])result.push(CubismFramework.getIdManager().getId(id));return result};
    for(const [group,entries] of Object.entries(refs.Motions||{})){
      for(const item of entries as any[]){
        const data=await bytes(resource(item.File));
        const motion=this.loadMotion(data,data.byteLength,item.File);
        if(!motion)throw new Error('动作解析失败：'+item.File);
        motion.setEffectIds(effectIds('EyeBlink'),effectIds('LipSync'));
        if(group==='Idle')this.idle.push(motion);else this.motions.push(motion);
      }
    }
    // Original parameter values are the baseline for every frame.
    this.getModel().saveParameters();
    this.parameters=parameterMap(this.getModel());
  }
  trigger(){const choices=this.motions.length?this.motions:this.idle;if(!choices.length)return;this._motionManager.startMotionPriority(choices[0],false,3);diagnostics.motions++;publish()}
  frame(dt:number,gl:WebGL2RenderingContext){
    this._model.loadParameters();
    if(this._motionManager.isFinished()&&this.idle.length)this._motionManager.startMotionPriority(this.idle[0],false,1);
    this._motionManager.updateMotion(this._model,dt);
    this.elapsed+=dt;
    diagnostics.state=states.current();
    renderWaiting();
    diagnostics.stateParameters=applyState(this._model,this.parameters,diagnostics.state,this.elapsed,states.ageSeconds(),this.tilt.sample(diagnostics.state,dt));
    for(const [id,value] of [['ParamAngleZ',headReview],['ParamBreath',breathReview]] as const){
      if(value!==null&&this.parameters.has(id)){const index=this.parameters.get(id)!;this._model.setParameterValueByIndex(index,Math.max(this._model.getParameterMinimumValue(index),Math.min(this._model.getParameterMaximumValue(index),value)));}
    }
    if(eyeReview!==null)applyEyeOpen(this._model,this.parameters,eyeSide,eyeReview);
    if(this._physics){this._physics.evaluate(this._model,dt);if(hairReview===null)boostTailOutput(this._model,this.parameters,hairGain);}
    if(hairReview!==null&&this.parameters.has('ParamHairSide'))this._model.setParameterValueByIndex(this.parameters.get('ParamHairSide')!,Math.max(-1,Math.min(1,hairReview)));
    if(this._pose)this._pose.updateParameters(this._model,dt);
    for(const id of ['ParamBreath','ParamAngleZ','ParamHairSide','ParamEyeLOpen','ParamEyeROpen']){
      const index=this.parameters.get(id);if(index===undefined)continue;const value=this._model.getParameterValueByIndex(index);
      diagnostics.parameterValues[id]=value;const range=diagnostics.parameterRanges[id];
      diagnostics.parameterRanges[id]={min:Math.min(range?.min??value,value),max:Math.max(range?.max??value,value)};
    }
    this._model.update();
    const matrix=new CubismMatrix44();matrix.scale(canvas.height/canvas.width*diagnostics.scale,diagnostics.scale);
    matrix.multiplyByMatrix(this.getModelMatrix());
    this.body?.draw(matrix,this.getModel().getModel().canvasinfo,canvas.width,canvas.height);
    gl.bindFramebuffer(gl.FRAMEBUFFER,null);gl.viewport(0,0,canvas.width,canvas.height);
    gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
    this.getRenderer().setMvpMatrix(matrix);
    this.getRenderer().setRenderState(null,[0,0,canvas.width,canvas.height]);
    this.getRenderer().drawModel('./shaders/');
  }
}
async function start(){
  const config=JSON.parse(new TextDecoder().decode(await bytes('./model-config.json')));
  if(Number.isFinite(config.scale)&&config.scale>=.5&&config.scale<=1.6){
    diagnostics.scale=config.scale;(document.querySelector('#scale') as HTMLInputElement).value=String(config.scale);
  }
  hairGain=new URLSearchParams(location.search).get('visual')==='before'?1:Math.max(1,Math.min(1.75,config.tailOutputGain??1.6));
  showFootShadow=new URLSearchParams(location.search).get('visual')!=='before' && !config.sample;
  document.querySelector('#label')!.textContent=config.label;
  const rect=canvas.getBoundingClientRect();const dpr=Math.min(devicePixelRatio,2);
  canvas.width=Math.max(1,Math.round(rect.width*dpr));canvas.height=Math.max(1,Math.round(rect.height*dpr));
  const gl=canvas.getContext('webgl2',{alpha:true,premultipliedAlpha:true,preserveDrawingBuffer:true});
  if(!gl)throw new Error('当前窗口不支持 WebGL2');
  const options=new Option();options.loggingLevel=LogLevel.LogLevel_Warning;options.logFunction=message=>console.log(message);
  CubismFramework.startUp(options);CubismFramework.initialize();
  let model=new PrototypeModel();
  try{await model.initialize(config.model,gl)}
  catch(error){
    model.release();
    if(!config.fallbackModel||config.fallbackModel===config.model)throw error;
    diagnostics.warnings.push('自定义模型加载失败：'+error+'；已回退官方 Wanko（非明日香）');
    diagnostics.fallback=true;showFootShadow=false;
    model=new PrototypeModel();await model.initialize(config.fallbackModel,gl);
    document.querySelector('#label')!.textContent='官方 Wanko 回退示例（非明日香）';
  }
  activeModel=model;
  if(config.staticBody&&!diagnostics.fallback){
    try{
      const source=await createImageBitmap(new Blob([await bytes(config.staticBody.source)]));
      const head=await createImageBitmap(new Blob([await bytes(config.staticBody.headMask)]));
      try{model.body=new StaticBody(source,head,config.staticBody.top,config.staticBody.outline);diagnostics.staticBody=true;
        if(Number.isFinite(config.staticBody.scale)&&config.staticBody.scale>=.5&&config.staticBody.scale<=1.6){diagnostics.scale=config.staticBody.scale;(document.querySelector('#scale') as HTMLInputElement).value=String(diagnostics.scale)}}
      finally{source.close();head.close()}
    }catch(error){diagnostics.warnings.push('静态身体加载失败，保留原 Live2D 头部：'+error)}
  }
  (document.querySelector('#eyeOpen') as HTMLInputElement).oninput=event=>{
    eyeReview=Number((event.target as HTMLInputElement).value);
    document.querySelector('#eyeValue')!.textContent=String(eyeReview);
  };
  (document.querySelector('#eyeSide') as HTMLSelectElement).onchange=event=>eyeSide=(event.target as HTMLSelectElement).value;
  (document.querySelector('#autoBlink') as HTMLButtonElement).onclick=()=>{eyeReview=null;document.querySelector('#eyeValue')!.textContent='自动'};
  for(const [control,id] of [['headAngle','ParamAngleZ'],['breathValue','ParamBreath'],['hairValue','ParamHairSide']]){
    const input=document.querySelector('#'+control) as HTMLInputElement;
    if(!input)continue;input.disabled=!model.parameters.has(id);
    input.oninput=()=>{const value=Number(input.value);if(!Number.isFinite(value))return;
      if(id==='ParamAngleZ')headReview=value;else if(id==='ParamHairSide')hairReview=value;else breathReview=value;
    };
  }
  const capture=document.querySelector('#capturePose') as HTMLButtonElement;
  if(capture){capture.disabled=false;capture.onclick=()=>{
    delete capture.dataset.png; delete capture.dataset.pose;
    // Capture after the next rendered frame, not the frame before a slider input.
    requestAnimationFrame(()=>{
      capture.dataset.png=canvas.toDataURL('image/png');
      capture.dataset.pose=JSON.stringify(diagnostics);
    });
  }};
  const autoPose=document.querySelector('#autoPose') as HTMLButtonElement;
  if(autoPose)autoPose.onclick=()=>{headReview=null;breathReview=null;hairReview=null};
  const eyes=['ParamEyeLOpen','PARAM_EYE_L_OPEN','ParamEyeROpen','PARAM_EYE_R_OPEN'].filter(id=>model.parameters.has(id));
  if(!eyes.length)diagnostics.warnings.push('模型缺少标准眼睛开闭参数，已安全跳过眨眼；参数存在也不代表已有绑定');
  motionButton.disabled=false;pauseButton.disabled=false;
  motionButton.onclick=()=>model.trigger();
  (document.querySelector('#assistantState') as HTMLSelectElement).onchange=event=>setState((event.target as HTMLSelectElement).value);
  pauseButton.onclick=()=>{diagnostics.paused=!diagnostics.paused;pauseButton.textContent=diagnostics.paused?'继续':'暂停';publish()};
  (document.querySelector('#scale') as HTMLInputElement).oninput=event=>{diagnostics.scale=Number((event.target as HTMLInputElement).value);publish()};
  let previous=performance.now(),notified=false;
  function loop(now:number){
    try{
      const dt=Math.min((now-previous)/1000,.05);previous=now;
      if(!diagnostics.paused&&!document.hidden){
        const size=canvas.getBoundingClientRect();const width=Math.round(size.width*dpr),height=Math.round(size.height*dpr);
        if(width>0&&height>0&&(canvas.width!==width||canvas.height!==height)){canvas.width=width;canvas.height=height}
        model.frame(dt,gl);diagnostics.frames++;
        if(!notified||diagnostics.frames%60===0||shadowScale!==diagnostics.scale){
          const pixels=new Uint8Array(canvas.width*canvas.height*4);gl.readPixels(0,0,canvas.width,canvas.height,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
          // Compact alpha occupancy map: only visible character pixels intercept input.
          if((window as any).webkit?.messageHandlers?.live2d){
            const cols=51,rows=56,cells=new Array(cols*rows).fill(0);
            for(let y=0;y<canvas.height;y++)for(let x=0;x<canvas.width;x++){
              if(pixels[(y*canvas.width+x)*4+3]>32){
                const cx=Math.min(cols-1,Math.floor(x/canvas.width*cols));
                const cy=Math.min(rows-1,Math.floor(y/canvas.height*rows));cells[cy*cols+cx]=1;
              }
            }
            (window as any).webkit.messageHandlers.live2d.postMessage({type:'hitMask',cols,rows,cells,frame:{x:size.left/window.innerWidth,y:(window.innerHeight-size.bottom)/window.innerHeight,width:size.width/window.innerWidth,height:size.height/window.innerHeight}});
          }
          drawFootShadow(pixels,canvas.width,canvas.height);
          diagnostics.alphaCorner=pixels[3];diagnostics.visiblePixels=0;
          let minX=canvas.width,maxX=-1,minY=canvas.height,maxY=-1;
          for(let i=3;i<pixels.length;i+=4){
            if(pixels[i]>0)diagnostics.visiblePixels++;
            if(pixels[i]>32){const p=(i-3)/4,x=p%canvas.width,y=Math.floor(p/canvas.width);minX=Math.min(minX,x);maxX=Math.max(maxX,x);minY=Math.min(minY,y);maxY=Math.max(maxY,y)}
          }
          if(maxX>=minX)waitingBubble.setCharacter({left:size.left+minX/width*size.width,top:size.top+(height-1-maxY)/height*size.height,width:(maxX-minX+1)/width*size.width,height:(maxY-minY+1)/height*size.height},`${width}:${height}:${size.left}:${size.top}:${diagnostics.scale}`);
          if(diagnostics.visiblePixels>0&&!notified){notified=true;diagnostics.ready=true;(document.querySelector('#recordReview') as HTMLButtonElement).disabled=false;native('ready')}
          status.textContent=`${config.sdk} · moc ${diagnostics.mocVersion}/${diagnostics.latestMoc}\n${diagnostics.ready?'模型已显示，背景透明':'等待着色器与模型绘制…'} · ${diagnostics.state}\n${diagnostics.warnings.join('\n')}`;publish();
        }
      }
      requestAnimationFrame(loop);
    }catch(error){fail(error)}
  }
  requestAnimationFrame(loop);
  installReviewRecording();
  setTimeout(()=>{if(!diagnostics.ready)fail('20 秒内未绘出模型，回退蓝色助手')},20000);
  window.addEventListener('pagehide',()=>{activeModel?.release();activeModel=null},{once:true});
}
// Review captures actual Cubism rendering at the native character size, not PNG animation.
function installReviewRecording(){
  const button=document.querySelector('#recordReview') as HTMLButtonElement;
  button.onclick=()=>{
    if(!reviewMode || !diagnostics.ready)return;
    button.disabled=true;button.textContent='正在录制：待机 → 搜索 → 回正 → 取消';delete button.dataset.video;
    const output=document.createElement('canvas');output.width=204;output.height=224;
    const ctx=output.getContext('2d')!;
    const mime=['video/mp4','video/webm;codecs=vp9','video/webm'].find(m=>MediaRecorder.isTypeSupported(m));
    if(!mime){button.disabled=false;button.textContent='当前浏览器不支持录像';return;}
    const chunks:Blob[]=[];const stream=output.captureStream(30);const recorder=new MediaRecorder(stream,{mimeType:mime});
    const frames:any[]=[];let start=performance.now();
    const sequence:[number,string][]=[[0,'idle'],[1.5,'searching'],[5,'generating'],[6.5,'complete'],[8,'idle'],[9,'searching'],[11,'idle']];
    let phase=-1;recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data)};
    recorder.onstop=()=>{
      stream.getTracks().forEach(t=>t.stop());
      const reader=new FileReader();reader.onload=()=>{button.dataset.video=String(reader.result);button.dataset.mime=mime;button.dataset.poses=JSON.stringify(frames);button.disabled=false;button.textContent='录像已完成（状态序列模拟，真实moc3）';};
      reader.readAsDataURL(new Blob(chunks,{type:mime}));
    };recorder.start();
    function record(now:number){
      const seconds=(now-start)/1000;
      const index=sequence.reduce((value,item,i)=>seconds>=item[0]?i:value,0);
      if(index!==phase){phase=index;setState(sequence[index][1]);button.textContent='正在录制：'+(sequence[index][1]==='searching'?'缓慢倾斜并保持':index===6?'取消后回正':sequence[index][1]);}
      ctx.fillStyle='#f5f6f8';ctx.fillRect(0,0,204,224);ctx.drawImage(shadowCanvas,50,32,154,192);ctx.drawImage(canvas,50,32,154,192);
      if(diagnostics.state==='searching'){
        ctx.save();ctx.translate(16,28);ctx.fillStyle='#fff';ctx.strokeStyle='#111';ctx.lineWidth=1.5;ctx.lineJoin='round';
        const bubble=new Path2D('M12 2 H56 Q64 2 64 10 V22 Q64 28 59 29 L72 40 L52 30 H12 Q4 30 4 22 V10 Q4 2 12 2 Z');ctx.fill(bubble);ctx.stroke(bubble);
        ctx.fillStyle='#111';for(let i=0;i<3;i++){ctx.beginPath();ctx.arc(20+i*11,16-3*Math.max(0,Math.sin(seconds*5.2-i*.8)),2,0,Math.PI*2);ctx.fill();}ctx.restore();
      }
      frames.push({seconds,state:diagnostics.state,parameters:{...diagnostics.parameterValues}});
      if(seconds<14)requestAnimationFrame(record);else{setState('idle');recorder.stop();}
    }
    requestAnimationFrame(record);
  };
}
start().catch(fail);
