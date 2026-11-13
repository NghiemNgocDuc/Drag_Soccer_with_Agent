/* Metro Motion: deterministic, DOM-free endless-runner simulation.
 * Units are metres and seconds. One caller owns animation scheduling.
 */
export const RULES = Object.freeze({
  step: 1 / 120, maxFrame: 0.25, viewDistance: 260,
  laneWidth: 3.2, laneRate: 7.5, jumpVelocity: 6.8, gravity: 15,
  barrierClearance: 0.72, collisionLaneRadius: 0.61,
});

const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const difficulties = {
  normal: { initialSpeed: 14, maxSpeed: 24, acceleration: 0.065, gap: 38 },
  gentle: { initialSpeed: 10, maxSpeed: 17, acceleration: 0.035, gap: 42 },
};

function seedNumber(seed) {
  if (typeof seed === 'number' && Number.isFinite(seed)) return seed >>> 0;
  let result = 2166136261;
  for (const letter of String(seed)) {
    result = Math.imul(result ^ letter.charCodeAt(0), 16777619);
  }
  return result >>> 0;
}

function random(state) {
  state.randomSeed = (state.randomSeed + 0x6D2B79F5) >>> 0;
  let value = state.randomSeed;
  value = Math.imul(value ^ (value >>> 15), value | 1);
  value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
  return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
}

function addRow(state) {
  const id = state.nextRowId++;
  // The guaranteed route moves at most one lane at each well-spaced row.
  const safeLane = id === 0 ? 0 : clamp(
    state.lastSafeLane + Math.floor(random(state) * 3) - 1, -1, 1,
  );
  const z = state.nextRowZ;
  const types = ['train', 'barrier', 'arch'];
  const obstacles = [-1, 0, 1].filter(lane => lane !== safeLane).map((lane, index) => {
    const type = id === 0 ? ['train', 'barrier'][index]
      : types[Math.floor(random(state) * types.length)];
    return { lane, type, length: type === 'train' ? 8 : 0.8 };
  });
  state.rows.push({
    id, z, safeLane, obstacles,
    coins: [-15, -10, -5].map(offset => ({ lane: safeLane, z: z + offset, collected: false })),
  });
  state.lastSafeLane = safeLane;
  state.nextRowZ += difficulties[state.difficulty].gap + random(state) * 6;
}

function fillRows(state) {
  while (state.nextRowZ < state.distance + RULES.viewDistance) addRow(state);
  state.rows = state.rows.filter(row => row.z + 26 > state.distance);
}

export function createGame({ seed = 1, difficulty = 'normal' } = {}) {
  const kind = Object.hasOwn(difficulties, difficulty) ? difficulty : 'normal';
  const state = {
    phase: 'ready', seed: seedNumber(seed), randomSeed: seedNumber(seed), difficulty: kind,
    distance: 0, elapsed: 0, speed: difficulties[kind].initialSpeed,
    score: 0, coins: 0, lane: 0, laneX: 0,
    jumpY: 0, jumpVelocity: 0, ducking: false, duckBlend: 0,
    rows: [], nextRowZ: 60, nextRowId: 0, lastSafeLane: 0,
    accumulator: 0, eventId: 0, lastEvent: null, collision: null,
  };
  fillRows(state);
  return state;
}

function event(state, type, detail = {}) {
  state.lastEvent = { id: ++state.eventId, ...detail, type };
}

export function startGame(state) {
  if (!state || !['ready', 'over'].includes(state.phase)) return false;
  if (state.phase === 'over') Object.assign(state, createGame({ seed: state.seed, difficulty: state.difficulty }));
  state.phase = 'running';
  event(state, 'start');
  return true;
}

export function resetGame(state, options = {}) {
  Object.assign(state, createGame({ seed: state.seed, difficulty: state.difficulty, ...options }));
  return state;
}

export function pauseGame(state) {
  if (state.phase !== 'running') return false;
  state.phase = 'paused';
  state.accumulator = 0;
  return true;
}

export function resumeGame(state) {
  if (state.phase !== 'paused') return false;
  state.phase = 'running';
  state.accumulator = 0;
  return true;
}

