// Landmark-only controls. Images never enter this module or persistent storage.
const REQUIRED = [0, 11, 12, 23, 24, 25, 26, 27, 28];
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const mean = (values) => values.reduce((sum, value) => sum + value, 0) / values.length;

function features(points) {
  if (!Array.isArray(points) || points.length < 29) return null;
  if (REQUIRED.some((i) => {
    const p = points[i];
    return !p || !Number.isFinite(p.x) || !Number.isFinite(p.y)
      || !Number.isFinite(p.visibility) || p.visibility < 0.55
      || !Number.isFinite(p.presence ?? 1) || (p.presence ?? 1) < 0.55
      || p.x < 0.015 || p.x > 0.985 || p.y < 0.015 || p.y > 0.985;
  })) return null;
  const shoulderX = (points[11].x + points[12].x) / 2;
  const shoulderY = (points[11].y + points[12].y) / 2;
  const hipX = (points[23].x + points[24].x) / 2;
  const hipY = (points[23].y + points[24].y) / 2;
  const width = Math.abs(points[11].x - points[12].x);
  const torso = hipY - shoulderY;
  if (width < 0.055 || torso < 0.075 || points[27].y - points[0].y < 0.32) return null;
  return { shoulderX, shoulderY, hipX, hipY, width, torso,
    headY: points[0].y, leftFootY: points[27].y, rightFootY: points[28].y };
}

export class PoseController {
  constructor({ mirrored = true, calibrationMs = 2000, staleMs = 450 } = {}) {
    this.mirrored = mirrored;
    this.calibrationMs = calibrationMs;
    this.staleMs = staleMs;
    this.calibrate();
  }

  calibrate() {
    this.baseline = null;
    this.samples = [];
    this.filtered = null;
    this.lastValid = -Infinity;
    this.lastUpdate = -Infinity;
    this.lane = 0;
    this.pendingLane = 0;
    this.laneSince = 0;
    this.duck = false;
    this.duckSince = null;
    this.jumpArmed = true;
    this.lastJump = -Infinity;
    this.groundedSince = null;
  }

  _result(tracked, progress = 0, jump = false, message = '') {
    return { tracked, calibrated: Boolean(this.baseline), progress,
      lane: this.lane, jump, duck: tracked && this.duck, message };
  }

  update(landmarks, timestamp) {
    if (!Number.isFinite(timestamp) || timestamp < this.lastValid
      || this.lastUpdate - timestamp > this.staleMs) {
      return this._result(false, this.baseline ? 1 : 0, false, 'Waiting for a fresh camera frame.');
    }
    // A watchdog may tick with the current clock while an older captured frame is
    // still being inferred. Compare frame order to the last valid frame, not that tick.
    this.lastUpdate = Math.max(this.lastUpdate, timestamp);
    const pose = features(landmarks);
    if (!pose) {
      if (!this.baseline) this.samples = [];
      if (timestamp - this.lastValid > this.staleMs) {
        this.filtered = null;
        this.duck = false;
        this.duckSince = null;
        this.jumpArmed = false;
        this.groundedSince = null;
        return this._result(false, this.baseline ? 1 : 0, false, 'Step back until your head and both feet are visible.');
      }
      return this._result(true, this.baseline ? 1 : 0, false, 'Keep your whole body in view.');
    }

    const elapsed = timestamp - this.lastValid;
    this.lastValid = timestamp;
    if (!this.baseline) {
      const first = this.samples[0];
      // Movement restarts the standing baseline instead of averaging a step or squat into it.
      if (first && (elapsed > 250 || Math.abs(pose.hipX - first.hipX) > first.width * 0.22
        || Math.abs(pose.hipY - first.hipY) > first.torso * 0.15
        || Math.abs(pose.headY - first.headY) > first.torso * 0.20
        || Math.abs(pose.leftFootY - first.leftFootY) > first.torso * 0.15
        || Math.abs(pose.rightFootY - first.rightFootY) > first.torso * 0.15)) this.samples = [];
      this.samples.push({ ...pose, timestamp });
      // Fixed-duration sampling keeps memory bounded even with unusually fast synthetic input.
      if (this.samples.length > 240) this.samples.splice(1, this.samples.length - 240);
      const progress = clamp((timestamp - this.samples[0].timestamp) / this.calibrationMs, 0, 1);
      if (progress < 1 || this.samples.length < 15) {
        return this._result(true, progress, false, 'Stand upright in the middle and hold still.');
      }
      this.baseline = Object.fromEntries(Object.keys(pose).map((key) => [key, mean(this.samples.map((s) => s[key]))]));
      this.samples = [];
      this.filtered = { ...pose };
      this.jumpArmed = true;
      return this._result(true, 1, false, 'Ready. Step sideways, jump, or duck.');
    }

    if (!this.filtered || elapsed > this.staleMs) {
      this.filtered = { ...pose };
      this.jumpArmed = false;
      this.groundedSince = null;
    } else {
      const alpha = 1 - Math.exp(-clamp(elapsed, 0, 200) / 65);
      for (const key of Object.keys(pose)) this.filtered[key] += (pose[key] - this.filtered[key]) * alpha;
    }
    const p = this.filtered, b = this.baseline;
    const horizontal = (p.hipX - b.hipX) / b.width * (this.mirrored ? -1 : 1);
    let desiredLane = this.lane;
    if (horizontal > 0.58) desiredLane = 1;
    else if (horizontal < -0.58) desiredLane = -1;
    else if (Math.abs(horizontal) < 0.32) desiredLane = 0;
    if (desiredLane !== this.pendingLane) {
      this.pendingLane = desiredLane;
      this.laneSince = timestamp;
    } else if (timestamp - this.laneSince >= 90) this.lane = desiredLane;

    const hipUp = (b.hipY - p.hipY) / b.torso;
    const headUp = (b.headY - p.headY) / b.torso;
    const feetUp = Math.min(b.leftFootY - p.leftFootY, b.rightFootY - p.rightFootY) / b.torso;
    const grounded = hipUp < 0.07 && feetUp < 0.055;
    if (grounded) {
      this.groundedSince ??= timestamp;
      if (timestamp - this.groundedSince >= 120 && timestamp - this.lastJump >= 650) this.jumpArmed = true;
    } else this.groundedSince = null;
    const jump = this.jumpArmed && !this.duck && hipUp > 0.17 && headUp > 0.13 && feetUp > 0.10
      && timestamp - this.lastJump >= 650;
    if (jump) {
      this.jumpArmed = false;
      this.lastJump = timestamp;
    }

    const wantsDuck = this.duck ? headUp < -0.16 && hipUp < -0.065 : headUp < -0.33 && hipUp < -0.14;
    if (wantsDuck && !jump) {
      this.duckSince ??= timestamp;
      if (timestamp - this.duckSince >= 80) this.duck = true;
    } else {
      this.duckSince = null;
      this.duck = false;
    }
    return this._result(true, 1, jump, this.duck ? 'Duck' : jump ? 'Jump' : this.lane < 0 ? 'Left lane' : this.lane > 0 ? 'Right lane' : 'Center lane');
  }
}
