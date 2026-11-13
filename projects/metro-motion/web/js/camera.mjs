const noop = () => {};
const abortError = () => Object.assign(new Error('Camera startup was cancelled.'), { name: 'AbortError' });
const stopTracks = (stream) => stream?.getTracks().forEach((track) => track.stop());

export class CameraTracker {
  constructor({ video, onPose = noop, onStatus = noop, onError = noop, dependencies = {} } = {}) {
    if (!video) throw new TypeError('A preview video is required.');
    this.video = video;
    this.onPose = onPose;
    this.onStatus = onStatus;
    this.onError = onError;
    this.env = {
      mediaDevices: globalThis.navigator?.mediaDevices,
      Worker: globalThis.Worker,
      createImageBitmap: globalThis.createImageBitmap?.bind(globalThis),
      setTimeout: globalThis.setTimeout.bind(globalThis),
      clearTimeout: globalThis.clearTimeout.bind(globalThis),
      now: () => performance.now(),
      secure: globalThis.isSecureContext,
      ...dependencies,
    };
    this.generation = 0;
    this.pendingWaits = new Set();
    this.running = false;
    this.starting = null;
    this.worker = null;
    this.stream = null;
    this.timer = null;
    this.watchdog = null;
    this.inFlight = false;
    this.sequence = 0;
    this.lastVideoTime = -1;
  }

  async start() {
    if (this.running) return true;
    if (this.starting) return this.starting;
    const generation = ++this.generation;
    const operation = this._start(generation);
    this.starting = operation;
    try { return await operation; }
    finally { if (this.starting === operation) this.starting = null; }
  }

