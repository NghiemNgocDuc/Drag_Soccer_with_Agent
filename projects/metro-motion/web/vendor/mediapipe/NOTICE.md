# MediaPipe assets

This directory vendors the unmodified public MediaPipe Tasks Vision runtime
version **0.10.21**, plus Google's **Pose Landmarker Lite float16 / version 1**.
The game loads these files from its own server, without contacting a CDN.

Upstream runtime: https://github.com/google-ai-edge/mediapipe

Runtime distribution: https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.21/

Model: https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task

Upstream license: `LICENSE` (MediaPipe v0.10.21, Apache 2.0 and bundled notices).
Model documentation: https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker#models

SHA-256 checksums are listed in `checksums.json`. The SIMD and non-SIMD WASM
variants are both retained for the runtime's feature-based selection.
