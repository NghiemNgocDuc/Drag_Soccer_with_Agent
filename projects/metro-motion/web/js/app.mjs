import { createGame, startGame, stepGame, setLane, moveLane, jump, setDuck, pauseGame, resumeGame } from './core.mjs';
import { RunnerRenderer } from './renderer.mjs';
import { PoseController } from './pose-controls.mjs';
import { CameraTracker } from './camera.mjs';
import { LearnedController, MAX_MODEL_BYTES, validateModel } from './learned-controls.mjs';

const $ = id => document.getElementById(id);
const canvas = $('runner-canvas');
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
const renderer = new RunnerRenderer(canvas, { reducedMotion: reduced.matches });
const controller = new PoseController({ mirrored: true });
const learned = new LearnedController();
let modelImportGeneration = 0;
let game = createGame({ seed: 42, difficulty: 'gentle' });
let mode = 'none', uiPhase = 'setup', pauseReason = '';
let cameraActive = false, cameraPending = false, cameraGeneration = 0;
let pose = { tracked: false, calibrated: false, progress: 0, lane: 0, duck: false };
let lastCaptureAt = -Infinity, staleNotified = false;
let frame = 0, lastFrame = 0, lastHUD = 0, countdownEnd = 0;
let lastEventId = 0, feedbackUntil = 0, noticeTimer = 0;
const duckInputs = new Set();
let best = 0;
try { const saved = Number(localStorage.getItem('metro-motion-best')); best = Number.isFinite(saved) ? Math.max(0, saved) : 0; } catch (_) {}
if (!location.pathname.startsWith('/metro-motion/')) document.querySelector('.back-link').hidden = true;

const text = (id, value) => { const node = $(id); if (node.textContent !== String(value)) node.textContent = String(value); };
const visible = (id, value) => { $(id).hidden = !value; };
const announce = message => {
  text('notice', message); visible('notice', true); clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => visible('notice', false), 5000);
};
const resetPose = () => {
  controller.calibrate();
  learned.reset();
  pose = { tracked: false, calibrated: false, progress: 0, lane: 0, duck: false };
  lastCaptureAt = -Infinity; staleNotified = false;
  $('calibration-progress').value = 0;
  syncModelPrediction();
};

function syncModelPrediction() {
  const prediction = pose.prediction;
  const label = prediction ? `${prediction.label.toUpperCase()} · ${Math.round(prediction.confidence * 100)}%` : null;
  text('model-prediction', !learned.model ? 'Waiting for a model.' : !cameraActive ? 'Enable the camera to compare your movements.'
    : !prediction ? 'Waiting for calibrated movement…' : learned.mode === 'compare' ? `${label} · comparing only`
    : `${label} · ${pose.source === 'learned' ? 'trained controls' : 'standard controls'} `);
}

function syncModelUI() {
  const loaded = Boolean(learned.model);
  $('model-mode').disabled = !loaded;
  $('model-mode').value = learned.mode;
  $('model-mode').querySelector('[value="control"]').disabled = !learned.model?.metrics.controlEligible;
  $('model-remove').disabled = !loaded;
  text('model-gate', !loaded ? 'Models load in comparison mode. Trained controls require a passed evaluation report.'
    : learned.model.metrics.controlEligible ? 'This file reports a passed evaluation. Compare its predictions in your space before trying trained controls.'
    : 'Comparison only: this model has not passed the training evaluation gate. Standard controls stay active.');
  syncModelPrediction();
}

async function importMotionModel(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  const generation = ++modelImportGeneration;
  text('model-status', 'Checking model…');
  try {
    if (file.size > MAX_MODEL_BYTES) throw new Error('Choose a model JSON file smaller than 2 MB.');
    const content = await file.text();
    if (generation !== modelImportGeneration) return;
    // Validate before pausing or replacing the previous model. Invalid imports
    // leave that model and the current control mode intact.
    const model = validateModel(content);
    pause();
    learned.load(model);
    pose = { ...pose, prediction: null, source: 'rules' };
    text('model-status', 'Model loaded in comparison mode. Its predictions do not change your controls.');
    syncModelUI();
  } catch (error) {
    if (generation === modelImportGeneration) text('model-status', `${error.message} ${learned.model ? 'Your previous model is still loaded.' : 'Standard controls remain active.'}`);
  } finally {
    if (generation === modelImportGeneration) event.target.value = '';
  }
}

