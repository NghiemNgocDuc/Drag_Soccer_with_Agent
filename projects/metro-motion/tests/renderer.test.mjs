import test from 'node:test';
import assert from 'node:assert/strict';
import { createGame, startGame, stepGame, jump, setDuck, setLane } from '../web/js/core.mjs';
import { RunnerRenderer } from '../web/js/renderer.mjs';

// A recording Canvas checks renderer contracts and geometry. Pixel appearance is
// covered separately by the browser verifier's real Canvas screenshots.
function surface(width = 960, height = 600) {
  function context() {
    const calls = [];
    const result = new Proxy({ calls }, {
      get(target, key) {
        if (key in target) return target[key];
        return (...args) => {
          for (const argument of args) {
            if (typeof argument === 'number') assert.ok(Number.isFinite(argument), `${String(key)} received ${argument}`);
          }
          if (key === 'ellipse') assert.ok(args[2] > 0 && args[3] > 0);
          calls.push([key, ...args]);
          if (key === 'createLinearGradient' || key === 'createRadialGradient') {
            return { addColorStop(offset, color) { assert.ok(offset >= 0 && offset <= 1); calls.push(['colorStop', offset, color]); } };
          }
        };
      },
    });
    return result;
  }
  const main = context(), background = context();
  const rect = { width, height };
  const backCanvas = { width: 0, height: 0, getContext: () => background };
  const canvas = {
    width: 0, height: 0, clientWidth: width, clientHeight: height,
    getContext: () => main, getBoundingClientRect: () => rect,
    ownerDocument: { createElement(name) { assert.equal(name, 'canvas'); return backCanvas; } },
  };
  return { canvas, main, background, backCanvas, rect };
}

function withDpr(value, fn) {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'devicePixelRatio');
  Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value });
  try { return fn(); }
  finally {
    if (descriptor) Object.defineProperty(globalThis, 'devicePixelRatio', descriptor);
    else delete globalThis.devicePixelRatio;
  }
}

test('backing canvas respects edge and total pixel budgets across phone, desktop and ultrawide sizes', () => {
  for (const [width, height] of [[320, 640], [1440, 900], [1920, 1080], [768, 1024], [4096, 2160], [20000, 600], [1, 1]]) {
    for (const dpr of [1, 1.25, 2, 3, 4]) {
      withDpr(dpr, () => {
        const h = surface(width, height);
        const renderer = new RunnerRenderer(h.canvas);
        assert.ok(h.canvas.width >= 1 && h.canvas.width <= 3072);
        assert.ok(h.canvas.height >= 1 && h.canvas.height <= 3072);
        assert.ok(h.canvas.width * h.canvas.height <= 2600000,
          `${width}×${height} at DPR ${dpr} allocated ${h.canvas.width * h.canvas.height} pixels`);
        assert.equal(h.backCanvas.width, h.canvas.width);
        assert.equal(h.backCanvas.height, h.canvas.height);
        renderer.destroy();
      });
    }
  }
});

test('the skyline is cached until size or pixel ratio changes', () => {
  withDpr(1, () => {
    const h = surface();
    const renderer = new RunnerRenderer(h.canvas);
    const initial = h.background.calls.length;
    renderer.render(createGame()); renderer.render(createGame(), 20); renderer.resize();
    assert.equal(h.background.calls.length, initial);
    h.rect.width = 800;
    renderer.resize();
    assert.ok(h.background.calls.length > initial);
    const resized = h.background.calls.length;
    withDpr(2, () => renderer.resize());
    assert.ok(h.background.calls.length > resized);
  });
});

test('rendering every gameplay phase produces finite geometry and never changes simulation state', () => {
  for (const size of [[320, 400], [1440, 900], [1, 1]]) {
    const h = surface(...size), renderer = new RunnerRenderer(h.canvas);
    const state = createGame();
    for (const phase of ['ready', 'running', 'paused', 'over']) {
      state.phase = phase;
      for (const pose of [{ laneX: 0, lane: 0, jumpY: 0, duckBlend: 0 },
        { laneX: -0.7, lane: -1, jumpY: 1.54, duckBlend: 0 },
        { laneX: 0.4, lane: 1, jumpY: 0, duckBlend: 1 }]) {
        Object.assign(state, pose);
        const before = structuredClone(state);
        renderer.render(state, 100);
        assert.deepEqual(state, before);
      }
    }
    assert.equal(h.main.calls.filter(([name]) => name === 'save').length,
      h.main.calls.filter(([name]) => name === 'restore').length);
    assert.ok(h.main.calls.some(([name]) => name === 'drawImage'));
  }
});

test('drawing cost stays bounded at large distances instead of retaining past scenery', () => {
  const h = surface(), renderer = new RunnerRenderer(h.canvas);
  const state = createGame(); startGame(state);
  let maximum = 0;
  for (const distance of [0, 60, 1000, 100000, 10000000]) {
    state.distance = distance;
    state.rows = createGame({ seed: distance }).rows.map(row => ({ ...row, z: row.z + distance,
      coins: row.coins.map(coin => ({ ...coin, z: coin.z + distance })) }));
    h.main.calls.length = 0;
    renderer.render(state, distance / 24);
    maximum = Math.max(maximum, h.main.calls.length);
    assert.ok(h.main.calls.length < 13000, `${distance}m caused ${h.main.calls.length} drawing calls`);
  }
  assert.ok(maximum > 1000, 'the contract should measure a fully rendered scene');
});

test('reduced motion freezes decorative coin rotation while retaining essential character movement', () => {
  const h = surface(), renderer = new RunnerRenderer(h.canvas, { reducedMotion: true });
  const state = createGame(); startGame(state);
  h.main.calls.length = 0;
  renderer.render(state, 0);
  const first = [...h.main.calls]; h.main.calls.length = 0;
  renderer.render(state, 100);
  assert.deepEqual(h.main.calls, first);
  jump(state); setLane(state, -1); stepGame(state, 0.2); setDuck(state, true);
  h.main.calls.length = 0;
  renderer.render(state, 100);
  assert.notDeepEqual(h.main.calls, first);
});

test('destroy releases the cached canvas and later renders/resizes are inert', () => {
  const h = surface(), renderer = new RunnerRenderer(h.canvas);
  renderer.render(createGame());
  const draws = h.main.calls.length, cached = h.background.calls.length;
  renderer.destroy(); renderer.destroy();
  renderer.render(createGame()); h.rect.width = 500; renderer.resize();
  assert.equal(h.backCanvas.width, 1); assert.equal(h.backCanvas.height, 1);
  assert.equal(h.main.calls.length, draws); assert.equal(h.background.calls.length, cached);
});

test('a browser without Canvas 2D reports a useful initialization error', () => {
  assert.throws(() => new RunnerRenderer({ getContext: () => null }), /cannot create a Canvas 2D game/);
});
