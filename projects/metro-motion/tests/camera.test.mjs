import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createContext, runInContext } from 'node:vm';
import { CameraTracker } from '../web/js/camera.mjs';

const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function harness({ media, bitmap, autoReady = true, secure = true } = {}) {
  let now = 0, nextId = 0;
  const timers = new Map(), workers = [], statuses = [], errors = [], poses = [], requests = [], bitmaps = [];
  const track = { stopped: 0, stop() { this.stopped++; } };
  const stream = { getTracks: () => [track] };
  const video = { muted: false, playsInline: false, readyState: 2, currentTime: 1, srcObject: null, play: () => Promise.resolve(), pause() {} };
  class FakeWorker {
    constructor(url, options) { this.url = url; this.options = options; this.messages = []; workers.push(this); }
    postMessage(message, transfers) {
      this.messages.push({ message, transfers });
      if (message.type === 'init' && autoReady) queueMicrotask(() => this.reply({ type: 'ready' }));
    }
    reply(data) { this.onmessage?.({ data }); }
    terminate() { this.terminated = true; }
  }
  const tracker = new CameraTracker({ video, onPose: (...args) => poses.push(args), onStatus: (state) => statuses.push(state), onError: (error) => errors.push(error), dependencies: {
    secure, Worker: FakeWorker, now: () => now,
    mediaDevices: { getUserMedia: (options) => { requests.push(options); return typeof media === 'function' ? media() : media || Promise.resolve(stream); } },
    createImageBitmap: () => { const item = { closed: 0, close() { this.closed++; } }; bitmaps.push(item); return bitmap || Promise.resolve(item); },
    setTimeout: (callback, delay) => { const id = ++nextId; timers.set(id, { callback, due: now + delay }); return id; },
    clearTimeout: (id) => timers.delete(id),
  } });
  async function advance(ms) {
    const end = now + ms;
    await flush();
    for (;;) {
      const entry = [...timers.entries()].filter(([, timer]) => timer.due <= end).sort((a, b) => a[1].due - b[1].due)[0];
      if (!entry) break;
      now = entry[1].due;
      timers.delete(entry[0]);
      entry[1].callback();
      await flush();
    }
    now = end;
    await flush();
  }
  return { tracker, video, track, stream, workers, statuses, errors, poses, requests, bitmaps, timers, advance };
}

test('only explicit start requests video; duplicate starts share one camera and worker', async () => {
  const h = harness();
  assert.equal(h.requests.length, 0);
  const starts = [h.tracker.start(), h.tracker.start()];
  assert.deepEqual(await Promise.all(starts), [true, true]);
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].audio, false);
  assert.equal(h.workers.length, 1);
  assert.equal(h.workers[0].options, undefined, 'classic worker supports the unmodified MediaPipe WASM loader');
  h.tracker.stop();
  assert.equal(h.track.stopped, 1);
  assert.equal(h.video.srcObject, null);
  assert.equal(h.workers[0].terminated, true);
  assert.equal(h.timers.size, 0);
});

test('stop cancels a permission prompt and disposes a stream arriving later', async () => {
  const permission = deferred();
  const h = harness({ media: permission.promise });
  const start = h.tracker.start();
  h.tracker.stop();
  assert.equal(await start, false);
  permission.resolve(h.stream);
  await flush();
  assert.equal(h.track.stopped, 1);
  assert.equal(h.video.srcObject, null);
  assert.equal(h.workers.length, 0);
  assert.equal(h.errors.length, 0);
});

test('permission timeout releases late streams and allows a new attempt', async () => {
  const permission = deferred();
  const h = harness({ media: permission.promise });
  const start = h.tracker.start();
  await h.advance(30001);
  assert.equal(await start, false);
  permission.resolve(h.stream);
  await flush();
  assert.equal(h.track.stopped, 1);
  assert.match(h.errors[0].message, /too long/);
  assert.equal(h.tracker.starting, null);
});

