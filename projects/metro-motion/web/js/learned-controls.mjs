import {
  BASELINE_FIELDS, FEATURE_VERSION, INPUT_SIZE, LABELS, MAX_GAP_MS, SAMPLE_HZ, WINDOW_SIZE,
  TemporalWindow, extractFeatures,
} from './motion-features.mjs';

export const MAX_MODEL_BYTES = 2 * 1024 * 1024;
const validatedModels = new WeakSet();
const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const requireValue = (condition, message) => { if (!condition) throw new Error(`Invalid motion model: ${message}`); };

function vector(value, length, name, minimum = -Infinity) {
  requireValue(Array.isArray(value) && value.length === length, `${name} must have ${length} values.`);
  requireValue(value.every((entry) => Number.isFinite(entry) && entry >= minimum), `${name} contains invalid numbers.`);
  return Object.freeze(value.slice());
}

function matrix(value, rows, columns, name) {
  requireValue(Array.isArray(value) && value.length === rows, `${name} must have ${rows} rows.`);
  return Object.freeze(value.map((row, index) => vector(row, columns, `${name}[${index}]`)));
}

export function validateModel(data) {
  let text;
  try { text = typeof data === 'string' ? data : JSON.stringify(data); }
  catch { throw new Error('Invalid motion model: expected JSON data.'); }
  requireValue(typeof text === 'string' && new TextEncoder().encode(text).length <= MAX_MODEL_BYTES,
    'file must be at most 2 MB.');
  let value;
  try { value = JSON.parse(text); }
  catch { throw new Error('Invalid motion model: file is not valid JSON.'); }
  requireValue(isObject(value), 'expected an object.');
  requireValue(value.format === 'metro-motion-motion-model' && value.version === 1, 'unsupported format or version.');
  requireValue(value.featureVersion === FEATURE_VERSION, 'incompatible body features.');
  requireValue(Array.isArray(value.labels) && value.labels.length === LABELS.length
    && LABELS.every((label, index) => value.labels[index] === label), 'labels must match the five supported actions.');
  requireValue(value.inputSize === INPUT_SIZE && value.windowSize === WINDOW_SIZE && value.sampleHz === SAMPLE_HZ,
    'incompatible temporal window.');
  requireValue(Number.isInteger(value.hiddenSize) && value.hiddenSize >= 1 && value.hiddenSize <= 128,
    'hiddenSize must be between 1 and 128.');
  requireValue(Number.isFinite(value.threshold) && value.threshold >= 0.5 && value.threshold <= 0.99,
    'confidence threshold must be between 0.5 and 0.99.');
  const temperature = Object.hasOwn(value, 'temperature') ? value.temperature : 1;
  requireValue(Number.isFinite(temperature) && temperature >= 0.25 && temperature <= 5,
    'temperature must be between 0.25 and 5.');
  requireValue(isObject(value.metrics) && typeof value.metrics.controlEligible === 'boolean',
    'metrics must explicitly declare controlEligible.');
  requireValue(!value.metrics.controlEligible || value.metrics.synthetic !== true,
    'synthetic models cannot enable experimental control.');
  const model = Object.freeze({
    format: value.format, version: value.version, featureVersion: FEATURE_VERSION,
    labels: LABELS, inputSize: INPUT_SIZE, windowSize: WINDOW_SIZE, sampleHz: SAMPLE_HZ,
    hiddenSize: value.hiddenSize, threshold: value.threshold, temperature,
    mean: vector(value.mean, INPUT_SIZE, 'mean'),
    scale: vector(value.scale, INPUT_SIZE, 'scale', 1e-6),
    weights1: matrix(value.weights1, value.hiddenSize, INPUT_SIZE, 'weights1'),
    bias1: vector(value.bias1, value.hiddenSize, 'bias1'),
    weights2: matrix(value.weights2, LABELS.length, value.hiddenSize, 'weights2'),
    bias2: vector(value.bias2, LABELS.length, 'bias2'),
    // Only this frozen top-level eligibility flag is used to permit control.
    metrics: Object.freeze({ ...value.metrics }),
  });
  validatedModels.add(model);
  return model;
}

export function inferModel(model, flat) {
  requireValue(validatedModels.has(model), 'validate the model before inference.');
  requireValue(Array.isArray(flat) && flat.length === INPUT_SIZE && flat.every(Number.isFinite),
    'inference requires 64 finite inputs.');
  const input = flat.map((value, index) => (value - model.mean[index]) / model.scale[index]);
  const hidden = model.weights1.map((row, index) => Math.max(0,
    row.reduce((sum, weight, column) => sum + weight * input[column], model.bias1[index])));
  const logits = model.weights2.map((row, index) =>
    row.reduce((sum, weight, column) => sum + weight * hidden[column], model.bias2[index]));
  requireValue(input.every(Number.isFinite) && hidden.every(Number.isFinite) && logits.every(Number.isFinite),
    'inference overflowed.');
  const maximum = Math.max(...logits);
  // Subtract before dividing to keep calibrated softmax stable for large logits.
  const exp = logits.map((value) => Math.exp((value - maximum) / model.temperature));
  const total = exp.reduce((sum, value) => sum + value, 0);
  const probabilities = exp.map((value) => value / total);
  const index = logits.indexOf(maximum);
  return { label: LABELS[index], confidence: probabilities[index], probabilities };
}

