// SoundManager — all sounds synthesized with Web Audio (zero asset files).
// Attach the page's AudioContext via attach(); call resume() from a user
// gesture (browsers suspend audio until then).
const SoundManager = {
  _ctx: null,
  _ready: false,
  muted: false,
  _ambientStartGain: 0.10,
  _ambientSource: null,
  _ambientGain: null,

  attach(ctx) {
    if (this._ctx && this._ctx !== ctx) {
      try { this._ctx.close(); } catch (_) {}
    }
    this._ctx = ctx;
    this._ready = !!ctx;
  },

  resume() {
    if (this._ctx && this._ctx.state === 'suspended') {
      try { return this._ctx.resume(); } catch (_) {}
    }
    return Promise.resolve();
  },

  toggleMute() {
    this.muted = !this.muted;
    const btn = document.getElementById('sound-btn');
    if (btn) btn.textContent = this.muted ? '\u{1F507}' : '\u{1F50A}';
    if (this._ambientGain) {
      const t = this._ctx ? this._ctx.currentTime : 0;
      const g = this._ambientGain.gain;
      g.cancelScheduledValues(t);
      const v = this.muted ? 0 : this._ambientStartGain;
      g.setValueAtTime(v, t);
      g.value = v;
    }
  },

  isMuted() { return this.muted; },

  async _ensure() {
    if (!this._ctx) {
      try {
        this._ctx = new (window.AudioContext || window.webkitAudioContext)();
      } catch (_) { return; }
    }
    this._ready = true;
    try { await this.resume(); } catch (_) {}
  },

  //  Shared buffer synthesis (used for positional kick/bounce) 
  // Pitch-dropping sine "thump" + lowpassed strike noise.
  makeImpactBuffer(opts) {
    const ctx = this._ctx;
    if (!ctx) return null;
    const f0 = opts.f0, f1 = opts.f1, dur = opts.dur;
    const strikeDur = opts.strikeDur, strikeGain = opts.strikeGain;
    const len = Math.ceil(ctx.sampleRate * dur);
    const out = ctx.createBuffer(1, len, ctx.sampleRate);
    const d = out.getChannelData(0);
    let phase = 0, lpN = 0;
    for (let i = 0; i < len; i++) {
      const t = i / ctx.sampleRate;
      const k = Math.min(t / dur, 1);
      const f = f1 + (f0 - f1) * Math.pow(1 - k, 2.2);
      phase += 2 * Math.PI * f / ctx.sampleRate;
      const env = Math.exp(-k * 4.5);
      let v = Math.sin(phase) * env * 0.9;
      if (t < strikeDur) {
        const sEnv = Math.exp(-t / (strikeDur * 0.30));
        const raw = (Math.random() * 2 - 1) * sEnv * strikeGain;
        lpN += 0.35 * (raw - lpN);
        v += lpN;
      }
      d[i] = v;
    }
    return out;
  },

  makeKickBuffer() {
    return this.makeImpactBuffer({ f0: 150, f1: 46, dur: 0.16, strikeDur: 0.05, strikeGain: 0.55 });
  },

  makeBounceBuffer() {
    return this.makeImpactBuffer({ f0: 205, f1: 82, dur: 0.10, strikeDur: 0.03, strikeGain: 0.40 });
  },

  //  Goal fanfare: bass + 4-note arpeggio with octave sparkle, 2 key variants 
  async goal() {
    if (this.muted) return;
    await this._ensure();
    const c = this._ctx;
    if (!c) return;
    const n = c.currentTime;
    const keys = [[523, 659, 784, 1047], [587, 740, 880, 1175]];
    const K = keys[Math.floor(Math.random() * keys.length)];
    const bass = c.createOscillator();
    bass.type = 'sine';
    bass.frequency.value = 110;
    const bg = c.createGain();
    bg.gain.setValueAtTime(0.0001, n);
    bg.gain.linearRampToValueAtTime(0.26, n + 0.03);
    bg.gain.exponentialRampToValueAtTime(0.001, n + 1.0);
    bass.connect(bg).connect(c.destination);
    bass.start(n); bass.stop(n + 1.0);
    for (let i = 0; i < 4; i++) {
      const t = n + i * 0.14;
      const o = c.createOscillator();
      o.type = 'triangle';
      o.frequency.value = K[i];
      const g = c.createGain();
      g.gain.setValueAtTime(0.0001, t);
      g.gain.linearRampToValueAtTime(0.27, t + 0.04);
      g.gain.exponentialRampToValueAtTime(0.001, t + 0.42);
      const o2 = c.createOscillator();
      o2.type = 'sine';
      o2.frequency.value = K[i] * 2;
      const g2 = c.createGain();
      g2.gain.setValueAtTime(0.0001, t);
      g2.gain.linearRampToValueAtTime(0.10, t + 0.03);
      g2.gain.exponentialRampToValueAtTime(0.001, t + 0.5);
      o.connect(g).connect(c.destination);
      o2.connect(g2).connect(c.destination);
      o.start(t); o.stop(t + 0.42);
      o2.start(t); o2.stop(t + 0.5);
    }
  },

  //  Referee whistle: dual detuned squares + vibrato + pea warble 
  async whistle() {
    if (this.muted) return;
    await this._ensure();
    const c = this._ctx;
    if (!c) return;
    const n = c.currentTime, dur = 0.8;
    const g = c.createGain();
    g.gain.setValueAtTime(0.0001, n);
    g.gain.linearRampToValueAtTime(0.16, n + 0.03);
    g.gain.setValueAtTime(0.16, n + 0.45);
    g.gain.exponentialRampToValueAtTime(0.001, n + dur);
    const lp = c.createBiquadFilter();
    lp.type = 'lowpass'; lp.frequency.value = 5500;
    lp.connect(g).connect(c.destination);
    const mk = (freq) => {
      const o = c.createOscillator();
      o.type = 'square';
      o.frequency.value = freq;
      const vib = c.createOscillator();
      vib.frequency.value = 6.5;
      const vg = c.createGain();
      vg.gain.value = 16;
      vib.connect(vg).connect(o.frequency);
      const warp = c.createOscillator();
      warp.frequency.value = 34;
      const wg = c.createGain();
      wg.gain.value = 0.05;
      warp.connect(wg).connect(g.gain);
      o.connect(lp);
      vib.start(n); vib.stop(n + dur);
      warp.start(n); warp.stop(n + dur);
      o.start(n); o.stop(n + dur);
    };
    mk(2220);
    mk(2360);
  },

  //  Ambient crowd murmur: stereo filtered noise, formant humps, flutter 
  async crowdAmbient() {
    if (this._ambientSource) return;
    await this._ensure();
    const ctx = this._ctx;
    if (!ctx) return;
    const dur = 4;
    const len = ctx.sampleRate * dur;
    const buf = ctx.createBuffer(2, len, ctx.sampleRate);
    for (let ch = 0; ch < 2; ch++) {
      const d = buf.getChannelData(ch);
      let l1 = 0, l2 = 0;
      for (let i = 0; i < len; i++) {
        const w = Math.random() * 2 - 1;
        l1 += 0.16 * (w - l1);
        l2 += 0.07 * (l1 - l2);
        const t = i / ctx.sampleRate;
        const flutter = 0.85 + 0.15 * Math.sin(2 * Math.PI * (ch === 0 ? 0.21 : 0.16) * t + ch * 1.7);
        d[i] = (w * 0.4 + l2 * 0.6) * flutter;
      }
    }
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.loop = true;
    const hp = ctx.createBiquadFilter();
    hp.type = 'highpass'; hp.frequency.value = 180;
    const lp = ctx.createBiquadFilter();
    lp.type = 'lowpass'; lp.frequency.value = 2600;
    const bp1 = ctx.createBiquadFilter();
    bp1.type = 'bandpass'; bp1.frequency.value = 480; bp1.Q.value = 0.9;
    const bp2 = ctx.createBiquadFilter();
    bp2.type = 'bandpass'; bp2.frequency.value = 1150; bp2.Q.value = 0.8;
    const g = ctx.createGain();
    this._ambientStartGain = 0.10;
    g.gain.value = this.muted ? 0 : this._ambientStartGain;
    src.connect(hp);
    hp.connect(bp1);
    hp.connect(bp2);
    bp1.connect(g);
    bp2.connect(g);
    g.connect(ctx.destination);
    src.start();
    this._ambientSource = src;
    this._ambientGain = g;
  },

  //  Crowd cheer: rising bandpass roar + rumble layer, ambient ducks 
  async crowdCheer() {
    if (this.muted) return;
    await this._ensure();
    const c = this._ctx;
    if (!c) return;
    const n = c.currentTime, dur = 2.5;
    if (this._ambientGain) {
      const base = this.muted ? 0 : this._ambientStartGain;
      const g = this._ambientGain.gain;
      g.cancelScheduledValues(n);
      g.setValueAtTime(g.value, n);
      g.linearRampToValueAtTime(base * 0.35, n + 0.15);
      g.linearRampToValueAtTime(base, n + dur + 0.4);
    }
    function mk() {
      const len = Math.ceil(c.sampleRate * dur);
      const buf = c.createBuffer(1, len, c.sampleRate);
      const d = buf.getChannelData(0);
      let lp = 0;
      for (let i = 0; i < len; i++) {
        const t = i / c.sampleRate;
        const k = t / dur;
        const env = Math.pow(Math.sin(Math.min(k, 1) * Math.PI), 0.8);
        const w = Math.random() * 2 - 1;
        lp += 0.06 * (w - lp);
        d[i] = (w * 0.55 + lp * 0.45) * env * (0.82 + 0.18 * Math.sin(2 * Math.PI * 7.3 * t));
      }
      return buf;
    }
    const srcL = c.createBufferSource();
    srcL.buffer = mk();
    const srcR = c.createBufferSource();
    srcR.buffer = mk();
    const sweep = c.createBiquadFilter();
    sweep.type = 'bandpass'; sweep.Q.value = 0.7;
    sweep.frequency.setValueAtTime(650, n);
    sweep.frequency.linearRampToValueAtTime(1500, n + dur * 0.7);
    const g1 = c.createGain();
    g1.gain.value = 0.2;
    srcL.connect(sweep);
    srcR.connect(sweep);
    sweep.connect(g1);
    const pL = c.createStereoPanner ? c.createStereoPanner() : null;
    const pR = c.createStereoPanner ? c.createStereoPanner() : null;
    if (pL && pR) {
      pL.pan.value = -0.45;
      pR.pan.value = 0.45;
      g1.connect(pL).connect(c.destination);
      g1.connect(pR).connect(c.destination);
    } else {
      g1.connect(c.destination);
    }
    const rumble = c.createBiquadFilter();
    rumble.type = 'lowpass'; rumble.frequency.value = 300;
    const g2 = c.createGain();
    g2.gain.value = 0.12;
    const srcR2 = c.createBufferSource();
    srcR2.buffer = mk();
    srcR2.connect(rumble).connect(g2).connect(c.destination);
    srcL.start(n); srcR.start(n); srcR2.start(n);
  },
};
