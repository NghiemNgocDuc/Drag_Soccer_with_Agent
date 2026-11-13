import test from 'node:test';
import assert from 'node:assert/strict';
import { PoseController } from '../web/js/pose-controls.mjs';

function upright({ x = 0, y = 0 } = {}) {
  const points = Array.from({ length: 33 }, () => ({ x: .5 + x, y: .5 + y, z: 0, visibility: .99, presence: .99 }));
  const anchors = { 0: [.5, .18], 11: [.4, .32], 12: [.6, .32], 23: [.44, .56], 24: [.56, .56], 25: [.44, .73], 26: [.56, .73], 27: [.44, .91], 28: [.56, .91] };
  for (const [index, [px, py]] of Object.entries(anchors)) Object.assign(points[index], { x: px + x, y: py + y });
  return points;
}
function calibrated(options) {
  const controller = new PoseController(options);
  let result;
  for (let t = 0; t <= 2100; t += 70) result = controller.update(upright(), t);
  assert.equal(result.calibrated, true);
  return controller;
}
function feed(controller, points, from, duration = 350, interval = 70) {
  const results = [];
  for (let t = from; t <= from + duration; t += interval) results.push(controller.update(points, t));
  return results;
}

test('calibration requires a stable visible full body for two seconds', () => {
  const controller = new PoseController();
  assert.equal(controller.update(upright(), 0).calibrated, false);
  assert.equal(controller.update(upright(), 1000).progress, 0, 'a long sampling gap restarts calibration');
  for (let t = 1070; t <= 1700; t += 70) controller.update(upright(), t);
  assert.equal(controller.update(upright({ x: .12 }), 1770).progress, 0, 'stepping restarts the baseline');
  const cropped = upright(); cropped[27].visibility = .2;
  assert.equal(controller.update(cropped, 2500).tracked, false);
  assert.equal(controller.update(upright(), 2570).progress, 0);
  for (let t = 2640; t < 4670; t += 70) controller.update(upright(), t);
  assert.equal(controller.update(upright(), 4670).calibrated, true);
});

test('mirrored sideways steps select lanes and hold through threshold jitter', () => {
  const controller = calibrated();
  assert.equal(feed(controller, upright({ x: .16 }), 2170).at(-1).lane, -1);
  for (let t = 2590; t < 3010; t += 70) {
    const result = controller.update(upright({ x: t % 140 ? .106 : .116 }), t);
    assert.equal(result.lane, -1, 'hysteresis keeps lane stable near the entry threshold');
  }
  assert.equal(feed(controller, upright(), 3010).at(-1).lane, 0);
  assert.equal(feed(controller, upright({ x: -.16 }), 3430).at(-1).lane, 1);
  const unmirrored = calibrated({ mirrored: false });
  assert.equal(feed(unmirrored, upright({ x: .16 }), 2170).at(-1).lane, 1);
});

test('one noisy frame cannot change lanes, and smoothing depends on elapsed time', () => {
  for (const interval of [35, 70, 100]) {
    const controller = calibrated();
    assert.equal(controller.update(upright({ x: .17 }), 2170).lane, 0);
    assert.equal(feed(controller, upright(), 2170 + interval, 350, interval).at(-1).lane, 0);
    assert.equal(feed(controller, upright({ x: .16 }), 2650, 400, interval).at(-1).lane, -1);
  }
});

test('a jump requires head, hips and BOTH feet to rise, emits once and rearms on landing', () => {
  const controller = calibrated();
  const armOnly = upright(); armOnly[15].y = .1; armOnly[16].y = .1;
  assert.equal(feed(controller, armOnly, 2170).some((result) => result.jump), false);
  const raisedBody = upright({ y: -.07 }); raisedBody[27].y = .91; raisedBody[28].y = .91;
  assert.equal(feed(controller, raisedBody, 2590).some((result) => result.jump), false);
  const raisedKnee = upright({ y: -.07 }); raisedKnee[28].y = .91;
  assert.equal(feed(controller, raisedKnee, 3010).some((result) => result.jump), false);
  feed(controller, upright(), 3430);
  const airborne = feed(controller, upright({ y: -.07 }), 3850, 1050);
  assert.equal(airborne.filter((result) => result.jump).length, 1);
  feed(controller, upright(), 4970, 420);
  assert.equal(feed(controller, upright({ y: -.07 }), 5460).filter((result) => result.jump).length, 1);
});

test('duck is held, releases smoothly and does not become a jump', () => {
  const controller = calibrated();
  const squat = upright();
  for (const i of [0, 11, 12]) squat[i].y += .13;
  for (const i of [23, 24]) squat[i].y += .07;
  const results = feed(controller, squat, 2170, 700);
  assert.equal(results.at(-1).duck, true);
  assert.equal(results.some((result) => result.jump), false);
  const standing = feed(controller, upright(), 2940);
  assert.equal(standing.at(-1).duck, false);
  assert.equal(standing.some((result) => result.jump), false);
});

test('lost, cropped, corrupt and stale poses cannot produce control events', () => {
  const controller = calibrated();
  assert.equal(controller.update(null, 2200).tracked, true, 'short gaps have a small grace period');
  assert.equal(controller.update([], 2600).tracked, false);
  assert.equal(controller.update(upright({ y: -.07 }), 2670).jump, false, 'reacquisition midair cannot create a jump');
  const corrupt = upright(); corrupt[23].x = NaN;
  assert.equal(controller.update(corrupt, 3200).tracked, false);
  assert.equal(controller.update(upright(), 2600).tracked, false, 'older frame timestamps are rejected');
  const cropped = upright(); cropped[28].y = 1.1;
  assert.equal(controller.update(cropped, 3300).tracked, false);
  feed(controller, upright(), 3370);
  assert.equal(feed(controller, upright({ y: -.07 }), 3790).some((result) => result.jump), true);
});

