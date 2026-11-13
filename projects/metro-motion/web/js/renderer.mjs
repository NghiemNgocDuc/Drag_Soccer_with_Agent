/* Original procedural artwork. Canvas is drawn only when the application asks. */
import { RULES } from './core.mjs';

const COLORS = {
  ink: '#302233', plum: '#4b2945', dark: '#251d30', cream: '#fff0d4',
  coral: '#de6d59', gold: '#ffc569', teal: '#3d8c8c', rail: '#e9d6b9',
};
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

function polygon(ctx, points, fill, stroke, width = 1) {
  ctx.beginPath();
  points.forEach((point, index) => index ? ctx.lineTo(point.x, point.y) : ctx.moveTo(point.x, point.y));
  ctx.closePath();
  if (fill) { ctx.fillStyle = fill; ctx.fill(); }
  if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = width; ctx.stroke(); }
}

function line(ctx, points, color, width) {
  ctx.beginPath();
  points.forEach((point, index) => index ? ctx.lineTo(point.x, point.y) : ctx.moveTo(point.x, point.y));
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.stroke();
}

function ellipse(ctx, x, y, rx, ry, fill) {
  ctx.beginPath();
  ctx.ellipse(x, y, Math.max(0.01, rx), Math.max(0.01, ry), 0, 0, Math.PI * 2);
  ctx.fillStyle = fill;
  ctx.fill();
}

export class RunnerRenderer {
  constructor(canvas, { reducedMotion = false } = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d', { alpha: false });
    if (!this.ctx) throw new Error('This browser cannot create a Canvas 2D game.');
    this.reducedMotion = reducedMotion;
    this.background = canvas.ownerDocument.createElement('canvas');
    this.width = 0;
    this.height = 0;
    this.destroyed = false;
    this.resize();
  }

  resize() {
    if (this.destroyed) return;
    const rect = this.canvas.getBoundingClientRect();
    const width = Math.max(1, Math.round(rect.width || this.canvas.clientWidth || 960));
    const height = Math.max(1, Math.round(rect.height || this.canvas.clientHeight || 600));
    const dpr = Math.min(2, globalThis.devicePixelRatio || 1, 3072 / Math.max(width, height),
      Math.sqrt(2600000 / (width * height)));
    if (width === this.width && height === this.height && dpr === this.dpr) return;
    this.width = width;
    this.height = height;
    this.dpr = dpr;
    // Round down: independently rounding both dimensions up can exceed the
    // pixel budget (for example, a 1440×900 view at DPR 2).
    this.canvas.width = Math.max(1, Math.floor(width * dpr));
    this.canvas.height = Math.max(1, Math.floor(height * dpr));
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.horizon = height * 0.28;
    this.ground = height * 0.87;
    this.lanePixels = Math.min(width * 0.216, height * 0.27);
    this.unit = this.lanePixels / RULES.laneWidth;
    this.makeBackground();
  }

  project(x, z, elevation = 0) {
    const scale = 15 / Math.max(4, 15 + z);
    return {
      x: this.width / 2 + x * this.unit * scale,
      y: this.horizon + (this.ground - this.horizon) * scale - elevation * this.unit * scale,
      scale,
    };
  }

