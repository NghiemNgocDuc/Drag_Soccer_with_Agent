import test from 'node:test';
import assert from 'node:assert/strict';
import {
  RULES, createGame, startGame, resetGame, pauseGame, resumeGame,
  stepGame, setLane, moveLane, jump, setDuck,
} from '../web/js/core.mjs';

function advance(state, seconds, dt = 1 / 60) {
  const count = Math.round(seconds / dt);
  for (let i = 0; i < count; i++) stepGame(state, dt);
  return state;
}

function hazard(type, { z = 5, lane = 0 } = {}) {
  const state = createGame();
  state.rows = [{ id: 0, z, safeLane: 1, obstacles: [
    { type, lane, length: type === 'train' ? 8 : 0.8 },
  ], coins: [] }];
  state.nextRowZ = 100000;
  startGame(state);
  return state;
}

test('the preview is inert; explicit start, pause and resume preserve a run', () => {
  const state = createGame();
  stepGame(state, 0.25);
  assert.equal(state.phase, 'ready');
  assert.equal(state.distance, 0);
  assert.equal(jump(state), false);
  assert.equal(setLane(state, 1), false);
  assert.equal(startGame(state), true);
  assert.equal(startGame(state), false);
  advance(state, 0.5);
  const before = structuredClone(state);
  assert.equal(pauseGame(state), true);
  advance(state, 2);
  assert.equal(state.distance, before.distance);
  assert.equal(state.elapsed, before.elapsed);
  assert.equal(setDuck(state, true), false);
  assert.equal(resumeGame(state), true);
  stepGame(state, 1 / 60);
  assert.ok(state.distance > before.distance);
});

test('seeded rows repeat exactly and a distinct seed changes the route', () => {
  const a = createGame({ seed: 'station-thirteen' });
  assert.deepEqual(a, createGame({ seed: 'station-thirteen' }));
  assert.notDeepEqual(a.rows, createGame({ seed: 'station-fourteen' }).rows);
  assert.equal(createGame({ difficulty: '__proto__' }).difficulty, 'normal');
});

test('fixed simulation yields the same result at 30Hz and 144Hz', () => {
  const a = createGame({ seed: 73 });
  const b = createGame({ seed: 73 });
  startGame(a); startGame(b);
  advance(a, 6, 1 / 30);
  advance(b, 6, 1 / 144);
  assert.equal(a.distance, b.distance);
  assert.equal(a.elapsed, b.elapsed);
  assert.equal(a.coins, b.coins);
  assert.equal(a.phase, b.phase);
  assert.deepEqual(a.rows, b.rows);
});

test('bad time input is inert and long stalls have bounded catch-up', () => {
  const state = createGame(); startGame(state);
  for (const dt of [NaN, Infinity, -1, 0, '100']) stepGame(state, dt);
  assert.equal(state.elapsed, 0);
  stepGame(state, 200);
  assert.ok(state.elapsed <= RULES.maxFrame + 1e-10);
  assert.ok(state.distance < 4);
});

test('lane movement is bounded, smooth and cannot accept nonfinite input', () => {
  const state = createGame(); startGame(state);
  assert.equal(setLane(state, NaN), false);
  assert.equal(moveLane(state, Infinity), false);
  moveLane(state, 1);
  stepGame(state, 1 / 60);
  assert.equal(state.lane, 1);
  assert.ok(state.laneX > 0 && state.laneX < 1);
  advance(state, 0.25);
  assert.equal(state.laneX, 1);
  assert.equal(moveLane(state, 1), false);
  setLane(state, -20);
  assert.equal(state.lane, -1);
  advance(state, 0.5);
  assert.equal(state.laneX, -1);
});

test('train collision is swept even when a large frame crosses its front', () => {
  const state = hazard('train', { z: 1 });
  stepGame(state, 0.25);
  assert.equal(state.phase, 'over');
  assert.deepEqual(state.collision, { type: 'train', lane: 0 });
  assert.equal(state.lastEvent.type, 'collision');
  const stopped = state.distance;
  advance(state, 1);
  assert.equal(state.distance, stopped);
});