function overlay(kicker, title, message, actions = [], footnote = '') {
  text('overlay-kicker', kicker); text('overlay-title', title); text('overlay-message', message);
  text('overlay-footnote', footnote);
  for (const id of ['keyboard-start', 'camera-run', 'resume-run', 'restart-run']) visible(id, actions.includes(id));
  visible('game-overlay', true);
}

function syncUI() {
  $('pause-run').disabled = !['running', 'countdown'].includes(uiPhase);
  $('resume-run').disabled = mode === 'camera' && (!cameraActive || !pose.calibrated || !pose.tracked);
  $('camera-run').disabled = !cameraActive || !pose.tracked || !pose.calibrated;
  visible('camera-start', !cameraActive && !cameraPending);
  visible('camera-stop', cameraActive || cameraPending);
  visible('recalibrate', cameraActive);
  visible('camera-live', cameraActive);
  visible('camera-placeholder', !cameraActive && !$('camera-video').srcObject);
  visible('calibration-progress', cameraActive && !pose.calibrated);
  $('camera-badge').classList.toggle('live', cameraActive);
  text('camera-badge', cameraActive ? 'CAMERA ON' : cameraPending ? 'CONNECTING' : 'CAMERA OFF');
  text('control-mode', mode === 'camera' ? 'Body control · mirrored view' : mode === 'keyboard' ? 'Keyboard · arrows / space' : 'Choose camera or keyboard to begin');
  text('pace-label', `${game.difficulty.toUpperCase()} PACE`);
  text('run-status', ({ setup: 'READY WHEN YOU ARE', 'camera-loading': 'CONNECTING YOUR CAMERA', calibrating: 'FINDING YOUR STRIDE', 'ready-camera': 'CALIBRATION COMPLETE', countdown: 'HERE WE GO', running: 'ON THE SUNSET LINE', paused: 'TAKE A BREATHER', over: 'THAT WAS A GOOD RUN' })[uiPhase]);
  for (const button of document.querySelectorAll('[data-action]')) button.disabled = uiPhase !== 'running' || mode !== 'keyboard';
  if (uiPhase === 'running') visible('game-overlay', false);
}

function hud() {
  text('distance', Math.floor(game.distance)); text('coins', game.coins); text('best-score', best);
  for (const node of document.querySelectorAll('[data-lane]')) node.classList.toggle('active', Number(node.dataset.lane) === game.lane);
}

function feedback(message) {
  text('move-feedback', message); feedbackUntil = performance.now() + 600;
}

function pause(reason = 'manual') {
  if (!['running', 'countdown'].includes(uiPhase)) return;
  duckInputs.clear();
  setDuck(game, false); pauseGame(game);
  uiPhase = 'paused'; pauseReason = reason; lastFrame = 0;
  const message = reason === 'tracking'
    ? 'Your run is safe. Step back into the frame, then choose Resume run.'
    : reason === 'hidden'
      ? (mode === 'camera' ? 'Your camera was turned off while you were away. Enable it again to continue.' : 'Your run paused while this page was out of view.')
      : 'Take your time. The city will be here when you are ready.';
  overlay('RUN PAUSED', reason === 'tracking' ? 'Let’s find you again.' : 'Catch your breath.', message, ['resume-run', 'keyboard-start'], 'P or Escape pauses your run.');
  syncUI(); draw();
}