  makeBackground() {
    const { background, width: w, height: h, dpr } = this;
    background.width = this.canvas.width;
    background.height = this.canvas.height;
    const c = background.getContext('2d', { alpha: false });
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    const sky = c.createLinearGradient(0, 0, 0, h * 0.57);
    sky.addColorStop(0, '#a56b77');
    sky.addColorStop(0.48, '#f0b38c');
    sky.addColorStop(1, '#ffe1a3');
    c.fillStyle = sky;
    c.fillRect(0, 0, w, h);
    // A broad evening sun and its static haze are rendered once per resize.
    const sunX = w * 0.64, sunY = h * 0.195, sunRadius = Math.min(w * 0.095, h * 0.13);
    const halo = c.createRadialGradient(sunX, sunY, sunRadius * 0.45, sunX, sunY, sunRadius * 2.8);
    halo.addColorStop(0, '#ffe3a999');
    halo.addColorStop(1, '#ffe3a900');
    c.fillStyle = halo;
    c.fillRect(0, 0, w, h * 0.5);
    ellipse(c, sunX, sunY, sunRadius, sunRadius, '#ffdfa1');
    c.fillStyle = '#ffe3bd55';
    for (let i = 0; i < 6; i++) {
      const x = ((i * 173 + 81) % 811) / 811 * w;
      c.fillRect(x, h * (0.07 + (i % 3) * 0.055), w * (0.065 + (i % 2) * 0.06), 2 + h * 0.004);
    }
    // Two skyline layers, deterministic at every resolution.
    for (let layer = 0; layer < 2; layer++) {
      const count = 27;
      const base = this.horizon + h * (0.055 + layer * 0.025);
      c.fillStyle = layer ? '#62435a' : '#936070';
      for (let i = -1; i < count; i++) {
        const bw = w / count * (0.8 + ((i + 31) % 3) * 0.14);
        const bh = h * (0.035 + ((i * 7 + layer * 3 + 121) % 11) * 0.007);
        const x = i * w / count + layer * 11;
        c.fillRect(x, base - bh, bw, bh);
        if ((i + 30) % 4 === 0) c.fillRect(x + bw * 0.45, base - bh - 12, 2, 12);
        if (layer) {
          c.fillStyle = '#dcae8c65';
          for (let col = 1; col <= 3; col++) {
            c.fillRect(x + bw * col / 5, base - bh + 8, Math.max(1, bw / 12), 3);
          }
          c.fillStyle = '#62435a';
        }
      }
    }
    const ground = c.createLinearGradient(0, this.horizon, 0, h);
    ground.addColorStop(0, '#92736e');
    ground.addColorStop(0.35, '#6a4b58');
    ground.addColorStop(1, '#342737');
    c.fillStyle = ground;
    c.fillRect(0, this.horizon + h * 0.074, w, h);
  }

  groundQuad(x1, x2, z1, z2, color) {
    polygon(this.ctx, [this.project(x1, z1), this.project(x2, z1),
      this.project(x2, z2), this.project(x1, z2)], color);
  }

  face(x1, x2, z, bottom, top, fill) {
    polygon(this.ctx, [this.project(x1, z, bottom), this.project(x2, z, bottom),
      this.project(x2, z, top), this.project(x1, z, top)], fill);
  }

  box(x, z, width, height, length, front, side, roof, bottom = 0) {
    const left = x - width / 2, right = x + width / 2, back = z + length;
    polygon(this.ctx, [this.project(left, z, bottom), this.project(left, back, bottom),
      this.project(left, back, height + bottom), this.project(left, z, height + bottom)], side);
    polygon(this.ctx, [this.project(right, z, bottom), this.project(right, back, bottom),
      this.project(right, back, height + bottom), this.project(right, z, height + bottom)], side);
    polygon(this.ctx, [this.project(left, z, height + bottom), this.project(right, z, height + bottom),
      this.project(right, back, height + bottom), this.project(left, back, height + bottom)], roof);
    this.face(left, right, z, bottom, bottom + height, front);
  }