test('train still collides when entering its side after the front passed', () => {
  const state = hazard('train', { z: 1 });
  state.lane = state.laneX = 1;
  advance(state, 0.25);
  assert.equal(state.phase, 'running');
  assert.ok(state.distance > 1);
  setLane(state, 0);
  advance(state, 0.25);
  assert.equal(state.phase, 'over');
});

test('changing lanes in time avoids a train; jumping cannot clear a train', () => {
  const dodge = hazard('train');
  setLane(dodge, 1);
  advance(dodge, 1.25);
  assert.equal(dodge.phase, 'running');
  const airborne = hazard('train');
  jump(airborne);
  advance(airborne, 0.5);
  assert.equal(airborne.phase, 'over');
});

test('a well-timed jump clears a barrier; standing and ducking do not', () => {
  const airborne = hazard('barrier');
  assert.equal(jump(airborne), true);
  assert.equal(jump(airborne), false);
  advance(airborne, 0.75);
  assert.equal(airborne.phase, 'running');
  advance(airborne, 0.5);
  assert.equal(airborne.jumpY, 0);
  assert.equal(airborne.jumpVelocity, 0);
  for (const crouching of [false, true]) {
    const grounded = hazard('barrier');
    setDuck(grounded, crouching);
    advance(grounded, 0.5);
    assert.equal(grounded.phase, 'over');
  }
});

test('ducking clears an arch, while standing or airborne crouching collides', () => {
  const low = hazard('arch');
  setDuck(low, true);
  assert.equal(jump(low), false);
  advance(low, 0.75);
  assert.equal(low.phase, 'running');
  assert.ok(low.duckBlend > 0.99);
  setDuck(low, false);
  advance(low, 0.5);
  assert.ok(low.duckBlend < 0.01);
  const standing = hazard('arch');
  advance(standing, 0.5);
  assert.equal(standing.phase, 'over');
  const airborne = hazard('arch');
  jump(airborne); setDuck(airborne, true);
  advance(airborne, 0.5);
  assert.equal(airborne.phase, 'over');
});

test('coins collect once along the swept path and score combines distance with coins', () => {
  const state = createGame(); startGame(state);
  state.rows = [{ id: 1, z: 5, safeLane: 0, obstacles: [], coins: [
    { lane: 0, z: 1, collected: false },
    { lane: 0, z: 2, collected: false },
    { lane: 1, z: 2, collected: false },
  ] }];
  state.nextRowZ = 100000;
  stepGame(state, 0.25);
  assert.equal(state.coins, 2);
  assert.equal(state.score, Math.floor(state.distance) + 50);
  advance(state, 0.5);
  assert.equal(state.coins, 2);
});

test('every generated row has a reachable safe route across many seeds', () => {
  for (let seed = 0; seed < 80; seed++) {
    const state = createGame({ seed }); startGame(state);
    let priorSafe = 0, highestId = -1;
    for (let frame = 0; frame < 1200; frame++) {
      for (const row of state.rows) {
        if (row.id <= highestId) continue;
        assert.ok([-1, 0, 1].includes(row.safeLane));
        assert.ok(Math.abs(priorSafe - row.safeLane) <= 1);
        assert.equal(row.obstacles.length, 2);
        assert.equal(row.obstacles.some(obstacle => obstacle.lane === row.safeLane), false);
        assert.ok(row.coins.every(coin => coin.lane === row.safeLane));
        priorSafe = row.safeLane; highestId = row.id;
      }
      const row = state.rows.find(row => row.z + 9 > state.distance);
      if (row && row.z - state.distance < 24) setLane(state, row.safeLane);
      stepGame(state, 0.1);
      assert.equal(state.phase, 'running', `seed ${seed} lost on safe route`);
      assert.ok(state.rows.length <= 9);
    }
    assert.ok(highestId > 50);
    assert.ok(state.coins > 100);
  }
});

