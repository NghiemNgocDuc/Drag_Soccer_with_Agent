// A classic Worker intentionally: MediaPipe's unmodified WASM loader uses importScripts.
// The ES module runtime is dynamically imported, and all inference remains off the UI thread.
let detector = null;
let loading = false;
let previousTimestamp = -1;

self.onmessage = async ({ data }) => {
  if (data.type === 'init') {
    if (loading || detector) return;
    loading = true;
    try {
      self.postMessage({ type: 'status', state: 'loading', message: 'Loading body tracking on this device…' });
      const assets = new URL('../vendor/mediapipe/', self.location.href);
      const { FilesetResolver, PoseLandmarker } = await import(new URL('vision_bundle.mjs', assets).href);
      const fileset = await FilesetResolver.forVisionTasks(new URL('wasm/', assets).href.replace(/\/$/, ''));
      detector = await PoseLandmarker.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: new URL('pose_landmarker_lite.task', assets).href, delegate: 'CPU' },
        canvas: new OffscreenCanvas(640, 480),
        runningMode: 'VIDEO', numPoses: 1, outputSegmentationMasks: false,
        minPoseDetectionConfidence: 0.55, minPosePresenceConfidence: 0.55, minTrackingConfidence: 0.55,
      });
      self.postMessage({ type: 'ready' });
    } catch (error) {
      detector?.close();
      detector = null;
      self.postMessage({ type: 'error', message: error?.message || 'Body tracking could not load.' });
    } finally { loading = false; }
    return;
  }
  if (data.type !== 'frame') return;
  const { bitmap, id, timestamp } = data;
  try {
    if (!detector) throw new Error('Body tracking is not ready.');
    if (!Number.isFinite(timestamp) || timestamp <= previousTimestamp) throw new Error('Camera timestamps must increase.');
    previousTimestamp = timestamp;
    const result = detector.detectForVideo(bitmap, timestamp);
    self.postMessage({ type: 'pose', id, timestamp, landmarks: result.landmarks[0] || [] });
  } catch (error) {
    self.postMessage({ type: 'error', message: error?.message || 'Body tracking stopped.' });
  } finally { bitmap?.close(); }
};
