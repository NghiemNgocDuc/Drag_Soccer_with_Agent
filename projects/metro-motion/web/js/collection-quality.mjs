import { LABELS } from './motion-features.mjs';

// Describes saved evidence, never estimates model accuracy from recording counts.
export function collectionCoverage(clips, participantId, sessionId) {
  const personal = clips.filter(clip => clip.participantId === participantId);
  const current = personal.filter(clip => clip.sessionId === sessionId);
  const counts = Object.fromEntries(LABELS.map(label => [label, current.filter(clip => clip.label === label).length]));
  const sessions = new Map();
  let neutralSeconds = 0;
  for (const clip of personal) {
    if (!sessions.has(clip.sessionId)) sessions.set(clip.sessionId, new Set());
    sessions.get(clip.sessionId).add(clip.label);
    if (clip.label === 'neutral') neutralSeconds += (clip.range[1] - clip.range[0]) / 1000;
  }
  const missing = LABELS.filter(label => !counts[label]);
  const nextLabel = LABELS.reduce((best, label) => counts[label] < counts[best] ? label : best, LABELS[0]);
  return {
    counts, missing, nextLabel,
    completeSessions: [...sessions.values()].filter(labels => LABELS.every(label => labels.has(label))).length,
    sessions: sessions.size, neutralSeconds,
  };
}

export function recordingQuality(clip) {
  const required = [0, 11, 12, 23, 24, 25, 26, 27, 28];
  let minimumVisibility = 1, maxGap = 0;
  clip.frames.forEach((frame, index) => {
    for (const joint of required) {
      const point = frame.landmarks[joint];
      minimumVisibility = Math.min(minimumVisibility, point.visibility, point.presence ?? 1);
    }
    if (index) maxGap = Math.max(maxGap, frame.t - clip.frames[index - 1].t);
  });
  const elapsed = clip.frames.at(-1).t - clip.frames[0].t;
  return { frames: clip.frames.length, minimumVisibility, maxGap,
    samplesPerSecond: elapsed > 0 ? (clip.frames.length - 1) * 1000 / elapsed : 0,
    weakTracking: minimumVisibility < .7 || maxGap > 150 };
}