test('a ten-minute run keeps speed, rows and entity count bounded', () => {
  for (const difficulty of ['gentle', 'normal']) {
    const state = createGame({ seed: 99, difficulty }); startGame(state);
    for (let frame = 0; frame < 6000; frame++) {
      const row = state.rows.find(row => row.z + 9 > state.distance);
      if (row && row.z - state.distance < 24) setLane(state, row.safeLane);
      stepGame(state, 0.1);
      assert.equal(state.phase, 'running');
      assert.ok(state.rows.length <= 9);
      assert.ok(state.rows.reduce((sum, row) => sum + row.coins.length + row.obstacles.length, 0) <= 45);
    }
    assert.ok(state.distance > 8000);
    assert.ok(state.speed <= (difficulty === 'gentle' ? 17 : 24));
    assert.ok(state.rows[0].z > state.distance - 26);
  }
});

test('restart reproduces its seed; reset can select a new difficulty and seed', () => {
  const state = hazard('train', { z: 1 });
  stepGame(state, 0.25);
  const seed = state.seed;
  assert.equal(startGame(state), true);
  assert.equal(state.phase, 'running');
  assert.equal(state.distance, 0);
  assert.equal(state.coins, 0);
  assert.equal(state.collision, null);
  assert.deepEqual(state.rows, createGame({ seed }).rows);
  resetGame(state, { seed: 83, difficulty: 'gentle' });
  assert.equal(state.phase, 'ready');
  assert.equal(state.seed, 83);
  assert.equal(state.speed, 10);
});

test('irregular display frames preserve a scripted jump, duck, lane change and coin run', () => {
  function run(frameTicks) {
    const state = createGame({ seed: 'timing-regression' });
    state.rows = [
      { id: 0, z: 5, safeLane: 1, obstacles: [{ type: 'barrier', lane: 0, length: 0.8 }], coins: [{ lane: 0, z: 2, collected: false }] },
      { id: 1, z: 25, safeLane: 1, obstacles: [{ type: 'arch', lane: 0, length: 0.8 }], coins: [{ lane: 0, z: 22, collected: false }] },
      { id: 2, z: 50, safeLane: -1, obstacles: [{ type: 'train', lane: 0, length: 8 }], coins: [{ lane: -1, z: 49, collected: false }] },
    ];
    state.nextRowZ = 100000;
    startGame(state);
    const actions = new Map([[0, () => jump(state)], [180, () => setDuck(state, true)],
      [240, () => setDuck(state, false)], [360, () => setLane(state, -1)]]);
    let tick = 0, frame = 0;
    while (tick < 720) {
      actions.get(tick)?.();
      const nextAction = [...actions.keys()].find(value => value > tick) ?? 720;
      const count = Math.min(frameTicks[frame++ % frameTicks.length], nextAction - tick, 720 - tick);
      stepGame(state, count * RULES.step);
      tick += count;
    }
    assert.equal(state.phase, 'running');
    assert.equal(state.coins, 3);
    return state;
  }
  assert.deepEqual(run([1]), run([4]));
  assert.deepEqual(run([1]), run([1, 2, 17, 5, 30, 3, 7]));
});

test('jump timing remains playable at both initial and maximum speeds', () => {
  for (const difficulty of ['normal', 'gentle']) {
    for (const elapsed of [0, 1000]) {
      const speed = difficulty === 'normal' ? (elapsed ? 24 : 14) : (elapsed ? 17 : 10);
      for (const [secondsToBarrier, succeeds] of [[0.3, true], [0.02, false], [1.1, false]]) {
        const state = hazard('barrier', { z: speed * secondsToBarrier });
        state.difficulty = difficulty;
        state.elapsed = elapsed;
        jump(state);
        advance(state, 1.5, RULES.step);
        assert.equal(state.phase, succeeds ? 'running' : 'over', `${difficulty}, elapsed ${elapsed}, jump ${secondsToBarrier}s before barrier`);
      }
    }
  }
});

