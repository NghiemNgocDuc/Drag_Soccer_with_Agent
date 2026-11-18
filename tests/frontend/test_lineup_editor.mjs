import assert from 'node:assert/strict';
import test from 'node:test';
import {clampPosition,canPlace,changeRole} from '../../static/js/game/lineup-editor.js';
const players=()=>[{role:'goalkeeper',x:.062,y:.5},{role:'defender',x:.162,y:.4486},{role:'defender',x:.162,y:.5514},{role:'support',x:.281,y:.5},{role:'attacker',x:.432,y:.5}];
test('a third player cannot enter a full group and the keeper stays separate',()=>{
  assert.equal(changeRole(players(),3,'defender'),null);
  assert.equal(changeRole(players(),0,'attacker'),null);
});
test('role changes find a free position and preserve jersey order',()=>{
  const original=players(),next=changeRole(original,4,'support');
  assert.equal(next[4].role,'support');assert.equal(next.filter(p=>p.role==='support').length,2);
  assert.ok(canPlace(next,4,next[4]));assert.equal(original[4].role,'attacker');
});
test('position input stays in the role area and rejects overlapping starts',()=>{
  assert.deepEqual(clampPosition({x:1,y:-2},'defender'),{x:.22,y:.15});
  assert.deepEqual(clampPosition({x:0,y:2},'attacker'),{x:.37,y:.85});
  assert.equal(canPlace(players(),1,players()[2]),false);
  assert.equal(canPlace(players(),1,{x:.2,y:.2}),true);
});
