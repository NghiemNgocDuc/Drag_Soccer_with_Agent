// Coalesce visual changes and animation callbacks into one draw per browser
// frame. The animation clock pauses while the canvas is hidden or offscreen.
export function createRenderLoop(options) {
  const doc = options.document ?? globalThis.document;
  const win = options.window ?? globalThis.window;
  const element = options.element;
  const raf = options.raf ?? (callback => globalThis.requestAnimationFrame(callback));
  const caf = options.caf ?? (id => globalThis.cancelAnimationFrame(id));
  const wallNow = options.now ?? (() => globalThis.performance.now());
  const reportError = options.onError ?? (error => {
    if (globalThis.reportError) globalThis.reportError(error);
    else globalThis.setTimeout(() => { throw error; }, 0);
  });
  const callbacks = new Map();
  let nextId = 1, frameId = null, frames = 0;
  let dirty = false, running = false, disposed = false;
  let suspended = false, pausedAt = null, pausedDuration = 0, lastTime = null;

  function visible() {
    if (doc?.hidden || doc?.visibilityState === 'hidden') return false;
    if (element?.getBoundingClientRect) {
      const rect = element.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0 || rect.right <= 0 || rect.bottom <= 0 ||
          rect.left >= (win?.innerWidth ?? Infinity) ||
          rect.top >= (win?.innerHeight ?? Infinity)) return false;
    }
    return !options.isVisible || options.isVisible();
  }

  function now() {
    return (pausedAt ?? wallNow()) - pausedDuration;
  }

  function pause() {
    if (!suspended) {
      pausedAt = wallNow();
      suspended = true;
    }
    if (frameId !== null) caf(frameId);
    frameId = null;
    lastTime = null;
  }

  function schedule() {
    if (disposed || running || frameId !== null || (!dirty && callbacks.size === 0)) return;
    if (!visible()) { pause(); return; }
    if (suspended) {
      pausedDuration += wallNow() - pausedAt;
      pausedAt = null;
      suspended = false;
    }
    frameId = raf(frame);
  }

  function frame(timestamp) {
    frameId = null;
    if (disposed) return;
    if (!visible()) { pause(); return; }
    const time = timestamp - pausedDuration;
    const dtMs = lastTime === null ? 1000 / 60 : Math.min(100, Math.max(0, time - lastTime));
    lastTime = time;
    running = true;
    const shouldDraw = dirty || callbacks.size > 0;
    dirty = false;
    try {
      // New callbacks belong to the next frame. Retain the map during this
      // batch so one callback can cancel another before it runs.
      for (const [id, callback] of Array.from(callbacks)) {
        if (!callbacks.has(id)) continue;
        callbacks.delete(id);
        try { callback(time); } catch (error) { reportError(error); }
      }
      if (shouldDraw) {
        options.render(time, dtMs);
        frames++;
      }
    } catch (error) {
      reportError(error);
    } finally {
      running = false;
      // Idle time is not an animation step. Start the next visual transition
      // with one normal frame instead of a large camera/weather jump.
      if (!dirty && callbacks.size === 0) lastTime = null;
      schedule();
    }
  }

  function invalidate() {
    if (disposed) return;
    dirty = true;
    schedule();
  }

  function resume() {
    if (disposed) return;
    if (!visible()) { pause(); return; }
    if (suspended) {
      pausedDuration += wallNow() - pausedAt;
      pausedAt = null;
      suspended = false;
      dirty = true;
    }
    schedule();
  }

  const listenerOptions = { passive: true, capture: true };
  doc?.addEventListener?.('visibilitychange', resume);
  win?.addEventListener?.('scroll', resume, listenerOptions);
  win?.addEventListener?.('resize', resume);
  const Observer = options.IntersectionObserver ?? globalThis.IntersectionObserver;
  const observer = element && Observer ? new Observer(resume) : null;
  observer?.observe(element);
  if (!visible()) pause();

  return {
    invalidate,
    now,
    resume,
    requestAnimationFrame(callback) {
      if (disposed) return 0;
      const id = nextId++;
      callbacks.set(id, callback);
      schedule();
      return id;
    },
    cancelAnimationFrame(id) {
      callbacks.delete(id);
      if (!dirty && callbacks.size === 0 && frameId !== null) {
        caf(frameId);
        frameId = null;
      }
    },
    stats() {
      return { frames, pendingFrame: frameId !== null, pendingCallbacks: callbacks.size,
        dirty, suspended, visible: visible() };
    },
    dispose() {
      disposed = true;
      if (frameId !== null) caf(frameId);
      frameId = null;
      callbacks.clear();
      observer?.disconnect();
      doc?.removeEventListener?.('visibilitychange', resume);
      win?.removeEventListener?.('scroll', resume, listenerOptions);
      win?.removeEventListener?.('resize', resume);
    },
  };
}