export function setLane(state, lane) {
  if (state.phase !== 'running' || !Number.isFinite(lane)) return false;
  const next = clamp(Math.round(lane), -1, 1);
  if (next === state.lane) return false;
  state.lane = next;
  event(state, 'lane', { lane: next });
  return true;
}

export function moveLane(state, delta) {
  return Number.isFinite(delta) ? setLane(state, state.lane + Math.sign(delta)) : false;
}

export function jump(state) {
  if (state.phase !== 'running' || state.jumpY > 0 || state.jumpVelocity > 0 || state.ducking) return false;
  state.jumpVelocity = RULES.jumpVelocity;
  event(state, 'jump');
  return true;
}

export function setDuck(state, ducking) {
  if (state.phase !== 'running') return false;
  const next = Boolean(ducking);
  if (next === state.ducking) return false;
  state.ducking = next;
  event(state, next ? 'duck' : 'stand');
  return true;
}

function hit(state, obstacle) {
  state.phase = 'over';
  state.collision = { type: obstacle.type, lane: obstacle.lane };
  state.ducking = false;
  state.accumulator = 0;
  event(state, 'collision', { obstacleType: obstacle.type, lane: obstacle.lane });
}

function tick(state, dt) {
  const settings = difficulties[state.difficulty];
  const before = state.distance;
  state.elapsed += dt;
  state.speed = Math.min(settings.maxSpeed, settings.initialSpeed + state.elapsed * settings.acceleration);
  state.distance += state.speed * dt;
  const laneDelta = clamp(state.lane - state.laneX, -RULES.laneRate * dt, RULES.laneRate * dt);
  state.laneX += laneDelta;
  if (Math.abs(state.laneX - state.lane) < 1e-8) state.laneX = state.lane;
  state.duckBlend += ((state.ducking ? 1 : 0) - state.duckBlend) * (1 - Math.exp(-18 * dt));
  if (state.jumpY > 0 || state.jumpVelocity > 0) {
    state.jumpY = Math.max(0, state.jumpY + state.jumpVelocity * dt - 0.5 * RULES.gravity * dt * dt);
    state.jumpVelocity -= RULES.gravity * dt;
    if (state.jumpY === 0 && state.jumpVelocity < 0) state.jumpVelocity = 0;
  }
  for (const row of state.rows) {
    // Swept depth intervals plus fixed steps prevent a fast frame skipping a hazard.
    if (state.distance >= row.z - 0.35 && before <= row.z + 8.35) {
      for (const obstacle of row.obstacles) {
        if (before > row.z + obstacle.length + 0.35) continue;
        if (Math.abs(state.laneX - obstacle.lane) > RULES.collisionLaneRadius) continue;
        const clears = obstacle.type === 'barrier' ? state.jumpY >= RULES.barrierClearance
          : obstacle.type === 'arch' ? state.ducking && state.jumpY < 0.25 : false;
        if (!clears) {
          hit(state, obstacle);
          break;
        }
      }
    }
    if (state.phase === 'over') break;
    for (const coin of row.coins) {
      if (!coin.collected && before <= coin.z + 0.65 && state.distance >= coin.z - 0.65
        && Math.abs(state.laneX - coin.lane) < 0.6) {
        coin.collected = true;
        state.coins++;
        event(state, 'coin', { lane: coin.lane });
      }
    }
  }
  state.score = Math.floor(state.distance) + state.coins * 25;
  fillRows(state);
}

/** A suspended tab never receives a catch-up burst: callers should also pause on hide. */
export function stepGame(state, dt) {
  if (state.phase !== 'running' || !Number.isFinite(dt) || dt <= 0) return state;
  state.accumulator += Math.min(dt, RULES.maxFrame);
  let steps = 0;
  while (state.accumulator + 1e-10 >= RULES.step && state.phase === 'running' && steps < 31) {
    state.accumulator -= RULES.step;
    tick(state, RULES.step);
    steps++;
  }
  if (Math.abs(state.accumulator) < 1e-9) state.accumulator = 0;
  return state;
}
