// Explicit, bounded landmark recordings. Camera images never enter this module.
import { extractFeatures, LABELS, FEATURE_VERSION } from './motion-features.mjs';

export const LIMITS = Object.freeze({ clips: 200, frames: 90, countdownMs: 3000, recordingMs: 3000, contextMs: 1000 * 7 / 15, rangeMs: 150, gapMs: 250, staleMs: 450, timeoutMs: 9000 });
const BASELINE_KEYS = ['shoulderX', 'shoulderY', 'hipX', 'hipY', 'width', 'torso', 'headY', 'leftFootY', 'rightFootY'];
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const finite = (n, lo = -10, hi = 10) => Number.isFinite(n) && n >= lo && n <= hi;
export const localId = () => globalThis.crypto?.randomUUID?.() ?? `local-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
const idValid = (id) => typeof id === 'string' && /^[a-zA-Z0-9_-]{1,80}$/.test(id);

function cleanBaseline(value) {
  assert(value && BASELINE_KEYS.every((key) => finite(value[key])), 'Standing calibration is missing or invalid.');
  assert(value.width >= .055 && value.torso >= .075, 'Standing calibration is too small. Recalibrate with your whole body visible.');
  return Object.fromEntries(BASELINE_KEYS.map((key) => [key, value[key]]));
}

function cleanPoints(points) {
  assert(Array.isArray(points) && points.length === 33, 'Each frame must contain 33 body landmarks.');
  return points.map((p) => {
    assert(p && finite(p.x) && finite(p.y) && finite(p.visibility, 0, 1), 'A body landmark is invalid. Record the clip again.');
    const result = { x: p.x, y: p.y, visibility: p.visibility };
    for (const key of ['z', 'presence']) if (p[key] !== undefined) {
      assert(key === 'presence' ? finite(p[key], 0, 1) : finite(p[key]), 'A body landmark is invalid.');
      result[key] = p[key];
    }
    return result;
  });
}

export function validateClip(clip) {
  assert(clip && idValid(clip.id) && idValid(clip.sessionId) && idValid(clip.participantId), 'Clip identifiers are missing or invalid.');
  assert(LABELS.includes(clip.label), 'Choose a supported movement label.');
  const baseline = cleanBaseline(clip.baseline);
  assert(Array.isArray(clip.frames) && clip.frames.length >= 9 && clip.frames.length <= LIMITS.frames, 'A clip needs 9 to 90 complete frames.');
  let previous = -Infinity;
  const frames = clip.frames.map((frame) => {
    assert(frame && finite(frame.t, 0, 6000) && frame.t > previous, 'Frame times must be finite and strictly increasing.');
    assert(previous === -Infinity || frame.t - previous <= LIMITS.gapMs, 'Tracking has a gap. Record the clip again.');
    previous = frame.t;
    const landmarks = cleanPoints(frame.landmarks);
    assert(extractFeatures(landmarks, baseline), 'A clip contains incomplete body tracking.');
    return { t: frame.t, landmarks };
  });
  const range = clip.range;
  assert(Array.isArray(range) && range.length === 2 && range.every((t) => finite(t, 0, 6000)), 'Choose the labeled part of the clip.');
  assert(range[0] + 1e-6 >= frames[0].t + LIMITS.contextMs && range[1] <= frames.at(-1).t && range[1] - range[0] >= LIMITS.rangeMs, 'Choose at least 0.15 seconds after the first 0.47 seconds of context.');
  assert(frames.some((frame) => frame.t >= range[0] && frame.t <= range[1]), 'The selected range contains no captured frame.');
  return { id: clip.id, sessionId: clip.sessionId, participantId: clip.participantId, label: clip.label, range: [...range], baseline, frames };
}

export function validateDataset(data) {
  assert(data?.format === 'metro-motion-motion-data' && data.version === 1 && data.featureVersion === FEATURE_VERSION && data.source === 'human', 'This is not a compatible human Motion Lab dataset.');
  assert(Array.isArray(data.clips) && data.clips.length <= LIMITS.clips, 'A dataset may contain at most 200 clips. Download and delete old clips to make room.');
  const ids = new Set(), sessions = new Map();
  const clips = data.clips.map((clip) => {
    const clean = validateClip(clip);
    assert(!ids.has(clean.id), 'The dataset contains duplicate clip IDs.');
    ids.add(clean.id);
    const identity = JSON.stringify([clean.participantId, clean.baseline]);
    assert(!sessions.has(clean.sessionId) || sessions.get(clean.sessionId) === identity, 'A session cannot mix participants or standing calibrations.');
    sessions.set(clean.sessionId, identity);
    return clean;
  });
  return { format: 'metro-motion-motion-data', version: 1, featureVersion: FEATURE_VERSION, source: 'human', clips };
}

export const makeDataset = (clips) => validateDataset({ format: 'metro-motion-motion-data', version: 1, featureVersion: FEATURE_VERSION, source: 'human', clips });

export class MotionRecorder {
  constructor() { this.phase = 'idle'; this.draft = null; this.reason = ''; }
  start({ label, baseline, participantId, sessionId }, now) {
    assert(this.phase === 'idle', 'Review or discard the current clip first.');
    assert(LABELS.includes(label) && finite(now, 0, Number.MAX_SAFE_INTEGER), 'Invalid movement or recording time.');
    assert(idValid(participantId) && idValid(sessionId), 'Start a new recording session.');
    this.draft = { id: localId(), participantId, sessionId, label, baseline: cleanBaseline(baseline), frames: [], range: null };
    this.startedAt = now; this.captureAt = now + LIMITS.countdownMs; this.lastFrameAt = now;
    this.lastCaptureAt = null; this.firstFrameAt = null;
    this.phase = 'countdown'; this.reason = '';
  }
  abort(reason = 'Recording cancelled. Nothing was saved.') {
    this.phase = 'idle'; this.draft = null; this.reason = reason;
  }
  frame(points, timestamp, now = timestamp) {
    if (!['countdown', 'recording'].includes(this.phase)) return;
    if (!finite(timestamp, 0, Number.MAX_SAFE_INTEGER) || timestamp > now + 5 || now - timestamp > LIMITS.staleMs || timestamp <= (this.lastCaptureAt ?? -Infinity)) return this.abort('The camera frame was stale. Try recording again.');
    let landmarks;
    try { landmarks = cleanPoints(points); assert(extractFeatures(landmarks, this.draft.baseline), 'Keep your whole body visible.'); }
    catch { return this.abort('Tracking was lost. Keep your head and feet in view, then record again.'); }
    if (timestamp - this.lastFrameAt > LIMITS.gapMs) return this.abort('The camera paused during capture. Try recording again.');
    this.lastFrameAt = timestamp; this.lastCaptureAt = timestamp;
    if (timestamp < this.captureAt) return;
    this.phase = 'recording';
    const frames = this.draft.frames;
    this.firstFrameAt ??= timestamp;
    const t = timestamp - this.firstFrameAt;
    if (t > LIMITS.recordingMs) { this.finish(); return; }
    if (frames.length >= LIMITS.frames) { this.abort('The recording exceeded its frame limit. Try again.'); return; }
    frames.push({ t, landmarks });
  }
  tick(now) {
    if (!['countdown', 'recording'].includes(this.phase)) return;
    if (now - this.lastFrameAt > LIMITS.staleMs) { this.abort('Tracking stopped during capture. Record again when your whole body is visible.'); return; }
    if (now - this.startedAt > LIMITS.timeoutMs) { this.abort('Recording timed out. Try again.'); return; }
    if (this.phase === 'recording' && now >= this.firstFrameAt + LIMITS.recordingMs) this.finish();
  }
  finish() {
    const frames = this.draft?.frames;
    if (!frames || frames.length < 9 || frames.at(-1).t < 2500) { this.abort('Too few frames were captured. Try again with steady tracking.'); return; }
    this.draft.range = [500, frames.at(-1).t];
    try { this.draft = validateClip(this.draft); this.phase = 'review'; }
    catch (error) { this.abort(error.message); }
  }
  take(range) {
    assert(this.phase === 'review', 'There is no clip to save.');
    const clip = validateClip({ ...this.draft, range });
    this.reset(); return clip;
  }
  reset() { this.abort(''); this.lastCaptureAt = null; this.firstFrameAt = null; }
}

// A single bounded record is committed atomically; write failures never discard memory.
export class LocalClipStore {
  constructor(indexedDB = globalThis.indexedDB) { this.indexedDB = indexedDB; this.db = null; this.queue = Promise.resolve(); }
  async open() {
    if (!this.indexedDB) throw new Error('Browser storage is unavailable. Clips will stay in this tab; download them before leaving.');
    this.db = await new Promise((resolve, reject) => {
      const request = this.indexedDB.open('metro-motion-lab-v1', 1);
      const timer = setTimeout(() => reject(new Error('Browser storage took too long. Download clips before leaving.')), 8000);
      request.onupgradeneeded = () => request.result.createObjectStore('library');
      request.onsuccess = () => { clearTimeout(timer); resolve(request.result); };
      request.onerror = () => { clearTimeout(timer); reject(new Error('Browser storage could not open. Download clips before leaving.')); };
      request.onblocked = () => { clearTimeout(timer); reject(new Error('Browser storage is blocked by another tab. Close it and reload, or download your clips.')); };
    });
    this.db.onversionchange = () => { this.db.close(); this.db = null; };
    return new Promise((resolve, reject) => {
      const tx = this.db.transaction('library', 'readonly'), request = tx.objectStore('library').get('current');
      request.onsuccess = () => {
        try {
          if (!request.result) { resolve(null); return; }
          const data = request.result;
          assert(idValid(data.participantId), 'The saved participant identifier is invalid.');
          resolve({ participantId: data.participantId, clips: makeDataset(data.clips).clips });
        } catch (error) { reject(error); }
      };
      request.onerror = () => reject(new Error('Saved clips could not be read. New clips remain in this tab until downloaded.'));
    });
  }
  save({ participantId, clips }) {
    const snapshot = { participantId, clips: makeDataset(clips).clips };
    const task = this.queue.catch(() => {}).then(() => new Promise((resolve, reject) => {
      if (!this.db) { reject(new Error('Clips are kept in this tab only. Download them before leaving.')); return; }
      let tx;
      try { tx = this.db.transaction('library', 'readwrite'); tx.objectStore('library').put(snapshot, 'current'); }
      catch { reject(new Error('Could not save to browser storage. Your clips are still in this tab; download them.')); return; }
      tx.oncomplete = () => resolve();
      tx.onerror = tx.onabort = () => reject(new Error('Browser storage is full or unavailable. Your clips are still in this tab; download them before leaving.'));
    }));
    this.queue = task; return task;
  }
}
