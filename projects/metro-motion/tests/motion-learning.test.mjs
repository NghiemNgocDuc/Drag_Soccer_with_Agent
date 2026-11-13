import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  FEATURE_VERSION, FEATURE_COUNT, INPUT_SIZE, LABELS, WINDOW_MS, WINDOW_SIZE,
  TemporalWindow, extractFeatures,
} from '../web/js/motion-features.mjs';
import { LearnedController, MAX_MODEL_BYTES, inferModel, validateModel } from '../web/js/learned-controls.mjs';

const baseline = { shoulderX: .5, shoulderY: .32, hipX: .5, hipY: .56,
  width: .2, torso: .24, headY: .18, leftFootY: .91, rightFootY: .91 };
const rules = { tracked: true, calibrated: true, progress: 1, lane: 0, jump: false, duck: false, message: 'Ready' };
function upright(x = 0, y = 0) {
  const points = Array.from({ length: 33 }, () => ({ x: .5 + x, y: .5 + y, z: 0, visibility: .99, presence: .99 }));
  const anchors = { 0: [.5, .18], 11: [.4, .32], 12: [.6, .32], 23: [.44, .56], 24: [.56, .56],
    25: [.44, .73], 26: [.56, .73], 27: [.44, .91], 28: [.56, .91] };
  for (const [index, [px, py]] of Object.entries(anchors)) Object.assign(points[index], { x: px + x, y: py + y });
  return points;
}
function squat() {
  const points = upright();
  for (const index of [0, 11, 12]) points[index].y += .13;
  for (const index of [23, 24]) points[index].y += .07;
  return points;
}
function model({ eligible = false, constant = null } = {}) {
  const data = { format: 'metro-motion-motion-model', version: 1, featureVersion: FEATURE_VERSION,
    labels: [...LABELS], windowSize: 8, sampleHz: 15, inputSize: 64, hiddenSize: 5,
    mean: Array(64).fill(0), scale: Array(64).fill(1),
    weights1: Array.from({ length: 5 }, () => Array(64).fill(0)), bias1: [1, 0, 0, 0, 0],
    weights2: Array.from({ length: 5 }, () => Array(5).fill(0)), bias2: Array(5).fill(0),
    threshold: .8, metrics: { controlEligible: eligible, synthetic: false, note: 'Test fixture, not trained accuracy evidence.' } };
  if (constant !== null) data.bias2[LABELS.indexOf(constant)] = 12;
  else {
    data.weights1[1][56] = -1; data.weights1[2][56] = 1;
    data.weights1[3][58] = 1; data.weights1[4][58] = -1;
    data.weights2[0][0] = 4;
    for (const index of [1, 2, 3]) data.weights2[index][index] = 24;
    data.weights2[4][4] = 40;
  }
  return data;
}
function close(actual, expected, tolerance = 1e-9) {
  assert.equal(actual.length, expected.length);
  actual.forEach((value, index) => assert.ok(Math.abs(value - expected[index]) <= tolerance,
    `index ${index}: ${value} versus ${expected[index]}`));
}
function feed(controller, points = upright(), start = 0, end = 700, rulePose = rules) {
  const results = [];
  for (let t = start; t <= end; t += 70) results.push(controller.update(points, t, baseline, rulePose));
  return results;
}
function active() {
  const controller = new LearnedController();
  controller.load(model({ eligible: true }));
  controller.setMode('control');
  feed(controller);
  return controller;
}

test('body features use mirrored directions, standing normalization, and a bounded eight-value layout', () => {
  assert.equal(FEATURE_COUNT, 8); assert.equal(WINDOW_SIZE, 8); assert.equal(INPUT_SIZE, 64);
  close(extractFeatures(upright(), baseline), [0, 0, 0, 0, 0, 0, 1, 1]);
  close(extractFeatures(upright(.16, -.06), baseline), [-.8, -.8, .25, .25, .25, .25, 1, 1]);
  const distorted = { ...baseline, hipX: 99, headY: -99 };
  const bounded = extractFeatures(upright(), distorted);
  assert.equal(bounded[0], 6); assert.equal(bounded[3], -6);
});