export const RENDER_QUALITY_KEY = 'agent-soccer-render-quality';
const RESOLUTIONS = { '1080p': 1920, '1440p': 2560, '4k': 3840 };

export function computeRenderSize(cssWidth, cssHeight, mode, devicePixelRatio = 1, limits = {}) {
  const width = Math.max(1, Number(cssWidth) || 1);
  const height = Math.max(1, Number(cssHeight) || 1);
  const nativeScale = Math.max(1, Math.min(2, Number(devicePixelRatio) || 1));
  const target = Object.hasOwn(RESOLUTIONS, mode) ? RESOLUTIONS[mode] : 3840;
  const wantedScale = mode === 'auto' ? nativeScale : target / Math.max(width, height);
  const maxWidth = Math.min(3840, limits.maxWidth ?? 3840);
  const maxHeight = Math.min(3840, limits.maxHeight ?? 3840);
  const maxPixels = Math.min(3840 * 2160, limits.maxPixels ?? 3840 * 2160);
  const scale = Math.min(wantedScale, maxWidth / width, maxHeight / height,
    Math.sqrt(maxPixels / (width * height)));
  return { width: Math.max(1, Math.floor(width * scale)),
    height: Math.max(1, Math.floor(height * scale)),
    cssWidth: width, cssHeight: height, pixelRatio: scale,
    limited: scale < wantedScale - 1e-6 };
}

export function createRenderQuality(renderer, camera, container, options = {}) {
  const win = options.window ?? globalThis.window;
  let storage = options.storage;
  if (!storage) {
    try { storage = globalThis.localStorage; } catch (_) { /* private browser */ }
  }
  let mode = '4k';
  try {
    const saved = storage?.getItem(RENDER_QUALITY_KEY);
    if (saved === 'auto' || Object.hasOwn(RESOLUTIONS, saved)) mode = saved;
  } catch (_) { /* graphics preferences are optional */ }

  const limits = {};
  try {
    const gl = renderer.getContext();
    const viewport = gl.getParameter(gl.MAX_VIEWPORT_DIMS);
    const maximum = Math.min(gl.getParameter(gl.MAX_RENDERBUFFER_SIZE),
      renderer.capabilities?.maxTextureSize ?? Infinity);
    if (maximum > 0) {
      limits.maxWidth = Math.min(maximum, viewport?.[0] || maximum);
      limits.maxHeight = Math.min(maximum, viewport?.[1] || maximum);
    }
  } catch (_) { /* use the bounded 4K defaults */ }

  // Keep CSS dimensions independent from the actual drawing buffer. A 4K
  // image can be downsampled cleanly into a smaller responsive game panel.
  const canvas = renderer.domElement;
  canvas.style.width = '100%';
  canvas.style.height = '100%';
  canvas.style.display = 'block';
  renderer.setPixelRatio(1);
  let current = null;

  function resize() {
    const rect = container.getBoundingClientRect();
    const width = rect.width || container.clientWidth;
    const height = rect.height || container.clientHeight;
    if (!(width > 0 && height > 0)) return false;
    const next = computeRenderSize(width, height, mode, win?.devicePixelRatio ?? 1, limits);
    const aspect = width / height;
    const aspectChanged = camera.aspect !== aspect;
    if (aspectChanged) {
      camera.aspect = aspect;
      camera.updateProjectionMatrix();
    }
    const resized = canvas.width !== next.width || canvas.height !== next.height;
    if (resized) renderer.setSize(next.width, next.height, false);
    const changed = resized || aspectChanged || current?.mode !== mode;
    current = { mode, ...next };
    if (changed) options.onChange?.(current);
    return changed;
  }

  function setQuality(nextMode) {
    if (nextMode !== 'auto' && !Object.hasOwn(RESOLUTIONS, nextMode)) return false;
    mode = nextMode;
    try { storage?.setItem(RENDER_QUALITY_KEY, mode); } catch (_) { /* private browser */ }
    resize();
    return true;
  }

  const ResizeObserverClass = options.ResizeObserver ?? globalThis.ResizeObserver;
  const observer = ResizeObserverClass ? new ResizeObserverClass(resize) : null;
  observer?.observe(container);
  win?.addEventListener?.('resize', resize);
  function restore() {
    resize();
    options.onChange?.(current);
  }
  canvas.addEventListener?.('webglcontextrestored', restore);
  resize();
  return {
    resize,
    setQuality,
    stats() { return current ? { ...current } : { mode, width: canvas.width, height: canvas.height }; },
    dispose() {
      observer?.disconnect();
      win?.removeEventListener?.('resize', resize);
      canvas.removeEventListener?.('webglcontextrestored', restore);
    },
  };
}
