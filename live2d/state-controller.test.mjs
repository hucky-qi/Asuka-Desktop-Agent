import test from 'node:test';
import assert from 'node:assert/strict';
import {StateController,parameterMap,applyState,applyEyeOpen,installChatStateBridge,TaskStateGate,TiltController,blinkOpen} from './state-controller.mjs';

test('blink starts open, holds closed briefly, reopens smoothly and remains bounded',()=>{
  assert.equal(blinkOpen(0),1);assert.equal(blinkOpen(3.72)>0,true);
  assert.equal(blinkOpen(3.8),0);assert.ok(blinkOpen(3.9)>0 && blinkOpen(3.9)<1);
  assert.equal(blinkOpen(4),1);
  for(let t=0;t<50;t+=.007)assert.ok(blinkOpen(t)>=0 && blinkOpen(t)<=1);
});
test('search holds one tilt, completion returns to slow idle and eyes reopen',()=>{
  const values=new Map();const model={getParameterMinimumValue:()=>-30,getParameterMaximumValue:()=>30,
    setParameterValueByIndex:(i,v)=>values.set(i,v)};
  const p=new Map([['ParamAngleZ',0],['ParamEyeLOpen',1]]);
  applyState(model,p,'searching',20,3);const held=values.get(0);
  applyState(model,p,'searching',22,3);assert.equal(values.get(0),held);
  applyState(model,p,'complete',22,.7);assert.equal(values.get(0),0);
  applyState(model,p,'complete',22,2);const settled=values.get(0);
  applyState(model,p,'idle',22,0);assert.equal(values.get(0),settled);
  applyState(model,p,'idle',0,0);assert.equal(values.get(0),0);assert.equal(values.get(1),1);
});

test('idle has no head oscillation while breath and blink remain active',()=>{
 const values=new Map();const model={getParameterMinimumValue:()=>-30,getParameterMaximumValue:()=>30,setParameterValueByIndex:(i,v,w=1)=>values.set(i,v*w)};
 for(let t=0;t<40;t+=.02){applyState(model,new Map([['ParamAngleZ',0],['ParamBreath',1]]),'idle',t,0);assert.equal(values.get(0),0);assert.ok(values.get(1)>=0 && values.get(1)<=.35);}
});
test('idle wind is bounded and continuous without writing the physics output or body position',()=>{
 const values=new Map();const model={getParameterMinimumValue:()=>-1,getParameterMaximumValue:()=>1,setParameterValueByIndex:(i,v)=>values.set(i,v)};
 const parameters=new Map([['ParamHairFront',0],['ParamHairSide',1],['ParamBodyX',2]]);
 let previous=0,low=0,high=0;
 for(let t=0;t<20;t+=1/60){assert.deepEqual(applyState(model,parameters,'idle',t),['ParamHairFront']);const value=values.get(0);assert.ok(Math.abs(value)<=.4);assert.ok(Math.abs(value-previous)<.007);low=Math.min(low,value);high=Math.max(high,value);previous=value;}
 assert.ok(low<-.39 && high>.39);assert.equal(values.has(1),false);assert.equal(values.has(2),false);
});
test('eye review controls only requested real parameters and skips missing side',()=>{
  const writes=[];const model={getParameterMinimumValue:()=>0,getParameterMaximumValue:()=>1,
    setParameterValueByIndex:(i,v)=>writes.push([i,v])};
  const parameters=new Map([['ParamEyeLOpen',4]]);
  assert.deepEqual(applyEyeOpen(model,parameters,'both',.25),['ParamEyeLOpen']);
  assert.deepEqual(applyEyeOpen(model,parameters,'right',.5),[]);
  assert.deepEqual(applyEyeOpen(model,parameters,'left',5),['ParamEyeLOpen']);
  assert.deepEqual(applyEyeOpen(model,parameters,'left',NaN),[]);
  assert.deepEqual(writes,[[4,.25],[4,1]]);
});