  drawTrack(distance) {
    const c = this.ctx;
    this.groundQuad(-5.7, 5.7, -5, 300, '#c0a289');
    this.groundQuad(-5.42, 5.42, -5, 300, '#786565');
    this.groundQuad(-7.8, -5.7, -5, 300, '#8f7773');
    this.groundQuad(5.7, 7.8, -5, 300, '#8f7773');
    this.groundQuad(-5.72, -5.58, -5, 300, '#f6d193');
    this.groundQuad(5.58, 5.72, -5, 300, '#f6d193');
    for (const lane of [-1, 0, 1]) {
      const x = lane * RULES.laneWidth;
      this.groundQuad(x - 1.34, x + 1.34, -5, 300, '#645458');
      const offset = distance % 3.4;
      for (let z = 251.6 - offset; z >= -5; z -= 3.4) {
        this.groundQuad(x - 1.24, x + 1.24, z, z + 0.32, '#a58d7b');
      }
      for (const rail of [-0.88, 0.88]) {
        this.groundQuad(x + rail - 0.07, x + rail + 0.07, -5, 300, '#e6d6bd');
        this.groundQuad(x + rail - 0.07, x + rail - 0.015, -5, 300, '#fff1d0');
      }
    }
    // Short platform tiles establish speed without screen shake.
    for (let z = 252 - (distance % 7); z > -5; z -= 7) {
      line(c, [this.project(-7.8, z), this.project(-5.72, z)], '#49344077', Math.max(0.6, this.project(0, z).scale));
      line(c, [this.project(5.72, z), this.project(7.8, z)], '#49344077', Math.max(0.6, this.project(0, z).scale));
    }
  }

  drawBuilding(side, z, index) {
    const x = side * (9.4 + (index % 3) * 0.5);
    const height = 6 + (index % 5) * 1.7;
    const shade = ['#654059', '#56384e', '#80515f'][index % 3];
    this.box(x, z, 5, height, 11, shade, '#392c40', '#aa756f');
    const left = x - 2.5;
    for (let row = 0; row < Math.min(5, Math.floor(height / 1.6)); row++) {
      for (let col = 0; col < 3; col++) {
        this.face(left + 0.55 + col * 1.35, left + 1.2 + col * 1.35,
          z - 0.03, 1.1 + row * 1.6, 1.75 + row * 1.6, (row + col + index) % 4 ? '#e9b47f' : '#392c40');
      }
    }
    if (index % 3 === 0) {
      this.box(x, z - 0.15, 3.8, 0.65, 0.25, '#d47a61', '#764256', '#f0ac76', 2.3);
    }
  }

  drawSignal(side, z) {
    const x = side * 6.05;
    const c = this.ctx;
    const base = this.project(x, z), top = this.project(x, z, 4.7);
    line(c, [base, top], '#312739', Math.max(1.5, 0.12 * this.unit * base.scale));
    const arm = this.project(x - side * 0.65, z, 4.7);
    line(c, [top, arm], '#312739', Math.max(1.5, 0.15 * this.unit * base.scale));
    this.box(x - side * 0.65, z - 0.1, 0.42, 0.56, 0.2, '#3b2b3c', '#211e2d', '#a78076', 4.2);
    const light = this.project(x - side * 0.65, z - 0.12, 4.48);
    ellipse(c, light.x, light.y, 0.11 * this.unit * base.scale, 0.11 * this.unit * base.scale, '#f8d787');
  }

