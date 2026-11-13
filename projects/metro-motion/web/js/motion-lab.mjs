import { CameraTracker } from './camera.mjs';
import { PoseController } from './pose-controls.mjs';
import { extractFeatures, LABELS } from './motion-features.mjs';
import { collectionCoverage, recordingQuality } from './collection-quality.mjs';
import { MotionRecorder, LocalClipStore, LIMITS, localId, makeDataset, validateClip } from './motion-recorder.mjs';

const $ = (id) => document.getElementById(id);
const recorder = new MotionRecorder(), pose = new PoseController(), storage = new LocalClipStore();
const hints = {
  neutral: 'Stay comfortably upright and still for the full clip. Neutral needs its own examples.',
  left: 'After recording starts, step to your left and hold. Review the interval after you reach that position.',
  right: 'After recording starts, step to your right and hold. Left and right follow your mirrored preview.',
  jump: 'Stand still for the first second, then make one small hop. Review and trim to takeoff and airborne motion.',
  duck: 'Stand still for the first second, then bend your knees and hold. Select only the ducking interval.',
};
const bones = [[11,12],[11,23],[12,24],[23,24],[11,13],[13,15],[12,14],[14,16],[23,25],[25,27],[24,26],[26,28],[27,29],[29,31],[28,30],[30,32]];
let clips = [], participantId = localId(), sessionId = localId(), initialized = false;
let cameraPending = false, cameraActive = false, trackingValid = false, lastPoseAt = -Infinity, timer = null;
let lastDeleted = null, page = 0, persistenceVersion = 0, cameraAttempt = 0;
let lastPhase = 'idle', notice = '', pendingSaves = 0;

function message(title, detail = '') { $('lab-status').textContent = title; $('lab-detail').textContent = detail; }
function counts() { return Object.fromEntries(LABELS.map((label) => [label, clips.filter((clip) => clip.label === label).length])); }
function storageMessage(text, error = false) { $('storage-status').textContent = text; $('storage-status').dataset.error = String(error); }
function freshTracking() { return trackingValid && performance.now() - lastPoseAt <= LIMITS.staleMs; }

function drawSkeleton(canvas, points, mirrored = false, width = 640, height = 480) {
  if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
  const ctx = canvas.getContext('2d'); ctx.clearRect(0, 0, width, height);
  if (!points) return;
  const visible = (point) => point && Number.isFinite(point.x) && Number.isFinite(point.y) && point.visibility >= .55;
  const xy = (point) => [(mirrored ? 1 - point.x : point.x) * width, point.y * height];
  ctx.strokeStyle = mirrored ? '#7b2943' : '#f7d19e'; ctx.lineWidth = Math.max(2, width / 180); ctx.lineCap = 'round';
  for (const [a,b] of bones) if (visible(points[a]) && visible(points[b])) {
    ctx.beginPath(); ctx.moveTo(...xy(points[a])); ctx.lineTo(...xy(points[b])); ctx.stroke();
  }
  ctx.fillStyle = mirrored ? '#7b2943' : '#fff7ea';
  points.forEach((point) => { if (visible(point)) { ctx.beginPath(); ctx.arc(...xy(point), Math.max(2.5, width / 160), 0, Math.PI * 2); ctx.fill(); } });
}

function renderControls() {
  const busy = ['countdown', 'recording'].includes(recorder.phase), review = recorder.phase === 'review';
  $('lab-camera-start').disabled = !initialized || cameraPending || cameraActive;
  $('lab-camera-start').hidden = cameraActive || cameraPending;
  $('lab-camera-stop').hidden = !cameraActive && !cameraPending;
  $('lab-recalibrate').hidden = !cameraActive;
  $('lab-recalibrate').disabled = busy || review;
  $('new-participant').disabled = !initialized || busy || review || cameraPending;
  $('lab-label').disabled = busy || review;
  $('suggest-movement').disabled = !initialized || busy || review;
  $('lab-record').disabled = !cameraActive || !pose.baseline || !freshTracking() || busy || review || clips.length >= LIMITS.clips;
  $('lab-record').hidden = busy;
  $('lab-cancel').hidden = !busy;
  $('capture-cue').hidden = !busy;
  $('lab-camera-badge').textContent = cameraPending ? 'LOADING' : cameraActive ? busy ? 'CAPTURING' : 'CAMERA ON' : 'CAMERA OFF';
  $('lab-placeholder').hidden = cameraActive || cameraPending;
  $('download-data').disabled = !initialized || clips.length === 0;
  $('participant-name').textContent = `Participant ${participantId.slice(0, 8)}`;
  $('lab-review').hidden = !review;
  $('lab-progress').hidden = !cameraActive && !busy;
}

