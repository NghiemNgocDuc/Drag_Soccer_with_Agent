// Shared with training/features.py. These are body coordinates, never images.
export const FEATURE_VERSION = 'mm-body-v1';
export const LABELS = Object.freeze(['neutral', 'left', 'right', 'jump', 'duck']);
export const FEATURE_COUNT = 8;
export const WINDOW_SIZE = 8;
export const SAMPLE_HZ = 15;
export const INPUT_SIZE = FEATURE_COUNT * WINDOW_SIZE;
export const WINDOW_MS = (WINDOW_SIZE - 1) * 1000 / SAMPLE_HZ;
export const MAX_GAP_MS = 250;
export const BASELINE_FIELDS = Object.freeze([
  'shoulderX', 'shoulderY', 'hipX', 'hipY', 'width', 'torso',
  'headY', 'leftFootY', 'rightFootY',
]);
const REQUIRED = [0, 11, 12, 23, 24, 25, 26, 27, 28];
const EPSILON_MS = 1e-7;
const clamp = (value) => Math.max(-6, Math.min(6, value));

export function validBaseline(baseline) {
  return Boolean(baseline && BASELINE_FIELDS.every((key) => Number.isFinite(baseline[key]))
    && baseline.width >= 0.055 && baseline.torso >= 0.075);
}

export function extractFeatures(points, baseline) {
  if (!validBaseline(baseline) || !Array.isArray(points) || points.length !== 33) return null;
  if (points.some((point) => !point || !Number.isFinite(point.x) || !Number.isFinite(point.y)
    || !Number.isFinite(point.visibility)
    || (Object.hasOwn(point, 'presence') && !Number.isFinite(point.presence))
    || (Object.hasOwn(point, 'z') && !Number.isFinite(point.z)))) return null;
  if (REQUIRED.some((index) => {
    const point = points[index];
    return point.visibility < 0.55 || (point.presence ?? 1) < 0.55
      || point.x < 0.015 || point.x > 0.985 || point.y < 0.015 || point.y > 0.985;
  })) return null;
  const shoulderX = (points[11].x + points[12].x) / 2;
  const shoulderY = (points[11].y + points[12].y) / 2;
  const hipX = (points[23].x + points[24].x) / 2;
  const hipY = (points[23].y + points[24].y) / 2;
  const width = Math.abs(points[11].x - points[12].x);
  if (width < 0.055 || hipY - shoulderY < 0.075 || points[27].y - points[0].y < 0.32) return null;
  return [
    -(hipX - baseline.hipX) / baseline.width,
    -(shoulderX - baseline.shoulderX) / baseline.width,
    (baseline.hipY - hipY) / baseline.torso,
    (baseline.headY - points[0].y) / baseline.torso,
    (baseline.leftFootY - points[27].y) / baseline.torso,
    (baseline.rightFootY - points[28].y) / baseline.torso,
    (hipY - shoulderY) / baseline.torso,
    width / baseline.width,
  ].map(clamp);
}

export class TemporalWindow {
  constructor() { this.reset(); }

  reset() { this.samples = []; }

  push(features, timestamp) {
    if (!Array.isArray(features) || features.length !== FEATURE_COUNT
      || features.some((value) => !Number.isFinite(value) || Math.abs(value) > 6)
      || !Number.isFinite(timestamp) || timestamp < 0) {
      this.reset();
      return null;
    }
    const previous = this.samples.at(-1);
    if (previous && timestamp <= previous.t) {
      this.reset();
      return null;
    }
    if (previous && timestamp - previous.t > MAX_GAP_MS) this.reset();
    this.samples.push({ t: timestamp, features: features.slice() });
    const start = timestamp - WINDOW_MS;
    while (this.samples.length > 1 && this.samples[1].t <= start) this.samples.shift();
    // The camera supplies at most 15 fps; a bound also protects synthetic/imported input.
    if (this.samples.length > 1024) this.samples.splice(0, this.samples.length - 1024);
    if (this.samples[0].t - start > EPSILON_MS) return null;
    const flat = [];
    let lower = 0;
    for (let index = 0; index < WINDOW_SIZE; index++) {
      const time = start + index * 1000 / SAMPLE_HZ;
      while (lower + 1 < this.samples.length && this.samples[lower + 1].t < time) lower++;
      const a = this.samples[lower], b = this.samples[Math.min(lower + 1, this.samples.length - 1)];
      const ratio = b.t === a.t ? 0 : Math.max(0, Math.min(1, (time - a.t) / (b.t - a.t)));
      for (let column = 0; column < FEATURE_COUNT; column++) {
        flat.push(a.features[column] + (b.features[column] - a.features[column]) * ratio);
      }
    }
    return flat;
  }
}