  drawTrain(obstacle, row, z) {
    const x = obstacle.lane * RULES.laneWidth;
    const colors = [
      ['#397d83', '#285361', '#d4e0cf'],
      ['#b65760', '#733c50', '#f2c0a0'],
      ['#d49748', '#92603e', '#f6d794'],
    ][row.id % 3];
    // The car's long side panels, roof and front all share the same projection.
    this.groundQuad(x - 1.42, x + 1.42, z - 0.4, z + 8.4, '#261f2f66');
    this.box(x, z, 2.5, 3.25, 8, colors[0], colors[1], colors[2], 0.16);
    this.face(x - 1.25, x + 1.25, z - 0.02, 2.8, 3.1, '#f9e7c2');
    this.face(x - 1.04, x + 1.04, z - 0.04, 1.65, 2.7, '#242b3d');
    this.face(x - 0.92, x - 0.05, z - 0.05, 1.79, 2.58, '#8db9b5');
    this.face(x + 0.05, x + 0.92, z - 0.05, 1.79, 2.58, '#7ba2a5');
    this.face(x - 1.25, x + 1.25, z - 0.03, 0.75, 1.0, '#f7dcb1');
    this.face(x - 0.4, x + 0.4, z - 0.04, 1.13, 1.43, '#302633');
    this.face(x - 1.1, x + 1.1, z - 0.05, 0.16, 0.39, '#302735');
    for (const offset of [-0.86, 0.86]) {
      const p = this.project(x + offset, z - 0.07, 0.67);
      ellipse(this.ctx, p.x, p.y, this.unit * p.scale * 0.17, this.unit * p.scale * 0.12, '#fff2c2');
    }
    for (const side of [-1, 1]) {
      for (let panel = 0; panel < 4; panel++) {
        const sx = x + side * 1.255, front = z + 1.2 + panel * 1.55;
        polygon(this.ctx, [this.project(sx, front, 1.7), this.project(sx, front + 1.0, 1.7),
          this.project(sx, front + 1.0, 2.55), this.project(sx, front, 2.55)], '#1f3444');
      }
    }
    const p = this.project(x, z - 0.06, 1.29);
    if (p.scale * this.unit > 9) {
      this.ctx.fillStyle = '#ffe3a1';
      this.ctx.font = `700 ${Math.max(5, this.unit * p.scale * 0.18)}px system-ui,sans-serif`;
      this.ctx.textAlign = 'center';
      this.ctx.textBaseline = 'middle';
      this.ctx.fillText('METRO', p.x, p.y);
    }
  }

  drawBarrier(obstacle, z) {
    const x = obstacle.lane * RULES.laneWidth;
    for (const side of [-0.94, 0.94]) this.box(x + side, z + 0.13, 0.16, 1.12, 0.36, '#533348', '#392638', '#ffc98b');
    this.box(x, z, 2.45, 0.65, 0.5, '#f1b465', '#a7634c', '#ffd397', 0.45);
    for (let i = 0; i < 5; i++) {
      const left = x - 1.17 + i * 0.48;
      polygon(this.ctx, [this.project(left, z - 0.02, 0.47), this.project(left + 0.23, z - 0.02, 0.47),
        this.project(Math.min(x + 1.22, left + 0.46), z - 0.02, 1.08),
        this.project(left + 0.23, z - 0.02, 1.08)], '#753b4a');
    }
    this.drawInstruction(x, z, 1.53, 'jump');
  }

  drawArch(obstacle, z) {
    const x = obstacle.lane * RULES.laneWidth;
    for (const side of [-1.08, 1.08]) this.box(x + side, z, 0.18, 2.6, 0.45, '#77aba4', '#3b686f', '#c3d2ae');
    this.box(x, z, 2.48, 1.2, 0.55, '#5e898c', '#365568', '#b8cbae', 1.4);
    this.face(x - 1.24, x + 1.24, z - 0.02, 1.4, 1.58, '#ffda91');
    this.drawInstruction(x, z - 0.05, 2.02, 'duck');
  }

  drawInstruction(x, z, y, action) {
    const p = this.project(x, z, y), c = this.ctx;
    const size = this.unit * p.scale * 0.36;
    if (size < 3) return;
    ellipse(c, p.x, p.y, size, size, '#392e40');
    const direction = action === 'jump' ? -1 : 1;
    line(c, [{ x: p.x, y: p.y - direction * size * 0.49 }, { x: p.x, y: p.y + direction * size * 0.49 }], '#fff2d1', Math.max(1.1, size * 0.17));
    line(c, [{ x: p.x - size * 0.38, y: p.y }, { x: p.x, y: p.y + direction * size * 0.49 },
      { x: p.x + size * 0.38, y: p.y }], '#fff2d1', Math.max(1.1, size * 0.17));
  }

