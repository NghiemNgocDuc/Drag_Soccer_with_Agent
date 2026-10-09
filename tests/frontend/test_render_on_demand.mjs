import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../../static/js/game/render-on-demand.js', import.meta.url), 'utf8');
const { createRenderLoop, createRenderQuality, computeRenderSize, RENDER_QUALITY_KEY } =
  await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

function eventTarget(extra = {}) {
  const listeners = new Map();
  return Object.assign(extra, {
    addEventListener(type, callback) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(callback);
    },
    removeEventListener(type, callback) { listeners.get(type)?.delete(callback); },
    emit(type) { for (const callback of listeners.get(type) || []) callback(); },
  });
}

function scheduler(render = () => {}) {
  let time = 100, nextId = 0;
  const pending = new Map(), errors = [];
  const document = eventTarget({ hidden: false, visibilityState: 'visible' });
  const window = eventTarget({ innerWidth: 1280, innerHeight: 720 });
  const rect = { left: 0, top: 0, right: 1280, bottom: 720, width: 1280, height: 720 };
  const loop = createRenderLoop({
    render, document, window, element: { getBoundingClientRect: () => rect },
    raf: callback => { const id = ++nextId; pending.set(id, callback); return id; },
    caf: id => pending.delete(id), now: () => time,
    onError: error => errors.push(error),
  });
  return {
    loop, document, window, rect, pending, errors,
    setTime(value) { time = value; },
    frame(value = time + 16) {
      time = value;
      const callbacks = [...pending.values()];
      pending.clear();
      for (const callback of callbacks) callback(time);
    },
  };
}

test('dirty invalidations coalesce into one draw and leave no idle RAF', () => {
  let draws = 0;
  const fixture = scheduler(() => draws++);
  assert.equal(fixture.pending.size, 0);
  for (let index = 0; index < 20; index++) fixture.loop.invalidate();
  assert.equal(fixture.pending.size, 1);
  fixture.frame();
  assert.equal(draws, 1);
  assert.deepEqual(fixture.loop.stats(), { frames: 1, pendingFrame: false,
    pendingCallbacks: 0, dirty: false, suspended: false, visible: true });
});

test('animation callbacks run before drawing and the final frame is drawn', () => {
  const events = [];
  const fixture = scheduler(() => events.push('draw'));
  fixture.loop.requestAnimationFrame(() => {
    events.push('first');
    fixture.loop.requestAnimationFrame(() => events.push('final'));
  });
  fixture.frame();
  fixture.frame();
  assert.deepEqual(events, ['first', 'draw', 'final', 'draw']);
  assert.equal(fixture.pending.size, 0);
});

test('starting after a long idle does not advance the camera by the idle gap', () => {
  const steps = [];
  const fixture = scheduler((_time, dt) => steps.push(dt));
  fixture.loop.invalidate(); fixture.frame(100);
  assert.equal(fixture.pending.size, 0);
  fixture.loop.requestAnimationFrame(() => fixture.loop.requestAnimationFrame(() => {}));
  fixture.frame(10100); fixture.frame(10120);
  assert.deepEqual(steps, [1000 / 60, 1000 / 60, 20]);
  assert.equal(fixture.pending.size, 0);
});

test('canceling the last callback cancels the browser RAF', () => {
  const fixture = scheduler();
  const id = fixture.loop.requestAnimationFrame(() => assert.fail('Canceled callback ran'));
  fixture.loop.cancelAnimationFrame(id);
  assert.equal(fixture.pending.size, 0);
  assert.equal(fixture.loop.stats().pendingCallbacks, 0);
});

test('one callback can cancel a later callback in the same frame', () => {
  const fixture = scheduler();
  let later;
  fixture.loop.requestAnimationFrame(() => fixture.loop.cancelAnimationFrame(later));
  later = fixture.loop.requestAnimationFrame(() => assert.fail('Canceled callback ran'));
  fixture.frame();
  assert.equal(fixture.loop.stats().frames, 1);
});

test('hidden animation preserves queued callbacks and pauses its clock', () => {
  const times = [];
  const fixture = scheduler();
  fixture.loop.requestAnimationFrame(time => times.push(time));
  fixture.setTime(120);
  fixture.document.hidden = true;
  fixture.document.visibilityState = 'hidden';
  fixture.document.emit('visibilitychange');
  fixture.setTime(2120);
  assert.equal(fixture.loop.now(), 120);
  assert.equal(fixture.pending.size, 0);
  assert.equal(fixture.loop.stats().pendingCallbacks, 1);
  fixture.document.hidden = false;
  fixture.document.visibilityState = 'visible';
  fixture.document.emit('visibilitychange');
  fixture.frame(2136);
  assert.deepEqual(times, [136]);
  assert.equal(fixture.loop.now(), 136);
  assert.equal(fixture.pending.size, 0);
});

test('offscreen dirty scenes pause and redraw exactly once when visible', () => {
  const fixture = scheduler();
  fixture.rect.top = 900; fixture.rect.bottom = 1620;
  fixture.window.emit('scroll');
  fixture.loop.invalidate();
  assert.equal(fixture.pending.size, 0);
  assert.equal(fixture.loop.stats().suspended, true);
  fixture.rect.top = 0; fixture.rect.bottom = 720;
  fixture.window.emit('scroll');
  fixture.frame();
  assert.equal(fixture.loop.stats().frames, 1);
  assert.equal(fixture.pending.size, 0);
});

