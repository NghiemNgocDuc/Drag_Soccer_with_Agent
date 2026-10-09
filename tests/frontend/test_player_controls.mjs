import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
const source = await readFile(new URL('../../static/js/game/player-controls.js', import.meta.url), 'utf8');
const { controlAccess, playerRadius, shotPower, shotFromPull, onlineRevision, onlineReplayPlan } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const state = overrides => ({ game_mode: 'hvh', is_player_a: true,
  players_a: Array.from({length:7},()=>({})), players_b: Array.from({length:3},()=>({})), ...overrides });

test('hotseat control follows the current team and its actual roster length', () => {
  assert.deepEqual(controlAccess(state()).indices, [0,1,2,3,4,5,6]);
  const turn = controlAccess(state({is_player_a:false}));
  assert.equal(turn.team, 'b'); assert.deepEqual(turn.indices, [0,1,2]);
});
test('human versus AI preserves captain control and blocks the AI turn', () => {
  assert.deepEqual(controlAccess(state({game_mode:'hvai'})).indices, [0]);
  assert.equal(controlAccess(state({game_mode:'hvai',is_player_a:false})).canKick, false);
  assert.equal(controlAccess(state({game_mode:'aivai'})).canKick, false);
});
test('online control requires membership, own turn, and an available normal match', () => {
  const game=state({is_player_a:false}), online={active:true,side:'b'};
  assert.deepEqual(controlAccess(game,online).indices,[0,1,2]);
  for(const options of [{active:true,side:'a'},{active:true,side:null}]) assert.equal(controlAccess(game,options).canKick,false);
  const penalty=controlAccess({...game,penalty_shootout:true},online);
  assert.equal(penalty.canKick,true); assert.equal(penalty.useBall,true); assert.deepEqual(penalty.indices,[0]);
});
test('online commands stay blocked until the room is synchronized, active, and connected', () => {
  const game=state(), online={active:true,side:'a'};
  for(const extra of [{ready:false},{status:'waiting'},{leaving:true},{connectionError:'Connection lost'}]) {
    assert.equal(controlAccess(game,{...online,...extra}).canKick,false);
  }
});
test('joining or refreshing snapshots the current room without replaying old goals', () => {
  const data={game:state({kick_count:5,online_move_count:7}),move_count:7,moves:[{kick_count:7,scored:true}]};
  assert.deepEqual(onlineReplayPlan(data,-1),{revision:7,moves:[],snap:true});
  assert.equal(onlineRevision(data.game,data),7);
  assert.equal(onlineRevision({kick_count:5,online_move_count:8}),8);
  assert.equal(onlineRevision({kick_count:5}),5);
});
test('a received move plays exactly once, including penalties with unchanged regulation kick counts', () => {
  const first={kick_count:11,mover:'a'}, second={kick_count:12,mover:'b'};
  const data={game:state({kick_count:10,online_move_count:12}),moves:[second,first,first]};
  const plan=onlineReplayPlan(data,10);
  assert.deepEqual(plan.moves,[first,second]); assert.equal(plan.revision,12); assert.equal(plan.snap,false);
  assert.deepEqual(onlineReplayPlan(data,12),{revision:12,moves:[],snap:false});
});
test('missed history snaps to the server snapshot instead of replaying incomplete or unrelated turns', () => {
  const data={game:state({online_move_count:25}),moves:[{kick_count:24},{kick_count:25}]};
  assert.deepEqual(onlineReplayPlan(data,1),{revision:25,moves:[],snap:true});
  const latest={game:state({kick_count:2}),last_move:{mover:'b'}};
  assert.deepEqual(onlineReplayPlan(latest,1).moves,[{mover:'b',kick_count:2}]);
  assert.equal(onlineRevision({online_move_count:Infinity}),0);
});
test('busy, finished, missing and empty matches cannot submit a kick', () => {
  for(const game of [null,state({game_over:true}),state({players_a:[]})]) assert.equal(controlAccess(game).canKick,false);
  assert.equal(controlAccess(state(),{},true).canKick,false);
});
test('penalty aiming uses the ball, keeps one legal kicker and leaves state unchanged', () => {
  const game=state({penalty_shootout:true,is_player_a:false,ball:{x:294,y:437.5}}), before=structuredClone(game);
  const turn=controlAccess(game); assert.equal(turn.team,'b'); assert.equal(turn.useBall,true); assert.deepEqual(turn.indices,[0]);
  shotFromPull(game.ball,{x:304,y:487.5});
  assert.deepEqual(game,before);
  assert.equal(controlAccess({...game,game_mode:'hvai'}).canKick,false);
});
test('pull directions match server coordinates and reverse the pull', () => {
  const origin={x:100,y:100};
  for(const [point,angle] of [[{x:45,y:100},0],[{x:100,y:45},90],[{x:155,y:100},180],[{x:100,y:155},270]]) {
    const shot=shotFromPull(origin,point); assert.equal(shot.angle,angle); assert.equal(shot.basePower,50); assert.equal(shot.power,50);
  }
  const capped=shotFromPull(origin,{x:1000,y:100}); assert.equal(capped.power,100); assert.equal(capped.pullX,210);
});
test('pass modifiers respect saved caps and never send non-finite strength', () => {
  assert.equal(shotPower(100,'short'),62); assert.equal(shotPower(100,'through'),88);
  assert.equal(shotPower(100,'long'),100); assert.equal(shotPower(100,'long',45),45);
  assert.equal(shotPower(72,'normal',0),0); assert.equal(shotPower(NaN),0);
});
test('picking uses the same Size radius as physics, including legal zero', () => {
  assert.equal(playerRadius({stats:{size:0}}),12); assert.equal(playerRadius({stats:{size:50}}),20);
  assert.equal(playerRadius({stats:{size:100}}),28); assert.equal(playerRadius({stats:{size:NaN}}),20);
});
