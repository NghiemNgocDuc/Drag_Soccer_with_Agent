import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const moduleURL = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const rules = await readFile(new URL('../../static/js/game/player-controls.js', import.meta.url), 'utf8');
const source = await readFile(new URL('../../static/js/game/player-input.js', import.meta.url), 'utf8');
const { createPlayerInput } = await import(moduleURL(source.replace("'./player-controls.js'", JSON.stringify(moduleURL(rules)))));

class Element {
  constructor(tag = 'div') { this.tag = tag; this.listeners = new Map(); this.children = []; this.attributes = new Map(); this._value = ''; }
  addEventListener(name, listener) { this.listeners.set(name, [...(this.listeners.get(name) || []), listener]); }
  emit(name, values = {}) {
    const event = { target: this, preventDefault() {}, stopImmediatePropagation() {}, ...values };
    for (const listener of this.listeners.get(name) || []) listener(event);
  }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes.set(name, value); }
  set value(value) {
    // A native select becomes blank when assigned a value absent from its options.
    this._value = this.tag === 'select' && !this.children.some(option => option.value === String(value)) ? '' : String(value);
  }
  get value() { return this._value; }
}

function setup(t, overrides = {}) {
  const oldDocument = globalThis.document, oldWindow = globalThis.window;
  const elements = new Map();
  const doc = new Element(), win = new Element(), canvas = new Element('canvas');
  doc.getElementById = id => {
    if (!elements.has(id)) elements.set(id, new Element(id === 'player-select' ? 'select' : 'div'));
    return elements.get(id);
  };
  doc.createElement = tag => new Element(tag);
  doc.querySelectorAll = () => [];
  doc.body = new Element('body');
  globalThis.document = doc; globalThis.window = win;
  t.after(() => { globalThis.document = oldDocument; globalThis.window = oldWindow; });
  const game = { game_mode: 'hvai', match_started: false, is_player_a: true, kick_count: 0,
    ball: { x: 700, y: 400 }, players_a: Array.from({ length: 5 }, (_, index) => ({ x: 100 + index * 100, y: 400 })),
    players_b: Array.from({ length: 5 }, (_, index) => ({ x: 1300 - index * 100, y: 400 })), ...overrides };
  const kicks = [], selections = [], controls = { busy: false };
  const input = createPlayerInput({ canvas, getState: () => game, getOnline: () => ({}), isBusy: () => controls.busy,
    getGround: (x, y) => ({ x, y }), hitTest: () => null, onAim() {}, onClear() {},
    onSelect: (...selection) => selections.push(selection), onKick: (...command) => kicks.push(command),
    setOrbitEnabled() {}, getOrbitEnabled: () => false });
  return { input, game, kicks, selections, controls, elements, doc, canvas };
}

test('fresh kickoff selects the legal human player before the first button or keyboard kick', t => {
  const fixture = setup(t), { input, game, kicks, selections, elements } = fixture;
  input.sync();
  assert.deepEqual(input.snapshot().allowedIndices, []);
  assert.equal(elements.get('kick-btn').disabled, true);
  game.match_started = true;
  input.sync();
  assert.equal(game.kick_count, 0);
  assert.equal(input.selectedIndex, 4);
  assert.equal(elements.get('player-select').value, '4');
  assert.deepEqual(selections.at(-1), ['a', 4, true]);
  assert.equal(elements.get('kick-btn').disabled, false);
  elements.get('kick-btn').emit('click');
  fixture.doc.emit('keydown', { target: fixture.canvas, key: 'Enter' });
  assert.deepEqual(kicks.map(command => command[0]), [4, 4]);
});

test('returning to setup and restarting without a move restores the human selection', t => {
  const { input, game, elements } = setup(t, { match_started: true,
    custom_lineup_a: [{ role: 'goalkeeper' }, { role: 'defender' }, { role: 'support' }, { role: 'attacker' }, { role: 'defender' }] });
  input.sync();
  assert.equal(input.selectedIndex, 4);
  game.match_started = false;
  input.sync();
  assert.deepEqual(input.snapshot().allowedIndices, []);
  assert.equal(elements.get('player-select').value, '');
  assert.equal(elements.get('kick-btn').disabled, true);
  game.match_started = true;
  input.sync();
  assert.equal(game.kick_count, 0);
  assert.equal(input.selectedIndex, 4);
  assert.equal(elements.get('player-select').value, '4');
  assert.match(input.snapshot().reason, /Defender/);
});

test('ordinary sync and animation locks preserve a valid hotseat selection', t => {
  const { input, controls, elements } = setup(t, { game_mode: 'hvh', match_started: true });
  input.sync();
  input.select(3);
  controls.busy = true;
  input.sync();
  assert.equal(elements.get('kick-btn').disabled, true);
  controls.busy = false;
  input.sync();
  assert.equal(input.selectedIndex, 3);
  assert.equal(elements.get('player-select').value, '3');
  assert.equal(elements.get('kick-btn').disabled, false);
});