function renderLibrary() {
  const totals = counts();
  $('clip-total').textContent = `${clips.length} / ${LIMITS.clips}`;
  $('label-counts').replaceChildren(...LABELS.map((label) => {
    const box = document.createElement('div'), count = document.createElement('strong'), name = document.createElement('span');
    count.textContent = totals[label]; name.textContent = label[0].toUpperCase() + label.slice(1); box.append(count, name); return box;
  }));
  const selected = $('clip-filter').value, filtered = clips.filter((clip) => selected === 'all' || clip.label === selected).reverse();
  page = Math.max(0, Math.min(page, Math.ceil(filtered.length / 6) - 1));
  $('clip-list').replaceChildren(...filtered.slice(page * 6, page * 6 + 6).map((clip) => {
    const li = document.createElement('li'), text = document.createElement('div'), title = document.createElement('strong'), detail = document.createElement('span'), button = document.createElement('button');
    title.textContent = `${clip.label[0].toUpperCase()}${clip.label.slice(1)} · ${((clip.range[1] - clip.range[0]) / 1000).toFixed(2)} s labeled`;
    detail.textContent = `Session ${clip.sessionId.slice(0,8)} · ${clip.frames.length} frames`;
    text.append(title, detail); button.type = 'button'; button.className = 'text-button'; button.textContent = 'Delete'; button.setAttribute('aria-label', `Delete ${clip.label} clip ${clip.id.slice(0,8)}`);
    button.addEventListener('click', () => {
      const index = clips.findIndex((item) => item.id === clip.id);
      if (index < 0) return;
      lastDeleted = { clip: clips[index], index }; clips.splice(index,1); $('undo-delete').hidden = false;
      persist(); renderLibrary(); renderControls();
    });
    li.append(text, button); return li;
  }));
  $('clips-empty').hidden = filtered.length > 0;
  $('clips-empty').textContent = clips.length ? 'No saved clips match this movement yet.' : 'Your first reviewed clip will appear here.';
  $('clips-page').textContent = filtered.length ? `${page * 6 + 1}–${Math.min(page * 6 + 6,filtered.length)} of ${filtered.length}` : '0 clips';
  $('clips-previous').disabled = page === 0; $('clips-next').disabled = (page + 1) * 6 >= filtered.length;
  renderCollectionCoach();
}

function renderCollectionCoach() {
  const coverage = collectionCoverage(clips, participantId, sessionId);
  $('collection-coverage').textContent = `${coverage.completeSessions} sessions with all five moves · ${coverage.neutralSeconds.toFixed(1)} s labeled neutral for this participant`;
  $('collection-advice').textContent = coverage.missing.length
    ? `This session still needs: ${coverage.missing.join(', ')}. Collect every movement in each session so training can hold out whole sessions.`
    : 'All five moves are represented in this session. Add varied examples, including natural upright movement. Separate sessions in different conditions matter more than repeating one identical pose.';
  const label = coverage.nextLabel;
  $('suggest-movement').dataset.label = label;
  $('suggest-movement').textContent = `Choose ${label} · ${coverage.counts[label]} clips this session`;
}

async function persist() {
  const version = ++persistenceVersion; pendingSaves++;
  storageMessage('Saving joint coordinates in this browser…');
  try { await storage.save({ participantId, clips }); if (version === persistenceVersion) storageMessage('Saved in this browser. Download a copy before clearing browser data.'); }
  catch (error) { if (version === persistenceVersion) storageMessage(error.message, true); }
  finally { pendingSaves--; }
}