function stopCamera({ preserveMode = true } = {}) {
  ++cameraGeneration;
  if (mode === 'camera') pause('camera-off');
  camera.stop(); cameraActive = false; cameraPending = false;
  resetPose();
  drawPose([]);
  if (!preserveMode) mode = 'none';
  if (['camera-loading', 'calibrating', 'ready-camera'].includes(uiPhase)) uiPhase = game.phase === 'paused' ? 'paused' : 'setup';
  if (mode === 'camera' && uiPhase !== 'over') overlay('CAMERA OFF', 'Your run can wait.', 'Enable your camera when you are ready, or start a new keyboard run.', ['keyboard-start']);
  text('tracking-status', 'Camera is off.');
  text('tracking-detail', 'Enable it when you are ready. Keyboard play works without a camera.');
  syncUI(); draw();
}

function cameraError(error) {
  if (mode !== 'camera') return;
  pause('camera-off'); cameraActive = cameraPending = false; resetPose();
  drawPose([]);
  uiPhase = game.phase === 'paused' ? 'paused' : 'setup';
  text('tracking-status', 'Camera unavailable.'); text('tracking-detail', error.message);
  overlay('CAMERA UNAVAILABLE', 'There’s another way in.', error.message, ['keyboard-start']);
  syncUI(); draw();
}

function drawPose(points) {
  const target = $('pose-overlay'), ctx = target.getContext('2d');
  const video = $('camera-video');
  const width = video.videoWidth || 640, height = video.videoHeight || 480;
  if (target.width !== width || target.height !== height) { target.width = width; target.height = height; }
  ctx.clearRect(0, 0, target.width, target.height);
  if (!points?.length) return;
  const visiblePoint = i => points[i] && (points[i].visibility ?? 0) > .5;
  ctx.lineWidth = 3 * width / 640; ctx.lineCap = 'round'; ctx.strokeStyle = '#ffe3a5'; ctx.fillStyle = '#fff9ed';
  for (const [a, b] of [[11, 12], [11, 13], [13, 15], [12, 14], [14, 16], [11, 23], [12, 24], [23, 24], [23, 25], [25, 27], [24, 26], [26, 28]]) {
    if (!visiblePoint(a) || !visiblePoint(b)) continue;
    ctx.beginPath(); ctx.moveTo(points[a].x * width, points[a].y * height); ctx.lineTo(points[b].x * width, points[b].y * height); ctx.stroke();
  }
  for (const i of [11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28]) if (visiblePoint(i)) {
    ctx.beginPath(); ctx.arc(points[i].x * width, points[i].y * height, 4 * width / 640, 0, Math.PI * 2); ctx.fill();
  }
}

function receivePose(points, timestamp) {
  if (mode !== 'camera' || document.hidden) return;
  const now = performance.now();
  lastCaptureAt = timestamp; staleNotified = false;
  const freshPoints = now - timestamp > 650 ? null : points;
  const rulePose = controller.update(freshPoints, timestamp);
  pose = learned.update(freshPoints, timestamp, controller.baseline, rulePose);
  syncModelPrediction();
  drawPose(points);
  $('calibration-progress').value = pose.progress;
  text('tracking-status', !pose.tracked ? 'Move into the frame.' : !pose.calibrated ? `Calibrating · ${Math.round(pose.progress * 100)}%` : 'Tracking your movement.');
  text('tracking-detail', pose.message);
  if (pose.calibrated && uiPhase === 'calibrating') {
    uiPhase = 'ready-camera';
    text('camera-run', game.phase === 'paused' ? 'Resume your run →' : 'Start my run →');
    overlay('CALIBRATION COMPLETE', 'You’re in.', 'Step left and right to choose a lane. Small jumps clear barriers; bend your knees to duck.', ['camera-run', 'keyboard-start'], 'Take your starting position, then begin.');
  }
  if (!pose.tracked && ['running', 'countdown'].includes(uiPhase)) pause('tracking');
  if (uiPhase === 'running' && pose.calibrated && pose.tracked) {
    const before = game.lane;
    setLane(game, pose.lane); setDuck(game, pose.duck);
    if (before !== game.lane) feedback(game.lane < 0 ? 'LEFT' : game.lane > 0 ? 'RIGHT' : 'CENTER');
    if (pose.jump && jump(game)) feedback('JUMP');
  }
  syncUI();
}

