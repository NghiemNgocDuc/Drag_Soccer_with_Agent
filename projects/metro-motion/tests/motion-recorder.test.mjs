import test from 'node:test';
import assert from 'node:assert/strict';
import { MotionRecorder, LocalClipStore, LIMITS, validateClip, validateDataset, makeDataset } from '../web/js/motion-recorder.mjs';

function points() {
  const result = Array.from({length:33}, () => ({x:.5,y:.5,z:0,visibility:.99,presence:.99}));
  const anchors = {0:[.5,.18],11:[.4,.32],12:[.6,.32],23:[.44,.56],24:[.56,.56],25:[.44,.73],26:[.56,.73],27:[.44,.91],28:[.56,.91]};
  for (const [index,[x,y]] of Object.entries(anchors)) Object.assign(result[index],{x,y});
  return result;
}
const baseline = {shoulderX:.5,shoulderY:.32,hipX:.5,hipY:.56,width:.2,torso:.24,headY:.18,leftFootY:.91,rightFootY:.91};
const metadata = () => ({label:'neutral',baseline:{...baseline},participantId:'participant-1',sessionId:'session-1'});
function complete(label='neutral') {
  const recorder = new MotionRecorder(); recorder.start({...metadata(),label},1000);
  for (let t=1000;t<=7000;t+=50) recorder.frame(points(),t,t);
  recorder.tick(7000);
  assert.equal(recorder.phase,'review'); return recorder;
}

test('recording is explicit and countdown frames never enter the dataset', () => {
  const recorder = new MotionRecorder(); recorder.frame(points(),1000); assert.equal(recorder.draft,null);
  recorder.start(metadata(),1000);
  for(let t=1000;t<4000;t+=50) recorder.frame(points(),t);
  assert.equal(recorder.phase,'countdown'); assert.equal(recorder.draft.frames.length,0);
  recorder.frame(points(),4000); assert.equal(recorder.phase,'recording'); assert.equal(recorder.draft.frames[0].t,0);
});

test('complete capture needs explicit review and validated range before take', () => {
  const recorder = complete('jump'); assert.equal(recorder.draft.label,'jump');
  assert.throws(()=>recorder.take([0,500]),/context/);
  const clip=recorder.take([1000,1200]); assert.deepEqual(clip.range,[1000,1200]); assert.equal(recorder.phase,'idle'); assert.equal(recorder.draft,null);
  assert.throws(()=>recorder.take([500,600]),/no clip/);
});

test('camera loss during either countdown or capture aborts without a partial clip', () => {
  for(const time of [1000,4500]) {
    const recorder=new MotionRecorder(); recorder.start(metadata(),1000);
    for(let t=1000;t<time;t+=50) recorder.frame(points(),t);
    recorder.frame(null,time); assert.equal(recorder.phase,'idle'); assert.equal(recorder.draft,null); assert.match(recorder.reason,/lost/);
  }
});

test('duplicate, backwards, future, delayed and nonfinite frame times cannot enter a clip', () => {
  for(const [timestamp,now] of [[1000,1050],[999,1050],[1100,1000],[1100,1600],[NaN,1100],[Infinity,1100]]) {
    const recorder=new MotionRecorder(); recorder.start(metadata(),1000); recorder.frame(points(),1000);
    recorder.frame(points(),timestamp,now); assert.equal(recorder.phase,'idle'); assert.equal(recorder.draft,null);
  }
});

test('missing frame watchdog and gaps abort, while recording can start again cleanly', () => {
  const recorder=new MotionRecorder(); recorder.start(metadata(),1000); recorder.frame(points(),1000); recorder.tick(1451);
  assert.equal(recorder.phase,'idle');
  recorder.start(metadata(),2000); recorder.frame(points(),2000); recorder.frame(points(),2251); assert.equal(recorder.phase,'idle');
  recorder.start(metadata(),3000);
  for(let t=3000;t<=9000;t+=50) recorder.frame(points(),t);
  recorder.tick(9000); assert.equal(recorder.phase,'review'); assert.equal(recorder.draft.frames[0].t,0);
});

test('early finish and an unbounded frame rate cannot create a clip', () => {
  const recorder=new MotionRecorder(); recorder.start(metadata(),1000); recorder.finish(); assert.equal(recorder.phase,'idle');
  recorder.start(metadata(),1000);
  for(let t=1000;t<4000;t+=50) recorder.frame(points(),t);
  for(let t=4000;t<4100;t++) recorder.frame(points(),t);
  assert.equal(recorder.phase,'idle'); assert.match(recorder.reason,/frame limit/);
});

