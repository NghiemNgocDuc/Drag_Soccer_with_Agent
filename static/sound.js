const SoundManager = {
  _ctx: null,
  _ready: false,
  muted: false,

  toggleMute() {
    this.muted = !this.muted;
    const btn = document.getElementById('sound-btn');
    if (btn) btn.textContent = this.muted ? '\u{1F507}' : '\u{1F50A}';
  },

  isMuted() { return this.muted; },

  async _ensure() {
    if (this._ready) return;
    try {
      this._ctx = new (window.AudioContext || window.webkitAudioContext)();
      if (this._ctx.state === 'suspended') await this._ctx.resume();
      this._ready = true;
    } catch (_) {}
  },

  _noise(ctx, dur) {
    const len = ctx.sampleRate * dur;
    const buf = ctx.createBuffer(1, len, ctx.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
    return buf;
  },

  async kick() {},

  async goal() {
    if (this.muted) return;
    await this._ensure();
    if (!this._ctx) return;
    const c = this._ctx, n = c.currentTime;
    for (let i = 0; i < 3; i++) {
      const f = [523, 659, 784][i];
      const t = n + i * 0.16;
      const o = c.createOscillator();
      o.type = 'triangle';
      o.frequency.value = f;
      const g = c.createGain();
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(0.35, t + 0.04);
      g.gain.exponentialRampToValueAtTime(0.001, t + 0.35);
      o.connect(g).connect(c.destination);
      o.start(t); o.stop(t + 0.35);
    }
  },

  async whistle() {
    if (this.muted) return;
    await this._ensure();
    if (!this._ctx) return;
    const c = this._ctx, n = c.currentTime;
    const o = c.createOscillator();
    o.type = 'sine';
    o.frequency.value = 800;
    const fm = c.createOscillator();
    fm.frequency.value = 5;
    const fmg = c.createGain();
    fmg.gain.value = 30;
    fm.connect(fmg).connect(o.frequency);
    fm.start(n); fm.stop(n + 0.6);
    const g = c.createGain();
    g.gain.setValueAtTime(0, n);
    g.gain.linearRampToValueAtTime(0.4, n + 0.06);
    g.gain.setValueAtTime(0.4, n + 0.35);
    g.gain.exponentialRampToValueAtTime(0.001, n + 0.6);
    o.connect(g).connect(c.destination);
    o.start(n); o.stop(n + 0.6);
  },

  async bounce() {},
};
