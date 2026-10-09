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
  let selection = null, aim = null, theme = {}, goal = null, whistleAt = null;
  let radius = 12, frames = 0, backgroundBuilds = 0, rosterStamp = '', mode = 'auto';
  let transform = pitchTransform(1, 1);
  try { mode = localStorage.getItem('agent-soccer-2d-quality') || 'auto'; } catch (_) {}
  if (!['auto', '1080p', '1440p', '4k'].includes(mode)) mode = 'auto';
  const circle = (c, x, y, r, fill, stroke = null, width = 2) => {
    c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2); c.fillStyle = fill; c.fill();
    if (stroke) { c.strokeStyle = stroke; c.lineWidth = width; c.stroke(); }
  };
  function paintBackground() {
    background.width = canvas.width; background.height = canvas.height;
    bg.fillStyle = '#102c29'; bg.fillRect(0, 0, background.width, background.height);
    bg.save(); bg.translate(transform.x, transform.y); bg.scale(transform.scale, transform.scale);
    const grass = color(theme.grass_shade, {
      dark:'#23734e', bright:'#3c914c', emerald:'#147958', winter:'#726b50', neon:'#239f46',
    }[theme.grass_shade] || '#23734e');
    bg.fillStyle = grass; bg.fillRect(0, 0, 1400, 875);
    bg.save(); bg.beginPath(); bg.rect(0,0,1400,875); bg.clip();
    bg.fillStyle = 'rgba(255,255,255,.035)'; bg.strokeStyle=bg.fillStyle;
    const pattern=theme.pitch_pattern || 'stripes';
    if(pattern==='stripes') for(let i=0;i<10;i+=2) bg.fillRect(i*140,0,140,875);
    else if(pattern==='checker') {
      for(let x=0;x<1400;x+=140) for(let y=0;y<875;y+=125)
        if((x/140+y/125)%2===0) bg.fillRect(x,y,140,125);
    } else if(pattern==='diagonal') {
      bg.lineWidth=110;
      for(let x=-875;x<1400;x+=220){bg.beginPath();bg.moveTo(x,0);bg.lineTo(x+875,875);bg.stroke();}
    } else if(pattern==='zigzag') {
      bg.lineWidth=30;
      for(let x=0;x<1400;x+=140){bg.beginPath();bg.moveTo(x,0);
        for(let y=0;y<=1000;y+=125)bg.lineTo(x+(y/125%2?50:0),y);bg.stroke();}
    }
    bg.restore();
    // Lightweight stands are painted once, never animated while waiting.
    for (let i = 0; i < 70; i++) {
      const x = 12 + i * 20;
      bg.fillStyle = i < 35 ? color(theme.team_a_color, '#61a5f5') : color(theme.team_b_color, '#f78679');
      bg.globalAlpha = .45;
      bg.fillRect(x, -37, 10, 8); bg.fillRect(x, 906, 10, 8);
    }
    bg.globalAlpha = 1;
    bg.strokeStyle = color(theme.field_line_color, '#d8eee0'); bg.lineWidth = 2.4;
    bg.strokeRect(20, 20, 1360, 835);
    bg.beginPath(); bg.moveTo(700, 20); bg.lineTo(700, 855); bg.stroke();
    bg.beginPath(); bg.arc(700, 437.5, 118, 0, Math.PI * 2); bg.stroke();
    circle(bg, 700, 437.5, 4, bg.strokeStyle);
    for (const side of [0, 1]) {
      bg.save(); if (side) { bg.translate(1400, 0); bg.scale(-1, 1); }
      bg.strokeRect(20, 178, 220, 519); bg.strokeRect(20, 320, 74, 235);
      circle(bg, 167, 437.5, 3.5, bg.strokeStyle);
      bg.beginPath(); bg.arc(167, 437.5, 118, -.9, .9); bg.stroke();
      bg.fillStyle = '#b9dedb'; bg.globalAlpha = .10; bg.fillRect(-30, 356, 50, 163); bg.globalAlpha = 1;
      bg.strokeStyle = color(theme.net_color, '#bed8d4'); bg.lineWidth = 1;
      for (let x = -30; x <= 20; x += 10) { bg.beginPath(); bg.moveTo(x,356); bg.lineTo(x,519); bg.stroke(); }
      for (let y = 356; y <= 519; y += 10) { bg.beginPath(); bg.moveTo(-30,y); bg.lineTo(20,y); bg.stroke(); }
      bg.lineWidth = 5; bg.strokeStyle = '#f8fafc'; bg.strokeRect(-30,356,50,163); bg.restore();
    }
    bg.restore(); backgroundBuilds++;
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
  function render(now) {
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.drawImage(background, 0, 0);
    ctx.save(); ctx.translate(transform.x, transform.y); ctx.scale(transform.scale, transform.scale);
    for (const side of ['a', 'b']) teams[side].forEach((p, i) => {
      if (!Number.isFinite(p.x) || !Number.isFinite(p.y) || p.x < -40 || p.x > 1440 || p.y < -40 || p.y > 915) return;
      const r = playerRadius(p), base = side === 'a' ? '#3181de' : '#db5c50';
      const kit = color(p.color, color(theme['team_' + side + '_color'], base));
      circle(ctx, p.x + 2, p.y + 4, r + 1, 'rgba(0,0,0,.24)');
      if (selection?.team === side && selection.index === i) circle(ctx, p.x, p.y, r + 7, 'transparent', '#ffe59a', 3);
      circle(ctx, p.x, p.y, r, i === 0 ? color(theme['keeper_color_' + side], kit) : kit, '#f2f8f4', 2);
      if (i === 0) { ctx.strokeStyle = '#f6df92'; ctx.lineWidth = 3; ctx.beginPath(); ctx.arc(p.x, p.y, r - 5, 0, Math.PI * 2); ctx.stroke(); }
      const heading = Math.atan2(ball.y - p.y, ball.x - p.x);
      circle(ctx, p.x + Math.cos(heading) * (r - 1), p.y + Math.sin(heading) * (r - 1), 3, '#f5f8f3');
      ctx.font = `700 ${Math.max(16, r * .88)}px sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.lineWidth = 3; ctx.strokeStyle = 'rgba(0,0,0,.45)'; ctx.strokeText(String(i + 1), p.x, p.y + 1);
      ctx.fillStyle = '#fff'; ctx.fillText(String(i + 1), p.x, p.y + 1);
    });
    if (referee && referee.x >= 0 && referee.y >= 0) {
      const {x,y} = referee;
      circle(ctx, x+1, y+3, 12, 'rgba(0,0,0,.25)');
      circle(ctx, x, y, 11, color(theme.ref_color, '#f3d575'), '#172e2a', 3);
      ctx.font = '700 12px sans-serif'; ctx.textAlign = 'center'; ctx.fillStyle = '#172e2a'; ctx.fillText('R', x, y + 1);
      if (whistleAt !== null && now - whistleAt < 800) {
        ctx.strokeStyle = '#fff1b6'; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x,y,17+(now-whistleAt)/60,-.7,.7); ctx.stroke(); loop.invalidate();
      } else whistleAt = null;
    }
    if (aim) {
      const {home,pull} = aim, length = 40 + pull.power * 2, angle = pull.kickAngle;
      const end = {x:home.x+Math.cos(angle)*length,y:home.y+Math.sin(angle)*length};
      ctx.strokeStyle = '#ffe59a'; ctx.lineWidth = 4; ctx.setLineDash([9,7]);
      ctx.beginPath(); ctx.moveTo(home.x,home.y); ctx.lineTo(end.x,end.y); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = '#ffe59a'; ctx.beginPath(); ctx.moveTo(end.x,end.y);
      ctx.lineTo(end.x-Math.cos(angle-.45)*19,end.y-Math.sin(angle-.45)*19);
      ctx.lineTo(end.x-Math.cos(angle+.45)*19,end.y-Math.sin(angle+.45)*19); ctx.closePath(); ctx.fill();
    }
    const lift = Math.min(85, Math.max(0, ball.z) * .35), br = radius * (1 + Math.min(.35, lift / 200));
    circle(ctx, ball.x+2, ball.y+4, radius*.9, 'rgba(0,0,0,.32)');
    if (lift > 2) { ctx.strokeStyle = 'rgba(255,255,255,.4)'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(ball.x,ball.y); ctx.lineTo(ball.x,ball.y-lift); ctx.stroke(); }
    circle(ctx, ball.x, ball.y-lift, br, color(theme.ball_color, '#f8fafc'), '#18302d', 2);
    ctx.fillStyle = '#263c39'; ctx.beginPath();
    for (let i=0;i<5;i++) { const a=i*Math.PI*2/5-Math.PI/2; const x=ball.x+Math.cos(a)*br*.43,y=ball.y-lift+Math.sin(a)*br*.43; i?ctx.lineTo(x,y):ctx.moveTo(x,y); }
    ctx.closePath(); ctx.fill();
    if (goal && now - goal.start < 1300) {
      ctx.globalAlpha = Math.min(1, (1300-(now-goal.start))/350);
      ctx.fillStyle = '#122c29'; ctx.fillRect(535, 365, 330, 130);
      ctx.font = '700 58px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillStyle = '#fff3c1'; ctx.fillText('GOAL',700,430);
      ctx.globalAlpha = 1; loop.invalidate();
    } else goal = null;
    ctx.restore(); frames++;
  }
  const loop = createRenderLoop({element:container,render});
  const observer = new ResizeObserver(resize); observer.observe(container);
  window.addEventListener('resize', resize); resize(); loop.invalidate();
  const setBall = (x,y,z=0) => { if (ball.x===x && ball.y===y && ball.z===z) return; ball={x,y,z}; loop.invalidate(); };
  return {
    canvas, loop, setBall,
    setRoster(a=[],b=[]) {
      const stamp=JSON.stringify([a,b]); if(stamp===rosterStamp)return;
      rosterStamp=stamp; teams={a:a.map(p=>({...p})),b:b.map(p=>({...p}))}; loop.invalidate();
    },
    setReferee(x,y) { const next=Number.isFinite(x)&&Number.isFinite(y)&&x>=0&&y>=0?{x,y}:null;
      if(next?.x===referee?.x&&next?.y===referee?.y)return;referee=next;loop.invalidate(); },
    setBallSize(size) { const next=12*({small:.82,large:1.18}[size]||1); if(next!==radius){radius=next;loop.invalidate();} },
    setSelection(team,index,active) {const next=active?{team,index}:null;if(JSON.stringify(next)===JSON.stringify(selection))return;selection=next;loop.invalidate();},
    setAim(home,pull) {aim=home?{home,pull}:null;loop.invalidate();},
    clearAim() {if(aim){aim=null;loop.invalidate();}},
    setTheme(value) {const next={...theme,...value};if(JSON.stringify(next)===JSON.stringify(theme))return;
      theme=next;paintBackground();loop.invalidate();},
    celebrate() {goal={start:loop.now()};loop.invalidate();},
    whistle() {whistleAt=loop.now();loop.invalidate();},
    setQuality(value) {if(!['auto','1080p','1440p','4k'].includes(value))return;mode=value;try{localStorage.setItem('agent-soccer-2d-quality',mode);}catch(_){}resize();},
    get quality() {return mode;},
    point:(x,y)=>pitchPoint(x,y,canvas.getBoundingClientRect()),
    clientPoint(x,y) {const rect=canvas.getBoundingClientRect(),t=pitchTransform(rect.width,rect.height);return{x:rect.left+t.x+x*t.scale,y:rect.top+t.y+y*t.scale};},
    hitTest(x,y,legal,state) {const point=pitchPoint(x,y,canvas.getBoundingClientRect());if(!point)return null;
      const t=pitchTransform(canvas.clientWidth,canvas.clientHeight);let best=null,distance=Infinity;
      for(const i of legal.indices){const p=legal.useBall?state.ball:state['players_'+legal.team]?.[i];if(!p)continue;
        const d=Math.hypot(point.x-p.x,point.y-p.y),r=Math.max(playerRadius(p)+6,16/t.scale);
        if(d<r&&d<distance){best=i;distance=d;}}
      return best;
    },
    snapshot:()=>({renderer:'canvas2d',frames,backgroundBuilds,quality:mode,width:canvas.width,height:canvas.height,
      ball:{...ball},players_a:teams.a.map(p=>({...p})),players_b:teams.b.map(p=>({...p})),referee,selection,loop:loop.stats()}),
    dispose(){observer.disconnect();window.removeEventListener('resize',resize);loop.dispose();canvas.remove();},
  };
}