function beginSession() {
  sessionId = localId(); pose.calibrate(); trackingValid = false;
  $('lab-progress').value = 0;
  $('session-note').textContent = `Session ${sessionId.slice(0,8)}. Stand upright for a fresh calibration. Recalibrate whenever the camera position changes.`;
  renderCollectionCoach();
}

function onPose(points, timestamp) {
  if (!cameraActive) return;
  const now = performance.now();
  if (!Number.isFinite(timestamp) || now - timestamp > LIMITS.staleMs || timestamp > now + 5) {
    trackingValid = false;
    if (['countdown','recording'].includes(recorder.phase)) recorder.abort('A delayed camera frame interrupted capture. Try again.');
    synchronize(); return;
  }
  const result = pose.update(points, timestamp);
  lastPoseAt = timestamp;
  trackingValid = result.tracked && !!pose.baseline && !!extractFeatures(points, pose.baseline);
  drawSkeleton($('lab-pose'), points, false, $('lab-video').videoWidth || 640, $('lab-video').videoHeight || 480);
  if (['countdown','recording'].includes(recorder.phase)) recorder.frame(points, timestamp, now);
  else if (recorder.phase !== 'review') {
    if (!result.calibrated) { message('Hold still for standing calibration.', result.message); $('lab-progress').value = result.progress; }
    else if (!trackingValid) message('Keep your whole body in view.', 'Recording is available when tracking is steady again.');
    else { message(notice || 'Calibrated. Choose a movement, then record.', hints[$('lab-label').value]); $('lab-progress').value = 1; }
  }
  synchronize();
}

const camera = new CameraTracker({ video: $('lab-video'), onPose,
  onStatus: ({ state, message: text }) => {
    if (['requesting','loading'].includes(state)) message(text, 'You can cancel at any time. Your clips are kept separately.');
    if (state === 'ready') { cameraActive = true; cameraPending = false; renderControls(); }
  },
  onError: (error) => {
    cameraActive = cameraPending = trackingValid = false;
    if (['countdown','recording'].includes(recorder.phase)) recorder.abort('Capture cancelled because the camera stopped.');
    clearInterval(timer); timer = null; drawSkeleton($('lab-pose'), null);
    message(error.message, 'Your saved clips are still available. Enable the camera to try again.');
    synchronize();
  },
});

function synchronize() {
  const now = performance.now();
  if (['countdown','recording'].includes(recorder.phase)) {
    const countdown = recorder.phase === 'countdown';
    const remaining = countdown ? Math.max(1, Math.ceil((recorder.captureAt - now) / 1000)) : Math.max(0, (LIMITS.recordingMs - (now - recorder.firstFrameAt)) / 1000);
    $('cue-number').textContent = countdown ? remaining : remaining.toFixed(1);
    $('cue-text').textContent = countdown ? 'GET READY' : `RECORD ${recorder.draft.label.toUpperCase()}`;
    message(countdown ? `Get ready: ${recorder.draft.label}.` : `Recording ${recorder.draft.label}…`, countdown ? 'Wait for RECORD before making your movement.' : 'Make one move, keeping your whole body visible.');
    $('lab-progress').value = countdown ? 0 : Math.min(1, (now - recorder.firstFrameAt) / LIMITS.recordingMs);
  }
  if (lastPhase !== recorder.phase) {
    if (recorder.phase === 'review') openReview();
    else if (recorder.phase === 'idle' && recorder.reason) { notice = recorder.reason; message(notice, 'Nothing from that capture was saved.'); }
    lastPhase = recorder.phase;
  }
  renderControls();
}

function tick() {
  recorder.tick(performance.now());
  if (cameraActive && performance.now() - lastPoseAt > LIMITS.staleMs) {
    trackingValid = false;
    if (recorder.phase === 'idle' && pose.baseline) message('Waiting for fresh tracking.', 'Record will become available when your whole body is visible again.');
  }
  synchronize();
}

