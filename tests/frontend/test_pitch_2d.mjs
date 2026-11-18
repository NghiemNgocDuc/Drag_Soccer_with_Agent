import assert from 'node:assert/strict';
import test from 'node:test';
import {basketballShotTarget,createPitch2D,pitchTransform,pitchPoint} from '../../static/js/game/pitch-2d.js';
import {playTrajectory2D} from '../../static/js/game/playback-2d.js';

test('2D pointer mapping preserves server coordinates at desktop, mobile and letterboxed sizes',()=>{
  for(const [width,height] of [[1200,794],[390,258],[1920,1080],[500,900]]) {
    const rect={left:17,top:83,width,height},t=pitchTransform(width,height);
    for(const p of [{x:-30,y:356},{x:1430,y:519},{x:700,y:437.5},{x:0,y:0}]) {
      const client={x:rect.left+t.x+p.x*t.scale,y:rect.top+t.y+p.y*t.scale};
      assert.ok(client.x>=rect.left&&client.x<=rect.left+width);
      assert.ok(client.y>=rect.top&&client.y<=rect.top+height);
      const result=pitchPoint(client.x,client.y,rect);
      assert.ok(Math.abs(result.x-p.x)<1e-9&&Math.abs(result.y-p.y)<1e-9);
    }
  }
  assert.equal(pitchPoint(0,0,{width:0,height:0}),null);
});

test('basketball aim predicts the descending rim crossing for both ends and saved Power stats',()=>{
  const power=70.32071565401695;
  const right=basketballShotTarget({x:650,y:437.5},0,power);
  const left=basketballShotTarget({x:750,y:437.5},Math.PI,power);
  assert.ok(Math.abs(right.x-1300)<1e-8&&Math.abs(right.y-437.5)<1e-8);
  assert.ok(Math.abs(left.x-100)<1e-8&&Math.abs(left.y-437.5)<1e-8);
  assert.ok(Math.abs(right.time-.9243364404851161)<1e-9);
  const weak=basketballShotTarget({x:0,y:0},0,50,0);
  const strong=basketballShotTarget({x:0,y:0},0,50,100);
  assert.ok(strong.x>weak.x+180);
});

const path=[0,.03,.2,.6,1].map((t,i)=>({t,x:700+300*t,y:437,z:0,
  a:[{x:605+120*t,y:437}],b:[{x:795,y:437}],ref:{x:700,y:795-60*t},bounce:i===2}));
for(const fps of [10,30,60,144]) test(`2D playback synchronizes all actors at ${fps} FPS and preserves final samples`,()=>{
  let queue=[],done=0,bounces=0,last;
  const before=structuredClone(path);
  playTrajectory2D(path,{loop:{requestAnimationFrame:fn=>queue.push(fn)},onFrame:p=>{last=p;},
    onBounce:()=>bounces++,onDone:()=>done++});
  for(let i=0;queue.length&&i<=fps+2;i++) {
    const batch=queue;queue=[];batch.forEach(fn=>fn(i*1000/fps));
    const t=Math.min(1,i/fps);
    assert.ok(Math.abs(last.x-(700+300*t))<1e-6);
    assert.ok(Math.abs(last.a[0].x-(605+120*t))<1e-6);
    assert.ok(Math.abs(last.ref.y-(795-60*t))<1e-6);
  }
  assert.equal(done,1);assert.equal(bounces,1);assert.equal(queue.length,0);
  assert.deepEqual(last,path.at(-1));assert.deepEqual(path,before);
});

test('cancelled 2D playback stops writing positions and completes once',()=>{
  let queue=[],current=true,writes=0,done=0;
  playTrajectory2D(path,{loop:{requestAnimationFrame:fn=>queue.push(fn)},current:()=>current,
    onFrame:()=>writes++,onDone:()=>done++});
  queue.shift()(0);current=false;queue.shift()(100);
  assert.equal(writes,1);assert.equal(done,1);assert.equal(queue.length,0);
});

test('empty and single-frame replays finish immediately',()=>{
  for(const points of [[],[path[0]]]) {
    let done=0,frames=[];
    playTrajectory2D(points,{loop:{requestAnimationFrame:()=>assert.fail('unexpected animation')},
      onFrame:p=>frames.push(p),onDone:()=>done++});
    assert.equal(done,1);assert.deepEqual(frames,points);
  }
});

test('slow frames deliver each player and wall impact once with full roster data',()=>{
  const points=path.map((p,i)=>({...p,contact:i===1||i===3}));
  let queue=[],contacts=0,walls=0,sounds=0,done=0;
  playTrajectory2D(points,{loop:{requestAnimationFrame:fn=>queue.push(fn)},
    pitch:{onContact:()=>contacts++,onBounce:()=>walls++},onBounce:()=>sounds++,
    onFrame:p=>assert.equal(p.b.length,1),onDone:()=>done++});
  queue.shift()(0);queue.shift()(900);queue.shift()(1000);
  assert.deepEqual({contacts,walls,sounds,done},{contacts:2,walls:1,sounds:3,done:1});
});

test('long 2D moves use the four-second playback cap and finish at the exact endpoint',()=>{
  const points=path.map(p=>({...p,t:p.t*8}));
  let queue=[],done=0,last;
  playTrajectory2D(points,{loop:{requestAnimationFrame:fn=>queue.push(fn)},
    onFrame:p=>{last=p;},onDone:()=>done++});
  queue.shift()(0);queue.shift()(2400);
  assert.equal(done,0);assert.ok(last.x<points.at(-1).x);
  queue.shift()(4000);
  assert.equal(done,1);assert.deepEqual(last,points.at(-1));assert.equal(queue.length,0);
});