test('crouch must last through an arch and the train tail remains solid', () => {
  const arch = hazard('arch', { z: 1 });
  setDuck(arch, true);
  advance(arch, 0.1, RULES.step);
  assert.equal(arch.phase, 'running');
  setDuck(arch, false);
  stepGame(arch, RULES.step);
  assert.equal(arch.phase, 'over');
  for (const [distance, succeeds] of [[7.7, false], [9.5, true]]) {
    const train = hazard('train', { z: 1 });
    train.lane = train.laneX = 1;
    train.distance = distance;
    setLane(train, 0);
    advance(train, 0.3, RULES.step);
    assert.equal(train.phase, succeeds ? 'running' : 'over', `entering train lane at ${distance}m`);
  }
});

test('a paused airborne run stays frozen and stale controls cannot change it', () => {
  const state = createGame();
  startGame(state); jump(state); advance(state, 0.2);
  pauseGame(state);
  const frozen = structuredClone(state);
  for (const dt of [0.001, 1 / 60, 0.25, 1000]) {
    assert.equal(jump(state), false);
    assert.equal(setDuck(state, true), false);
    assert.equal(moveLane(state, -1), false);
    stepGame(state, dt);
    assert.deepEqual(state, frozen);
  }
  resumeGame(state);
  advance(state, 1);
  assert.equal(state.jumpY, 0);
  assert.equal(state.jumpVelocity, 0);
});

test('game over rejects every action and reset clears partially completed movement', () => {
  const state = hazard('train', { z: 0.5 });
  jump(state); stepGame(state, 0.25);
  const frozen = structuredClone(state);
  assert.equal(setLane(state, 1), false);
  assert.equal(moveLane(state, -1), false);
  assert.equal(jump(state), false);
  assert.equal(setDuck(state, true), false);
  assert.equal(pauseGame(state), false);
  assert.equal(resumeGame(state), false);
  stepGame(state, 1000);
  assert.deepEqual(state, frozen);
  resetGame(state, { seed: 'fresh-run', difficulty: 'gentle' });
  assert.deepEqual(state, createGame({ seed: 'fresh-run', difficulty: 'gentle' }));
});

test('invalid controller values leave an active run unchanged', () => {
  const state = createGame(); startGame(state);
  const before = structuredClone(state);
  for (const input of [NaN, Infinity, -Infinity, null, undefined, '1', {}, []]) {
    assert.equal(setLane(state, input), false);
    assert.equal(moveLane(state, input), false);
    stepGame(state, input);
    assert.deepEqual(state, before);
  }
});

test('safe routes stay reachable at maximum speed across 200 seeded courses and jittery frames', () => {
  const frames = [1 / 120, 1 / 60, 1 / 30, 0.1, 0.25];
  let sections = 0;
  for (const difficulty of ['normal', 'gentle']) {
    for (let seed = 0; seed < 100; seed++) {
      const state = createGame({ seed, difficulty }); startGame(state);
      state.elapsed = 1000; // Exercise the maximum speed from the first obstacle onward.
      let lastId = -1;
      for (let frame = 0; frame < 2200; frame++) {
        const row = state.rows.find(row => row.z + 9 > state.distance);
        if (row && row.z - state.distance < 24) setLane(state, row.safeLane);
        stepGame(state, frames[frame % frames.length]);
        assert.equal(state.phase, 'running', `${difficulty} seed ${seed} frame ${frame}`);
        assert.equal(state.speed, difficulty === 'normal' ? 24 : 17);
        assert.ok(state.rows.length <= 9);
        assert.ok(Number.isFinite(state.distance) && state.score >= 0);
        if (row && row.id > lastId) { sections++; lastId = row.id; }
      }
      assert.ok(state.coins > 100);
      assert.ok(state.distance > 3000);
    }
  }
  assert.ok(sections > 15000, `only exercised ${sections} obstacle sections`);
});