test('camera denial and insecure origins report actionable errors without spawning inference', async () => {
  const denied = Promise.reject(Object.assign(new Error('Denied'), { name: 'NotAllowedError' }));
  const h = harness({ media: denied });
  assert.equal(await h.tracker.start(), false);
  assert.match(h.errors[0].message, /not allowed/);
  assert.equal(h.workers.length, 0);
  const insecure = harness({ secure: false });
  assert.equal(await insecure.tracker.start(), false);
  assert.equal(insecure.requests.length, 0);
  assert.match(insecure.errors[0].message, /HTTPS/);
});

test('worker initialization timeout closes the camera and terminates the worker', async () => {
  const h = harness({ autoReady: false });
  const start = h.tracker.start();
  await flush();
  await h.advance(45001);
  assert.equal(await start, false);
  assert.equal(h.track.stopped, 1);
  assert.equal(h.workers[0].terminated, true);
  assert.equal(h.timers.size, 0);
  assert.match(h.errors[0].message, /too long/);
});

test('only one bitmap is in flight, sampling is at most 15fps, and stale replies are ignored', async () => {
  const h = harness();
  assert.equal(await h.tracker.start(), true);
  await h.advance(0);
  const worker = h.workers[0];
  assert.equal(h.bitmaps.length, 1);
  await h.advance(300);
  assert.equal(h.bitmaps.length, 1, 'no queue grows while worker is busy');
  const frame = worker.messages.at(-1).message;
  worker.reply({ type: 'pose', id: frame.id - 1, timestamp: frame.timestamp, landmarks: [] });
  assert.equal(h.poses.length, 0);
  worker.reply({ type: 'pose', id: frame.id, timestamp: frame.timestamp, landmarks: [] });
  h.video.currentTime = 2;
  await h.advance(0);
  assert.equal(h.bitmaps.length, 2);
  const next = worker.messages.at(-1).message;
  worker.reply({ type: 'pose', id: next.id, timestamp: next.timestamp, landmarks: [] });
  h.video.currentTime = 3;
  await h.advance(60);
  assert.equal(h.bitmaps.length, 2, 'fast inference still respects the 15fps ceiling');
  await h.advance(7);
  assert.equal(h.bitmaps.length, 3);
  assert.equal(worker.messages.at(-1).transfers.length, 1);
  h.tracker.stop();
  worker.reply({ type: 'pose', id: h.tracker.sequence, timestamp: 500, landmarks: [] });
  assert.equal(h.poses.length, 2);
});

test('stop during bitmap creation closes the eventual bitmap without posting it', async () => {
  const capture = deferred(), bitmap = { closed: 0, close() { this.closed++; } };
  const h = harness({ bitmap: capture.promise });
  await h.tracker.start();
  await h.advance(0);
  h.tracker.stop();
  capture.resolve(bitmap);
  await flush();
  assert.equal(bitmap.closed, 1);
  assert.equal(h.workers[0].messages.filter(({ message }) => message.type === 'frame').length, 0);
  assert.equal(h.timers.size, 0);
});

test('hung inference and disconnected hardware stop camera resources', async () => {
  const h = harness();
  await h.tracker.start();
  await h.advance(8001);
  assert.equal(h.tracker.running, false);
  assert.equal(h.track.stopped, 1);
  assert.match(h.errors[0].message, /stopped responding/);
  const disconnected = harness();
  await disconnected.tracker.start();
  disconnected.track.onended();
  assert.equal(disconnected.tracker.running, false);
  assert.equal(disconnected.track.stopped, 1);
  assert.match(disconnected.errors[0].message, /disconnected/);
});