test('disposal cancels all callbacks and blocks subsequent redraws', () => {
  const fixture = scheduler();
  fixture.loop.requestAnimationFrame(() => assert.fail('Disposed callback ran'));
  fixture.loop.dispose();
  fixture.loop.invalidate();
  fixture.loop.resume();
  fixture.document.emit('visibilitychange');
  assert.equal(fixture.pending.size, 0);
});

test('4K drawing-buffer dimensions preserve aspect and obey hardware limits', () => {
  assert.deepEqual(computeRenderSize(1280, 720, '4k'), {
    width: 3840, height: 2160, cssWidth: 1280, cssHeight: 720, pixelRatio: 3, limited: false,
  });
  const limited = computeRenderSize(1280, 720, '4k', 1, { maxWidth: 2048, maxHeight: 2048 });
  assert.equal(limited.width, 2048);
  assert.equal(limited.height, 1152);
  assert.equal(limited.limited, true);
  const portrait = computeRenderSize(720, 1280, '1440p');
  assert.equal(portrait.width, 1440);
  assert.equal(portrait.height, 2560);
});

test('Auto uses device density within the drawing-buffer safety bounds', () => {
  const native = computeRenderSize(1280, 720, 'auto', 1.5);
  assert.equal(native.width, 1920);
  assert.equal(native.height, 1080);
  const highDensity = computeRenderSize(1280, 720, 'auto', 5);
  assert.equal(highDensity.width, 2560);
  assert.equal(highDensity.height, 1440);
});

function qualityFixture(storage = new Map(), maximum = 8192) {
  let changes = 0, resizes = 0, projections = 0;
  const canvas = eventTarget({ width: 300, height: 150, style: {} });
  const renderer = {
    domElement: canvas, capabilities: { maxTextureSize: maximum },
    getContext: () => ({ MAX_VIEWPORT_DIMS: 'viewport', MAX_RENDERBUFFER_SIZE: 'maximum',
      getParameter: key => key === 'viewport' ? [maximum, maximum] : maximum }),
    setPixelRatio: ratio => assert.equal(ratio, 1),
    setSize(width, height, updateStyle) {
      assert.equal(updateStyle, false); canvas.width = width; canvas.height = height; resizes++;
    },
  };
  const camera = { aspect: 1, updateProjectionMatrix: () => projections++ };
  const quality = createRenderQuality(renderer, camera,
    { getBoundingClientRect: () => ({ width: 1280, height: 720 }) },
    { window: eventTarget({ devicePixelRatio: 1 }),
      storage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) },
      onChange: () => changes++ });
  return { quality, canvas, camera, storage, counters: () => ({ changes, resizes, projections }) };
}

test('quality defaults to 4K and unchanged resize does not invalidate a draw', () => {
  const fixture = qualityFixture();
  assert.equal(fixture.quality.stats().mode, '4k');
  assert.equal(fixture.canvas.width, 3840);
  assert.equal(fixture.canvas.height, 2160);
  assert.equal(fixture.camera.aspect, 16 / 9);
  const before = fixture.counters();
  assert.equal(fixture.quality.resize(), false);
  assert.deepEqual(fixture.counters(), before);
});

test('quality selection persists across renderer creation and rejects invalid modes', () => {
  const storage = new Map();
  const first = qualityFixture(storage);
  assert.equal(first.quality.setQuality('1080p'), true);
  assert.equal(storage.get(RENDER_QUALITY_KEY), '1080p');
  assert.equal(first.canvas.width, 1920);
  const next = qualityFixture(storage);
  assert.equal(next.quality.stats().mode, '1080p');
  for (const invalid of ['invalid', 'constructor', 'toString', '__proto__']) {
    assert.equal(next.quality.setQuality(invalid), false);
  }
  assert.equal(next.quality.stats().mode, '1080p');
});

test('inherited saved mode names fall back to 4K instead of NaN dimensions', () => {
  for (const invalid of ['constructor', 'toString', '__proto__']) {
    const fixture = qualityFixture(new Map([[RENDER_QUALITY_KEY, invalid]]));
    assert.equal(fixture.quality.stats().mode, '4k');
    assert.equal(fixture.canvas.width, 3840);
    assert.equal(computeRenderSize(1280, 720, invalid).width, 3840);
  }
});

test('quality uses a safe fallback when the GPU cannot allocate 4K', () => {
  const fixture = qualityFixture(new Map(), 2048);
  const stats = fixture.quality.stats();
  assert.equal(stats.mode, '4k');
  assert.equal(stats.width, 2048);
  assert.equal(stats.height, 1152);
  assert.equal(stats.limited, true);
});

test('a restored WebGL context requests a fresh draw even without resizing', () => {
  const fixture = qualityFixture();
  const before = fixture.counters();
  fixture.canvas.emit('webglcontextrestored');
  const after = fixture.counters();
  assert.equal(after.resizes, before.resizes);
  assert.equal(after.changes, before.changes + 1);
});
