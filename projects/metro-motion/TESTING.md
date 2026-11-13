# Metro Motion verification

## Accuracy workflow checks - November 12, 2026

| Suite | Passed | Failed |
| --- | ---: | ---: |
| Node game, camera, collection quality and motion learning tests | 96 | 0 |
| Python dataset, optimization, controller evaluation and training tests | 22 | 0 |
| Chrome Motion Lab integration | 42 | 0 |

The Python suite trained on CPU, checked deterministic checkpoint resume,
calibrated export parity with JavaScript, and replayed emitted controls against
the shipped browser controller. Exported reports include validation and test
control metrics. Synthetic fixtures remain ineligible for experimental control;
these checks do not establish real human movement accuracy or GPU performance.
The earlier Edge and original gameplay results below were not rerun in this pass.

## Motion Lab checks — November 7, 2026

| Suite | Passed | Failed |
| --- | ---: | ---: |
| Node game, camera, features, learned controls and recorder tests | 88 | 0 |
| Python dataset, evaluation and training tests | 14 | 0 |
| Chrome Motion Lab integration | 42 | 0 |
| Edge Motion Lab integration | 42 | 0 |
| Chrome original gameplay regression (without optional person photo) | 44 | 0 |

The Python run used Python 3.12 and CPU PyTorch 2.15.0, installed only in an
ignored workspace dependency directory. It actually trained a synthetic dataset,
resumed a checkpoint, compared resumed weights to uninterrupted training, and
evaluated the exported model. The shipped JavaScript evaluator matched PyTorch
probabilities within 0.000002. Python/JavaScript temporal features and rule
baseline decisions also match their fixture tests. The full 13-test Python run
and the additional baseline-parity test were run separately. The synthetic
model remained comparison-only. No real movement accuracy is claimed.

Motion Lab browser checks exercise explicit video-only camera permission,
calibration, countdown/capture, review acknowledgment and trim, IndexedDB reload,
download shape, delete/undo, participant grouping, lost tracking, cancellation,
hidden-page cleanup, and widths 320–1440px. Runner checks cover comparison versus
conflicting rule input, gated control, mode-change pause, tracking-loss pause,
malformed/oversized JSON, removal during an asynchronous import, non-persistence,
and keyboard access to the model panel. Both final reports contain no JavaScript
exceptions or external requests. Hidden-page events use a synthetic visibility
fixture; no physical camera is opened. Test-only eligible metadata exercises the
control branch and is never represented as a qualifying trained model.

The Unity Slurm script passes Bash syntax validation. No cluster job or CUDA
training was executed. Live human recordings, actual GPU execution, diverse-user
accuracy, and gameplay quality with a learned model remain to be measured.
Evaluation metrics describe classifier windows and onset estimates; they do not
benchmark the live controller's dwell/rearm logic or camera/rendering latency.

```sh
node --test projects/metro-motion/tests/*.test.mjs
python -m unittest discover -s projects/metro-motion/training/tests -v
python tools/browser/verify_motion_lab.py --channel chrome
python tools/browser/verify_motion_lab.py --channel msedge
```

PyTorch is optional for dataset tests; install it and Node to run training/export
parity. Browser reports/screenshots are under the system temporary directory at
`agent-soccer-motion-lab-{chrome,msedge}`. The complete workflow and evaluation
requirements are in [the training guide](training/README.md).

## Results recorded through November 5, 2026

| Suite | Passed | Failed |
| --- | ---: | ---: |
| Node simulation, pose, camera and renderer tests | 59 | 0 |
| Chrome gameplay/browser suite | 45 | 0 |
| Edge gameplay/browser suite | 45 | 0 |
| Chrome edge-case suite | 71 | 0 |
| Edge edge-case suite | 71 | 0 |

That is 59 automated module tests and 232 browser assertions across the two
browsers. The final browser reports contain no JavaScript exceptions. Expanded
browser runs used Chrome 156.0.8078.80 and Edge 156.0.4314.58 on Windows. The
original Edge gameplay pass was recorded before the final fullscreen-rejection
fix; that fix and actual keyboard movement after rejection passed in both final
edge-case runs. No physical webcam was opened.

## Coverage

The Node suite covers the simulation, pose interpreter, camera lifecycle and
renderer contracts. The two browser suites exercise the shipped application in
installed Chrome and Edge. Browser tests start an isolated loopback Flask server;
the gameplay suite also checks the documented standalone HTTP server.

