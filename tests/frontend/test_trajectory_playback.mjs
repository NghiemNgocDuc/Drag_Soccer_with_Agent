import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../../static/js/game/trajectory-playback.js', import.meta.url), 'utf8');
const { createTrajectoryPlayback, createTrajectorySampler } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('simulation timestamps preserve uneven sample spacing', () => {
  const playback = createTrajectoryPlayback([{ t: 5 }, { t: 5.02 }, { t: 5.17 }, { t: 5.2 }]);
  assert.ok(Math.abs(playback.duration - 200) < 1e-9);
  const sample = playback.sample(100);
  assert.equal(sample.segIdx, 1);
  assert.ok(Math.abs(sample.segFrac - 0.08 / 0.15) < 1e-9);
});

test('long physics paths remain responsive and respect playback speed', () => {
  const points = [{ t: 0 }, { t: 8 }];
  assert.equal(createTrajectoryPlayback(points).duration, 2400);
  assert.equal(createTrajectoryPlayback(points, 2).duration, 1200);
  assert.equal(createTrajectoryPlayback(points).sample(2400).segFrac, 1);
});

test('legacy replay duration remains 24 ms per sample', () => {
  const playback = createTrajectoryPlayback([{}, {}, {}]);
  assert.equal(playback.duration, 48);
  assert.equal(playback.sample(12).segFrac, 0.5);
  assert.equal(playback.sample(48).done, true);
});

test('invalid and zero timestamps fall back safely', () => {
  for (const points of [[{ t: 0 }, { t: 0 }], [{ t: 2 }, { t: 1 }], [{ t: 0 }, {}]]) {
    const playback = createTrajectoryPlayback(points);
    assert.equal(playback.duration, 24);
    assert.equal(playback.sample(24).done, true);
  }
});

test('30 and 60 FPS playback end at the same position and time', () => {
  const points = [{ t: 0 }, { t: 0.01 }, { t: 0.3 }, { t: 0.8 }];
  for (const fps of [30, 60]) {
    const playback = createTrajectoryPlayback(points);
    let sample;
    for (let frame = 0; frame <= fps * 0.8; frame++) sample = playback.sample(frame * 1000 / fps);
    assert.equal(sample.progress, 3);
    assert.equal(sample.segFrac, 1);
    assert.equal(sample.done, true);
  }
});

test('bounces trigger once even when a slow frame crosses multiple samples', () => {
  const points = [{ t: 0, b: [] }, { t: 0.01, bounce: true },
    { t: 0.02, b: true }, { t: 0.2, bounce: true }];
  const playback = createTrajectoryPlayback(points);
  assert.deepEqual(playback.takeBounces(playback.sample(0).progress), []);
  assert.deepEqual(playback.takeBounces(playback.sample(100).progress), points.slice(1, 3));
  assert.deepEqual(playback.takeBounces(playback.sample(110).progress), []);
  assert.deepEqual(playback.takeBounces(playback.sample(200).progress), [points[3]]);
  assert.deepEqual(playback.takeBounces(playback.sample(200).progress), []);
});

test('empty and single-frame paths finish without invalid segment indices', () => {
  for (const points of [[], [{ x: 4, y: 8, t: 0 }]]) {
    assert.deepEqual(createTrajectoryPlayback(points).sample(0),
      { progress: 0, segIdx: 0, segFrac: 0, done: true });
  }
});

test('ball and player interpolation preserve constant velocity across uneven timestamps', () => {
  const points = [0, 0.02, 0.17, 0.4, 1].map(t => ({ t, x: 100 + 80 * t, y: 50 + 20 * t, z: 0,
    a: [{ x: 40 + 80 * t, y: 50 + 20 * t }], b: [{ x: 900, y: 200 }] }));
  const before = structuredClone(points), clock = createTrajectoryPlayback(points), motion = createTrajectorySampler(points);
  for (const fps of [10, 30, 60, 120, 144]) {
    for (let frame = 0; frame <= fps; frame++) {
      const sample = clock.sample(frame * 1000 / fps), ball = motion.ball(sample), player = motion.player('a', 0, sample);
      assert.ok(Math.abs(ball.x - (100 + 80 * frame / fps)) < 1e-9);
      assert.ok(Math.abs(ball.y - (50 + 20 * frame / fps)) < 1e-9);
      assert.ok(Math.abs(ball.x - player.x - 60) < 1e-9);
    }
  }
  assert.deepEqual(points, before);
});

test('smooth deceleration has continuous velocity at interior samples', () => {
  const points = [0, .1, .25, .5, 1].map(t => ({ t, x: 100 * (2 * t - t * t), y: 20, z: 0 }));
  const clock = createTrajectoryPlayback(points), motion = createTrajectorySampler(points);
  for (const t of [.1, .25, .5]) {
    const x = offset => motion.ball(clock.sample((t + offset) * 1000)).x;
    const h = 1e-6;
    assert.ok(Math.abs((x(0) - x(-h)) / h - (x(h) - x(0)) / h) < .01);
  }
});

test('interpolation stays within adjacent samples at stops and abrupt reversals', () => {
  const points = [0, 100, 102, 102, -10, 4].map((x, i) => ({ t: i * .07, x, y: -x, z: i % 2 ? 0 : 10 }));
  const motion = createTrajectorySampler(points);
  for (let segIdx = 0; segIdx < points.length - 1; segIdx++) {
    for (let j = 0; j <= 100; j++) {
      const value = motion.ball({ segIdx, segFrac: j / 100 });
      for (const axis of ['x', 'y', 'z']) {
        const low = Math.min(points[segIdx][axis], points[segIdx + 1][axis]);
        const high = Math.max(points[segIdx][axis], points[segIdx + 1][axis]);
        assert.ok(value[axis] >= low && value[axis] <= high);
      }
    }
  }
});

test('wall and ground contacts keep the collision corner without rounding through it', () => {
  const points = [{ t: 0, x: 50, y: 10, z: 10 }, { t: .1, x: 100, y: 20, z: 0, bounce: true },
    { t: .2, x: 70, y: 30, z: 5 }, { t: .4, x: 30, y: 40, z: 0 }];
  const motion = createTrajectorySampler(points);
  assert.deepEqual(motion.ball({ segIdx: 0, segFrac: .5 }), { x: 75, y: 15, z: 5 });
  assert.deepEqual(motion.ball({ segIdx: 1, segFrac: .5 }), { x: 85, y: 25, z: 2.5 });
  assert.deepEqual(motion.ball({ segIdx: 2, segFrac: 1 }), { x: 30, y: 40, z: 0 });
});

test('legacy bounce metadata and duplicate timestamps have safe fallbacks', () => {
  const points = [{ t: 0, x: 0, y: 0, a: [{ x: 1, y: 2 }], b: [] },
    { t: 0, x: 2, y: 1, a: [{ x: 1, y: 2 }], b: true }, { t: 1, x: 4, y: 3 }];
  const motion = createTrajectorySampler(points);
  assert.equal(motion.hasPlayer('a', 0), false);
  assert.equal(motion.player('b', 0, { segIdx: 0, segFrac: 0 }), null);
  assert.deepEqual(motion.ball({ segIdx: 0, segFrac: .5 }), { x: 1, y: .5, z: 0 });
  const single = createTrajectorySampler([points[0]]);
  assert.deepEqual(single.ball(createTrajectoryPlayback([points[0]]).sample(0)), { x: 0, y: 0, z: 0 });
});