  drawCoin(coin, z, time) {
    const p = this.project(coin.lane * RULES.laneWidth, z, 0.85);
    const c = this.ctx, radius = this.unit * p.scale * 0.23;
    const turn = this.reducedMotion ? 0.9 : 0.55 + Math.abs(Math.cos(time * 2.4 + coin.z)) * 0.45;
    ellipse(c, p.x, p.y + radius * 0.08, radius * turn, radius, '#b87538');
    ellipse(c, p.x, p.y - radius * 0.06, radius * turn * 0.86, radius * 0.86, '#ffd47b');
    c.beginPath();
    c.ellipse(p.x, p.y - radius * 0.06, radius * turn * 0.56, radius * 0.56, 0, 0, Math.PI * 2);
    c.strokeStyle = '#fff3bf';
    c.lineWidth = Math.max(0.7, radius * 0.14);
    c.stroke();
    if (radius > 3) line(c, [{ x: p.x, y: p.y - radius * 0.34 }, { x: p.x, y: p.y + radius * 0.25 }], '#ad7137', Math.max(1, radius * 0.13));
  }

  drawRunner(state) {
    const c = this.ctx;
    const scale = clamp(this.unit / 47, 0.55, 1.7);
    const base = this.project(state.laneX * RULES.laneWidth, 0);
    const jump = state.jumpY * this.unit;
    const duck = state.duckBlend || 0;
    const active = state.phase === 'running' && !this.reducedMotion;
    const stride = active && state.jumpY < 0.08 ? Math.sin(state.distance * 1.35) : 0;
    const bob = active && state.jumpY < 0.08 ? Math.abs(stride) * 2 : 0;
    ellipse(c, base.x, base.y + 3 * scale, (22 - state.jumpY * 3) * scale,
      7 * scale, `rgba(27,23,38,${0.3 - state.jumpY * 0.06})`);
    // Quiet lane halo helps players read the body-controlled character's position.
    c.beginPath();
    c.ellipse(base.x, base.y + 2 * scale, 27 * scale, 9 * scale, 0, 0, Math.PI * 2);
    c.strokeStyle = '#ffe3a77a';
    c.lineWidth = 1.5 * scale;
    c.stroke();
    c.save();
    c.translate(base.x, base.y - jump - bob * scale);
    c.scale(scale, scale);
    c.translate(0, duck * 31);
    const lean = clamp(state.lane - state.laneX, -1, 1) * 0.14;
    c.rotate(lean);
    const strideX = state.jumpY > 0.08 ? 6 : stride * 8;
    const limb = (points, color, width) => {
      c.lineCap = 'round'; c.lineJoin = 'round'; line(c, points.map(([x, y]) => ({ x, y })), color, width);
    };
    // Shoes, articulated trouser legs, warm jacket and small daypack, viewed from behind.
    limb([[-8, -39], [-12 - strideX * 0.5, -20 + duck * -15], [-11 + strideX, -5 - duck * 25]], '#243344', 11);
    limb([[8, -39], [12 + strideX * 0.5, -19 + duck * -15], [12 - strideX, -5 - duck * 25]], '#344b5c', 11);
    limb([[-16 + strideX, -3 - duck * 25], [-7 + strideX, -3 - duck * 25]], '#fff0d3', 7);
    limb([[8 - strideX, -3 - duck * 25], [18 - strideX, -3 - duck * 25]], '#f2d8bb', 7);
    const shoulderY = -76 + duck * 4;
    limb([[-15, shoulderY + 5], [-23 - strideX * 0.3, -56], [-19 + strideX * 0.3, -47]], '#ba574d', 10);
    limb([[15, shoulderY + 5], [23 + strideX * 0.3, -58], [19 - strideX * 0.3, -49]], '#e99164', 10);
    ellipse(c, -19 + strideX * 0.3, -45, 4.5, 5, '#edbc96');
    ellipse(c, 19 - strideX * 0.3, -47, 4.5, 5, '#ffd3a5');
    polygon(c, [{ x: -16, y: shoulderY }, { x: 16, y: shoulderY }, { x: 13, y: -36 },
      { x: -13, y: -36 }], '#e58c60');
    polygon(c, [{ x: -16, y: shoulderY }, { x: -10, y: shoulderY + 5 },
      { x: -7, y: -36 }, { x: -13, y: -36 }], '#bd6553');
    limb([[-10, shoulderY + 2], [-9, -43]], '#edd3a4', 3);
    limb([[10, shoulderY + 2], [9, -43]], '#edd3a4', 3);
    c.fillStyle = '#5d3b53';
    c.beginPath(); c.roundRect(-10.5, shoulderY + 9, 21, 26, 6); c.fill();
    c.fillStyle = '#8b5768';
    c.beginPath(); c.roundRect(-7, shoulderY + 23, 14, 9, 2); c.fill();
    line(c, [{ x: -5, y: shoulderY + 27 }, { x: 5, y: shoulderY + 27 }], '#efc38b', 1.5);
    ellipse(c, 0, shoulderY - 7, 11, 13, '#edbc96');
    ellipse(c, 0, shoulderY - 13, 12, 10, '#302a39');
    polygon(c, [{ x: -12, y: shoulderY - 15 }, { x: 9, y: shoulderY - 20 },
      { x: 13, y: shoulderY - 10 }, { x: -12, y: shoulderY - 8 }], '#64a6a4');
    line(c, [{ x: -12, y: shoulderY - 9 }, { x: 15, y: shoulderY - 9 }], '#b3d6b9', 3);
    c.restore();
  }