test('invalid baselines, cropped knees, incomplete and corrupt landmark arrays never produce features', () => {
  for (const invalid of [null, {}, { ...baseline, width: .01 }, { ...baseline, torso: NaN }, { ...baseline, hipY: true }]) {
    assert.equal(extractFeatures(upright(), invalid), null);
  }
  const variants = [null, [], upright().slice(0, 29), [...upright(), upright()[0]]];
  for (const [index, key, value] of [[25, 'visibility', .54], [28, 'presence', .4], [27, 'y', .99],
    [0, 'x', NaN], [15, 'visibility', undefined], [17, 'z', Infinity], [16, 'presence', null], [18, 'z', null]]) {
    const points = upright(); points[index][key] = value; variants.push(points);
  }
  for (const points of variants) assert.equal(extractFeatures(points, baseline), null);
});

test('temporal interpolation is time-based, oldest first, with the full causal window required', () => {
  const temporal = new TemporalWindow();
  let result;
  for (const t of [0, 50, 140, 220, 330, 410]) {
    result = temporal.push(Array(8).fill(t / 100), t);
    assert.equal(result, null);
  }
  result = temporal.push(Array(8).fill(5), 500);
  const expected = Array.from({ length: 8 }, (_, index) => Array(8).fill((500 - WINDOW_MS + index * 1000 / 15) / 100)).flat();
  close(result, expected);
  const exact = new TemporalWindow();
  for (let index = 0; index < 8; index++) result = exact.push(Array(8).fill(index / 2), index * 1000 / 15);
  assert.equal(result.length, 64, 'floating-point endpoint roundoff cannot delay the first complete window');
});

test('missing data, duplicates, backwards timestamps, and 250+ ms gaps never interpolate across a break', () => {
  for (const invalid of [[null, 550], [Array(8).fill(0), 490], [Array(8).fill(0), 100],
    [Array(8).fill(NaN), 550], [Array(8).fill(7), 550], [Array(8).fill(0), -1]]) {
    const temporal = new TemporalWindow();
    for (let t = 0; t <= 490; t += 70) temporal.push(Array(8).fill(0), t);
    assert.equal(temporal.push(...invalid), null);
    assert.equal(temporal.samples.length, 0);
    assert.equal(temporal.push(Array(8).fill(1), 600), null);
  }
  const temporal = new TemporalWindow();
  temporal.push(Array(8).fill(0), 0);
  temporal.push(Array(8).fill(0), 250);
  assert.ok(temporal.push(Array(8).fill(0), 500));
  assert.equal(temporal.push(Array(8).fill(1), 751), null);
  assert.equal(temporal.samples.length, 1);
});

test('temporal history copies its input and remains bounded under pathological sample frequency', () => {
  const temporal = new TemporalWindow();
  const features = Array(8).fill(1);
  temporal.push(features, 0); features[0] = 5;
  assert.equal(temporal.samples[0].features[0], 1);
  for (let index = 1; index < 3000; index++) temporal.push(Array(8).fill(1), index / 10);
  assert.ok(temporal.samples.length <= 1024);
});

test('model validation rejects incompatible, malformed, nonfinite and oversized artifacts', () => {
  const edits = [
    (m) => { m.format = 'pickle'; }, (m) => { m.version = 2; },
    (m) => { m.featureVersion = 'other'; }, (m) => { m.labels.reverse(); },
    (m) => { m.inputSize = 63; }, (m) => { m.windowSize = 7; }, (m) => { m.sampleHz = 30; },
    (m) => { m.hiddenSize = 129; }, (m) => { m.hiddenSize = 1.5; },
    (m) => { m.scale[0] = 0; }, (m) => { m.mean[0] = NaN; },
    (m) => { m.weights1[0].pop(); }, (m) => { m.weights2[0][0] = Infinity; },
    (m) => { m.bias2.pop(); }, (m) => { m.threshold = .49; }, (m) => { m.threshold = 1; },
    ...[null, '1', true, 0, .249, 5.001, NaN, Infinity].map((temperature) =>
      (m) => { m.temperature = temperature; }),
    (m) => { m.metrics = {}; }, (m) => { m.metrics.controlEligible = 'true'; },
    (m) => { m.metrics = { controlEligible: true, synthetic: true }; },
  ];
  for (const edit of edits) { const artifact = model(); edit(artifact); assert.throws(() => validateModel(artifact), /Invalid motion model/); }
  for (const artifact of ['not JSON', null, 3, [], ' '.repeat(MAX_MODEL_BYTES + 1)]) {
    assert.throws(() => validateModel(artifact), /Invalid motion model/);
  }
});