  async _start(generation) {
    try {
      if (!this.env.secure || !this.env.mediaDevices?.getUserMedia) throw new Error('Camera play needs HTTPS or localhost and a browser with webcam support.');
      if (!this.env.Worker || !this.env.createImageBitmap) throw new Error('This browser cannot run body tracking. Try a recent Chrome or Edge, or use keyboard mode.');
      this.onStatus({ state: 'requesting', message: 'Allow the camera, then stand where your whole body is visible.' });
      // getUserMedia has no cancellation API: dispose any stream that arrives after stop/timeout.
      const request = this.env.mediaDevices.getUserMedia({
        audio: false, video: { facingMode: 'user', width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 30, max: 30 } },
      }).then((stream) => {
        if (generation !== this.generation) { stopTracks(stream); throw abortError(); }
        return stream;
      });
      const stream = await this._wait(request, 30000, 'Camera permission took too long. Try again when ready.');
      if (generation !== this.generation) { stopTracks(stream); throw abortError(); }
      this.stream = stream;
      for (const track of this.stream.getTracks()) {
        track.onended = () => { if (generation === this.generation) this._fail(new Error('The camera disconnected. Reconnect it and enable the camera again.')); };
      }
      this.video.muted = true;
      this.video.playsInline = true;
      this.video.srcObject = this.stream;
      await this._wait(Promise.resolve(this.video.play()), 10000, 'The camera preview could not start.');
      if (generation !== this.generation) throw abortError();
      const worker = this.worker = new this.env.Worker(new URL('./pose-worker.mjs', import.meta.url));
      const ready = new Promise((resolve, reject) => {
        worker.onmessage = ({ data }) => {
          if (generation !== this.generation) return;
          if (data.type === 'ready') resolve();
          else if (data.type === 'status') this.onStatus({ state: data.state, message: data.message });
          else if (data.type === 'error') {
            const error = new Error(data.message || 'Body tracking failed.');
            if (!this.running) reject(error); else this._fail(error);
          } else if (data.type === 'pose' && this.running && this.inFlight && data.id === this.sequence) {
            this.env.clearTimeout(this.watchdog);
            this.watchdog = null;
            this.inFlight = false;
            this.onPose(data.landmarks, data.timestamp);
            this._schedule(generation, Math.max(0, 1000 / 15 - (this.env.now() - this.frameStarted)));
          }
        };
        worker.onerror = (event) => {
          event.preventDefault?.();
          if (generation !== this.generation) return;
          const error = new Error('Body tracking could not start. Reload or choose keyboard mode.');
          if (!this.running) reject(error); else this._fail(error);
        };
      });
      worker.postMessage({ type: 'init' });
      await this._wait(ready, 45000, 'Body tracking took too long to load. Check the connection and retry.');
      if (generation !== this.generation) throw abortError();
      this.running = true;
      this.lastVideoTime = -1;
      this.lastVideoFrameAt = this.env.now();
      this.onStatus({ state: 'ready', message: 'Camera ready. Stand upright to calibrate.' });
      this._schedule(generation, 0);
      return true;
    } catch (error) {
      if (generation === this.generation) this._fail(error);
      return false;
    }
  }

  _wait(promise, timeout, message) {
    return new Promise((resolve, reject) => {
      let settled = false;
      const finish = (callback, value) => {
        if (settled) return;
        settled = true;
        this.env.clearTimeout(timer);
        this.pendingWaits.delete(cancel);
        callback(value);
      };
      const cancel = () => finish(reject, abortError());
      const timer = this.env.setTimeout(() => finish(reject, new Error(message)), timeout);
      this.pendingWaits.add(cancel);
      Promise.resolve(promise).then((value) => finish(resolve, value), (error) => finish(reject, error));
    });
  }

  _schedule(generation, delay) {
    if (generation !== this.generation || !this.running) return;
    this.env.clearTimeout(this.timer);
    this.timer = this.env.setTimeout(() => { this.timer = null; this._frame(generation); }, delay);
  }

  async _frame(generation) {
    if (!this.running || generation !== this.generation || this.inFlight) return;
    if (this.video.readyState < 2 || this.video.currentTime === this.lastVideoTime) {
      if (this.env.now() - this.lastVideoFrameAt >= 8000) {
        this._fail(new Error('The camera stopped providing frames. Enable it again or use keyboard mode.'));
        return;
      }
      this._schedule(generation, 50);
      return;
    }
    this.inFlight = true;
    this.lastVideoTime = this.video.currentTime;
    this.frameStarted = this.env.now();
    this.lastVideoFrameAt = this.frameStarted;
    const id = ++this.sequence;
    this.watchdog = this.env.setTimeout(() => {
      if (generation === this.generation && this.inFlight) this._fail(new Error('Body tracking stopped responding. Enable the camera again or use keyboard mode.'));
    }, 8000);
    let bitmap;
    try {
      bitmap = await this.env.createImageBitmap(this.video);
      if (generation !== this.generation || !this.running) { bitmap.close(); return; }
      this.worker.postMessage({ type: 'frame', id, timestamp: this.frameStarted, bitmap }, [bitmap]);
      bitmap = null; // ownership transferred to the worker, which closes it in finally.
    } catch (error) {
      bitmap?.close();
      if (generation === this.generation) this._fail(error);
    }
  }

  _fail(error) {
    this.stop();
    if (error.name === 'AbortError') return;
    const messages = {
      NotAllowedError: 'Camera access was not allowed. Enable it in your browser, or use keyboard mode.',
      NotFoundError: 'No camera was found. Connect one, or use keyboard mode.',
      NotReadableError: 'The camera is busy in another app. Close that app and try again.',
    };
    const friendly = new Error(messages[error.name] || error.message || 'The camera could not start.');
    this.onStatus({ state: 'error', message: friendly.message });
    this.onError(friendly);
  }

  stop() {
    ++this.generation;
    this.running = false;
    this.starting = null;
    this.env.clearTimeout(this.timer);
    this.env.clearTimeout(this.watchdog);
    this.timer = this.watchdog = null;
    for (const cancel of [...this.pendingWaits]) cancel();
    this.worker?.terminate();
    this.worker = null;
    if (this.stream) {
      for (const track of this.stream.getTracks()) track.onended = null;
      stopTracks(this.stream);
    }
    this.stream = null;
    this.video.pause();
    this.video.srcObject = null;
    this.inFlight = false;
    this.onStatus({ state: 'idle', message: 'Camera off.' });
  }
}