test('complete expires, repeat state does not prolong it, invalid state is ignored',()=>{
  let time=0;const state=new StateController(()=>time);
  assert.equal(state.set('searching'),true);assert.equal(state.set('invalid'),false);
  state.set('complete');time=2000;state.set('complete');time=3001;
  assert.equal(state.current(),'idle');
});
test('missing parameters are skipped without synthetic parameter creation',()=>{
  const model={getParameterCount:()=>0,getParameterIndex:()=>{throw Error('must not be called')}};
  for(const state of ['idle','searching','complete'])assert.deepEqual(applyState(model,parameterMap(model),state,.1),[]);
});
test('existing parameters are clamped to their actual model range',()=>{
  const writes=[];const model={getParameterCount:()=>1,getParameterId:()=>({getString:()=>({s:'ParamAngleZ'})}),
    getParameterMinimumValue:()=>-.1,getParameterMaximumValue:()=>.1,
    setParameterValueByIndex:(i,value)=>writes.push([i,value])};
  assert.deepEqual(applyState(model,parameterMap(model),'searching',1),['ParamAngleZ']);
  assert.deepEqual(writes,[[0,.1]]);
});
test('real request events gate phases, stale completion and sequence replay',()=>{
 const states=[];const gate=new TaskStateGate(s=>states.push(s));
 gate.accept({state:'queued',request_id:'a',sequence:0});
 gate.accept({state:'searching',request_id:'a',sequence:2});
 gate.accept({state:'generating',request_id:'a',sequence:3});
 gate.accept({state:'complete',request_id:'a',sequence:4});
 assert.equal(gate.accept({state:'searching',request_id:'a',sequence:5}),false);
 gate.accept({state:'queued',request_id:'b',sequence:0});
 assert.equal(gate.accept({state:'complete',request_id:'a',sequence:6}),false);
 gate.accept({state:'searching',request_id:'b',sequence:2});
 assert.equal(gate.accept({state:'generating',request_id:'b',sequence:1}),false);
 gate.accept({state:'idle',request_id:'b',sequence:100});
 assert.equal(gate.accept({state:'complete',request_id:'b',sequence:101}),false);
 assert.deepEqual(states,['idle','searching','generating','complete','idle','searching','idle']);
});
test('bridge reconnect preserves active phase, pagehide resets and disposal detaches',()=>{
 const events={},windowEvents={},states=[];
 const view={localFileChatTaskState:{request_id:'a',state:'generating',sequence:4,at:Date.now()},
  addEventListener:(n,f)=>windowEvents[n]=f,removeEventListener:n=>delete windowEvents[n]};
 const doc={defaultView:view,addEventListener:(n,f)=>events[n]=f,removeEventListener:n=>delete events[n]};
 const dispose=installChatStateBridge(doc,s=>states.push(s));
 assert.equal(states.at(-1),'generating');windowEvents.pagehide();assert.equal(states.at(-1),'idle');
 dispose();assert.equal(Object.keys(events).length,0);
});
test('old completion is not replayed after reconnect; generating uses natural idle',()=>{
 const states=[];installChatStateBridge({defaultView:{addEventListener:()=>{},localFileChatTaskState:{state:'complete',request_id:'old',sequence:5,at:Date.now()-4000}},addEventListener:()=>{}},s=>states.push(s));
 assert.ok(!states.includes('complete'));
 const vals=[];const model={getParameterMinimumValue:()=>-3,getParameterMaximumValue:()=>3,setParameterValueByIndex:(i,v)=>vals.push(v)};
 applyState(model,new Map([['ParamAngleZ',0]]),'generating',10,0);const gen=vals.at(-1);
 applyState(model,new Map([['ParamAngleZ',0]]),'idle',10,0);assert.equal(gen,vals.at(-1));
});

test('lean is slow, held, continuously returned and never oscillates',()=>{
 const tilt=new TiltController();let prev=0;
 for(let i=0;i<240;i++){let v=tilt.sample('searching',1/60);assert.ok(v>=prev && v<=3.000001);assert.ok(v-prev<.055);prev=v;}
 assert.ok(prev>2.99);
 for(let i=0;i<240;i++){let v=tilt.sample('complete',1/60);assert.ok(v<=prev && v>=0);assert.ok(prev-v<.055);prev=v;}
 assert.ok(prev<.001);
 for(let i=0;i<240;i++)assert.ok(tilt.sample('idle',1/60)<.001);
});
test('cancel and intermediate tool phases do not replay a search lean',()=>{
 const tilt=new TiltController();for(let i=0;i<150;i++)tilt.sample('searching',1/60);
 for(let i=0;i<180;i++)tilt.sample('generating',1/60);
 const settled=tilt.value;for(let i=0;i<120;i++)tilt.sample('searching',1/60);assert.ok(tilt.value<settled);
 tilt.sample('idle',1/60);for(let i=0;i<180;i++)tilt.sample('searching',1/60);assert.ok(tilt.value>2.99);
 let previous=tilt.value;for(let i=0;i<180;i++){const v=tilt.sample('idle',1/60);assert.ok(Math.abs(v-previous)<.055);previous=v;}assert.ok(previous<.01);
});

// Tail-only gain preserves head/body and remains inside rig ranges.
test('tail gain is bounded and leaves head/body parameters unchanged',async ()=>{
  const {boostTailOutput}=await import('./state-controller.mjs');
  const values=[.4,3,.25];
  const model={getParameterValueByIndex:i=>values[i],getParameterMinimumValue:()=>-1,getParameterMaximumValue:()=>1,setParameterValueByIndex:(i,v)=>{values[i]=v}};
  boostTailOutput(model,new Map([['ParamHairSide',0]]),1.4);
  assert.ok(Math.abs(values[0]-.56)<1e-9);assert.equal(values[1],3);assert.equal(values[2],.25);
  values[0]=.9;boostTailOutput(model,new Map([['ParamHairSide',0]]),1.4);assert.equal(values[0],1);
  boostTailOutput(model,new Map(),1.4);assert.equal(values[0],1);
});