test('validated model data is isolated from imports and inference applies standardization, ReLU and stable softmax', () => {
  const artifact = model({ constant: 'left' });
  artifact.bias2 = [0, 0, 0, 0, 0]; artifact.bias1 = [-4, 0, 0, 0, 0];
  artifact.mean[0] = 2; artifact.scale[0] = 2; artifact.weights1[1][0] = 3;
  artifact.weights2[2][1] = 2;
  const validated = validateModel(JSON.stringify(artifact));
  const inputs = Array(64).fill(0); inputs[0] = 4;
  const result = inferModel(validated, inputs);
  assert.equal(result.label, 'right');
  close(result.probabilities, [1, 1, Math.exp(6), 1, 1].map((value) => value / (4 + Math.exp(6))));
  artifact.bias2[1] = 100;
  assert.equal(inferModel(validated, inputs).label, 'right');
  assert.throws(() => { validated.weights1[0][0] = 100; }, TypeError);
  assert.throws(() => inferModel(artifact, inputs), /validate the model/);
  assert.throws(() => inferModel(validated, [NaN]), /64 finite/);
  const overflowing = model(); overflowing.weights1[0][0] = Number.MAX_VALUE;
  const hugeInput = Array(64).fill(2);
  assert.throws(() => inferModel(validateModel(overflowing), hugeInput), /overflowed/);
});

test('temperature calibration preserves labels and matches scaled-logit softmax, including legacy artifacts', () => {
  const artifact = model({ constant: 'right' });
  artifact.bias2 = [-3, 1, 4, .5, -1];
  const input = Array(64).fill(0);
  const legacy = validateModel(artifact);
  assert.equal(legacy.temperature, 1);
  const probabilities = [];
  for (const temperature of [.25, 1, 2, 5]) {
    const calibrated = validateModel({ ...artifact, temperature });
    const result = inferModel(calibrated, input);
    const exp = artifact.bias2.map((logit) => Math.exp(logit / temperature));
    close(result.probabilities, exp.map((value) => value / exp.reduce((sum, x) => sum + x, 0)));
    assert.equal(result.label, 'right');
    if (temperature === 1) close(result.probabilities, inferModel(legacy, input).probabilities);
    probabilities.push(result.confidence);
  }
  for (let index = 1; index < probabilities.length; index++) {
    assert.ok(probabilities[index] < probabilities[index - 1], 'higher temperatures reduce this prediction confidence');
  }
  const huge = validateModel({ ...artifact, temperature: .25, bias2: [10000, 10001, 10003, 10002, 9999] });
  assert.ok(inferModel(huge, input).probabilities.every(Number.isFinite));
});

test('models always load in comparison and experimental control requires explicit eligible metrics', () => {
  const controller = new LearnedController();
  controller.load(model());
  assert.equal(controller.mode, 'compare');
  assert.throws(() => controller.setMode('control'), /held-out evaluation/);
  assert.throws(() => controller.setMode('other'), /compare or control/);
  controller.load(model({ eligible: true })); controller.setMode('control');
  controller.load(model({ eligible: true }));
  assert.equal(controller.mode, 'compare', 'import never silently enables experimental controls');
  const result = feed(controller, upright(.16), 0, 700, { ...rules, lane: 1, duck: true }).at(-1);
  assert.equal(result.prediction.label, 'left');
  assert.equal(result.lane, 1); assert.equal(result.duck, true); assert.equal(result.source, 'rules');
});

test('unavailable, warming, low-confidence and failed inference retain existing rule controls', () => {
  const controller = new LearnedController();
  assert.equal(controller.update(null, 0, null, rules).reason, 'no-model');
  controller.load(model({ eligible: true })); controller.setMode('control');
  const fallback = { ...rules, jump: true, lane: -1 };
  assert.equal(controller.update(upright(), 0, baseline, fallback).jump, true);
  const uncertain = model({ eligible: true }); uncertain.weights2 = uncertain.weights2.map((row) => row.map(() => 0));
  controller.load(uncertain); controller.setMode('control');
  let result = feed(controller, upright(), 0, 700, fallback).at(-1);
  assert.equal(result.reason, 'low-confidence'); assert.equal(result.jump, true); assert.equal(result.lane, -1);
  const overflow = model({ eligible: true }); overflow.weights1[0][56] = Number.MAX_VALUE;
  controller.load(overflow); controller.setMode('control');
  result = feed(controller, upright(-.3), 0, 700, fallback).at(-1);
  assert.equal(result.reason, 'prediction-failed'); assert.equal(result.jump, true);
});

