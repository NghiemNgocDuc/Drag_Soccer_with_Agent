# Metro Motion

An original three-lane endless runner controlled by your webcam: step left or
right, make a small jump, and duck while watching the screen. Keyboard and touch
controls are also available. This is a screen-based body-tracking game; a VR
headset is not required. It is a separate project inside the Agent Soccer repo,
with its own game state and local best score.

## Run it

With the existing Flask app running, open **`/metro-motion/`**. The runner is
public and does not require an Agent Soccer account.

You can also serve the standalone project from the repository root:

```sh
python -m http.server 8080 --bind 127.0.0.1 --directory projects/metro-motion/web
```

Then open **http://127.0.0.1:8080/**. Use an HTTP server rather than opening
`index.html` as a file: JavaScript modules, workers and model files must be
served. Webcam access requires **HTTPS or localhost**. A phone visiting another
computer's plain-HTTP LAN address will need HTTPS for its camera.

## Play

Choose **keyboard** to start immediately, or enable the camera, allow permission,
and stand where the preview can see your head, shoulders, hips, knees and feet.
Hold a relaxed upright pose during calibration, then start the run. Stay near
the calibrated position and use small comfortable movements in a clear space.

| Action | Body control | Keyboard |
| --- | --- | --- |
| Change lane | Step left or right; return to center for the middle lane | Left / Right arrows |
| Jump a low barrier | Small jump | Space / Up arrow |
| Duck under an arch | Bend your knees and lower your body | Hold Down arrow |
| Pause | Pause button | P / Pause button |

Trains block a lane, low barriers can be jumped, and overhead arches can be
ducked under. Collect coins and travel as far as possible. The track keeps
generating ahead and speeds up gradually. Every row includes a clear lane;
successive clear lanes remain reachable.

If tracking is lost, the game pauses and asks you to return to view. Resume is
explicit so a recovered camera cannot unexpectedly restart the run. Recalibrate
after moving the camera or changing your standing position. Stop camera releases
the video stream; keyboard play remains available if permission is denied or
tracking cannot initialize.

## Motion Lab: train your own movement model

Choose **Open Motion Lab** in the runner or open `/metro-motion/motion-lab.html`.
Explicitly record three-second landmark clips, review/trim their labels, and save
them locally. Export your dataset, train the small classifier with PyTorch on
Unity or a local CPU, then import `model.json` in the runner to compare predictions.
The [training guide](training/README.md) includes the Unity Slurm job, checkpoint
resume, evaluation requirements and complete commands. No trained personal model
is included yet; real recordings are needed to measure an improvement.

## Privacy and implementation

Camera frames stay in the browser. Gameplay does not record movements. Motion Lab
saves only explicitly recorded and reviewed body coordinates; no video is saved
or uploaded, and the microphone is not requested. MediaPipe Pose Landmarker, its WebAssembly
runtime and its lite pose model are vendored under `web/vendor/mediapipe/` so
camera startup does not contact a third-party CDN. Refer to that directory's
license and provenance files for upstream artifacts.

Pose inference runs in a dedicated worker and accepts one frame at a time.
Normalized body positions are smoothed and calibrated before becoming lane,
jump or duck input. Rendering uses a separate time-based loop; the game pauses
when the tab is hidden. The runner's artwork, course and characters are original;
no Subway Surfers assets or branding are included.

## Verify

See [the verification report](TESTING.md) for coverage, fixes and testing limits.

Run the project's Node tests and the isolated browser verifier from the repo
root:

```sh
node --test projects/metro-motion/tests/*.test.mjs
python -m unittest discover -s projects/metro-motion/training/tests -v
python tools/browser/verify_motion_lab.py --channel chrome
python tools/browser/verify_metro_motion.py
python tools/browser/verify_metro_motion_edges.py --channel chrome
python tools/browser/verify_metro_motion_edges.py --channel msedge
```

The browser verifier starts the Flask app in development isolation on loopback,
checks desktop/mobile layouts and keyboard gameplay, and exercises permission
denial, late permission cleanup, calibration and tracking loss using a synthetic
canvas stream with deterministic landmark fixtures. It never opens a physical
camera. These mocked camera checks validate input and lifecycle behavior, not
real-person tracking accuracy.

A separate test loads the **actual vendored MediaPipe worker and model** and
performs blank-frame inference. To extend this smoke to a known person image:

```sh
python tools/browser/verify_metro_motion.py --pose-fixture /path/to/person.jpg
python tools/browser/verify_metro_motion_edges.py --channel chrome --pose-fixture /path/to/person.jpg
```

The edge-case verifier adds held/repeated keys, touch cancellation, focus changes,
fullscreen (including short landscape screens), storage failures, mode switches,
camera retries, actual missing-model failure, route isolation and asset checksums.
Its optional image runs through synthetic widescreen and portrait video streams
and the real capture/worker/model pipeline. Dispatched persisted page events test
lifecycle handlers; they do not claim actual browser back-forward-cache coverage.

Reports and desktop/mobile screenshots are written to the system temporary
directory under `agent-soccer-metro-motion-qa*` and
`agent-soccer-metro-motion-edges-{chrome,msedge}`. Real-world webcam responsiveness
and recognition still depend on lighting, framing, clothing, camera frame rate
and the device. Automated synthetic poses and one sample image do not establish
accuracy across users or cameras.

## Research used

- [Google's Pose Landmarker web guide](https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker/web_js)
  describes the video API and recommends a worker because inference is
  synchronous and can block the UI thread.
- [MDN: getUserMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia)
  documents secure contexts, permission errors and requests that may remain
  unresolved until the user responds.
- [MDN: stopping media tracks](https://developer.mozilla.org/en-US/docs/Web/API/MediaStreamTrack/stop)
  explains releasing each track and clearing the video element's source.
- [MDN: requestAnimationFrame](https://developer.mozilla.org/en-US/docs/Web/API/Window/requestAnimationFrame)
  explains timestamp-based animation and background-tab behavior.
