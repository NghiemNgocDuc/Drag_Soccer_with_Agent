import test from 'node:test';
import assert from 'node:assert/strict';
import { collectionCoverage, recordingQuality } from '../web/js/collection-quality.mjs';

const clip = (label, sessionId = 's1', participantId = 'p1') => ({ label, sessionId, participantId, range: [500, 2500] });
test('collection guidance balances the current participant and session without counting others as evidence', () => {
  const clips = [clip('neutral'), clip('neutral'), clip('left', 's2'), clip('right', 's1', 'p2')];
  const result = collectionCoverage(clips, 'p1', 's1');
  assert.equal(result.nextLabel, 'left');
  assert.equal(result.sessions, 2);
  assert.equal(result.completeSessions, 0);
  assert.equal(result.neutralSeconds, 4);
  assert.deepEqual(result.missing, ['left', 'right', 'jump', 'duck']);
  assert.equal(collectionCoverage(clips, 'p1', 'new').nextLabel, 'neutral');
});
test('only sessions containing all five labels count as complete and extra clips do not invent sessions', () => {
  const clips = ['neutral', 'left', 'right', 'jump', 'duck'].map(label => clip(label));
  clips.push(clip('neutral'), clip('neutral', 's2'));
  const result = collectionCoverage(clips, 'p1', 's1');
  assert.equal(result.completeSessions, 1);
  assert.equal(result.nextLabel, 'left');
  assert.deepEqual(result.missing, []);
});
test('recording quality measures actual timing and required-joint confidence including weak knees', () => {
  const landmarks = () => Array.from({ length: 33 }, () => ({ visibility: .99, presence: .98 }));
  const frames = [0, 70, 140, 350].map(t => ({ t, landmarks: landmarks() }));
  frames[1].landmarks[26].visibility = .61;
  frames[0].landmarks[2].visibility = .1; // optional facial point does not set the full-body quality floor
  const result = recordingQuality({ frames });
  assert.equal(result.minimumVisibility, .61);
  assert.equal(result.maxGap, 210);
  assert.equal(result.samplesPerSecond, 3000 / 350);
  assert.equal(result.weakTracking, true);
  assert.equal(recordingQuality({ frames: [{ t: 0, landmarks: landmarks() }, { t: 70, landmarks: landmarks() }] }).weakTracking, false);
});
