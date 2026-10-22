// Bounded requests for turn-based online play. Mutations are never retried here:
// a lost response may still mean the server accepted the command.
export const ONLINE_REQUEST_TIMEOUT_MS = 12000;

export function retryDelay(failures, base = 1500, ceiling = 12000) {
  return Math.min(ceiling, base * 2 ** Math.min(4, Math.max(0, failures)));
}

export function isRetryableOnlineError(error) {
  return error?.name !== 'AbortError' && (!error?.status || [408, 425, 429, 500, 502, 503, 504].includes(error.status));
}

export function parseRoomCode(value, origin = 'http://localhost') {
  const raw = String(value || '').trim();
  if (/^[a-zA-Z0-9]{6,64}$/.test(raw)) return raw;
  try {
    const url = new URL(raw, origin);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return null;
    const room = url.searchParams.get('room') || url.pathname.match(/^\/join\/([a-zA-Z0-9]{6,64})\/?$/)?.[1];
    return /^[a-zA-Z0-9]{6,64}$/.test(room || '') ? room : null;
  } catch (_) { return null; }
}

function abortError() { return new DOMException('Request cancelled.', 'AbortError'); }

function delay(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(abortError()); return; }
    const onAbort = () => { clearTimeout(timer); reject(abortError()); };
    const timer = setTimeout(() => { signal?.removeEventListener('abort', onAbort); resolve(); }, ms);
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

async function attemptJSON(url, options, fetcher, timeoutMs) {
  const controller = new AbortController(), external = options.signal;
  let timer, onAbort;
  const interrupted = new Promise((_, reject) => {
    onAbort = () => { controller.abort(); reject(abortError()); };
    if (external?.aborted) { onAbort(); return; }
    external?.addEventListener('abort', onAbort, { once: true });
    timer = setTimeout(() => {
      const error = new Error('The connection is taking too long. Reconnecting…');
      error.name = 'TimeoutError';
      controller.abort(); reject(error);
    }, timeoutMs);
  });
  try {
    const request = (async () => {
      if (external?.aborted) throw abortError();
      const response = await fetcher(url, { ...options, signal: controller.signal, cache: 'no-store' });
      let data;
      try { data = await response.json(); }
      catch (_) {
        const error = new Error(response.redirected ? 'Please log in before joining a match.' : 'Could not read the match response. Reconnecting…');
        error.status = response.redirected ? 401 : response.ok ? 502 : response.status;
        throw error;
      }
      if (!response.ok || data?.error) {
        const error = new Error(data?.error || 'Could not connect to this match. Try again.');
        error.status = response.status; error.data = data; throw error;
      }
      if (!data || typeof data !== 'object' || Array.isArray(data)) {
        const error = new Error('The match response was incomplete. Reconnecting…');
        error.status = 502; throw error;
      }
      return data;
    })();
    return await Promise.race([interrupted, request]);
  } finally {
    clearTimeout(timer); external?.removeEventListener('abort', onAbort);
  }
}

export async function requestOnlineJSON(url, options = {}, {
  fetcher = globalThis.fetch, timeoutMs = ONLINE_REQUEST_TIMEOUT_MS, retries = 1, retryMs = 400,
} = {}) {
  const safe = ['GET', 'HEAD'].includes(String(options.method || 'GET').toUpperCase());
  const limit = safe ? Math.max(0, Math.min(2, retries)) : 0;
  for (let attempt = 0; ; attempt++) {
    try { return await attemptJSON(url, options, fetcher, timeoutMs); }
    catch (error) {
      if (options.signal?.aborted) throw abortError();
      if (attempt >= limit || !isRetryableOnlineError(error)) throw error;
      await delay(retryMs * 2 ** attempt, options.signal);
    }
  }
}