test('an abandoned permission result cannot interrupt a newer running session', async () => {
  const oldPermission = deferred();
  let attempts = 0;
  const oldTrack = { stopped: 0, stop() { this.stopped++; } };
  const oldStream = { getTracks: () => [oldTrack] };
  const h = harness({ media: () => ++attempts === 1 ? oldPermission.promise : Promise.resolve(h.stream) });
  const oldStart = h.tracker.start();
  h.tracker.stop();
  assert.equal(await h.tracker.start(), true);
  oldPermission.resolve(oldStream);
  assert.equal(await oldStart, false);
  await flush();
  assert.equal(oldTrack.stopped, 1);
  assert.equal(h.track.stopped, 0);
  assert.equal(h.tracker.running, true);
  assert.equal(h.video.srcObject, h.stream);
  assert.equal(h.errors.length, 0);
  h.tracker.stop();
});

test('late messages and queued errors from a retired worker cannot stop its replacement', async () => {
  const h = harness();
  await h.tracker.start();
  const retired = h.workers[0];
  const retiredEnded = h.track.onended;
  h.tracker.stop();
  await h.tracker.start();
  retired.reply({ type: 'error', message: 'Old failure' });
  retired.onerror({ preventDefault() {} });
  retiredEnded();
  assert.equal(h.tracker.running, true);
  assert.equal(h.errors.length, 0);
  assert.equal(h.workers[1].terminated, undefined);
  h.tracker.stop();
});

test('stopping a stalled preview cancels startup and a late rejection stays harmless', async () => {
  const preview = deferred(), h = harness();
  h.video.play = () => preview.promise;
  const start = h.tracker.start();
  await flush();
  h.tracker.stop();
  assert.equal(await start, false);
  preview.reject(new Error('Late play rejection'));
  await flush();
  assert.equal(h.track.stopped, 1);
  assert.equal(h.workers.length, 0);
  assert.equal(h.errors.length, 0);
  assert.equal(h.timers.size, 0);
});

test('preview timeout and worker initialization errors clean every acquired resource', async () => {
  const h = harness();
  h.video.play = () => new Promise(() => {});
  const start = h.tracker.start();
  await h.advance(10001);
  assert.equal(await start, false);
  assert.match(h.errors[0].message, /preview/);
  assert.equal(h.track.stopped, 1);
  assert.equal(h.workers.length, 0);
  assert.equal(h.timers.size, 0);
  for (const kind of ['error-message', 'crash']) {
    const broken = harness({ autoReady: false });
    const pending = broken.tracker.start();
    await flush();
    if (kind === 'crash') broken.workers[0].onerror({ preventDefault() {} });
    else broken.workers[0].reply({ type: 'error', message: 'Model unavailable' });
    assert.equal(await pending, false);
    assert.equal(broken.track.stopped, 1);
    assert.equal(broken.workers[0].terminated, true);
    assert.equal(broken.errors.length, 1);
    assert.equal(broken.timers.size, 0);
  }
});

test('missing or frozen video frames time out instead of polling indefinitely', async () => {
  for (const missing of [true, false]) {
    const h = harness();
    if (missing) h.video.readyState = 0;
    await h.tracker.start();
    await h.advance(0);
    if (!missing) {
      const frame = h.workers[0].messages.at(-1).message;
      h.workers[0].reply({ type: 'pose', id: frame.id, timestamp: frame.timestamp, landmarks: [] });
    }
    await h.advance(8100);
    assert.equal(h.tracker.running, false);
    assert.equal(h.track.stopped, 1);
    assert.match(h.errors[0].message, /providing frames/);
    assert.equal(h.timers.size, 0);
  }
});

test('temporary video stalls recover without repeatedly capturing the same frame', async () => {
  const h = harness();
  h.video.readyState = 0;
  await h.tracker.start();
  await h.advance(1500);
  assert.equal(h.bitmaps.length, 0);
  h.video.readyState = 2;
  await h.advance(50);
  const frame = h.workers[0].messages.at(-1).message;
  h.workers[0].reply({ type: 'pose', id: frame.id, timestamp: frame.timestamp, landmarks: [] });
  await h.advance(5000);
  assert.equal(h.bitmaps.length, 1);
  h.video.currentTime++;
  await h.advance(50);
  assert.equal(h.bitmaps.length, 2);
  assert.equal(h.tracker.running, true);
  assert.equal(h.errors.length, 0);
  h.tracker.stop();
});