const camera = new CameraTracker({
  video: $('camera-video'), onPose: receivePose, onError: cameraError,
  onStatus: status => { if (mode === 'camera' && status.state !== 'error') { text('tracking-status', status.state === 'ready' ? 'Stand tall in the center.' : 'Connecting your camera…'); text('tracking-detail', status.message); } },
});

async function enableCamera() {
  if (cameraActive || cameraPending) return;
  pause('manual'); stopCamera();
  const generation = ++cameraGeneration;
  mode = 'camera'; cameraPending = true; uiPhase = 'camera-loading'; pauseReason = '';
  overlay('YOUR MOTION STUDIO', 'Let’s get you in frame.', 'Allow camera access, then stand back until your whole body is visible. You can cancel at any time.', ['keyboard-start'], 'Only video is requested. No microphone.');
  syncUI(); draw();
  const started = await camera.start();
  if (generation !== cameraGeneration || mode !== 'camera') return;
  cameraPending = false;
  if (!started) { syncUI(); return; }
  cameraActive = true; uiPhase = 'calibrating';
  overlay('CALIBRATING YOUR SPACE', 'Stand tall. Hold still.', 'Face the screen with your head and both feet visible. Hold your starting position for two seconds.', ['keyboard-start']);
  syncUI(); draw();
}

function newGame() {
  duckInputs.clear();
  game = createGame({ seed: Date.now() >>> 0, difficulty: $('difficulty').value });
  lastEventId = 0; pauseReason = ''; lastFrame = 0; hud();
}

function countdown({ resume = false } = {}) {
  if (mode === 'camera' && (!cameraActive || !pose.calibrated || !pose.tracked)) return;
  if (!resume) newGame();
  uiPhase = 'countdown'; countdownEnd = performance.now() + 3000; lastFrame = 0;
  overlay('FIND YOUR CENTER', '3', mode === 'camera' ? 'Small steps. Eyes on the track.' : '← → to change lanes · Space to jump · Hold ↓ to duck');
  syncUI(); draw();
  canvas.focus({ preventScroll: true });
}

function startKeyboard() {
  stopCamera({ preserveMode: false }); mode = 'keyboard';
  countdown();
}

function finish() {
  uiPhase = 'over'; pauseReason = '';
  const score = Math.floor(game.score);
  if (score > best) { best = score; try { localStorage.setItem('metro-motion-best', String(best)); } catch (_) {} }
  const type = game.collision?.type;
  const tip = type === 'train' ? 'Trains fill the lane. Follow the coins to an open route.' : type === 'barrier' ? 'Jump over low striped barriers, or take another lane.' : 'Duck beneath the high arches, or switch to an open lane.';
  overlay('RUN COMPLETE', `${Math.floor(game.distance)} meters. Nice run.`, `${game.coins} coins · ${score} points. ${tip}`, ['restart-run', 'keyboard-start'], 'Every run is a fresh stretch of the city.');
  text('restart-run', mode === 'camera' ? 'Run again with camera ↻' : 'Run again ↻');
  syncUI(); hud();
  $('restart-run').focus({ preventScroll: true });
}