function stopCamera(text = 'Camera off. Your saved clips stay here.') {
  ++cameraAttempt;
  if (['countdown','recording'].includes(recorder.phase)) recorder.abort('Capture cancelled. Nothing was saved.');
  camera.stop(); cameraActive = cameraPending = trackingValid = false;
  clearInterval(timer); timer = null; pose.calibrate(); drawSkeleton($('lab-pose'), null);
  message(text, 'Enable the camera for a new standing calibration when you are ready.'); synchronize();
}

function selectedRange() { return [Number($('range-start').value), Number($('range-end').value)]; }
function updateReview() {
  if (recorder.phase !== 'review') return;
  const range = selectedRange(), time = Number($('review-time').value), frames = recorder.draft.frames;
  const frame = frames.reduce((closest, item) => Math.abs(item.t - time) < Math.abs(closest.t - time) ? item : closest);
  drawSkeleton($('review-canvas'), frame.landmarks, true);
  $('review-time-label').textContent = `${(frame.t / 1000).toFixed(2)} s${frame.t >= range[0] && frame.t <= range[1] ? ' · labeled' : ' · context'}`;
  $('range-start-label').textContent = `${(range[0] / 1000).toFixed(2)} s`; $('range-end-label').textContent = `${(range[1] / 1000).toFixed(2)} s`;
  let error = '';
  try { validateClip({ ...recorder.draft, range }); } catch (failure) { error = failure.message; }
  $('review-error').hidden = !error; $('review-error').textContent = error;
  $('save-clip').disabled = !!error || !$('review-confirm').checked || clips.length >= LIMITS.clips;
}

function openReview() {
  const draft = recorder.draft, end = Math.floor(draft.frames.at(-1).t);
  $('review-label').textContent = draft.label.toUpperCase();
  $('review-time').max = end; $('review-time').value = 0;
  $('range-start').min = Math.ceil(draft.frames[0].t + LIMITS.contextMs); $('range-start').max = end - LIMITS.rangeMs; $('range-start').value = 500;
  $('range-end').max = end; $('range-end').min = Math.ceil(draft.frames[0].t + LIMITS.contextMs) + LIMITS.rangeMs; $('range-end').value = end;
  $('review-confirm').checked = false;
  const quality = recordingQuality(draft);
  $('review-quality').textContent = `${quality.frames} frames · ${quality.samplesPerSecond.toFixed(1)} samples/s · lowest required-joint visibility ${Math.round(quality.minimumVisibility * 100)}%. ${quality.weakTracking ? 'Tracking was weak or uneven. Inspect the skeleton closely; rerecord if it jumps or misses your movement.' : 'Inspect the skeleton and trim precisely. Tracking quality does not verify the movement label.'}`;
  $('review-quality').classList.toggle('quality-caution', quality.weakTracking);
  $('review-guidance').textContent = draft.label === 'jump' ? 'Find the takeoff and airborne part of your jump. Exclude standing before the jump and landing afterward; the clip is not automatically labeled.' : `Select only the interval showing ${draft.label}. Transitions outside your selected range are context, not neutral examples.`;
  $('lab-review').hidden = false; updateReview();
  message('Clip ready for your review.', 'Scrub the skeleton below, trim the labeled interval, then save or discard.');
  $('review-title').focus({ preventScroll: true }); $('lab-review').scrollIntoView({ block: 'start', behavior: 'instant' });
}

