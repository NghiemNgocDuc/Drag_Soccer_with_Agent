// Keep playback tied to simulation time even when the server samples a path
// unevenly. Older saved replays retain their original 24 ms/sample cadence.
export function isBouncePoint(point) {
  return point?.bounce === true || point?.b === true || point?.contact === true;
}

function hasSimulationTimes(trajectory) {
  return trajectory.length > 1 && trajectory.at(-1)?.t > trajectory[0]?.t &&
    trajectory.every((point, index) => Number.isFinite(point.t) &&
      (index === 0 || point.t >= trajectory[index - 1].t));
}

export function createTrajectoryPlayback(trajectory, speed = 1, maxDuration = 2400) {
  const lastIndex = trajectory.length - 1;
  const rate = Number.isFinite(speed) && speed > 0 ? speed : 1;
  const firstTime = trajectory[0]?.t;
  const lastTime = trajectory[lastIndex]?.t;
  const timed = hasSimulationTimes(trajectory);
  const duration = timed
    ? Math.min(maxDuration, Math.max(180, (lastTime - firstTime) * 1000)) / rate
    : Math.max(8, 24 / rate) * Math.max(0, lastIndex);
  let nextBounce = 0;

  return {
    duration,
    sample(elapsed) {
      if (lastIndex <= 0) return { progress: 0, segIdx: 0, segFrac: 0, done: true };
      const fraction = duration > 0 ? Math.max(0, Math.min(1, elapsed / duration)) : 1;
      let progress = fraction * Math.max(0, lastIndex);
      if (timed && fraction < 1) {
        const time = firstTime + fraction * (lastTime - firstTime);
        let low = 0, high = lastIndex;
        while (low + 1 < high) {
          const mid = (low + high) >> 1;
          if (trajectory[mid].t <= time) low = mid;
          else high = mid;
        }
        const span = trajectory[high].t - trajectory[low].t;
        progress = low + (span > 0 ? (time - trajectory[low].t) / span : 0);
      }
      const segIdx = Math.max(0, Math.min(Math.floor(progress), lastIndex - 1));
      return { progress, segIdx, segFrac: progress - segIdx, done: fraction >= 1 };
    },
    takeBounces(progress) {
      const bounces = [];
      // Visit every crossed sample, including samples skipped at low FPS.
      while (nextBounce <= Math.floor(progress) && nextBounce <= lastIndex) {
        const point = trajectory[nextBounce++];
        if (isBouncePoint(point)) bounces.push(point);
      }
      return bounces;
    },
  };
}

// Time-aware, shape-preserving Hermite interpolation. Slopes use neighbouring
// sample times, not sample indices; sign changes stop the tangent so a curve
// cannot overshoot a stop, contact or reversal. No extra network data is needed.
// Formula: https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.PchipInterpolator.html
export function createTrajectorySampler(trajectory) {
  const timed = hasSimulationTimes(trajectory);
  const times = trajectory.map((point, i) => timed ? point.t : i * 0.024);

  function buildTrack(points, ball = false) {
    if (!points.length || !points.every(p => Number.isFinite(p?.x) && Number.isFinite(p?.y))) return null;
    const axes = ball ? ['x', 'y', 'z'] : ['x', 'y'];
    const values = axes.map(axis => points.map(p => p[axis] ?? 0));
    const slopes = values.map(v => v.map((_, i) => {
      const left = i > 0 ? times[i] - times[i - 1] : 0;
      const right = i + 1 < v.length ? times[i + 1] - times[i] : 0;
      const a = left > 0 ? (v[i] - v[i - 1]) / left : 0;
      const b = right > 0 ? (v[i + 1] - v[i]) / right : 0;
      if (i === 0) return b;
      if (i === v.length - 1) return a;
      if (!(left > 0 && right > 0) || a * b <= 0) return 0;
      const w1 = 2 * right + left, w2 = right + 2 * left;
      return (w1 + w2) / (w1 / a + w2 / b);
    }));
    const contact = points.map((p, i) => ball && (isBouncePoint(p) || p.contact === true ||
      ((p.z ?? 0) === 0 && ((points[i - 1]?.z ?? 0) > 0 || (points[i + 1]?.z ?? 0) > 0))));
    return { axes, values, slopes, contact };
  }

  const ball = buildTrack(trajectory, true);
  const players = {};
  for (const team of ['a', 'b']) {
    const count = Array.isArray(trajectory[0]?.[team]) ? trajectory[0][team].length : 0;
    players[team] = Array.from({ length: count }, (_, i) => buildTrack(trajectory.map(p => p[team]?.[i])));
  }

  function sample(track, point, out) {
    if (!track) return null;
    const i = Math.min(Math.max(0, point.segIdx), times.length - 1);
    const j = Math.min(i + 1, times.length - 1);
    const t = Math.max(0, Math.min(1, point.segFrac));
    const dt = times[j] - times[i];
    const linear = times.length < 3 || dt <= 0 || track.contact[i] || track.contact[j];
    const t2 = t * t, t3 = t2 * t;
    track.axes.forEach((axis, k) => {
      const a = track.values[k][i], b = track.values[k][j];
      const value = linear ? a + (b - a) * t :
        (2 * t3 - 3 * t2 + 1) * a + (t3 - 2 * t2 + t) * dt * track.slopes[k][i] +
        (-2 * t3 + 3 * t2) * b + (t3 - t2) * dt * track.slopes[k][j];
      out[axis] = Math.max(Math.min(a, b), Math.min(Math.max(a, b), value));
    });
    if ('z' in out) out.z = Math.max(0, out.z);
    return out;
  }

  return {
    hasPlayer: (team, index) => !!players[team]?.[index],
    ball: (point, out = {}) => sample(ball, point, out),
    player: (team, index, point, out = {}) => sample(players[team]?.[index], point, out),
  };
}
