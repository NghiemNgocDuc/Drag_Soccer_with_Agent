# Motion Lab v1 contract

This is the shared recording, training and browser-inference contract. No trained
human model ships with this feature. Synthetic fixtures are tests, not evidence
of recognition accuracy.

## Recordings

JSON root: `format: "metro-motion-motion-data"`, `version: 1`,
`featureVersion: "mm-body-v1"`, `source: "human"`, `clips: [...]`.
Each clip has `id`, `sessionId`, `participantId` (opaque local IDs), `label`,
`range: [startMs, endMs]`, `baseline`, and `frames: [{t, landmarks}]`.
`t` is a strictly increasing capture time in milliseconds relative to clip start.
Landmarks are 33 objects with finite `x`, `y`, `visibility` and optional `z` and
`presence`. They contain body coordinates, not images. `baseline` uses the fields
from `PoseController.baseline`: shoulderX, shoulderY, hipX, hipY, width, torso,
headY, leftFootY, rightFootY. A recording session uses one standing calibration.
Start a new session after changing participant, camera position or calibration.

Labels, in order: `neutral`, `left`, `right`, `jump`, `duck`. Left/right are the
player's mirrored-screen directions. Ranges select the actual labeled part of a
clip; frames outside the range are context only, never silently labeled neutral.
Review/trim jumps so the selected interval covers takeoff/airborne motion. Neutral
examples must be collected separately. Record one action at a time for v1.

## Features and time

Eight features, in this order, each clamped to [-6, 6]:

1. `-(hipX - baseline.hipX) / baseline.width`
2. `-(shoulderX - baseline.shoulderX) / baseline.width`
3. `(baseline.hipY - hipY) / baseline.torso`
4. `(baseline.headY - noseY) / baseline.torso`
5. `(baseline.leftFootY - leftAnkleY) / baseline.torso`
6. `(baseline.rightFootY - rightAnkleY) / baseline.torso`
7. `(hipY - shoulderY) / baseline.torso`
8. `abs(leftShoulderX - rightShoulderX) / baseline.width`

Coordinates use MediaPipe indices 0, 11, 12, 23, 24, 27, 28; valid full-body
tracking additionally requires knees 25 and 26. Required landmarks need
visibility/presence >= .55 and image x/y inside [.015,.985]. Current torso must
exceed .075 and shoulder width .055; head-to-left-foot height must exceed .32.
Baseline width >= .055, torso >= .075. Invalid data resets temporal history.

Eight equally spaced samples at 15 Hz, oldest first, flatten to 64 values. Linear
interpolation uses capture times. Require a complete 466.667 ms history and no
gap >250 ms. Do not interpolate across invalid frames, clips or sessions. A
training window is labeled only if its final timestamp lies in the clip range.

## Model artifact

The bounded browser model uses a temporal MLP: 64 inputs, one ReLU hidden layer
(default 32; maximum 128), five logits and softmax. PyTorch trains it on CPU/CUDA.
The JSON export is `format: "metro-motion-motion-model"`, `version: 1`,
`featureVersion: "mm-body-v1"`, `labels` in the fixed order, `windowSize: 8`,
`sampleHz: 15`, `inputSize: 64`, `hiddenSize`, `mean` and `scale` (64 values each),
`weights1` ([hidden,64]), `bias1`, `weights2` ([5,hidden]), `bias2`, `threshold`
(.5–.99; default .8), and `metrics` (report summary). Scale is finite and >=1e-6;
all weights and input values are finite. Standardize using training-set-only
mean/scale, then apply the two layers. No executable code or pickle is imported
by the browser. Maximum model file size is 2 MB.

An optional `temperature` in [.25, 5] divides logits before softmax; legacy files
default to 1. It is fitted using validation data only. Current training uses
balanced train-only normalization and augmentation, checkpoint format version 2,
and requires full-frame emitted-control replay evidence before control eligibility.

Models always load into comparison mode. Existing tracking/calibration/loss-pause
checks remain authoritative. Explicit experimental control is permitted only
when the artifact's `metrics.controlEligible` is true; low-confidence, missing,
stale or failed predictions fall back to existing controls. Importing metrics is
not a trust/security boundary or proof of generalization. A model can be removed
at any time. No samples or model files upload automatically.

## Training and evaluation

Split whole sessions (or whole participants when requested), never random windows
from the same recording into train/validation/test. Use separate validation for
checkpoint selection and untouched test data for final metrics. Refuse missing
classes, duplicate/conflicting clips, incompatible schemas and group leakage.
Write per-class precision/recall, confusion matrix, held-out counts, neutral false
actions per minute and action-detection latency estimates, with metric scope
documented. Evaluate existing rules on the same clips as a baseline.

Export comparison models even when gates fail, but require all classes in each
split, adequate held-out evidence and an explicit non-regression gate against
the rule baseline before `controlEligible: true`. Checkpoints must include model,
optimizer, normalization, split/data identity and random state for resumability.
Synthetic data can exercise plumbing only and must never be control-eligible.