function tick(now) {
  frame = 0;
  if (document.hidden) return;
  const dt = lastFrame ? Math.min((now - lastFrame) / 1000, .1) : 0;
  lastFrame = now;
  if (mode === 'camera' && ['running', 'countdown'].includes(uiPhase) && now - lastCaptureAt > 700 && !staleNotified) {
    staleNotified = true;
    pose = controller.update(null, Math.max(now, lastCaptureAt + 701));
    learned.reset(); syncModelPrediction();
    pause('tracking'); text('tracking-status', 'Waiting for a fresh camera frame.');
  }
  if (uiPhase === 'countdown') {
    const left = Math.ceil((countdownEnd - now) / 1000);
    if (left > 0) text('overlay-title', left);
    else { if (game.phase === 'paused') resumeGame(game); else startGame(game); uiPhase = 'running'; pauseReason = ''; syncUI(); }
  }
  if (uiPhase === 'running') {
    stepGame(game, dt);
    if (game.phase === 'over') finish();
    if (game.lastEvent?.id !== lastEventId) {
      lastEventId = game.lastEvent?.id;
      if (game.lastEvent?.type === 'coin') feedback('+ COIN');
    }
  }
  if (now > feedbackUntil) text('move-feedback', '');
  renderer.render(game, game.elapsed);
  if (now - lastHUD > 100) { hud(); lastHUD = now; }
  if (['running', 'countdown'].includes(uiPhase)) draw();
}

function draw() { if (!frame && !document.hidden) frame = requestAnimationFrame(tick); }

function action(name, down = true, source = 'button') {
  if (name === 'duck' && !down) duckInputs.delete(source);
  if (uiPhase !== 'running' || mode !== 'keyboard') return;
  if (name === 'left' && down) { moveLane(game, -1); feedback('LEFT'); }
  if (name === 'right' && down) { moveLane(game, 1); feedback('RIGHT'); }
  if (name === 'jump' && down && jump(game)) feedback('JUMP');
  if (name === 'duck') { if (down) duckInputs.add(source); setDuck(game, duckInputs.size > 0); if (down) feedback('DUCK'); }
  hud(); draw();
}

$('camera-start').addEventListener('click', enableCamera);
$('motion-model').addEventListener('change', importMotionModel);
$('model-mode').addEventListener('change', () => {
  const selected = $('model-mode').value;
  try {
    if (selected === 'control' && !learned.model?.metrics.controlEligible) throw new Error('This model is available for comparison only.');
    pause(); learned.setMode(selected);
    pose = { ...pose, prediction: null, source: 'rules' };
    text('model-status', selected === 'control' ? 'Experimental trained controls enabled. Uncertain predictions use standard controls.' : 'Comparison mode enabled. Standard controls are active.');
  } catch (error) { text('model-status', error.message); }
  syncModelUI();
});
$('model-remove').addEventListener('click', () => {
  ++modelImportGeneration; pause(); learned.clear();
  pose = { ...pose, prediction: null, source: 'rules' };
  $('motion-model').value = '';
  text('model-status', 'Model removed. Standard controls are active.'); syncModelUI();
});
$('camera-stop').addEventListener('click', () => stopCamera());
$('keyboard-start').addEventListener('click', startKeyboard);
$('camera-run').addEventListener('click', () => countdown({ resume: game.phase === 'paused' }));
$('pause-run').addEventListener('click', () => pause());
$('resume-run').addEventListener('click', () => countdown({ resume: true }));
$('restart-run').addEventListener('click', () => {
  if (mode === 'camera' && !cameraActive) enableCamera();
  else if (mode === 'camera' && (!pose.calibrated || !pose.tracked)) announce('Step into the camera frame before starting again.');
  else countdown();
});
$('recalibrate').addEventListener('click', () => {
  if (!cameraActive) return;
  pause(); resetPose(); uiPhase = 'calibrating';
  overlay('RESET YOUR STARTING POSITION', 'Stand tall. Hold still.', 'Return to the center with your head and feet in view. We will measure your new position.', ['keyboard-start']);
  syncUI(); draw();
});
$('difficulty').addEventListener('change', () => text('settings-note', $('difficulty').value === 'gentle' ? 'A little more room to learn the moves. Applies to your next run.' : 'A quicker city with more pace. Applies to your next run.'));
$('fullscreen-run').addEventListener('click', async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen(); else await $('game').requestFullscreen();
  }
  catch (_) { announce('Fullscreen is unavailable here. You can still play in this window.'); }
  finally { if (['running', 'countdown'].includes(uiPhase)) canvas.focus({ preventScroll: true }); }
});
document.addEventListener('fullscreenchange', () => { renderer.resize(); draw(); });