test('learned lanes require a brief stable prediction, neutral returns center, and jump/duck preserve rule lanes', () => {
  const controller = active();
  const left = feed(controller, upright(.16), 770, 1050);
  assert.equal(left[0].lane, 0); assert.equal(left.at(-1).lane, -1);
  assert.equal(feed(controller, upright(), 1120, 1400).at(-1).lane, 0);
  assert.equal(feed(controller, upright(-.16), 1470, 1750).at(-1).lane, 1);
  const duck = feed(controller, squat(), 1820, 2100, { ...rules, lane: -1 }).at(-1);
  assert.equal(duck.duck, true); assert.equal(duck.lane, -1);
  const jump = feed(controller, upright(0, -.08), 2170, 2450, { ...rules, lane: 1 }).at(-1);
  assert.equal(jump.lane, 1);
});

test('a learned jump pulses once, needs landed non-jump evidence to rearm, and shares the fallback cooldown', () => {
  const controller = active();
  let results = feed(controller, upright(0, -.08), 770, 1750);
  assert.equal(results.filter((result) => result.jump).length, 1);
  feed(controller, upright(), 1820, 2170);
  results = feed(controller, upright(0, -.08), 2240, 2590);
  assert.equal(results.filter((result) => result.jump).length, 1);
  const stuck = new LearnedController(); stuck.load(model({ eligible: true, constant: 'jump' })); stuck.setMode('control');
  assert.equal(feed(stuck, upright(), 0, 2800).some((result) => result.jump), false,
    'a model stuck on jump cannot arm itself from standing frames');
  const fallback = active();
  const ambiguous = fallback.update(upright(1 / 30), 770, baseline, { ...rules, jump: true });
  assert.equal(ambiguous.reason, 'low-confidence'); assert.equal(ambiguous.jump, true);
  feed(fallback, upright(), 840, 980);
  assert.equal(feed(fallback, upright(0, -.08), 1050, 1260).some((result) => result.jump), false,
    'a confident prediction cannot fire a second jump inside the fallback jump cooldown');
  feed(fallback, upright(), 1330, 1610);
  assert.equal(feed(fallback, upright(0, -.08), 1680, 1960).filter((result) => result.jump).length, 1);
});

test('brief uncertainty at takeoff preserves physically armed jumps without adding a prediction delay', () => {
  for (const ambiguousPoints of [upright(1 / 30), upright(0, -.04)]) {
    const controller = active();
    const uncertain = controller.update(ambiguousPoints, 770, baseline, rules);
    assert.equal(uncertain.reason, 'low-confidence');
    assert.equal(uncertain.jump, false);
    const takeoff = controller.update(upright(0, -.08), 840, baseline, rules);
    assert.equal(takeoff.source, 'learned');
    assert.equal(takeoff.jump, true, 'first confident airborne frame must retain preceding standing evidence');
    assert.equal(feed(controller, upright(0, -.08), 910, 1680).some((result) => result.jump), false);
  }
});

test('valid standing frames during warmup and uncertain landings can arm the next physical jump', () => {
  const controller = new LearnedController();
  controller.load(model({ eligible: true })); controller.setMode('control');
  assert.ok(feed(controller, upright(), 0, 420).every((result) => result.reason === 'warming-up'));
  assert.equal(controller.update(upright(0, -.08), 490, baseline, rules).jump, true);
  const uncertainLanding = feed(controller, upright(1 / 30), 560, 1260);
  assert.ok(uncertainLanding.every((result) => result.reason === 'low-confidence'));
  assert.equal(controller.update(upright(0, -.08), 1330, baseline, rules).jump, true);
});

