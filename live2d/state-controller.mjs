// Own runtime glue: no model content, chat text or file paths cross this bridge.
export const STATES = ['idle', 'searching', 'generating', 'complete'];
export class StateController {
  constructor(clock = () => performance.now()) {
    this.clock = clock; this.state = 'idle'; this.since = clock();
  }
  set(state) {
    if (!STATES.includes(state)) return false;
    if (this.state !== state) { this.state = state; this.since = this.clock(); }
    return true;
  }
  current() {
    if (this.state === 'complete' && this.clock() - this.since >= 3000) this.set('idle');
    return this.state;
  }
  ageSeconds() { this.current(); return Math.max(0, (this.clock() - this.since) / 1000); }
}
export function parameterMap(model) {
  const result = new Map();
  for (let i = 0; i < model.getParameterCount(); i++) {
    const id = model.getParameterId(i).getString();
    result.set(typeof id === 'string' ? id : id.s, i);
  }
  return result;
}
const smooth = value => { const x = Math.max(0, Math.min(1, value)); return x*x*(3-2*x); };
// Bounded repeatable pauses; eye values drive the real rig, not an image overlay.
export function blinkOpen(seconds) {
  const pauses = [3.7, 4.8, 3.2, 5.1];
  const cycle = pauses.reduce((sum, pause) => sum + pause + .3, 0);
  let phase = ((seconds % cycle) + cycle) % cycle;
  for (const pause of pauses) {
    if (phase < pause) return 1;
    phase -= pause;
    if (phase < .09) return 1 - smooth(phase / .09);
    if (phase < .13) return 0;
    if (phase < .3) return smooth((phase - .13) / .17);
    phase -= .3;
  }
  return 1;
}
// One request episode, one eased lean. Only this controller owns head AngleZ.
export class TiltController {
  constructor(){this.value=0;this.velocity=0;this.seenSearch=false;this.searchPoseActive=false;this.lastState='idle';}
  resetEpisode(){this.seenSearch=false;this.searchPoseActive=false;}
  sample(state,dt){
    if(state==='idle' || state==='complete')this.resetEpisode();
    let target=0;
    if(state==='searching'){
      if(this.lastState!=='searching' && !this.seenSearch){this.seenSearch=true;this.searchPoseActive=true;}
      // Repeated tools in one request do not restart the lean after generating.
      target=this.searchPoseActive?3:0;
    } else if(state==='generating')this.searchPoseActive=false;
    this.lastState=state;
    dt=Math.max(0,Math.min(.1,dt));
    const omega=2.8,d=this.value-target,e=Math.exp(-omega*dt),temp=(this.velocity+omega*d)*dt;
    this.value=target+(d+temp)*e;
    this.velocity=(this.velocity-omega*temp)*e;
    return this.value;
  }
}
export function applyState(model, parameters, state, seconds, stateAge = seconds, tiltValue) {
  const written = [];
  const set = (aliases, value, weight = 1) => {
    const id = aliases.find(name => parameters.has(name));
    if (!id || !Number.isFinite(value)) return;
    const index = parameters.get(id);
    const clamped = Math.max(model.getParameterMinimumValue(index),
      Math.min(model.getParameterMaximumValue(index), value));
    model.setParameterValueByIndex(index, clamped, weight); written.push(id);
  };
  // Small deformations of existing rig parameters; never transform the whole PNG.
  set(['ParamBreath','PARAM_BREATH'], (Math.sin(seconds * 1.8) + 1) / 2, .35);
  // Existing unbound front parameter is a physics input, not a new front-hair rig.
  set(['ParamHairFront','PARAM_HAIR_FRONT'], .4*Math.sin(seconds*.92));
  const open = blinkOpen(seconds);
  set(['ParamEyeLOpen','PARAM_EYE_L_OPEN'], open);
  set(['ParamEyeROpen','PARAM_EYE_R_OPEN'], open);
  // No idle oscillation or completion pulse. Actual frames use the continuous controller.
  const angle=tiltValue ?? (state==='searching' ? 1.6*smooth(stateAge/1.8) : 0);
  set(['ParamAngleZ','PARAM_ANGLE_Z'], angle);
  return written;
}

// Review real rig parameters; never replace a model with image animation.
export function applyEyeOpen(model, parameters, side, value) {
  if (!['both','left','right'].includes(side) || !Number.isFinite(value)) return [];
  const result=[];
  for (const eye of side==='both'?['left','right']:[side]) {
    const aliases=eye==='left'?['ParamEyeLOpen','PARAM_EYE_L_OPEN']:['ParamEyeROpen','PARAM_EYE_R_OPEN'];
    const id=aliases.find(name=>parameters.has(name));
    if(!id)continue;
    const index=parameters.get(id);
    model.setParameterValueByIndex(index,Math.max(model.getParameterMinimumValue(index),
      Math.min(model.getParameterMaximumValue(index),value)));
    result.push(id);
  }
  return result;
}

// Backend NDJSON phases, forwarded only by the compact native session.
export class TaskStateGate {
  constructor(emit){this.emit=emit;this.id=null;this.sequence=-1;this.closed=true;}
  accept(event){
    if(!event || typeof event.request_id!=='string' || !Number.isFinite(event.sequence))return false;
    if(event.state==='queued'){
      this.id=event.request_id;this.sequence=event.sequence;this.closed=false;this.emit('idle');return true;
    }
    if(event.request_id!==this.id || this.closed || event.sequence<=this.sequence)return false;
    this.sequence=event.sequence;
    if(!STATES.includes(event.state))return false;
    this.emit(event.state);
    if(event.state==='idle' || event.state==='complete')this.closed=true;
    return true;
  }
  disconnect(){this.id=null;this.closed=true;this.emit('idle');}
}
export function installChatStateBridge(doc, emit) {
  const gate=new TaskStateGate(emit);
  const listen=event=>gate.accept(event.detail);
  doc.addEventListener('local-file-chat-task',listen);
  emit('idle');
  const snapshot=doc.defaultView?.localFileChatTaskState;
  if(snapshot?.request_id && snapshot.state!=='idle'){
    gate.accept({...snapshot,state:'queued',sequence:-1});
    // Never replay a stale completion after a page/bridge reconnect.
    if(snapshot.state!=='complete' || Date.now()-snapshot.at<3000)gate.accept(snapshot);
  }
  const leave=()=>gate.disconnect();
  doc.defaultView?.addEventListener('pagehide',leave);
  return ()=>{doc.removeEventListener('local-file-chat-task',listen);doc.defaultView?.removeEventListener('pagehide',leave);gate.disconnect();};
}

// Only the existing tail physics output is amplified, after physics, never head/body.
export function boostTailOutput(model, parameters, gain=1.6) {
  const index=parameters.get('ParamHairSide');
  if(index===undefined || !Number.isFinite(gain))return;
  const value=model.getParameterValueByIndex(index);
  model.setParameterValueByIndex(index,Math.max(model.getParameterMinimumValue(index),
    Math.min(model.getParameterMaximumValue(index),value*Math.max(1,Math.min(1.75,gain)))));
}