test('bitmap creation failures, transfer failures and hung captures reclaim resources', async () => {
  const capture = deferred(), h = harness({ bitmap: capture.promise });
  await h.tracker.start();
  await h.advance(0);
  capture.reject(new Error('Capture failed'));
  await flush();
  assert.match(h.errors[0].message, /Capture failed/);
  assert.equal(h.track.stopped, 1);
  assert.equal(h.timers.size, 0);

  const failedTransfer = harness();
  await failedTransfer.tracker.start();
  failedTransfer.workers[0].postMessage = () => { throw new Error('Transfer failed'); };
  await failedTransfer.advance(0);
  assert.equal(failedTransfer.bitmaps[0].closed, 1);
  assert.equal(failedTransfer.track.stopped, 1);
  assert.match(failedTransfer.errors[0].message, /Transfer failed/);

  const lateCapture = deferred(), hung = harness({ bitmap: lateCapture.promise });
  await hung.tracker.start();
  await hung.advance(8001);
  assert.equal(hung.tracker.running, false);
  const lateBitmap = { closed: 0, close() { this.closed++; } };
  lateCapture.resolve(lateBitmap);
  await flush();
  assert.equal(lateBitmap.closed, 1);
  assert.equal(hung.workers[0].messages.length, 1, 'no frame is posted after termination');
  assert.equal(hung.timers.size, 0);
});

test('camera failures and missing browser capabilities retain an actionable fallback', async () => {
  for (const [name, message] of [['NotFoundError', /No camera/], ['NotReadableError', /busy/]]) {
    const h = harness({ media: Promise.reject(Object.assign(new Error('Raw failure'), { name })) });
    assert.equal(await h.tracker.start(), false);
    assert.match(h.errors[0].message, message);
    assert.equal(h.workers.length, 0);
  }
  for (const capability of ['Worker', 'createImageBitmap', 'mediaDevices']) {
    const h = harness();
    h.tracker.env[capability] = undefined;
    assert.equal(await h.tracker.start(), false);
    assert.equal(h.requests.length, 0);
    assert.equal(h.errors.length, 1);
    assert.equal(h.timers.size, 0);
  }
});

test('the real worker frame handler closes every bitmap and rejects invalid or unordered timestamps', async () => {
  const messages = [], calls = [];
  const self = { postMessage: (message) => messages.push(message) };
  const context = createContext({ self });
  runInContext(readFileSync(new URL('../web/js/pose-worker.mjs', import.meta.url), 'utf8'), context);
  const send = async (timestamp) => {
    const bitmap = { closed: 0, close() { this.closed++; } };
    await self.onmessage({ data: { type: 'frame', id: 17, bitmap, timestamp } });
    assert.equal(bitmap.closed, 1);
    return messages.at(-1);
  };
  assert.match((await send(1)).message, /not ready/);
  context.fakeDetector = { detectForVideo: (bitmap, timestamp) => { calls.push(timestamp); return { landmarks: [] }; } };
  runInContext('detector = fakeDetector', context);
  const pose = await send(2);
  assert.equal(pose.type, 'pose');
  assert.equal(pose.id, 17);
  assert.equal(pose.timestamp, 2);
  assert.equal(pose.landmarks.length, 0, 'an empty result means no person was found');
  for (const timestamp of [NaN, Infinity, 2, 1]) assert.match((await send(timestamp)).message, /timestamps must increase/);
  assert.deepEqual(calls, [2], 'bad frames never reach inference');
  assert.equal((await send(3)).type, 'pose');
  context.fakeDetector.detectForVideo = () => { throw new Error('Inference failure'); };
  assert.match((await send(4)).message, /Inference failure/);
});