$('lab-camera-start').addEventListener('click', async () => {
  if (cameraActive || cameraPending || !initialized) return;
  const attempt = ++cameraAttempt;
  cameraPending = true; notice = ''; beginSession(); renderControls();
  if (!timer) timer = setInterval(tick, 100);
  const started = await camera.start();
  if (attempt !== cameraAttempt) return;
  if (!started) { cameraPending = false; renderControls(); }
});
$('lab-camera-stop').addEventListener('click', () => stopCamera());
$('lab-recalibrate').addEventListener('click', () => { notice = ''; beginSession(); message('A new session has started.', 'Stand upright and still for two seconds.'); renderControls(); });
$('new-participant').addEventListener('click', () => {
  participantId = localId(); notice = ''; beginSession(); persist(); renderControls();
  message('New participant. A fresh calibration is required.', 'Existing clips keep their original participant and session IDs.');
});
$('lab-label').addEventListener('change', () => { $('label-hint').textContent = hints[$('lab-label').value]; notice = ''; });
$('suggest-movement').addEventListener('click', () => {
  if ($('lab-label').disabled) return;
  $('lab-label').value = $('suggest-movement').dataset.label;
  $('lab-label').dispatchEvent(new Event('change'));
  $('lab-label').focus({ preventScroll: true });
});
$('lab-record').addEventListener('click', () => {
  if (!cameraActive || !freshTracking() || !pose.baseline || clips.length >= LIMITS.clips) return;
  try { notice = ''; recorder.start({ label: $('lab-label').value, baseline: pose.baseline, participantId, sessionId }, performance.now()); synchronize(); }
  catch (error) { message(error.message); }
});
$('lab-cancel').addEventListener('click', () => { recorder.abort(); synchronize(); });
for (const id of ['range-start','range-end']) $(id).addEventListener('input', () => { $('review-confirm').checked = false; $('review-time').value = $(id).value; updateReview(); });
$('review-time').addEventListener('input', updateReview); $('review-confirm').addEventListener('change', updateReview);
$('save-clip').addEventListener('click', () => {
  if (!$('review-confirm').checked || clips.length >= LIMITS.clips) return;
  try {
    const clip = validateClip({ ...recorder.draft, range: selectedRange() });
    makeDataset([...clips, clip]);
    clips.push(recorder.take(selectedRange())); notice = 'Reviewed clip saved. Ready for your next move.';
    persist(); renderLibrary(); synchronize(); message(notice, 'Collect each movement across several separate sessions.'); $('lab-record').focus();
  } catch (error) { $('review-error').hidden = false; $('review-error').textContent = error.message; }
});
$('discard-clip').addEventListener('click', () => { recorder.reset(); notice = 'Clip discarded. Nothing was saved.'; synchronize(); message(notice); $('lab-record').focus(); });
$('clip-filter').addEventListener('change', () => { page = 0; renderLibrary(); });
$('clips-previous').addEventListener('click', () => { page--; renderLibrary(); }); $('clips-next').addEventListener('click', () => { page++; renderLibrary(); });
$('undo-delete').addEventListener('click', () => {
  if (!lastDeleted || clips.length >= LIMITS.clips) return;
  clips.splice(Math.min(lastDeleted.index,clips.length), 0, lastDeleted.clip); lastDeleted = null; $('undo-delete').hidden = true;
  persist(); renderLibrary(); renderControls();
});
$('download-data').addEventListener('click', () => {
  try {
    const dataset = makeDataset(clips);
    if (!dataset.clips.length) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(dataset)], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = `metro-motion-${new Date().toISOString().slice(0,10)}.json`; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  } catch (error) { storageMessage(`Download could not start: ${error.message}`, true); }
});
document.addEventListener('visibilitychange', () => { if (document.hidden && (cameraActive || cameraPending)) stopCamera('Camera stopped because this page was hidden.'); });
window.addEventListener('pagehide', () => stopCamera());
window.addEventListener('beforeunload', (event) => { if (recorder.phase === 'review' || pendingSaves || (clips.length && $('storage-status').dataset.error === 'true')) { event.preventDefault(); event.returnValue = ''; } });

Object.defineProperty(window, '__motionLab', { value: Object.freeze({ state: () => ({ phase: recorder.phase, initialized, counts: counts(), clipCount: clips.length, sessionId, participantId, cameraActive, cameraPending, calibrated: !!pose.baseline, trackingValid: freshTracking(), pendingSaves, storageError: $('storage-status').dataset.error === 'true', draft: recorder.draft ? { label: recorder.draft.label, frameCount: recorder.draft.frames.length, range: recorder.phase === 'review' ? selectedRange() : null, duration: recorder.draft.frames.at(-1)?.t ?? 0 } : null }) }) });

try {
  const saved = await storage.open();
  if (saved) { participantId = saved.participantId; clips = saved.clips; }
  storageMessage('Joint coordinates save locally after you review each clip. Download a copy when ready.');
} catch (error) { storageMessage(error.message, true); }
initialized = true; renderLibrary(); renderControls();
