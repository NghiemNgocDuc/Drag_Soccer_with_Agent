// Stadium soundscape, synthesized locally once and reused. No media downloads.
// Positional audio can connect its listener gain to getOutput() for shared mute.
const SoundManager = {
  _ctx: null,
  _ready: false,
  muted: false,
  _master: null,
  _headroom: null,
  _generation: 0,
  _wave: null,
  _buffers: new Map(),
  _crowdPending: new Map(),
  _synthesisLongestMs: 0,
  _synthesisChunks: 0,
  _effects: new Set(),
  _ambientStartGain: 0.10,
  _ambientSource: null,
  _ambientGain: null,
  _ambientPending: null,
  _lastCheerAt: -Infinity,
  _goalOnly: false,

  attach(ctx, {goalOnly = false} = {}) {
    if (ctx === this._ctx && this._master && this._goalOnly === goalOnly) return;
    const previous = this._ctx;
    this._release();
    this._ctx = ctx || null;
    this._goalOnly = goalOnly;
    this._ready = !!ctx;
    if (ctx) {
      this._master = ctx.createGain();
      this._master.gain.value = this.muted ? 0 : 1;
      this._headroom = ctx.createDynamicsCompressor();
      this._headroom.threshold.value = -8;
      this._headroom.knee.value = 10;
      this._headroom.ratio.value = 6;
      this._headroom.attack.value = 0.003;
      this._headroom.release.value = 0.20;
      this._master.connect(this._headroom).connect(ctx.destination);
      // Build both crowd buffers during page setup, even while audio is
      // gesture-locked. This creates no sources and never resumes the context.
      // Prepare the shorter reaction first so it is ready before ambience.
      if (!this._goalOnly) {
        this._crowdBufferAsync(true).catch(() => {});
        this._crowdBufferAsync(false).catch(() => {});
      }
    }
    if (previous && previous !== ctx && typeof previous.close === 'function') {
      try { Promise.resolve(previous.close()).catch(() => {}); } catch (_) {}
    }
  },

  // The master includes ongoing effects and the page's positional sounds.
  getOutput() { return this._master; },

  resume() {
    // Offline renders use explicit suspension points for verification; they
    // have no browser gesture lock and must retain their scheduled timeline.
    if (typeof OfflineAudioContext !== 'undefined' && this._ctx instanceof OfflineAudioContext) return Promise.resolve();
    if (this._ctx && this._ctx.state === 'suspended' && typeof this._ctx.resume === 'function') {
      try { return Promise.resolve(this._ctx.resume()); } catch (_) {}
    }
    return Promise.resolve();
  },

  toggleMute() {
    this.muted = !this.muted;
    const btn = typeof document !== 'undefined' && document.getElementById('sound-btn');
    if (btn) {
      btn.textContent = this.muted ? 'Sound off' : 'Sound on';
      btn.setAttribute('aria-pressed', String(this.muted));
      btn.setAttribute('aria-label', this.muted ? 'Unmute sound' : 'Mute sound');
    }
    if (this._master && this._ctx) {
      const gain = this._master.gain, now = this._ctx.currentTime;
      gain.cancelScheduledValues(now);
      // A short ramp avoids an audible click when muting during a cheer.
      gain.setValueAtTime(gain.value, now);
      gain.linearRampToValueAtTime(this.muted ? 0 : 1, now + 0.008);
    }
    return this.muted;
  },

  isMuted() { return this.muted; },

  async _ensure() {
    if (!this._ctx) {
      try { this.attach(new (window.AudioContext || window.webkitAudioContext)()); }
      catch (_) { return null; }
    }
    const ctx = this._ctx;
    try { await this.resume(); } catch (_) {}
    return ctx === this._ctx && ctx.state !== 'closed' ? ctx : null;
  },

  _disconnect(node) { try { node.disconnect(); } catch (_) {} },

  _release() {
    this._generation++;
    if (this._ambientSource) {
      this._ambientSource.onended = null;
      try { this._ambientSource.stop(); } catch (_) {}
      this._disconnect(this._ambientSource);
    }
    if (this._ambientGain) this._disconnect(this._ambientGain);
    for (const effect of [...this._effects]) this._finishEffect(effect, true);
    if (this._master) this._disconnect(this._master);
    if (this._headroom) this._disconnect(this._headroom);
    this._headroom = null;
    this._master = this._ambientSource = this._ambientGain = this._ambientPending = null;
    this._buffers.clear();
    this._crowdPending.clear();
    this._synthesisLongestMs = this._synthesisChunks = 0;
    this._lastCheerAt = -Infinity;
  },

  // Release only this sound graph; the page owns its AudioContext.
  dispose() { this._release(); this._ctx = null; this._ready = false; },

  _finishEffect(effect, stop = false) {
    if (!this._effects.delete(effect)) return;
    effect.source.onended = null;
    if (stop) { try { effect.source.stop(); } catch (_) {} }
    this._disconnect(effect.source);
    this._disconnect(effect.gain);
  },

  _play(buffer, volume, rate = 1, kind = 'effect') {
    if (this._goalOnly && kind !== 'goal-whistle') return null;
    const ctx = this._ctx;
    if (!ctx || !buffer || !this._master || this.muted) return null;
    // Reactions may overlap at high replay speeds, but cannot pile up into a
    // distorted roar. A new reaction replaces the oldest third reaction.
    if (kind === 'cheer') {
      const cheers = [...this._effects].filter(effect => effect.kind === 'cheer');
      if (cheers.length >= 2) this._finishEffect(cheers[0], true);
    }
    // A bounded graph also protects against duplicate network goal updates.
    while (this._effects.size >= 6) this._finishEffect(this._effects.values().next().value, true);
    const source = ctx.createBufferSource(), gain = ctx.createGain();
    source.buffer = buffer;
    source.playbackRate.value = rate;
    gain.gain.value = volume;
    source.connect(gain).connect(this._master);
    const effect = {source, gain, kind};
    this._effects.add(effect);
    source.onended = () => this._finishEffect(effect);
    source.start(ctx.currentTime);
    return source;
  },

  _rng(seed) {
    let state = seed >>> 0;
    return () => {
      state ^= state << 13; state ^= state >>> 17; state ^= state << 5;
      return (state >>> 0) / 4294967296;
    };
  },

  _waveTable() {
    if (!this._wave) {
      this._wave = new Float32Array(4096);
      for (let i = 0; i < this._wave.length; i++) this._wave[i] = Math.sin(i * 2 * Math.PI / 4096);
    }
    return this._wave;
  },

  _cache(key, create) {
    if (!this._ctx) return null;
    if (this._buffers.has(key)) return this._buffers.get(key);
    const buffer = create();
    // Public makeImpactBuffer accepts custom recipes, but cache growth is finite.
    if (this._buffers.size >= 12) this._buffers.delete(this._buffers.keys().next().value);
    this._buffers.set(key, buffer);
    return buffer;
  },

  _level(buffer, targetRms, limit = 0.88) {
    let energy = 0, peak = 0;
    const channels = buffer.numberOfChannels, length = buffer.length;
    for (let ch = 0; ch < channels; ch++) {
      const data = buffer.getChannelData(ch);
      let mean = 0;
      for (let i = 0; i < length; i++) mean += data[i];
      mean /= length;
      for (let i = 0; i < length; i++) {
        data[i] -= mean;
        energy += data[i] * data[i];
        peak = Math.max(peak, Math.abs(data[i]));
      }
    }
    const rms = Math.sqrt(energy / (channels * length));
    const scale = Math.min(targetRms / Math.max(rms, 1e-9), limit / Math.max(peak, 1e-9));
    for (let ch = 0; ch < channels; ch++) {
      const data = buffer.getChannelData(ch);
      for (let i = 0; i < length; i++) data[i] *= scale;
    }
    return buffer;
  },

  // Ball shell resonance and a short boot/ground contact, rather than a laser
  // pitch sweep. The noise transient and two damped modes share one envelope.
  makeImpactBuffer(opts = {}) {
    const recipe = {
      f0: Math.max(40, Math.min(500, Number(opts.f0) || 145)),
      f1: Math.max(30, Math.min(500, Number(opts.f1) || 98)),
      dur: Math.max(0.03, Math.min(0.6, Number(opts.dur) || 0.18)),
      strikeDur: Math.max(0.005, Math.min(0.15, Number(opts.strikeDur) || 0.03)),
      strikeGain: Math.max(0, Math.min(2, Number.isFinite(Number(opts.strikeGain)) ? Number(opts.strikeGain) : 0.60)),
    };
    return this._cache('impact:' + JSON.stringify(recipe), () => {
      const sr = Math.min(this._ctx.sampleRate, 44100);
      const out = this._ctx.createBuffer(1, Math.ceil(sr * recipe.dur), sr);
      const data = out.getChannelData(0), random = this._rng(47123);
      let phase = 0, shellPhase = 0, lowNoise = 0;
      for (let i = 0; i < data.length; i++) {
        const t = i / sr;
        const frequency = recipe.f1 + (recipe.f0 - recipe.f1) * Math.exp(-t / 0.026);
        phase += 2 * Math.PI * frequency / sr;
        shellPhase += 2 * Math.PI * recipe.f1 * 2.65 / sr;
        const noise = random() * 2 - 1;
        lowNoise += 0.26 * (noise - lowNoise);
        const contact = lowNoise * recipe.strikeGain * Math.exp(-t / (recipe.strikeDur * 0.24));
        const modes = Math.sin(phase) * Math.exp(-t / 0.034) * 0.78 +
          Math.sin(shellPhase) * Math.exp(-t / 0.017) * 0.15;
        const edge = Math.min(1, t / 0.001) * Math.min(1, (recipe.dur - t) / 0.014);
        data[i] = (modes + contact) * edge;
      }
      return this._level(out, 0.20);
    });
  },

  makeKickBuffer() {
    return this.makeImpactBuffer({f0: 145, f1: 98, dur: 0.18, strikeDur: 0.04, strikeGain: 0.90});
  },

  makeBounceBuffer() {
    return this.makeImpactBuffer({f0: 180, f1: 130, dur: 0.13, strikeDur: 0.022, strikeGain: 0.48});
  },

  // Three air-column tones, a breath-shaped attack and quiet early reflections
  // suggest a pealess referee whistle. This is an original synthesized sound,
  // not a recording or acoustic model of any manufacturer's whistle.
  _whistleBuffer() {
    return this._cache('whistle', () => {
      const sr = 44100, blow = 0.64, duration = 0.90;
      const buffer = this._ctx.createBuffer(2, Math.ceil(sr * duration), sr);
      const direct = new Float32Array(buffer.length), random = this._rng(28719);
      let phaseA = 0, phaseB = 0, phaseC = 0, noise = 0;
      for (let i = 0; i < Math.floor(blow * sr); i++) {
        const t = i / sr, attack = Math.min(1, t / .022), release = Math.min(1, (blow - t) / .11);
        const breath = Math.sin(attack * Math.PI / 2) * release * release;
        const pressure = 1 - .075 * Math.sin(t * 10) + .025 * Math.sin(t * 47);
        const drift = -85 * Math.exp(-t * 80) - 60 * (1 - release) + 7 * Math.sin(t * 32);
        phaseA += 2 * Math.PI * (2960 + drift) / sr;
        phaseB += 2 * Math.PI * (4070 + drift * .72) / sr;
        phaseC += 2 * Math.PI * (4435 + drift * .55) / sr;
        const white = random() * 2 - 1;
        noise += .22 * (white - noise);
        direct[i] = breath * (pressure * (Math.sin(phaseA) * .43 + Math.sin(phaseB) * .24 +
          Math.sin(phaseC) * .16) + (white - noise) * (.028 + .055 * (1 - attack)));
      }
      for (let ch = 0; ch < 2; ch++) {
        const data = buffer.getChannelData(ch);
        const early = Math.round(sr * (ch ? .053 : .041)), late = Math.round(sr * (ch ? .109 : .127));
        for (let i = 0; i < data.length; i++) {
          data[i] = direct[i] + (i >= early ? direct[i - early] * .105 : 0) +
            (i >= late ? direct[i - late] * .045 : 0);
        }
      }
      return this._level(buffer, 0.21, 0.72);
    });
  },

  async whistle(goal = false) {
    if (this.muted || (this._goalOnly && !goal)) return;
    const ctx = await this._ensure();
    if (!ctx || this.muted) return;
    // Repeated event delivery must not layer several piercing whistles.
    if ([...this._effects].some(effect => effect.kind === 'whistle' || effect.kind === 'goal-whistle')) return;
    this._play(this._whistleBuffer(), goal ? 0.055 : 0.22, 1, goal ? 'goal-whistle' : 'whistle');
  },

  // Each group has a different pitch, syllable rhythm, vowel resonance and
  // stereo position. Excitation combines breath and voiced pulses; the vowel
  // resonators are deliberately broad for the sound of distant spectators.
  _addVoices(buffer, cheering, random, first = 0, last = cheering ? 14 : 10) {
    const sr = buffer.sampleRate, left = buffer.getChannelData(0), right = buffer.getChannelData(1);
    const duration = buffer.duration, sine = this._waveTable();
    for (let voice = first; voice < last; voice++) {
      const pan = random() * 1.8 - 0.9;
      const gainL = Math.sqrt((1 - pan) / 2), gainR = Math.sqrt((1 + pan) / 2);
      const fundamental = 95 + random() * 145;
      const cycle = cheering ? 0.56 + random() * 0.45 : 0.8 + random() * 1.8;
      const formants = [460 + random() * 330, 1050 + random() * 650, 2350 + random() * 550];
      const filters = formants.map((f, index) => {
        const radius = Math.exp(-Math.PI * (100 + index * 65) / sr);
        return {b: 2 * radius * Math.cos(2 * Math.PI * f / sr), r2: radius * radius, scale: 1 - radius};
      });
      const [f1, f2, f3] = filters;
      let a1 = 0, a2 = 0, b1 = 0, b2 = 0, c1 = 0, c2 = 0;
      let phase = random(), breathLow = 0, syllable = random();
      let flutterIndex = random() * 4096, slowIndex = flutterIndex;
      const cycleStep = 1 / (cycle * sr), flutterStep = 4096 * 5.1 / sr;
      const slowStep = 4096 / (duration / (voice % 3 + 1) * sr);
      const delay = 23 + voice * 11;
      for (let i = 0; i < left.length; i++) {
        const syllableSine = Math.max(0, sine[(syllable * 2048) & 4095]);
        const squared = syllableSine * syllableSine;
        const envelope = cheering ? Math.sqrt(syllableSine) : squared * squared;
        const slow = 0.60 + 0.40 * sine[slowIndex & 4095];
        phase += (fundamental + 3.5 * sine[flutterIndex & 4095]) / sr;
        if (phase >= 1) phase -= 1;
        const white = random() * 2 - 1;
        breathLow += 0.10 * (white - breathLow);
        // Rounded glottal pulse; less buzzy than a saw/square-wave chorus.
        const pulse = phase < 0.4 ? sine[(phase * 5120) & 4095] : 0;
        const excitation = (pulse - 0.255) * (cheering ? 0.55 : 0.38) + (white - breathLow) * 0.25;
        const va = excitation * f1.scale + f1.b * a1 - f1.r2 * a2;
        const vb = excitation * f2.scale + f2.b * b1 - f2.r2 * b2;
        const vc = excitation * f3.scale + f3.b * c1 - f3.r2 * c2;
        a2 = a1; a1 = va; b2 = b1; b1 = vb; c2 = c1; c1 = vc;
        const value = (va + vb * 0.8 + vc * 0.32) * envelope * slow * (cheering ? 1.45 : 1);
        left[i] += value * gainL;
        // Different propagation delays preserve width without phase inversion.
        if (i + delay < right.length) right[i + delay] += value * gainR;
        syllable += cycleStep; if (syllable >= 1) syllable -= 1;
        flutterIndex += flutterStep; slowIndex += slowStep;
      }
    }
  },

  _addClaps(buffer, cheering, random) {
    const sr = buffer.sampleRate, duration = buffer.duration;
    const count = cheering ? 70 : 24;
    const channels = [buffer.getChannelData(0), buffer.getChannelData(1)];
    for (let clap = 0; clap < count; clap++) {
      const start = cheering ? 0.12 + random() * (duration - 0.65) : random() * (duration - 0.14);
      const pan = random(), power = (cheering ? 0.18 : 0.035) * (0.4 + random() * 0.6);
      const first = Math.floor(start * sr), length = Math.floor(0.11 * sr);
      let low = 0;
      for (let i = 0; i < length; i++) {
        const t = i / sr, white = random() * 2 - 1;
        low += 0.14 * (white - low);
        const value = (white - low) * Math.exp(-t / 0.012) * power;
        for (let ch = 0; ch < 2; ch++) {
          const data = channels[ch];
          const index = first + i + (ch ? 31 : 0);
          if (index < data.length) data[index] += value * (ch ? pan : 1 - pan);
        }
      }
    }
  },

  *_crowdSynthesis(ctx, cheering) {
    // Production consumes one chunk per task; no long first-gesture block.
    const sr = 16000, duration = cheering ? 4.8 : 12;
    const buffer = ctx.createBuffer(2, Math.ceil(sr * duration), sr);
    const random = this._rng(cheering ? 731357 : 315731), sine = this._waveTable();
    for (let ch = 0; ch < 2; ch++) {
      const data = buffer.getChannelData(ch);
      let low = 0, body = 0, air = 0;
      for (let first = 0; first < data.length; first += 24000) {
        const end = Math.min(data.length, first + 24000);
        for (let i = first; i < end; i++) {
          const t = i / sr, white = random() * 2 - 1;
          low += 0.015 * (white - low);
          body += 0.15 * (white - body);
          air += 0.48 * (white - air);
          const swell = 0.76 + 0.16 * sine[(4096 * (t / duration + ch * 1.4 / (2 * Math.PI))) & 4095] +
            0.08 * sine[(4096 * (3 * t / duration + ch / (2 * Math.PI))) & 4095];
          data[i] = ((body - low) * 0.48 + (air - body) * 0.10 + low * 0.38) * swell;
        }
        yield;
      }
    }
    for (let voice = 0; voice < (cheering ? 14 : 10); voice++) {
      this._addVoices(buffer, cheering, random, voice, voice + 1);
      yield;
    }
    this._addClaps(buffer, cheering, random);
    yield;
    for (let ch = 0; ch < 2; ch++) {
      const data = buffer.getChannelData(ch);
      if (cheering) {
        for (let i = 0; i < data.length; i++) {
          const t = i / sr;
          const attack = 1 - Math.exp(-t / 0.10);
          const tail = Math.min(1, Math.max(0, (duration - t) / 1.6));
          data[i] *= attack * tail;
        }
      } else {
        // Blend both sides of the loop seam; no recurring click every 12s.
        const seam = Math.floor(sr * 0.06);
        for (let i = 0; i < seam; i++) {
          const start = data[i], end = data[data.length - 1 - i];
          const blend = (1 - i / seam) * 0.5;
          data[i] = start * (1 - blend) + end * blend;
          data[data.length - 1 - i] = end * (1 - blend) + start * blend;
        }
      }
      yield;
    }
    return this._level(buffer, cheering ? 0.23 : 0.18);
  },

  // Synchronous source-buffer inspection is useful for OfflineAudioContext
  // verification. Gameplay uses the cooperative path below.
  _crowdBuffer(cheering) {
    return this._cache(cheering ? 'cheer' : 'ambient', () => {
      const chunks = this._crowdSynthesis(this._ctx, cheering);
      let chunk = chunks.next();
      while (!chunk.done) chunk = chunks.next();
      return chunk.value;
    });
  },

  _crowdBufferAsync(cheering) {
    const key = cheering ? 'cheer' : 'ambient';
    if (this._buffers.has(key)) return Promise.resolve(this._buffers.get(key));
    if (this._crowdPending.has(key)) return this._crowdPending.get(key);
    const ctx = this._ctx, generation = this._generation;
    if (!ctx) return Promise.resolve(null);
    const pending = (async () => {
      const chunks = this._crowdSynthesis(ctx, cheering);
      while (true) {
        // Only synthesis uses these finite tasks; playback runs entirely in
        // WebAudio and adds no rendering loop or permanent JS timer.
        await new Promise(resolve => setTimeout(resolve, 0));
        if (ctx !== this._ctx || generation !== this._generation) return null;
        const start = performance.now(), chunk = chunks.next();
        this._synthesisLongestMs = Math.max(this._synthesisLongestMs, performance.now() - start);
        this._synthesisChunks++;
        if (chunk.done) return this._cache(key, () => chunk.value);
      }
    })();
    this._crowdPending.set(key, pending);
    pending.finally(() => { if (this._crowdPending.get(key) === pending) this._crowdPending.delete(key); }).catch(() => {});
    return pending;
  },

  crowdAmbient() {
    if (this._goalOnly) return Promise.resolve();
    if (this._ambientSource) return Promise.resolve();
    if (this._ambientPending) return this._ambientPending;
    const ensuring = this._ensure();
    const generation = this._generation;
    const pending = (async () => {
      const ctx = await ensuring;
      // Concurrent gesture handlers and replaced contexts must not add loops.
      if (!ctx || generation !== this._generation || this._ambientSource) return;
      const buffer = await this._crowdBufferAsync(false);
      if (!buffer || ctx !== this._ctx || generation !== this._generation) return;
      const source = ctx.createBufferSource(), gain = ctx.createGain();
      source.buffer = buffer;
      source.loop = true;
      gain.gain.setValueAtTime(0, ctx.currentTime);
      gain.gain.linearRampToValueAtTime(this._ambientStartGain, ctx.currentTime + 0.65);
      source.connect(gain).connect(this._master);
      this._ambientSource = source;
      this._ambientGain = gain;
      source.start(ctx.currentTime);
      // Prepare the reaction in finite background chunks before the first goal.
      this._crowdBufferAsync(true).catch(() => {});
    })();
    this._ambientPending = pending;
    pending.finally(() => { if (this._ambientPending === pending) this._ambientPending = null; }).catch(() => {});
    return pending;
  },

  async crowdCheer() {
    if (this._goalOnly) return;
    if (this.muted) return;
    const ctx = await this._ensure();
    if (!ctx || this.muted) return;
    const requestedAt = ctx.currentTime;
    // Existing pages call goal() and crowdCheer() for the same goal.
    if (requestedAt - this._lastCheerAt < 0.35) return;
    this._lastCheerAt = requestedAt;
    const buffer = await this._crowdBufferAsync(true);
    if (!buffer || ctx !== this._ctx || this.muted) return;
    const now = ctx.currentTime, rate = 0.97 + Math.random() * 0.06;
    this._play(buffer, 0.48, rate, 'cheer');
    if (this._ambientGain) {
      const gain = this._ambientGain.gain;
      gain.cancelScheduledValues(now);
      gain.setValueAtTime(gain.value, now);
      gain.linearRampToValueAtTime(this._ambientStartGain * 0.35, now + 0.16);
      gain.setValueAtTime(this._ambientStartGain * 0.35, now + buffer.duration / rate - 0.9);
      gain.linearRampToValueAtTime(this._ambientStartGain, now + buffer.duration / rate + 0.35);
    }
  },

  // Served 2D views use a quiet goal whistle; legacy stadium views retain cheers.
  goal() { return this._goalOnly ? this.whistle(true) : this.crowdCheer(); },
};
