import test from 'node:test';
import assert from 'node:assert/strict';
import {simplifyRun, clampPoint, emptyPlans} from '../../static/js/game/tactics-board.js';

test('long curved strokes keep endpoints and bends within the server limit',()=>{
  const points=Array.from({length:200},(_,i)=>({x:.12+i*.003,y:.5+Math.sin(i/199*Math.PI)*.25}));
  const run=simplifyRun(points);
  assert.ok(run.length<=16);
  assert.deepEqual(run[0],points[0]);assert.deepEqual(run.at(-1),points.at(-1));
  assert.ok(run.some(p=>p.y>.74));
});
test('keeper and outfield cursor bounds match the server',()=>{
  assert.deepEqual(clampPoint({x:2,y:-1},0),{x:.14,y:.4});
  assert.deepEqual(clampPoint({x:2,y:-1},3),{x:.94,y:.09});
  assert.deepEqual(clampPoint({x:-1,y:2},0),{x:.06,y:.6});
});
test('each player starts with independent automatic instructions',()=>{
  const plans=emptyPlans();plans[1].points.push({x:.4,y:.4});
  assert.equal(plans.length,5);assert.equal(plans[0].points.length,0);
  assert.equal(emptyPlans()[1].points.length,0);
});

test('a straight freehand run does not keep dozens of tiny stops',()=>{
  const points=Array.from({length:120},(_,i)=>({x:.2+i*.004,y:.3}));
  assert.deepEqual(simplifyRun(points),[points[0],points.at(-1)]);
});