| Area | Cases exercised |
| --- | --- |
| Course and physics | Seed repeatability, both paces, 200 courses at maximum speed, over 15,000 obstacle sections, jump windows, train fronts/sides/tails, duck arches, coin collection, lane limits, irregular frame timing, bounded entities and catch-up |
| Run lifecycle | Initial countdown, pause/resume, natural collisions, restart, pace selection, hidden tabs, synthetic persisted page events, idle rendering and read-only diagnostics |
| Inputs | Arrow keys, A/D, jump, held duck, overlapping duck keys, focus changes, repeated pause keys, actual touch start/cancel, touch-to-keyboard handoff, successful and rejected fullscreen |
| Camera lifecycle | Explicit video-only permission, denial, missing/busy cameras, unsupported browser features, late permission results, cancelled startup, preview/worker/capture timeouts, disconnect/reconnect, frozen video, retired-worker messages/errors, bitmap ownership, bounded inference scheduling |
| Pose controls | Stable calibration, mirrored lanes, hysteresis, jitter, different body scales, jump landing/rearm, duck release, cropped/low-confidence/malformed poses, stale/out-of-order timestamps, tracking loss, recalibration |
| Real model | Blank frame and person-image inference; synthetic 16:9 and portrait video through actual capture, bitmap transfer and the shipped MediaPipe worker/model; missing-model download failure |
| Layout and rendering | Widths from 320 to 3840 CSS pixels, DPR 3, reduced motion, 568×320 fullscreen start/resume/restart actions, camera-overlay aspect ratios, finite geometry, background caching and canvas allocation caps |
| Storage and serving | Unavailable/full storage, invalid best scores, persistence, public access, JavaScript/WASM MIME types, camera/microphone policy, encoded parent-path rejection, byte ranges, all bundled asset sizes/checksums |

The maximum-speed course test simulates about ten hours in aggregate. This is a
deterministic engine stress test, not a ten-hour browser or human play session.
The browser gameplay test follows six generated sections using real keyboard
events, then deliberately collides and restarts without changing game state.

## Reproduced bugs fixed during the review

- Old worker errors could shut down a replacement camera session. Generation
  checks now fence errors as well as messages and pending work.
- A frozen video clock could poll forever. Eight seconds without new frames now
  releases the camera and worker with a retry message.
- Fullscreen success or rejection, and on-screen lane actions, could leave focus
  on a button and block movement keys. Active play now returns focus to the canvas.
- Duck could remain held after tabbing away. Held input sources now release
  independently, including cancellation, blur and overlapping keyboard keys.
- Holding P could repeatedly alternate pause and resume. Repeat events are ignored.
- A page-exit event without a preceding visibility event did not pause the game.
  Page exit now pauses before resource cleanup; restored pages wait for input.
- Skeleton rendering assumed a 4:3 camera. It now shares the video's actual
  intrinsic aspect ratio and containment, including portrait video.
- Short fullscreen overlays clipped actions. Their content is scrollable, and
  real Start, Resume and Restart clicks are covered at 568×320.
- Canvas rounding could exceed the 2.6-million-pixel budget. Backing dimensions
  now round down and are checked in both mocked and real fullscreen rendering.

## Reproduce

From the repository root, with the app dependencies and Playwright installed:

```sh
node --test projects/metro-motion/tests/*.test.mjs
python tools/browser/verify_metro_motion.py --channel chrome --pose-fixture /path/to/person.jpg
python tools/browser/verify_metro_motion.py --channel msedge --pose-fixture /path/to/person.jpg
python tools/browser/verify_metro_motion_edges.py --channel chrome --pose-fixture /path/to/person.jpg
python tools/browser/verify_metro_motion_edges.py --channel msedge --pose-fixture /path/to/person.jpg
```

The image is optional. These runs used Google's public
[MediaPipe pose sample](https://storage.googleapis.com/mediapipe-assets/pose.jpg).
The file is supplied locally to the tests and is not shipped as game artwork.
The standard gameplay suite rejects external page requests. Camera fixtures
replace `getUserMedia`, so none of these commands opens a physical webcam.

Reports and screenshots are in the operating system's temporary directory under
`agent-soccer-metro-motion-qa-{chrome,msedge}` and
`agent-soccer-metro-motion-edges-{chrome,msedge}`. Each report records individual
assertions, failures and browser errors; current runs also record browser version.

## Limits

No automated suite establishes every possible device or user case. Chrome and
Edge share Chromium; Firefox, Safari and physical mobile browsers were not run.
Touch and screen sizes are browser emulations. Persisted page events are dispatched
to verify handlers, not proof of a real back-forward-cache restoration.

Synthetic landmarks validate movement decisions. A still photo through real
inference validates the model pipeline but cannot establish live-person accuracy,
latency across devices, lighting tolerance or performance during actual jumping.
A wide photo fitted into portrait video can be detected by the model while being
too small for the game's standing-calibration requirements; those checks remain
separate. A physical webcam trial should cover full-body framing, calibration,
left/right, a comfortable jump, duck, leaving/returning to view and camera shutdown.
