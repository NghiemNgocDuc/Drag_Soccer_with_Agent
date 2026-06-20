import re

with open('templates/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update canvas size
content = content.replace('<canvas id="game-canvas" width="800" height="500"></canvas>', 
                          '<canvas id="game-canvas" width="1000" height="700"></canvas>')

# 2. Update cssToCv function
old_cssToCv = """function cssToCv(e) {
  const r  = canvas.getBoundingClientRect();
  const sx = W / r.width;
  const sy = H / r.height;
  const x  = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
  const y  = (e.touches ? e.touches[0].clientY : e.clientY) - r.top;
  return { x: x * sx, y: y * sy };
}"""
new_cssToCv = """function cssToCv(e) {
  const r  = canvas.getBoundingClientRect();
  const sx = 1000 / r.width;
  const sy = 700 / r.height;
  const x  = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
  const y  = (e.touches ? e.touches[0].clientY : e.clientY) - r.top;
  return { x: x * sx - 100, y: y * sy - 100 };
}"""
content = content.replace(old_cssToCv, new_cssToCv)

# 3. Remove old audience dots from drawField
old_audience = """  // Audience (dots outside field)
  ctx.fillStyle = '#1e293b';
  for(let i=0; i<150; i++) {
    const x = Math.random() < 0.5 ? Math.random()*20 : W - 20 + Math.random()*20;
    const y = Math.random() * H;
    ctx.fillRect(x, y, 3, 3);
  }
  for(let i=0; i<300; i++) {
    const x = Math.random() * W;
    const y = Math.random() < 0.5 ? Math.random()*20 : H - 20 + Math.random()*20;
    if (x > 20 && x < W-20) ctx.fillRect(x, y, 3, 3);
  }"""
content = content.replace(old_audience, "")

# 4. Update drawDragVisuals definition and power bar coordinates
old_drawDrag_def = "function drawDragVisuals(hx, hy, pullX, pullY, power, kickAngle) {"
new_drawDrag_def = "function drawDragVisuals(hx, hy, pullX, pullY, power, kickAngle, cx, cy) {"
content = content.replace(old_drawDrag_def, new_drawDrag_def)

old_powerbar = "const bw = 54, bh = 8, bx2 = pullX - bw/2, by2 = pullY - 20;"
new_powerbar = "const bw = 54, bh = 8, bx2 = cx - bw/2, by2 = cy - 30;"
content = content.replace(old_powerbar, new_powerbar)

# 5. Update render() calls to drawDragVisuals
old_human_drag = """if (pull !== null) {
    drawDragVisuals(pull.hx, pull.hy, pull.pullX, pull.pullY, pull.power, pull.kickAngle);
  }"""
new_human_drag = """if (pull !== null) {
    drawDragVisuals(pull.hx, pull.hy, pull.pullX, pull.pullY, pull.power, pull.kickAngle, dragX, dragY);
  }"""
content = content.replace(old_human_drag, new_human_drag)

old_ai_drag = """if (aiDragPreview !== null) {
    const d = aiDragPreview;
    drawDragVisuals(d.hx, d.hy, d.pullX, d.pullY, d.power, d.kickAngle);
  }"""
new_ai_drag = """if (aiDragPreview !== null) {
    const d = aiDragPreview;
    drawDragVisuals(d.hx, d.hy, d.pullX, d.pullY, d.power, d.kickAngle, d.pullX, d.pullY);
  }"""
content = content.replace(old_ai_drag, new_ai_drag)

# 6. Update render() start and end
old_render_start = """function render(ballOverride) {
  if (!gameState) return;
  ctx.clearRect(0, 0, W, H);
  drawField();"""

new_render_start = """function render(ballOverride) {
  if (!gameState) return;
  ctx.clearRect(0, 0, 1000, 700);

  // Galaxy background for audience area
  ctx.save();
  const galGrad = ctx.createRadialGradient(500, 350, 100, 500, 350, 700);
  galGrad.addColorStop(0, '#0f172a');
  galGrad.addColorStop(1, '#020617');
  ctx.fillStyle = galGrad;
  ctx.fillRect(0, 0, 1000, 700);
  
  // Stars / Audience dots
  ctx.fillStyle = 'rgba(255,255,255,0.8)';
  for (let i = 0; i < 400; i++) {
    const rx = (Math.sin(i * 12.34) * 0.5 + 0.5) * 1000;
    const ry = (Math.cos(i * 43.21) * 0.5 + 0.5) * 700;
    if (rx < 90 || rx > 910 || ry < 90 || ry > 610) {
      const size = (Math.sin(i * 21.43) * 0.5 + 0.5) * 2 + 0.5;
      ctx.fillStyle = `hsla(${(i*13)%360}, 70%, 80%, ${(Math.sin(i*9.87)*0.5+0.5)*0.8 + 0.2})`;
      ctx.beginPath(); ctx.arc(rx, ry, size, 0, Math.PI*2); ctx.fill();
    }
  }
  ctx.restore();

  ctx.save();
  ctx.translate(100, 100);

  drawField();"""
content = content.replace(old_render_start, new_render_start)

# Now we need to add ctx.restore() to the end of render()
# The end of render() is right after the if (aiDragPreview !== null) block
# Let's find that block and append to it.
content = content.replace(new_ai_drag, new_ai_drag + "\n\n  ctx.restore();")

with open('templates/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("index.html modified for galaxy theme successfully.")
