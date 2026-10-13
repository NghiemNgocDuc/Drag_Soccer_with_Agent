import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../../static/js/game/goalkeeper-input.js', import.meta.url), 'utf8');
const { goalkeeperAccess, keeperDirectionFromDrag, createGoalkeeperInput } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const keeper = { x: 50, y: 400 }, ball = { x: 300, y: 400 };
const state = () => ({ game_mode: 'hvai', penalty_shootout: true, is_player_a: false,
  players_a: [{ ...keeper }], ball: { ...ball }, penalty_kick_num: 0, kick_count: 120 });

test('keeper drags use side lanes or forward toward the ball, never reverse slingshot', () => {
  assert.equal(keeperDirectionFromDrag(keeper, { x: 50, y: 360 }, ball), 'left');
  assert.equal(keeperDirectionFromDrag(keeper, { x: 50, y: 440 }, ball), 'right');
  assert.equal(keeperDirectionFromDrag(keeper, { x: 90, y: 410 }, ball), 'center');
  assert.equal(keeperDirectionFromDrag(keeper, { x: 10, y: 400 }, ball), null);
  assert.equal(keeperDirectionFromDrag(keeper, { x: 51, y: 403 }, ball), null);
  assert.equal(keeperDirectionFromDrag(keeper, keeper, ball, 0), null);
  assert.equal(keeperDirectionFromDrag(keeper, { x: NaN, y: 400 }, ball), null);
});

test('forward follows the ball at either goal and lateral choices keep server Y signs', () => {
  const rightKeeper = { x: 1350, y: 400 }, leftBall = { x: 1100, y: 400 };
  assert.equal(keeperDirectionFromDrag(rightKeeper, { x: 1300, y: 400 }, leftBall), 'center');
  assert.equal(keeperDirectionFromDrag(rightKeeper, { x: 1350, y: 360 }, leftBall), 'left');
  assert.equal(keeperDirectionFromDrag(rightKeeper, { x: 1350, y: 440 }, leftBall), 'right');
  assert.equal(keeperDirectionFromDrag(rightKeeper, { x: 1390, y: 400 }, leftBall), null);
});

test('keeper access is restricted to an uncommitted local human defensive penalty', () => {
  assert.deepEqual(goalkeeperAccess(state()), { team: 'a', indices: [0], canSave: true, useBall: false });
  for (const changes of [{ penalty_shootout: false }, { is_player_a: true }, { game_over: true },
    { game_mode: 'hvh' }, { game_mode: 'aivai' }, { players_a: [] }, { ball: null },
    { penalty_goalkeeper_move: 'center' }]) assert.equal(goalkeeperAccess({ ...state(), ...changes }).canSave, false);
  assert.equal(goalkeeperAccess(state(), { active: true }).canSave, false);
  assert.equal(goalkeeperAccess(state(), {}, true).canSave, false);
  assert.equal(goalkeeperAccess(null).canSave, false);
});

