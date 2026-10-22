import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
const source = await readFile(new URL('../../static/js/game/online-transport.js', import.meta.url), 'utf8');
const { requestOnlineJSON, parseRoomCode, retryDelay } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });

test('room parser accepts codes and invitations, rejecting unsafe or malformed links', () => {
  for (const value of ['abc123def0', ' https://soccer.test/play2d?room=abc123def0 ', '/join/abc123def0/', '?room=abc123def0'])
    assert.equal(parseRoomCode(value), 'abc123def0');
  for (const value of ['', 'short', 'javascript:alert(1)?room=abc123def0', 'https://user:pass@host/?room=abc123def0', '/play2d?room=a/b', '/other/abc123def0'])
    assert.equal(parseRoomCode(value), null);
});

test('GET retries a temporary server failure and retains no-store behavior', async () => {
  let calls = 0;
  const data = await requestOnlineJSON('/state', {}, { retryMs: 1, fetcher: async (_, options) => {
    assert.equal(options.cache, 'no-store'); return ++calls === 1 ? response({ error: 'Busy' }, 503) : response({ revision: 2 });
  } });
  assert.equal(calls, 2); assert.equal(data.revision, 2);
});

test('POST is sent once even when the response is lost', async () => {
  let calls = 0;
  await assert.rejects(requestOnlineJSON('/move', { method: 'POST' }, { retries: 2, retryMs: 1, fetcher: async () => {
    calls++; throw new TypeError('Network failed');
  } }), /Network failed/);
  assert.equal(calls, 1);
});

test('timeouts bound hung fetches and hung response bodies', async () => {
  for (const fetcher of [() => new Promise(() => {}), async () => ({ ok: true, json: () => new Promise(() => {}) })]) {
    await assert.rejects(requestOnlineJSON('/state', {}, { fetcher, timeoutMs: 10, retries: 0 }), { name: 'TimeoutError' });
  }
});

test('external cancellation interrupts a hung request without retry', async () => {
  const controller = new AbortController(); let calls = 0;
  const pending = requestOnlineJSON('/state', { signal: controller.signal }, { fetcher: () => { calls++; return new Promise(() => {}); } });
  controller.abort();
  await assert.rejects(pending, { name: 'AbortError' }); assert.equal(calls, 1);
});

test('external cancellation stops the GET retry delay', async () => {
  const controller = new AbortController(); let calls = 0;
  const pending = requestOnlineJSON('/state', { signal: controller.signal }, { retryMs: 1000, fetcher: async () => {
    calls++; setTimeout(() => controller.abort(), 5); return response({ error: 'Busy' }, 503);
  } });
  await assert.rejects(pending, { name: 'AbortError' }); assert.equal(calls, 1);
});

test('authorization failures are terminal and preserve structured room errors', async () => {
  let calls = 0;
  await assert.rejects(requestOnlineJSON('/join', {}, { fetcher: async () => { calls++; return response({ error: 'Password required', room_id: 'abc123def0' }, 403); } }), error => {
    assert.equal(error.status, 403); assert.equal(error.data.room_id, 'abc123def0'); return true;
  });
  assert.equal(calls, 1);
});

test('redirected login pages are terminal while malformed successful responses are retryable', async () => {
  let calls = 0;
  await assert.rejects(requestOnlineJSON('/state', {}, { fetcher: async () => { calls++; return { ok: true, redirected: true, json: async () => { throw new SyntaxError(); } }; } }), { status: 401 });
  assert.equal(calls, 1);
  await assert.rejects(requestOnlineJSON('/state', {}, { retryMs: 1, fetcher: async () => response(null) }), { status: 502 });
});

test('reconnect delay grows and remains bounded', () => {
  assert.deepEqual([0, 1, 2, 3, 10].map(count => retryDelay(count)), [1500, 3000, 6000, 12000, 12000]);
});