const keyActions = { ArrowLeft: 'left', KeyA: 'left', ArrowRight: 'right', KeyD: 'right', ArrowUp: 'jump', KeyW: 'jump', Space: 'jump', ArrowDown: 'duck', KeyS: 'duck' };
window.addEventListener('keydown', event => {
  if (event.target.closest?.('input,select,textarea,[contenteditable="true"]') || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.code === 'KeyP' || event.code === 'Escape') {
    if (event.repeat) return;
    if (['running', 'countdown'].includes(uiPhase)) { event.preventDefault(); pause(); }
    else if (event.code === 'KeyP' && uiPhase === 'paused') { event.preventDefault(); countdown({ resume: true }); }
    return;
  }
  if (event.target.closest?.('button,a,summary,[role="button"]')) return;
  if (keyActions[event.code] && uiPhase === 'running' && mode === 'keyboard') {
    event.preventDefault(); if (!event.repeat) action(keyActions[event.code], true, `key:${event.code}`);
  }
});
window.addEventListener('keyup', event => { if (keyActions[event.code] === 'duck') action('duck', false, `key:${event.code}`); });
window.addEventListener('blur', () => { duckInputs.clear(); if (uiPhase === 'running') setDuck(game, false); });
for (const button of document.querySelectorAll('[data-action]')) {
  if (button.dataset.action === 'duck') {
    button.addEventListener('pointerdown', event => { button.setPointerCapture(event.pointerId); action('duck', true, `pointer:${event.pointerId}`); });
    for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) button.addEventListener(type, event => action('duck', false, `pointer:${event.pointerId}`));
    button.addEventListener('keydown', event => { if (['Space', 'Enter'].includes(event.code)) { event.preventDefault(); action('duck', true, `button:${event.code}`); } });
    button.addEventListener('keyup', event => action('duck', false, `button:${event.code}`));
    button.addEventListener('blur', () => { for (const code of ['Space', 'Enter']) action('duck', false, `button:${code}`); });
  } else button.addEventListener('click', () => { action(button.dataset.action); canvas.focus({ preventScroll: true }); });
}

document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    pause('hidden');
    if (cameraActive || cameraPending) { stopCamera(); pauseReason = 'hidden'; }
    cancelAnimationFrame(frame); frame = 0; lastFrame = 0;
  } else draw();
});
window.addEventListener('pagehide', () => {
  pause('hidden');
  if (cameraActive || cameraPending) stopCamera();
  else camera.stop();
  cancelAnimationFrame(frame); frame = 0; lastFrame = 0;
});
window.addEventListener('pageshow', event => {
  if (!event.persisted) return;
  cameraActive = cameraPending = false; resetPose();
  if (mode === 'camera') { uiPhase = 'paused'; pauseReason = 'hidden'; overlay('WELCOME BACK', 'Ready when you are.', 'Enable your camera again to continue, or start a keyboard run.', ['keyboard-start']); }
  syncUI(); draw();
});
const resizeObserver = new ResizeObserver(() => { renderer.resize(); draw(); });
resizeObserver.observe(canvas);
reduced.addEventListener('change', () => { renderer.reducedMotion = reduced.matches; draw(); });

// Read-only snapshots for diagnostics. No video, landmarks or mutation hooks.
window.__metroMotion = Object.freeze({ state: () => structuredClone({ ...game, mode, uiPhase, pauseReason, cameraActive, cameraPending, calibrated: pose.calibrated, trackingValid: pose.tracked, modelLoaded: Boolean(learned.model), modelMode: learned.mode, modelEligible: Boolean(learned.model?.metrics.controlEligible), prediction: pose.prediction ?? null, controlSource: pose.source ?? 'rules' }) });
syncUI(); syncModelUI(); hud(); draw();