class Target {
  listeners = new Map();
  addEventListener(name, fn) { this.listeners.set(name, [...(this.listeners.get(name) ?? []), fn]); }
  removeEventListener(name, fn) { this.listeners.set(name, (this.listeners.get(name) ?? []).filter(f => f !== fn)); }
  emit(name, extra = {}) {
    const event = { pointerId: 1, button: 0, isPrimary: true, clientX: 50, clientY: 400,
      preventDefault() { this.prevented = true; }, stopImmediatePropagation() { this.stopped = true; }, ...extra };
    for (const fn of this.listeners.get(name) ?? []) fn(event);
    return event;
  }
}
function setup(options = {}) {
  const doc = new Target(), win = new Target(), canvas = new Target();
  doc.defaultView = win; canvas.ownerDocument = doc;
  canvas.capture = null;
  canvas.setPointerCapture = id => { canvas.capture = id; };
  canvas.hasPointerCapture = id => canvas.capture === id;
  canvas.releasePointerCapture = () => { canvas.capture = null; };
  canvas.focus = () => {};
  const game = state(), calls = [], previews = [], errors = [];
  const controls = { busy: false, hit: 0, scale: 1 };
  const input = createGoalkeeperInput({ canvas, getState: () => game, isBusy: () => controls.busy,
    getGround: (x, y) => ({ x: x / controls.scale, y: y / controls.scale }), hitTest: () => controls.hit,
    onSubmit: direction => { calls.push(direction); return options.submit?.(direction); },
    onPreview: direction => previews.push(direction), onClear: () => previews.push(null),
    onError: error => errors.push(error) });
  return { doc, win, canvas, game, calls, previews, errors, controls, input };
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test('a completed drag submits once, captures the pointer, and locks that penalty', async () => {
  const t = setup();
  assert.equal(t.canvas.emit('pointerdown').prevented, true);
  assert.equal(t.canvas.capture, 1);
  t.canvas.emit('pointermove', { clientY: 370 });
  assert.equal(t.input.snapshot().direction, 'left');
  t.canvas.emit('pointerup', { clientY: 370 });
  t.canvas.emit('pointerup', { clientY: 370 });
  await flush();
  assert.deepEqual(t.calls, ['left']); assert.equal(t.canvas.capture, null);
  assert.equal(t.input.snapshot().canSave, false);
  assert.equal(t.canvas.emit('pointerdown').prevented, undefined);
  t.game.penalty_kick_num++;
  assert.equal(t.input.snapshot().canSave, true);
  t.input.destroy();
});

test('tap, backward drag and empty pitch never commit a save', async () => {
  for (const point of [{ clientX: 51, clientY: 403 }, { clientX: 20, clientY: 400 }]) {
    const t = setup();
    t.canvas.emit('pointerdown'); t.canvas.emit('pointerup', point);
    await flush(); assert.deepEqual(t.calls, []); assert.equal(t.input.snapshot().canSave, true);
    t.controls.hit = null;
    assert.equal(t.canvas.emit('pointerdown').prevented, undefined);
    t.input.destroy();
  }
});

test('movement threshold uses CSS pixels and contact starts relative to pointer anchor', async () => {
  const t = setup(); t.controls.scale = .2;
  t.canvas.emit('pointerdown', { clientX: 10, clientY: 80 });
  t.canvas.emit('pointerup', { clientX: 15, clientY: 80 });
  await flush(); assert.deepEqual(t.calls, []);
  t.canvas.emit('pointerdown', { clientX: 12, clientY: 82 });
  t.canvas.emit('pointerup', { clientX: 32, clientY: 82 });
  await flush(); assert.deepEqual(t.calls, ['center']);
  t.input.destroy();
});

test('cancellation, hidden page, focus loss, and a second touch cancel without saving', async () => {
  for (const cause of ['pointercancel', 'lostpointercapture', 'escape', 'hidden', 'blur', 'second']) {
    const t = setup();
    t.canvas.emit('pointerdown'); t.canvas.emit('pointermove', { clientY: 450 });
    if (cause === 'escape') t.doc.emit('keydown', { key: 'Escape' });
    else if (cause === 'hidden') { t.doc.hidden = true; t.doc.emit('visibilitychange'); }
    else if (cause === 'blur') t.win.emit('blur');
    else if (cause === 'second') t.canvas.emit('pointerdown', { pointerId: 2, isPrimary: false });
    else t.canvas.emit(cause);
    t.canvas.emit('pointerup', { clientY: 450 });
    await flush(); assert.deepEqual(t.calls, [], cause); assert.equal(t.input.snapshot().dragging, false);
    t.input.destroy();
  }
});

test('server turn changes and busy transitions invalidate an in-progress drag', async () => {
  for (const cause of ['turn', 'busy', 'revision']) {
    const t = setup(); t.canvas.emit('pointerdown');
    if (cause === 'turn') t.game.is_player_a = true;
    if (cause === 'busy') t.controls.busy = true;
    if (cause === 'revision') t.game.penalty_kick_num++;
    t.canvas.emit('pointerup', { clientX: 100 });
    await flush(); assert.deepEqual(t.calls, []); assert.equal(t.canvas.capture, null);
    t.input.destroy();
  }
});

test('failed submission permits retry; destruction detaches all gesture handlers', async () => {
  const t = setup({ submit: () => Promise.reject(new Error('Disconnected')) });
  t.canvas.emit('pointerdown'); t.canvas.emit('pointerup', { clientY: 450 });
  await flush(); assert.equal(t.errors[0].message, 'Disconnected'); assert.equal(t.input.snapshot().canSave, true);
  t.input.destroy();
  t.canvas.emit('pointerdown'); t.canvas.emit('pointerup', { clientX: 100 });
  await flush(); assert.deepEqual(t.calls, ['right']); assert.equal(t.input.snapshot().canSave, false);
});

test('a session change between release and dispatch cannot submit a stale keeper choice', async () => {
  const t = setup();
  t.canvas.emit('pointerdown'); t.canvas.emit('pointerup', { clientX: 100 });
  t.game.penalty_kick_num++;
  await flush(); assert.deepEqual(t.calls, []); assert.equal(t.input.snapshot().canSave, true);
  t.input.destroy();
});