test('duplicate start cannot replace a pending recording and metadata is captured defensively', () => {
  const recorder=new MotionRecorder(), meta=metadata(); recorder.start(meta,1000); meta.baseline.width=99;
  assert.equal(recorder.draft.baseline.width,.2); assert.throws(()=>recorder.start(metadata(),1050),/Review or discard/);
});

test('all five labels round trip with raw landmarks and copied baseline, no hidden payload', () => {
  const dataset=makeDataset(['neutral','left','right','jump','duck'].map((label)=>complete(label).take([500,2900])));
  assert.equal(dataset.featureVersion,'mm-body-v1'); assert.equal(dataset.source,'human');
  const dirty={...dataset,video:'private',clips:dataset.clips.map((clip)=>({...clip,image:'private',frames:clip.frames.map((f)=>({...f,blob:'private',landmarks:f.landmarks.map((p)=>({...p,url:'private'}))}))}))};
  const clean=validateDataset(dirty); assert.equal(JSON.stringify(clean).includes('private'),false);
  dirty.clips[0].frames[0].landmarks[0].x=.1; assert.equal(clean.clips[0].frames[0].landmarks[0].x,.5);
});

test('ranges are bounded, have context, and include actual captured frames', () => {
  const clip=complete().take([500,2900]);
  for(const range of [[0,200],[500,600],[2900,3100],[800,700],[NaN,800],[500],[Infinity,700]]) assert.throws(()=>validateClip({...clip,range}));
  assert.doesNotThrow(()=>validateClip({...clip,range:[LIMITS.contextMs,LIMITS.contextMs+150]}));
});

test('invalid coordinates, missing joints, confidence and cropped feet fail validation', () => {
  const clip=complete().take([500,2900]);
  for(const mutate of [p=>p.pop(),p=>p[0].x=Infinity,p=>p[1].z=NaN,p=>p[1].visibility=2,p=>p[1].presence=-1,p=>p[28].visibility=.2,p=>p[27].y=1.1]) {
    const changed=structuredClone(clip); mutate(changed.frames[2].landmarks); assert.throws(()=>validateClip(changed));
  }
});

test('invalid timing, excessive frames, IDs, labels and baseline fail validation', () => {
  const clip=complete().take([500,2900]);
  for(const mutate of [c=>c.frames[1].t=0,c=>c.frames[1].t=-10,c=>c.frames[1].t=400,c=>c.frames=c.frames.slice(0,8),c=>c.frames.push(...c.frames),c=>c.label='slide',c=>c.id='<script>',c=>c.baseline.width=.001,c=>c.baseline.torso=NaN]) {
    const changed=structuredClone(clip); mutate(changed); assert.throws(()=>validateClip(changed));
  }
});

test('datasets reject duplicates, mixed session identities, unsupported versions and clip quota', () => {
  const clip=complete().take([500,2900]), data=makeDataset([clip]);
  assert.throws(()=>makeDataset([clip,clip]),/duplicate/);
  assert.throws(()=>makeDataset([clip,{...clip,id:'clip2',participantId:'participant2'}]),/session/);
  assert.throws(()=>makeDataset([clip,{...clip,id:'clip2',baseline:{...baseline,hipX:.6}}]),/session/);
  for(const change of [{version:2},{featureVersion:'unknown'},{source:'synthetic'},{format:'other'}]) assert.throws(()=>validateDataset({...data,...change}));
  const many=Array.from({length:201},(_,i)=>({...clip,id:`clip-${i}`})); assert.throws(()=>makeDataset(many),/200 clips/);
  assert.equal(makeDataset(many.slice(0,200)).clips.length,200);
});

test('local persistence unavailable reports a download fallback and leaves caller data intact', async () => {
  const store=new LocalClipStore(null), clips=[complete().take([500,2900])];
  await assert.rejects(store.open(),/storage is unavailable/);
  await assert.rejects(store.save({participantId:'person',clips}),/Download/);
  assert.equal(clips.length,1); assert.equal(clips[0].frames.length,61);
});

test('persistence writes are serialized, snapshotted and recover after quota failure', async () => {
  const store=new LocalClipStore(null), transactions=[];
  store.db={transaction(){const tx={objectStore:()=>({put(value){tx.value=value;}})};transactions.push(tx);return tx;}};
  const clips=[complete().take([500,2900])];
  const first=store.save({participantId:'person',clips}), second=store.save({participantId:'person',clips:[]});
  const rejection=assert.rejects(first,/storage is full/);
  clips[0].frames[0].landmarks[0].x=.2;
  await new Promise(setImmediate); assert.equal(transactions.length,1); assert.equal(transactions[0].value.clips[0].frames[0].landmarks[0].x,.5);
  transactions[0].onerror(); await rejection; await new Promise(setImmediate);
  assert.equal(transactions.length,2); assert.deepEqual(transactions[1].value.clips,[]); transactions[1].oncomplete(); await second;
});