export class LearnedController {
  constructor() {
    this.model = null;
    this.mode = 'compare';
    this.window = new TemporalWindow();
    this.reset();
  }

  load(data) {
    const model = validateModel(data);
    this.model = model;
    this.mode = 'compare';
    this.reset();
    return model;
  }

  clear() { this.model = null; this.mode = 'compare'; this.reset(); }

  setMode(mode) {
    if (mode !== 'compare' && mode !== 'control') throw new Error('Choose compare or control mode.');
    if (mode === 'control' && !this.model?.metrics.controlEligible) {
      throw new Error('This model has not passed the held-out evaluation gate. Use comparison mode.');
    }
    this.mode = mode;
    this.reset();
  }

  reset() {
    this.window.reset();
    this.baselineKey = null;
    this.lastTime = -Infinity;
    this.lastJump = -Infinity;
    this._clearGestures();
  }

  _clearGestures() {
    this.jumpArmed = false;
    this.groundedSince = null;
    this._clearHolds();
  }

  _clearHolds() {
    this.duckSince = null;
    this.pendingLane = null;
    this.laneSince = null;
    this.lane = null;
  }

  _fallback(rulePose, reason, prediction = null, timestamp = null) {
    // A confidence handoff does not mean tracking was lost. Keep physical landing
    // evidence so an ambiguous takeoff frame cannot erase a ready learned jump.
    this._clearHolds();
    let jump = rulePose.jump;
    if (this.mode === 'control' && jump && Number.isFinite(timestamp)) {
      jump = timestamp - this.lastJump >= 650;
      if (jump) {
        this.lastJump = timestamp;
        this.jumpArmed = false;
        this.groundedSince = null;
      }
    }
    return { ...rulePose, jump, prediction, source: 'rules', reason };
  }

  _observeGrounding(features, timestamp, prediction = null) {
    const grounded = features[2] < 0.07 && Math.max(features[4], features[5]) < 0.055;
    if (grounded && prediction?.label !== 'jump') {
      this.groundedSince ??= timestamp;
      if (timestamp - this.groundedSince >= 120 && timestamp - this.lastJump >= 650) this.jumpArmed = true;
    } else this.groundedSince = null;
    return grounded;
  }

  update(points, timestamp, baseline, rulePose) {
    const rules = { tracked: false, calibrated: false, progress: 0, lane: 0, jump: false, duck: false, ...rulePose };
    if (!this.model) return this._fallback(rules, 'no-model', null, timestamp);
    const features = extractFeatures(points, baseline);
    if (!rules.tracked || !rules.calibrated || !features || !Number.isFinite(timestamp)
      || timestamp < 0 || timestamp <= this.lastTime) {
      this.window.reset();
      this._clearGestures();
      return this._fallback(rules.tracked && rules.calibrated ? rules
        : { ...rules, jump: false, duck: false }, 'invalid-tracking', null, timestamp);
    }
    const baselineKey = BASELINE_FIELDS.map((key) => baseline[key]).join(',');
    if (baselineKey !== this.baselineKey || timestamp - this.lastTime > MAX_GAP_MS) {
      this.window.reset();
      this._clearGestures();
      this.baselineKey = baselineKey;
    }
    this.lastTime = timestamp;
    const flat = this.window.push(features, timestamp);
    if (!flat) {
      this._observeGrounding(features, timestamp);
      return this._fallback(rules, 'warming-up', null, timestamp);
    }
    let prediction;
    try { prediction = inferModel(this.model, flat); }
    catch {
      this._observeGrounding(features, timestamp);
      return this._fallback(rules, 'prediction-failed', null, timestamp);
    }
    const grounded = this._observeGrounding(features, timestamp, prediction);
    if (this.mode !== 'control') return this._fallback(rules, 'comparison', prediction, timestamp);
    if (prediction.confidence < this.model.threshold) return this._fallback(rules, 'low-confidence', prediction, timestamp);

    const { label } = prediction;
    // A confident label alone cannot trigger a jump while physically standing.
    const jump = label === 'jump' && !grounded && this.jumpArmed && timestamp - this.lastJump >= 650;
    if (jump) { this.jumpArmed = false; this.lastJump = timestamp; }
    if (label === 'duck') this.duckSince ??= timestamp;
    else this.duckSince = null;
    const duck = this.duckSince !== null && timestamp - this.duckSince >= 80;
    // v1 records one action at a time. Preserve the rule-based lateral position
    // during jump/duck so a jump in a side lane never teleports to the center.
    let lane = rules.lane;
    if (label === 'neutral' || label === 'left' || label === 'right') {
      const desired = label === 'left' ? -1 : label === 'right' ? 1 : 0;
      this.lane ??= rules.lane;
      if (desired !== this.pendingLane) { this.pendingLane = desired; this.laneSince = timestamp; }
      else if (timestamp - this.laneSince >= 90) this.lane = desired;
      lane = this.lane;
    } else {
      this.pendingLane = null;
      this.lane = lane;
    }
    return { ...rules, lane, jump, duck, prediction, source: 'learned', reason: 'confident' };
  }
}