  render(state, time = 0) {
    if (this.destroyed || !state) return;
    const c = this.ctx;
    c.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    c.clearRect(0, 0, this.width, this.height);
    c.drawImage(this.background, 0, 0, this.width, this.height);
    this.drawTrack(state.distance);
    const objects = [];
    const block = Math.floor(state.distance / 24);
    for (let index = block; index < block + 12; index++) {
      const z = index * 24 + 5 - state.distance;
      if (z < -8) continue;
      objects.push({ z, draw: () => {
        this.drawBuilding(-1, z, index + 41);
        this.drawBuilding(1, z + 9, index + 47);
      } });
    }
    const signalBlock = Math.floor(state.distance / 32);
    for (let index = signalBlock; index < signalBlock + 9; index++) {
      const z = index * 32 + 19 - state.distance;
      if (z < -2) continue;
      objects.push({ z, draw: () => { this.drawSignal(-1, z); this.drawSignal(1, z); } });
    }
    for (const row of state.rows) {
      const z = row.z - state.distance;
      if (z > 270 || z < -10) continue;
      for (const obstacle of row.obstacles) {
        if (z < -obstacle.length - 1) continue;
        objects.push({ z, draw: () => {
          if (obstacle.type === 'train') this.drawTrain(obstacle, row, z);
          else if (obstacle.type === 'barrier') this.drawBarrier(obstacle, z);
          else this.drawArch(obstacle, z);
        } });
      }
      for (const coin of row.coins) {
        const coinZ = coin.z - state.distance;
        if (!coin.collected && coinZ > -1 && coinZ < 260) {
          objects.push({ z: coinZ, draw: () => this.drawCoin(coin, coinZ, time) });
        }
      }
    }
    objects.sort((a, b) => b.z - a.z);
    for (const object of objects) object.draw();
    // Gentle distant fog unifies the city, while nearby obstacles retain contrast.
    const fog = c.createLinearGradient(0, this.horizon, 0, this.horizon + this.height * 0.22);
    fog.addColorStop(0, '#efba9645'); fog.addColorStop(1, '#efba9600');
    c.fillStyle = fog;
    c.fillRect(0, this.horizon, this.width, this.height * 0.22);
    this.drawRunner(state);
    if (state.phase === 'over') {
      c.fillStyle = '#8f304b18';
      c.fillRect(0, 0, this.width, this.height);
    }
  }

  destroy() {
    this.destroyed = true;
    this.background.width = 1;
    this.background.height = 1;
  }
}
