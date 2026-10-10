import { createRenderLoop } from './render-on-demand.js?v=2';
import { playerRadius } from './player-controls.js';

export const FIELD = { width: 1400, height: 875, margin: 76 };
const color = (value, fallback) => /^#[\da-f]{6}$/i.test(value || '') ? value : fallback;
export function pitchTransform(width, height) {
  const { width: w, height: h, margin: m } = FIELD;
  const scale = Math.min(width / (w + m * 2), height / (h + m * 2));
  return { scale, x: (width - w * scale) / 2, y: (height - h * scale) / 2 };
}
export function pitchPoint(clientX, clientY, rect) {
  if (!(rect.width > 0 && rect.height > 0)) return null;
  const t = pitchTransform(rect.width, rect.height);
  return { x: (clientX - rect.left - t.x) / t.scale, y: (clientY - rect.top - t.y) / t.scale };
}

// ── Ping-pong visual juice ──────────────────────────────────────────
// A short trail and contact particles on a stationary board.

const TRAIL_LENGTH = 18;        // positions remembered
const TRAIL_MIN_SPEED = 40;     // px/s before trail appears
const PARTICLE_LIFETIME = 400;  // ms
const PARTICLE_COUNT = 8;       // per impact burst

function lerpColor(a, b, t) {
  const pa = [parseInt(a.slice(1, 3), 16), parseInt(a.slice(3, 5), 16), parseInt(a.slice(5, 7), 16)];
  const pb = [parseInt(b.slice(1, 3), 16), parseInt(b.slice(3, 5), 16), parseInt(b.slice(5, 7), 16)];
  const c = pa.map((v, i) => Math.round(v + (pb[i] - v) * t));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

// Canvas only: one cached pitch image, two small rosters, and a finite goal effect.
export function createPitch2D(container) {
  const canvas = document.createElement('canvas');
  canvas.tabIndex = 0; canvas.setAttribute('aria-label', 'Soccer pitch. Select a player, drag back to aim, and release to kick.');
  if (document.getElementById('player-control-help')) canvas.setAttribute('aria-describedby', 'player-control-help');
  canvas.style.cssText = 'display:block;width:100%;height:100%;touch-action:none';
  container.append(canvas);
  const ctx = canvas.getContext('2d', { alpha: false });
  if (!ctx) throw new Error('This browser cannot create the 2D pitch.');
  const background = document.createElement('canvas'), bg = background.getContext('2d', { alpha: false });
  let teams = { a: [], b: [] }, ball = { x: 700, y: 437.5, z: 0 }, referee = null;
  let selection = null, humanPlayer = null, aim = null, theme = {}, goal = null, whistleAt = null;
  let radius = 12, frames = 0, backgroundBuilds = 0, rosterStamp = '', mode = 'auto';
  let transform = pitchTransform(1, 1);
  try { mode = localStorage.getItem('agent-soccer-2d-quality') || 'auto'; } catch (_) {}
  if (!['auto', '1080p', '1440p', '4k'].includes(mode)) mode = 'auto';

  // ── Juice state ────────────────────────────────────────────────────
  const trail = [];                    // { x, y, z, t }
  const particles = [];                // { x, y, vx, vy, born, color, r }
  let prevBall = { x: ball.x, y: ball.y };
  let ballSpeed = 0;

  const circle = (c, x, y, r, fill, stroke = null, width = 2) => {
    c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2); c.fillStyle = fill; c.fill();
    if (stroke) { c.strokeStyle = stroke; c.lineWidth = width; c.stroke(); }
  };

  function spawnParticles(x, y, isPlayer) {
    const baseColor = isPlayer ? '#4fc3f7' : '#ffee58';
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      const angle = Math.PI * 2 * i / PARTICLE_COUNT + (Math.random() - 0.5) * 0.6;
      const speed = 80 + Math.random() * 160;
      particles.push({
        x, y, vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed,
        born: loop.now(), color: baseColor,
        r: 2.5 + Math.random() * 3,
      });
    }
    if (particles.length > 128) particles.splice(0, particles.length - 128);
  }


  const PLAYER_SCALE = 2.1; // Larger player markers and matching pointer targets.

  function paintBackground() {
    background.width = canvas.width; background.height = canvas.height;
    bg.fillStyle = '#07151a'; bg.fillRect(0, 0, background.width, background.height);
    bg.save(); bg.translate(transform.x, transform.y); bg.scale(transform.scale, transform.scale);

    // Stadium floodlight ambient glow outside the touchlines
    const outerGlow = bg.createRadialGradient(700, 437.5, 300, 700, 437.5, 800);
    outerGlow.addColorStop(0, '#0c2621');
    outerGlow.addColorStop(1, '#051114');
    bg.fillStyle = outerGlow;
    bg.fillRect(-60, -60, 1520, 995);

    // Lush manicured pitch grass
    const grass = color(theme.grass_shade, {
      dark:'#0a2b20', bright:'#13472d', emerald:'#0c3829', winter:'#25291f', neon:'#09381c',
    }[theme.grass_shade] || '#0c3829');
    bg.fillStyle = grass; bg.fillRect(0, 0, 1400, 875);

    // Mowing pattern & turf texture
    bg.save(); bg.beginPath(); bg.rect(0,0,1400,875); bg.clip();
    const pattern = theme.pitch_pattern || 'stripes';
    if (pattern === 'stripes') {
      for (let i = 0; i < 10; i += 2) {
        bg.fillStyle = 'rgba(255, 255, 255, 0.035)';
        bg.fillRect(i * 140, 0, 140, 875);
      }
    } else if (pattern === 'checker') {
      for (let x = 0; x < 1400; x += 140) {
        for (let y = 0; y < 875; y += 125) {
          if ((x / 140 + y / 125) % 2 === 0) {
            bg.fillStyle = 'rgba(255, 255, 255, 0.04)';
            bg.fillRect(x, y, 140, 125);
          }
        }
      }
    } else if (pattern === 'diagonal') {
      bg.lineWidth = 110; bg.strokeStyle = 'rgba(255, 255, 255, 0.035)';
      for (let x = -875; x < 1400; x += 220) {
        bg.beginPath(); bg.moveTo(x, 0); bg.lineTo(x + 875, 875); bg.stroke();
      }
    } else if (pattern === 'zigzag') {
      bg.lineWidth = 30; bg.strokeStyle = 'rgba(255, 255, 255, 0.035)';
      for (let x = 0; x < 1400; x += 140) {
        bg.beginPath(); bg.moveTo(x, 0);
        for (let y = 0; y <= 1000; y += 125) bg.lineTo(x + (y / 125 % 2 ? 50 : 0), y);
        bg.stroke();
      }
    }

    // Four corner stadium floodlight beams illuminating the pitch inward
    const cornerR = 360;
    const corners = [[0, 0], [1400, 0], [0, 875], [1400, 875]];
    corners.forEach(([cx, cy]) => {
      const grad = bg.createRadialGradient(cx, cy, 10, cx, cy, cornerR);
      grad.addColorStop(0, 'rgba(224, 255, 240, 0.12)');
      grad.addColorStop(0.5, 'rgba(56, 189, 248, 0.04)');
      grad.addColorStop(1, 'rgba(0, 0, 0, 0)');
      bg.fillStyle = grad;
      bg.fillRect(cx - cornerR, cy - cornerR, cornerR * 2, cornerR * 2);
    });

    // Pitch perimeter turf vignette (darkens slightly near the edges)
    const pitchVignette = bg.createRadialGradient(700, 437.5, 450, 700, 437.5, 800);
    pitchVignette.addColorStop(0, 'rgba(0, 0, 0, 0)');
    pitchVignette.addColorStop(1, 'rgba(0, 0, 0, 0.22)');
    bg.fillStyle = pitchVignette;
    bg.fillRect(0, 0, 1400, 875);
    bg.restore();

    // Stadium perimeter LED ribbon boards & crowd energy dots
    for (let i = 0; i < 70; i++) {
      const x = 12 + i * 20;
      const ribbonColor = i < 35 ? color(theme.team_a_color, '#0ea5e9') : color(theme.team_b_color, '#f97316');
      bg.fillStyle = ribbonColor;
      bg.globalAlpha = 0.55;
      bg.fillRect(x, -38, 14, 10);
      bg.fillRect(x, 903, 14, 10);
      // Small spectator light dots
      bg.globalAlpha = 0.35;
      bg.fillStyle = i % 2 === 0 ? '#e2e8f0' : ribbonColor;
      bg.beginPath(); bg.arc(x + 7, -46, 2.5, 0, Math.PI * 2); bg.fill();
      bg.beginPath(); bg.arc(x + 7, 921, 2.5, 0, Math.PI * 2); bg.fill();
    }
    bg.globalAlpha = 1;

    // Field lines (crisp, luminous tournament white-mint)
    const lineColor = color(theme.field_line_color, '#e2fded');
    bg.strokeStyle = lineColor;
    bg.lineWidth = 2.5;

    // Outer touchline
    bg.strokeRect(20, 20, 1360, 835);

    // Halfway line & center circle
    bg.beginPath(); bg.moveTo(700, 20); bg.lineTo(700, 855); bg.stroke();
    bg.beginPath(); bg.arc(700, 437.5, 118, 0, Math.PI * 2); bg.stroke();
    circle(bg, 700, 437.5, 5, lineColor);

    // 4 Corner arcs
    const cornerArc = (x, y, start, end) => {
      bg.beginPath(); bg.arc(x, y, 25, start, end); bg.stroke();
    };
    cornerArc(20, 20, 0, Math.PI / 2);
    cornerArc(1380, 20, Math.PI / 2, Math.PI);
    cornerArc(20, 855, -Math.PI / 2, 0);
    cornerArc(1380, 855, Math.PI, -Math.PI / 2);

    // Penalty areas, goal areas, spots and 3D goal nets
    for (const side of [0, 1]) {
      bg.save(); if (side) { bg.translate(1400, 0); bg.scale(-1, 1); }
      // 18-yard penalty box
      bg.strokeRect(20, 178, 220, 519);
      // 6-yard goal area
      bg.strokeRect(20, 320, 74, 235);
      // Penalty spot
      circle(bg, 167, 437.5, 4.5, lineColor);
      // Penalty D arc
      bg.beginPath(); bg.arc(167, 437.5, 118, -0.9, 0.9); bg.stroke();

      // Goal net depth box
      bg.fillStyle = '#06171a'; bg.globalAlpha = 0.65;
      bg.fillRect(-30, 356, 50, 163);
      bg.globalAlpha = 1;

      // Depth netting mesh
      bg.strokeStyle = color(theme.net_color, '#38bdf8');
      bg.lineWidth = 1;
      bg.globalAlpha = 0.45;
      for (let x = -30; x <= 20; x += 10) { bg.beginPath(); bg.moveTo(x, 356); bg.lineTo(x, 519); bg.stroke(); }
      for (let y = 356; y <= 519; y += 10) { bg.beginPath(); bg.moveTo(-30, y); bg.lineTo(20, y); bg.stroke(); }
      bg.globalAlpha = 1;

      // 3D Metallic goal posts
      bg.lineWidth = 6;
      bg.strokeStyle = '#f8fafc';
      bg.strokeRect(-30, 356, 50, 163);
      bg.restore();
    }
    bg.restore();
    backgroundBuilds++;
  }

  function resize() {
    const rect = container.getBoundingClientRect(); if (!rect.width || !rect.height) return;
    const longEdge = { '1080p': 1920, '1440p': 2560, '4k': 3840 }[mode];
    const dpr = longEdge ? longEdge / Math.max(rect.width, rect.height) : Math.min(2, window.devicePixelRatio || 1);
    const factor = Math.min(dpr, 4096 / Math.max(rect.width, rect.height), Math.sqrt(10000000 / (rect.width * rect.height)));
    const width = Math.max(1, Math.round(rect.width * factor)), height = Math.max(1, Math.round(rect.height * factor));
    if (canvas.width === width && canvas.height === height) return;
    canvas.width = width; canvas.height = height; transform = pitchTransform(width, height);
    paintBackground(); loop.invalidate();
  }

  function render(now, dtMs) {
    // ── Update ball speed and trail ───────────────────────────────
    const dt = Math.max(1, dtMs) / 1000;
    const dx = ball.x - prevBall.x, dy = ball.y - prevBall.y;
    ballSpeed = Math.hypot(dx, dy) / dt;
    prevBall = { x: ball.x, y: ball.y };

    if (ballSpeed > TRAIL_MIN_SPEED) {
      trail.push({ x: ball.x, y: ball.y, z: ball.z || 0, t: now });
    }
    while (trail.length > TRAIL_LENGTH) trail.shift();
    while (trail.length && now - trail[0].t > 300) trail.shift();
    if (trail.length) loop.invalidate();

    // ── Draw ─────────────────────────────────────────────────────
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = '#07151a'; ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(background, 0, 0);
    ctx.save(); ctx.translate(transform.x, transform.y); ctx.scale(transform.scale, transform.scale);

    // ── Ball trail with luminous energy ──────────────────────────
    if (trail.length > 1) {
      const speedFrac = Math.min(1, ballSpeed / 800);
      for (let i = 1; i < trail.length; i++) {
        const frac = i / trail.length;
        const alpha = frac * 0.55 * speedFrac;
        const trailR = (radius || 17) * (0.35 + frac * 0.65);
        const lift = Math.min(85, Math.max(0, trail[i].z) * 0.35);
        ctx.globalAlpha = alpha;
        const trailColor = lerpColor('#38bdf8', '#ff4500', speedFrac);
        circle(ctx, trail[i].x, trail[i].y - lift, trailR, trailColor);
      }
      ctx.globalAlpha = 1;
    }

    // ── Players (Bigger, 3D Token Discs with Bevel & Gloss) ────────
    for (const side of ['a', 'b']) teams[side].forEach((p, i) => {
      if (!Number.isFinite(p.x) || !Number.isFinite(p.y) || p.x < -40 || p.x > 1440 || p.y < -40 || p.y > 915) return;

      const r = playerRadius(p) * PLAYER_SCALE;
      const baseKit = side === 'a' ? '#0ea5e9' : '#ef4444';
      const kitColor = color(p.color, color(theme['team_' + side + '_color'], baseKit));

      // 1. Soft ambient drop shadow
      ctx.save();
      circle(ctx, p.x + 3, p.y + 6, r + 1, 'rgba(0, 0, 0, 0.45)');
      ctx.restore();

      // 2. Selection ring (pulsing energized aura)
      if (selection?.team === side && selection.index === i) {
        ctx.save();
        ctx.shadowColor = '#38bdf8';
        ctx.shadowBlur = 18;
        circle(ctx, p.x, p.y, r + 9, 'transparent', '#38bdf8', 3.5);
        ctx.shadowBlur = 0;
        circle(ctx, p.x, p.y, r + 14, 'transparent', 'rgba(56, 189, 248, 0.4)', 1.5);
        ctx.restore();
      }

      // 3. Outer metallic rim with bevel highlight
      const rimGrad = ctx.createLinearGradient(p.x - r, p.y - r, p.x + r, p.y + r);
      rimGrad.addColorStop(0, '#ffffff');
      rimGrad.addColorStop(0.35, '#94a3b8');
      rimGrad.addColorStop(0.7, '#475569');
      rimGrad.addColorStop(1, '#0f172a');
      circle(ctx, p.x, p.y, r, rimGrad);

      // 4. Inner jersey token core
      const innerR = r - 3.5;
      const coreGrad = ctx.createRadialGradient(p.x - innerR * 0.35, p.y - innerR * 0.4, innerR * 0.05, p.x, p.y, innerR);
      if (i === 0) {
        // Goalkeeper: Special golden/amber keeper kit
        const gkCol = side === 'a' ? '#f59e0b' : '#10b981';
        coreGrad.addColorStop(0, '#fef08a');
        coreGrad.addColorStop(0.5, gkCol);
        coreGrad.addColorStop(1, '#78350f');
      } else if (side === 'a') {
        // Team A: Sky blue gradient
        coreGrad.addColorStop(0, '#7dd3fc');
        coreGrad.addColorStop(0.5, kitColor);
        coreGrad.addColorStop(1, '#0369a1');
      } else {
        // Team B: Coral / Red gradient
        coreGrad.addColorStop(0, '#fca5a5');
        coreGrad.addColorStop(0.5, kitColor);
        coreGrad.addColorStop(1, '#991b1b');
      }
      circle(ctx, p.x, p.y, innerR, coreGrad);

      // Goalkeeper golden inner emblem ring
      if (i === 0) {
        ctx.save();
        ctx.strokeStyle = '#fef08a'; ctx.lineWidth = 2.5;
        ctx.beginPath(); ctx.arc(p.x, p.y, innerR - 5, 0, Math.PI * 2); ctx.stroke();
        ctx.restore();
      }

      // 5. Specular gloss arc (upper hemisphere shine)
      ctx.save();
      ctx.beginPath();
      ctx.ellipse(p.x, p.y - innerR * 0.35, innerR * 0.72, innerR * 0.42, 0, 0, Math.PI * 2);
      const glossGrad = ctx.createLinearGradient(p.x, p.y - innerR * 0.75, p.x, p.y + innerR * 0.1);
      glossGrad.addColorStop(0, 'rgba(255, 255, 255, 0.7)');
      glossGrad.addColorStop(0.6, 'rgba(255, 255, 255, 0.15)');
      glossGrad.addColorStop(1, 'rgba(255, 255, 255, 0)');
      ctx.fillStyle = glossGrad;
      ctx.fill();
      ctx.restore();

      // 6. Direction eye notch (facing ball)
      const heading = Math.atan2(ball.y - p.y, ball.x - p.x);
      const eyeDist = innerR - 3;
      circle(ctx, p.x + Math.cos(heading) * eyeDist, p.y + Math.sin(heading) * eyeDist, 3.5, '#ffffff', '#0f172a', 1.5);

      // 7. Jersey number (Space Grotesk bold)
      const numSize = Math.max(16, Math.round(r * 0.78));
      ctx.font = `800 ${numSize}px 'Space Grotesk', system-ui, sans-serif`;
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      // Number shadow
      ctx.fillStyle = 'rgba(0, 0, 0, 0.65)';
      ctx.fillText(String(i + 1), p.x, p.y + 2.5);
      // Number face
      ctx.fillStyle = '#ffffff';
      ctx.fillText(String(i + 1), p.x, p.y + 1);

      // 8. YOU Badge / Captain Indicator (Striker 5 or Human Player)
      if (humanPlayer?.team === side && humanPlayer.index === i) {
        ctx.save();
        const badgeY = p.y - r - 22;
        const bw = 46, bh = 19;
        ctx.shadowColor = '#fbbf24'; ctx.shadowBlur = 12;
        ctx.fillStyle = 'rgba(15, 23, 42, 0.9)';
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(p.x - bw / 2, badgeY - bh / 2, bw, bh, 999);
        else ctx.rect(p.x - bw / 2, badgeY - bh / 2, bw, bh);
        ctx.fill();
        ctx.strokeStyle = '#fde047'; ctx.lineWidth = 1.8; ctx.stroke();

        // Downward pointer arrow
        ctx.fillStyle = '#fde047';
        ctx.beginPath();
        ctx.moveTo(p.x - 5, badgeY + bh / 2);
        ctx.lineTo(p.x + 5, badgeY + bh / 2);
        ctx.lineTo(p.x, badgeY + bh / 2 + 5);
        ctx.closePath();
        ctx.fill();

        ctx.font = '800 11px system-ui, sans-serif';
        ctx.fillStyle = '#fde047';
        ctx.shadowBlur = 0;
        ctx.fillText('YOU', p.x, badgeY);
        ctx.restore();
      }
    });

    // ── Referee ───────────────────────────────────────────────────
    if (referee && referee.x >= 0 && referee.y >= 0) {
      const { x, y } = referee;
      const refR = 16;
      circle(ctx, x + 2, y + 4, refR + 1, 'rgba(0, 0, 0, 0.4)');
      circle(ctx, x, y, refR, color(theme.ref_color, '#fde047'), '#0f172a', 3.5);
      // Official ref stripes
      ctx.fillStyle = '#0f172a';
      ctx.fillRect(x - 3, y - refR + 4, 6, refR * 2 - 8);
      ctx.font = '800 13px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillStyle = '#ffffff'; ctx.fillText('R', x, y + 1);
      if (whistleAt !== null && now - whistleAt < 800) {
        ctx.strokeStyle = '#fff3c1'; ctx.lineWidth = 3;
        ctx.beginPath(); ctx.arc(x, y, refR + 8 + (now - whistleAt) / 40, -0.7, 0.7); ctx.stroke();
        loop.invalidate();
      } else whistleAt = null;
    }

    // ── Aim arrow & trajectory preview ───────────────────────────
    if (aim) {
      const { home, pull } = aim;
      const length = 46 + pull.power * 2.4;
      const angle = pull.kickAngle;
      const end = { x: home.x + Math.cos(angle) * length, y: home.y + Math.sin(angle) * length };

      const selected = selection && teams[selection.team]?.[selection.index];
      const discRadius = (selected ? playerRadius(selected) : 20) * PLAYER_SCALE;

      // 1. Elastic pull tension cord
      ctx.save();
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.85)';
      ctx.lineWidth = 3.5;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(home.x, home.y);
      ctx.lineTo(pull.pullX, pull.pullY);
      ctx.stroke();
      ctx.setLineDash([]);

      // 2. Ghost disc at drag origin
      circle(ctx, pull.pullX, pull.pullY, discRadius, 'rgba(56, 189, 248, 0.15)', 'rgba(255, 255, 255, 0.9)', 2.5);

      // 3. Power meter gauge around home disc
      const powerFrac = pull.power / 100;
      const gaugeCol = powerFrac < 0.4 ? '#4ade80' : powerFrac < 0.75 ? '#facc15' : '#ef4444';
      ctx.strokeStyle = gaugeCol;
      ctx.lineWidth = 6;
      ctx.shadowColor = gaugeCol; ctx.shadowBlur = 10;
      ctx.beginPath();
      ctx.arc(home.x, home.y, discRadius + 15, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * powerFrac);
      ctx.stroke();
      ctx.shadowBlur = 0;

      // 4. Trajectory tracer line
      ctx.strokeStyle = '#4ade80';
      ctx.lineWidth = 4;
      ctx.setLineDash([10, 8]);
      ctx.shadowColor = '#4ade80'; ctx.shadowBlur = 12;
      ctx.beginPath();
      ctx.moveTo(home.x, home.y);
      ctx.lineTo(end.x, end.y);
      ctx.stroke();
      ctx.setLineDash([]);

      // 5. Arrowhead
      ctx.fillStyle = '#4ade80';
      ctx.beginPath();
      ctx.moveTo(end.x, end.y);
      ctx.lineTo(end.x - Math.cos(angle - 0.42) * 22, end.y - Math.sin(angle - 0.42) * 22);
      ctx.lineTo(end.x - Math.cos(angle + 0.42) * 22, end.y - Math.sin(angle + 0.42) * 22);
      ctx.closePath();
      ctx.fill();

      // 6. Floating power readout tag
      const tagDist = length + 24;
      const tagX = home.x + Math.cos(angle) * tagDist;
      const tagY = home.y + Math.sin(angle) * tagDist;
      ctx.fillStyle = 'rgba(15, 23, 42, 0.9)';
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(tagX - 22, tagY - 12, 44, 24, 6);
      else ctx.rect(tagX - 22, tagY - 12, 44, 24);
      ctx.fill();
      ctx.strokeStyle = gaugeCol; ctx.lineWidth = 1.5; ctx.stroke();
      ctx.font = '800 12px system-ui, sans-serif';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillStyle = '#ffffff'; ctx.fillText(`${Math.round(pull.power)}%`, tagX, tagY);

      ctx.restore();
    }

    // ── Ball (Bigger, 3D Soccer Ball with Realistic Sphere Gradient & Panels) ──
    const lift = Math.min(85, Math.max(0, ball.z) * 0.35);
    const br = 18 * (1 + Math.min(0.35, lift / 200));

    // Ground shadow
    ctx.save();
    const shadowScale = 1 + lift * 0.015;
    const shadowAlpha = Math.max(0.15, 0.48 - lift * 0.005);
    ctx.fillStyle = `rgba(0, 0, 0, ${shadowAlpha})`;
    ctx.beginPath();
    ctx.ellipse(ball.x + 3, ball.y + 6 + lift * 0.2, br * 0.95 * shadowScale, br * 0.6 * shadowScale, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();

    // Height indicator tether
    if (lift > 2) {
      ctx.save();
      ctx.strokeStyle = 'rgba(56, 189, 248, 0.6)'; ctx.lineWidth = 2; ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(ball.x, ball.y); ctx.lineTo(ball.x, ball.y - lift); ctx.stroke();
      ctx.restore();
    }

    // Speed-based glow
    const speedFrac = Math.min(1, ballSpeed / 600);
    if (speedFrac > 0.15) {
      ctx.save();
      ctx.shadowColor = lerpColor('#38bdf8', '#ff3d00', speedFrac);
      ctx.shadowBlur = 10 + speedFrac * 25;
      circle(ctx, ball.x, ball.y - lift, br + 2, 'transparent', 'rgba(255, 255, 255, 0.5)', 2);
      ctx.restore();
    }

    // Ball body
    ctx.save();
    ctx.translate(ball.x, ball.y - lift);

    const ballGrad = ctx.createRadialGradient(-br * 0.35, -br * 0.4, br * 0.05, 0, 0, br);
    ballGrad.addColorStop(0, '#ffffff');
    ballGrad.addColorStop(0.7, '#f1f5f9');
    ballGrad.addColorStop(1, '#94a3b8');
    circle(ctx, 0, 0, br, ballGrad, '#0f172a', 2.5);

    // Pentagon patches
    ctx.fillStyle = '#0f172a';
    ctx.beginPath();
    for (let i = 0; i < 5; i++) {
      const a = i * Math.PI * 2 / 5 - Math.PI / 2;
      const px = Math.cos(a) * br * 0.38, py = Math.sin(a) * br * 0.38;
      i === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py);
    }
    ctx.closePath();
    ctx.fill();

    // Outer panel seams and edge patches
    for (let i = 0; i < 5; i++) {
      const a = i * Math.PI * 2 / 5 - Math.PI / 2;
      const sx = Math.cos(a) * br * 0.38, sy = Math.sin(a) * br * 0.38;
      const ex = Math.cos(a) * br * 0.88, ey = Math.sin(a) * br * 0.88;
      ctx.strokeStyle = 'rgba(15, 23, 42, 0.6)'; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(ex, ey); ctx.stroke();

      ctx.fillStyle = '#0f172a';
      ctx.beginPath();
      ctx.arc(Math.cos(a) * br * 0.98, Math.sin(a) * br * 0.98, br * 0.22, 0, Math.PI * 2);
      ctx.fill();
    }

    // Specular shine
    ctx.beginPath();
    ctx.ellipse(-br * 0.35, -br * 0.35, br * 0.3, br * 0.18, -Math.PI / 4, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(255, 255, 255, 0.85)';
    ctx.fill();
    ctx.restore();

    // ── Impact particles ─────────────────────────────────────────
    for (let i = particles.length - 1; i >= 0; i--) {
      const p = particles[i];
      const age = now - p.born;
      if (age > PARTICLE_LIFETIME) { particles.splice(i, 1); continue; }
      const frac = age / PARTICLE_LIFETIME;
      const px = p.x + p.vx * (age / 1000);
      const py = p.y + p.vy * (age / 1000);
      ctx.globalAlpha = 1 - frac;
      circle(ctx, px, py, p.r * (1 - frac * 0.5), p.color);
      loop.invalidate();
    }
    ctx.globalAlpha = 1;

    // ── Goal banner ──────────────────────────────────────────────
    if (goal && now - goal.start < 1300) {
      const gfrac = Math.min(1, (1300-(now-goal.start))/350);
      ctx.globalAlpha = gfrac;
      // Glow background
      ctx.shadowColor = '#ffd740'; ctx.shadowBlur = 30;
      ctx.fillStyle = 'rgba(10,26,31,.88)'; ctx.fillRect(535, 365, 330, 130);
      ctx.font = '700 58px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillStyle = '#ffd740'; ctx.fillText('GOAL',700,430);
      ctx.shadowBlur = 0;
      ctx.globalAlpha = 1; loop.invalidate();
    } else goal = null;

    ctx.restore(); frames++;
  }
  const loop = createRenderLoop({element:container,render});
  const observer = new ResizeObserver(resize); observer.observe(container);
  window.addEventListener('resize', resize); resize(); loop.invalidate();

  const setBall = (x,y,z=0) => {
    if (ball.x===x && ball.y===y && ball.z===z) return;
    ball={x,y,z};
    loop.invalidate();
  };

  // Juice triggers callable from playback
  function onBounce(x, y) {
    spawnParticles(x, y, false);
    loop.invalidate();
  }
  function onContact(x, y) {
    spawnParticles(x, y, true);
    loop.invalidate();
  }

  return {
    canvas, loop, setBall,
    onBounce, onContact,
    setRoster(a=[],b=[]) {
      const stamp=JSON.stringify([a,b]); if(stamp===rosterStamp)return;
      rosterStamp=stamp; teams={a:a.map(p=>({...p})),b:b.map(p=>({...p}))}; loop.invalidate();
    },
    setReferee(x,y) { const next=Number.isFinite(x)&&Number.isFinite(y)&&x>=0&&y>=0?{x,y}:null;
      if(next?.x===referee?.x&&next?.y===referee?.y)return;referee=next;loop.invalidate(); },
    setBallSize(size) { const next=17.5*({small:.82,large:1.18}[size]||1); if(next!==radius){radius=next;loop.invalidate();} },
    setSelection(team,index,active) {const next=active?{team,index}:null;if(JSON.stringify(next)===JSON.stringify(selection))return;selection=next;loop.invalidate();},
    setHumanPlayer(team,index) {const next=team?{team,index}:null;if(JSON.stringify(next)===JSON.stringify(humanPlayer))return;humanPlayer=next;loop.invalidate();},
    setAim(home,pull) {aim=home?{home,pull}:null;loop.invalidate();},
    clearAim() {if(aim){aim=null;loop.invalidate();}},
    setTheme(value) {const next={...theme,...value};if(JSON.stringify(next)===JSON.stringify(theme))return;
      theme=next;paintBackground();loop.invalidate();},
    celebrate() {
      goal={start:loop.now()};
      // Big celebration burst!
      for (let i = 0; i < 20; i++) {
        const angle = Math.PI * 2 * i / 20;
        const speed = 120 + Math.random() * 200;
        particles.push({
          x: ball.x, y: ball.y, vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed,
          born: loop.now(), color: ['#ffd740','#ff6e40','#4fc3f7','#69f0ae'][i % 4],
          r: 3 + Math.random() * 4,
        });
      }
      if (particles.length > 128) particles.splice(0, particles.length - 128);
      loop.invalidate();
    },
    whistle() {whistleAt=loop.now();loop.invalidate();},
    setQuality(value) {if(!['auto','1080p','1440p','4k'].includes(value))return;mode=value;try{localStorage.setItem('agent-soccer-2d-quality',mode);}catch(_){}resize();},
    get quality() {return mode;},
    point:(x,y)=>pitchPoint(x,y,canvas.getBoundingClientRect()),
    clientPoint(x,y) {const rect=canvas.getBoundingClientRect(),t=pitchTransform(rect.width,rect.height);return{x:rect.left+t.x+x*t.scale,y:rect.top+t.y+y*t.scale};},
    hitTest(x,y,legal,state) {const point=pitchPoint(x,y,canvas.getBoundingClientRect());if(!point)return null;
      const t=pitchTransform(canvas.clientWidth,canvas.clientHeight);let best=null,distance=Infinity;
      for(const i of legal.indices){const p=legal.useBall?state.ball:state['players_'+legal.team]?.[i];if(!p)continue;
        const d=Math.hypot(point.x-p.x,point.y-p.y),r=Math.max((playerRadius(p)*PLAYER_SCALE)+8,20/t.scale);
        if(d<r&&d<distance){best=i;distance=d;}}
      return best;
    },
    snapshot:()=>({renderer:'canvas2d',frames,backgroundBuilds,quality:mode,width:canvas.width,height:canvas.height,
      effects:{particles:particles.length,trail:trail.length,shaking:false,squashing:false},
      ball:{...ball},players_a:teams.a.map(p=>({...p})),players_b:teams.b.map(p=>({...p})),referee,selection,humanPlayer,aim,loop:loop.stats()}),
    dispose(){observer.disconnect();window.removeEventListener('resize',resize);loop.dispose();canvas.remove();},
  };
}
