import assert from 'node:assert/strict';
import test from 'node:test';
import {pitchTransform,pitchPoint} from '../../static/js/game/pitch-2d.js';
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