test('rules and learned controls share one emitted-jump cooldown while comparison remains untouched', () => {
  const controller = active();
  assert.equal(controller.update(upright(0, -.08), 770, baseline, rules).jump, true);
  const fallback = controller.update(upright(0, -.04), 840, baseline, { ...rules, jump: true });
  assert.equal(fallback.reason, 'low-confidence');
  assert.equal(fallback.jump, false, 'late rule detection cannot duplicate a learned takeoff');
  feed(controller, upright(), 910, 1540);
  assert.equal(controller.update(upright(0, -.04), 1610, baseline, { ...rules, jump: true }).jump, true);
  assert.equal(controller.update(upright(0, -.08), 1680, baseline, rules).jump, false,
    'late learned detection cannot duplicate a rule takeoff');
  controller.setMode('compare');
  assert.equal(controller.update(upright(), 1750, baseline, { ...rules, jump: true }).jump, true);
  assert.equal(controller.update(upright(), 1820, baseline, { ...rules, jump: true }).jump, true);
});

test('tracking breaks, long frame gaps and recalibration require fresh landed evidence before another learned jump', () => {
  for (const kind of ['tracking', 'gap', 'calibration']) {
    const controller = active();
    let next = 770, currentBaseline = baseline;
    if (kind === 'tracking') {
      controller.update(null, next, baseline, { ...rules, tracked: false });
      next += 70;
    } else if (kind === 'gap') next = 1050;
    else currentBaseline = { ...baseline, hipX: .51 };
    for (let t = next; t <= next + 980; t += 70) {
      assert.equal(controller.update(upright(0, -.08), t, currentBaseline, rules).jump, false,
        `${kind} cannot reuse landing evidence from before the break`);
    }
    for (let t = next + 1050; t <= next + 1400; t += 70) controller.update(upright(), t, currentBaseline, rules);
    assert.equal(controller.update(upright(0, -.08), next + 1470, currentBaseline, rules).jump, true);
  }
});

test('duck releases on the next different prediction, tracking loss, removal, and mode changes', () => {
  const controller = active();
  assert.equal(feed(controller, squat(), 770, 1050).at(-1).duck, true);
  assert.equal(controller.update(upright(), 1120, baseline, rules).duck, false);
  assert.equal(feed(controller, squat(), 1190, 1470).at(-1).duck, true);
  const lost = controller.update(null, 1540, baseline, { ...rules, tracked: false, duck: true, jump: true });
  assert.equal(lost.duck, false); assert.equal(lost.jump, false);
  controller.clear();
  assert.equal(controller.mode, 'compare'); assert.equal(controller.model, null);
  assert.equal(controller.update(upright(), 1610, baseline, rules).source, 'rules');
});

test('the learner preserves the rule tracker grace period and cannot bypass tracking/calibration readiness', () => {
  const controller = active();
  const grace = controller.update(null, 770, baseline, { ...rules, duck: true });
  assert.equal(grace.source, 'rules'); assert.equal(grace.duck, true, 'a brief rule tracker occlusion keeps its existing behavior');
  for (const flag of ['tracked', 'calibrated']) {
    const result = controller.update(upright(), 840, baseline, { ...rules, [flag]: false, jump: true, duck: true });
    assert.equal(result.source, 'rules'); assert.equal(result.jump, false); assert.equal(result.duck, false);
  }
});

test('calibration changes, invalid samples, stale capture times and resets discard learned history', () => {
  const controller = active();
  assert.equal(controller.update(upright(), 600, baseline, rules).reason, 'invalid-tracking');
  assert.equal(controller.update(upright(), 770, baseline, rules).reason, 'warming-up');
  feed(controller, upright(), 840, 1400);
  const movedBaseline = { ...baseline, hipX: .51 };
  assert.equal(controller.update(upright(), 1470, movedBaseline, rules).reason, 'warming-up');
  controller.reset();
  assert.equal(controller.update(upright(), 0, baseline, rules).reason, 'warming-up', 'new sessions may restart their clock');
  assert.equal(controller.update(upright(), 70, baseline, { ...rules, calibrated: false }).prediction, null);
});

test('browser features and temporal windows match the Python training parity fixture', () => {
  const fixture = JSON.parse(readFileSync(new URL('../training/tests/parity.json', import.meta.url), 'utf8'));
  const temporal = new TemporalWindow();
  for (const frame of fixture.frames) {
    const features = extractFeatures(frame.landmarks, fixture.baseline);
    if (frame.features === null) assert.equal(features, null);
    else close(features, frame.features);
    const window = temporal.push(features, frame.t);
    if (frame.window === null) assert.equal(window, null);
    else close(window, frame.window);
  }
});