test('watchdog ticks do not reject fresh frames that finish inference after capture', () => {
  const controller = calibrated();
  controller.update(null, 2200);
  assert.equal(controller.update(upright(), 2170).tracked, true);
  controller.update(null, 2300);
  assert.equal(controller.update(upright(), 2240).tracked, true);
  controller.update(null, 2900);
  assert.equal(controller.update(upright(), 2310).tracked, false, 'a genuinely stale inference result is rejected');
});

test('recalibration discards the old center and does not retain a duck or lane', () => {
  const controller = calibrated();
  feed(controller, upright({ x: .16 }), 2170);
  controller.calibrate();
  let result;
  for (let t = 3000; t <= 5100; t += 70) result = controller.update(upright({ x: .12 }), t);
  assert.equal(result.calibrated, true);
  assert.equal(result.lane, 0);
  assert.equal(result.duck, false);
  assert.equal(controller.update(upright({ x: .12 }), 5170).lane, 0);
});

test('visibility, presence, cropping and malformed landmarks cannot establish a baseline', () => {
  const cases = [null, [], upright().slice(0, 28)];
  for (const [index, field, value] of [[0, 'visibility', .54], [27, 'presence', .2], [28, 'y', 1], [23, 'x', Infinity], [11, 'visibility', undefined], [25, 'y', NaN]]) {
    const points = upright(); points[index][field] = value; cases.push(points);
  }
  for (const points of cases) {
    const controller = new PoseController();
    for (let t = 0; t < 4000; t += 70) {
      const result = controller.update(points, t);
      assert.equal(result.calibrated, false);
      assert.equal(result.tracked, false);
      assert.equal(result.jump, false);
      assert.equal(result.duck, false);
    }
  }
});

test('calibration restarts for gradual movement and remains memory-bounded with fast inputs', () => {
  const controller = new PoseController();
  for (let t = 0; t <= 1000; t++) {
    const result = controller.update(upright(), t);
    assert.equal(result.calibrated, false);
    assert.ok(controller.samples.length <= 240);
  }
  assert.equal(controller.update(upright({ x: .06 }), 1070).progress, 0);
  const before = controller.update(upright({ x: .06 }), 1140).progress;
  assert.ok(before > 0);
  const moved = controller.update(upright({ y: -.06, x: .06 }), 1210);
  assert.equal(moved.progress, 0, 'a rising body cannot become the standing baseline');
});

test('normalized actions work for different visible body sizes and horizontal calibration centers', () => {
  for (const scale of [.75, 1, 1.1]) {
    const body = (x = 0, y = 0) => upright().map((point) => ({ ...point,
      x: .5 + (point.x - .5) * scale + .04 + x,
      y: .5 + (point.y - .5) * scale + y,
    }));
    const controller = new PoseController();
    for (let t = 0; t <= 2100; t += 70) controller.update(body(), t);
    assert.ok(controller.baseline);
    assert.equal(feed(controller, body(.16 * scale), 2170).at(-1).lane, -1);
    assert.equal(feed(controller, body(), 2590).at(-1).lane, 0);
    assert.equal(feed(controller, body(0, -.07 * scale), 3010).filter((result) => result.jump).length, 1);
    feed(controller, body(), 3430);
    const squat = body();
    for (const index of [0, 11, 12]) squat[index].y += .13 * scale;
    for (const index of [23, 24]) squat[index].y += .07 * scale;
    assert.equal(feed(controller, squat, 3850).at(-1).duck, true);
  }
});

test('small body jitter never produces jumps, ducks, or lane changes over a long sample stream', () => {
  const controller = calibrated();
  for (let t = 2170; t < 60000; t += 70) {
    const result = controller.update(upright({ x: Math.sin(t) * .018, y: Math.cos(t * .7) * .012 }), t);
    assert.equal(result.tracked, true);
    assert.equal(result.lane, 0);
    assert.equal(result.jump, false);
    assert.equal(result.duck, false);
  }
});

test('tracking loss releases held duck and reacquisition requires landing before another jump', () => {
  const controller = calibrated();
  const squat = upright();
  for (const i of [0, 11, 12]) squat[i].y += .13;
  for (const i of [23, 24]) squat[i].y += .07;
  assert.equal(feed(controller, squat, 2170).at(-1).duck, true);
  assert.equal(controller.update(null, 2600).duck, true, 'brief occlusion preserves a held gesture');
  const lost = controller.update(null, 3100);
  assert.equal(lost.tracked, false);
  assert.equal(lost.duck, false);
  const inAir = feed(controller, upright({ y: -.07 }), 3170, 1400);
  assert.equal(inAir.some((result) => result.jump), false);
  feed(controller, upright(), 4640, 420);
  assert.equal(feed(controller, upright({ y: -.07 }), 5130).filter((result) => result.jump).length, 1);
});

test('invalid clock values and out-of-order samples do not poison subsequent valid tracking', () => {
  const controller = calibrated();
  for (const timestamp of [NaN, Infinity, -Infinity, 2000]) {
    const result = controller.update(upright({ y: -.07 }), timestamp);
    assert.equal(result.tracked, false);
    assert.equal(result.jump, false);
  }
  assert.equal(controller.update(upright(), 2170).tracked, true);
  assert.equal(feed(controller, upright({ x: -.16 }), 2240).at(-1).lane, 1);
});