test('basketball playback preserves release and catch possession between recorded samples',()=>{
  const carrierA={side:'A',player_idx:0},carrierB={side:'B',player_idx:2};
  const points=path.map((point,index)=>({...point,sport:'basketball',basketball:true,
    possession:index===0?carrierA:index===path.length-1?carrierB:null}));
  let queue=[],last;
  playTrajectory2D(points,{loop:{requestAnimationFrame:callback=>queue.push(callback)},
    onFrame:point=>{last=point;},onDone:()=>{}});
  queue.shift()(0);assert.deepEqual(last.possession,carrierA);
  queue.shift()(100);assert.equal(last.possession,null);
  assert.equal(last.sport,'basketball');assert.equal(last.basketball,true);
  queue.shift()(900);assert.equal(last.possession,null);
  queue.shift()(1000);assert.deepEqual(last.possession,carrierB);
  assert.equal(queue.length,0);
});

test('basketball switches the cached court, tracks possession and finishes its score effect at idle',t=>{
  const pending=new Map(),texts=[],arcs=[],attributes={};let nextId=1,now=0;
  const noop=()=>{};
  const context=new Proxy({
    createLinearGradient:()=>({addColorStop:noop}),createRadialGradient:()=>({addColorStop:noop}),
    fillText:value=>texts.push(value),
    arc:(...values)=>arcs.push(values),
  },{get:(target,key)=>target[key]??noop});
  const rect={left:0,top:0,right:1000,bottom:660,width:1000,height:660};
  const container={getBoundingClientRect:()=>rect,append:noop};
  const globals={
    document:{hidden:false,getElementById:()=>null,addEventListener:noop,removeEventListener:noop,
      createElement:()=>({width:300,height:150,style:{},getContext:()=>context,remove:noop,
        setAttribute:(key,value)=>{attributes[key]=value;},getBoundingClientRect:()=>rect})},
    window:{devicePixelRatio:1,innerWidth:2000,innerHeight:2000,addEventListener:noop,removeEventListener:noop},
    ResizeObserver:class {observe(){}disconnect(){}},localStorage:{getItem:()=>null},
    requestAnimationFrame:callback=>{const id=nextId++;pending.set(id,callback);return id;},
    cancelAnimationFrame:id=>pending.delete(id),performance:{now:()=>now},reportError:error=>{throw error;},
  };
  const originals=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
  for(const [key,value] of Object.entries(globals))Object.defineProperty(globalThis,key,{value,configurable:true,writable:true});
  let pitch;
  t.after(()=>{pitch?.dispose();for(const [key,descriptor] of originals) {
    if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];
  }});
  pitch=createPitch2D(container);
  const flush=time=>{now=time;const batch=[...pending.values()];pending.clear();batch.forEach(callback=>callback(time));};
  flush(0);
  pitch.setBall(730,437.5);flush(16);
  assert.ok(pitch.snapshot().ballRotation>0);
  const originalBuilds=pitch.snapshot().backgroundBuilds;
  const state={sport:'basketball',possession:{side:'A',player_idx:0},last_points:3};
  pitch.setState(state);pitch.setRoster([{x:650,y:437.5}],[{x:1000,y:400}]);flush(16);
  assert.equal(pitch.snapshot().sport,'basketball');
  assert.deepEqual(pitch.snapshot().possession,{team:'a',index:0});
  assert.match(attributes['aria-label'],/Basketball court/);assert.ok(texts.includes('BALL'));
  assert.equal(pitch.snapshot().backgroundBuilds,originalBuilds+1);
  const threePointArcs=arcs.filter(([, ,radius])=>radius===360);
  assert.equal(threePointArcs.length,2);
  for(const [x,y,radius,start,end] of threePointArcs){
    // Baseline intersections retain the same radial 2/3-point boundary as midfield.
    assert.ok(Math.abs(x+radius*Math.cos(start)-20)<1e-9);
    assert.ok(Math.abs(x+radius*Math.cos(end)-20)<1e-9);
    assert.ok(y+radius*Math.sin(start)<97.5);
    assert.ok(y+radius*Math.sin(end)>777.5);
  }
  pitch.setState({...state});
  assert.equal(pending.size,0);assert.equal(pitch.snapshot().backgroundBuilds,originalBuilds+1);
  pitch.setSelection('a',0,true);
  pitch.setAim({x:650,y:437.5},{kickAngle:0,power:70.32071565401695,pullX:580,pullY:437.5});
  flush(24);assert.ok(texts.includes('70% · +3 AIM'));assert.equal(pending.size,0);
  pitch.clearAim();flush(28);
  pitch.celebrate();flush(32);assert.ok(texts.includes('BASKET +3'));
  flush(1500);assert.equal(pending.size,0);assert.equal(pitch.snapshot().effects.particles,0);
  pitch.setState({});flush(1516);
  assert.equal(pitch.snapshot().sport,'soccer');assert.equal(pitch.snapshot().possession,null);
  assert.match(attributes['aria-label'],/Soccer pitch/);
  assert.equal(pitch.snapshot().backgroundBuilds,originalBuilds+2);
});
