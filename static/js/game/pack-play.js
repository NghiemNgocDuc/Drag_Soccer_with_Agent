/* Draw an authored table and play the physics bot. No animation loop while idle. */
(function () {
  const canvas = document.getElementById("pack-canvas");
  const ctx = canvas.getContext("2d");
  const statusEl = document.getElementById("pack-status");
  const scoreEl = document.getElementById("pack-score");
  const helpEl = document.getElementById("pack-help");
  const form = document.getElementById("pack-form");
  const presetRow = document.getElementById("pack-presets");
  const nativeRow = document.getElementById("pack-native");
  const playBtn = document.getElementById("pack-play");
  const updateBtn = document.getElementById("pack-update");
  const jsonBox = document.getElementById("pack-json");
  const family = document.getElementById("pack-family");
  const resumeBtn = document.getElementById("pack-resume");
  const modeEl = document.getElementById("pack-mode");
  const titleEl = document.getElementById("pack-table-title");

  let geometry = null;
  let positions = null;
  let viewState = null;
  let playing = false;
  let busy = false;
  let active = false;
  let baseSpec = {};
  let pointerId = null;
  let aiming = null;
  let keyboard = { angle: 0, power: 62, on: false };

  function say(text) { statusEl.textContent = text; }
  function setBusy(value) {
    busy = value;
    form.querySelectorAll("input, select, textarea, button").forEach(el => { el.disabled = value; });
    resumeBtn.disabled = value;
    document.querySelector(".pack-layout").setAttribute("aria-busy", String(value));
  }
  function setMode(live) {
    active = live;
    modeEl.textContent = live ? (viewState?.game_over ? "Finished" : "Match live") : "Preview";
    canvas.setAttribute("aria-disabled", String(!live));
    playBtn.textContent = live ? "Start a new match" : "Play the bot";
  }
  function canShoot() { return active && !busy && !playing && viewState?.turn === "A" && !viewState.game_over; }
  function cancelAim() { aiming = null; pointerId = null; keyboard.on = false; draw(); }

  function specFromForm() {
    const pasted = jsonBox.value.trim();
    if (pasted) {
      let data;
      try { data = JSON.parse(pasted); }
      catch (_err) { throw new Error("The pasted table is not valid JSON."); }
      if (!data || typeof data !== "object" || Array.isArray(data)) {
        throw new Error("Paste one table object.");
      }
      return data;
    }
    const read = (id) => document.getElementById(id).value;
    const count = Number(read("pack-bumpers"));
    const keepBumpers = Array.isArray(baseSpec.bumpers) && baseSpec.bumpers.length === count
      && Number(read("pack-w")) === baseSpec.field_w && Number(read("pack-h")) === baseSpec.field_h;
    return {
      ...baseSpec,
      family: family.value,
      name: read("pack-name"),
      field_w: Number(read("pack-w")),
      field_h: Number(read("pack-h")),
      gravity: Number(read("pack-gravity")),
      win_score: Number(read("pack-win")),
      ball_r: Number(read("pack-ball")),
      ball_bounce: Number(read("pack-bounce")),
      striker_r: Number(read("pack-mallet")),
      pocket_r: Number(read("pack-pocket")),
      bumpers: keepBumpers ? baseSpec.bumpers : count,
      damping: Number(read("pack-damping")) || undefined,
      striker_speed: Number(read("pack-speed")) || undefined,
    };
  }

  function fillForm(spec) {
    baseSpec = structuredClone(spec);
    const set = (id, value) => { document.getElementById(id).value = value; };
    set("pack-name", spec.name || "Table game");
    family.value = spec.family || "strike";
    set("pack-w", spec.field_w);
    set("pack-h", spec.field_h);
    set("pack-gravity", spec.gravity || 0);
    set("pack-win", spec.win_score);
    set("pack-ball", spec.ball ? spec.ball.r : spec.ball_r);
    set("pack-bounce", spec.ball ? spec.ball.bounce : spec.ball_bounce);
    set("pack-mallet", spec.striker ? spec.striker.r : (spec.striker_r || 28));
    set("pack-pocket", spec.pocket_r || 22);
    set("pack-bumpers", Array.isArray(spec.bumpers) ? spec.bumpers.length : (spec.bumpers || 0));
    set("pack-damping", spec.damping || "");
    set("pack-speed", spec.striker ? spec.striker.speed : (spec.cue ? spec.cue.speed : (spec.striker_speed || "")));
    syncFamily();
  }

  function syncFamily() {
    const pot = family.value === "pot";
    document.getElementById("pack-gravity-label").hidden = pot;
    document.getElementById("pack-mallet-label").hidden = pot;
    document.getElementById("pack-pocket-label").hidden = !pot;
    helpEl.textContent = pot
      ? "Drag from the cue ball toward the shot you want. Release to strike. Pot the yellow ball. Scratching the cue cancels the point."
      : "Drag from your mallet through the ball. A longer drag hits harder. Release to play the shot. First to the target wins.";
  }

  async function post(url, body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(url, body === undefined ? { signal: controller.signal, cache: "no-store" } : {
        method: "POST", signal: controller.signal,
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (response.redirected || !response.headers.get("content-type")?.includes("application/json")) {
        throw new Error("Your session may have expired. Sign in again, then resume your saved match.");
      }
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "The table could not be prepared.");
      return data;
    } catch (err) {
      if (err.name === "AbortError") throw new Error("The request timed out. Use Resume saved match to check its result.");
      throw err;
    } finally { clearTimeout(timer); }
  }

  function show(data) {
    geometry = data.view ? data.view.geometry : data.geometry;
    positions = data.view ? data.view.bodies : data.bodies;
    viewState = data.view || data;
    titleEl.textContent = geometry.name;
    if (data.spec) fillForm(data.spec);
    scoreEl.textContent = scoreLine(viewState);
    fit();
  }

  function scoreLine(state) {
    const target = geometry ? geometry.win_score : "";
    const who = state.game_over
      ? (state.winner === "A" ? "You win." : "The bot wins.")
      : (state.turn === "A" ? "Your shot." : "Bot's shot.");
    return "You " + state.score_a + "  ·  Bot " + state.score_b + "  ·  first to " + target + "  ·  " + who;
  }

  async function renderTable() {
    if (busy || playing) return;
    setBusy(true);
    cancelAim();
    try {
      const data = await post("/api/packs/preview", specFromForm());
      show(data);
      setMode(false);
      scoreEl.textContent = "First to " + geometry.win_score + " points · You vs bot";
      say("Preview ready in " + data.elapsed_ms + " ms. Choose Play the bot to begin.");
    } catch (err) {
      say(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function startMatch() {
    if (busy || playing || !form.reportValidity()) return;
    setBusy(true);
    cancelAim();
    try {
      const data = await post("/api/packs/start", specFromForm());
      show(data);
      setMode(true);
      say("Match on. " + (data.elapsed_ms != null ? "Ready in " + data.elapsed_ms + " ms. " : "") + "Your shot.");
      if (window.innerWidth <= 760) document.querySelector(".pack-stage").scrollIntoView({ block: "start" });
    } catch (err) {
      setMode(false);
      say(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function resumeMatch() {
    if (busy || playing) return;
    setBusy(true);
    cancelAim();
    try {
      const data = await post("/api/packs/state");
      if (!data.active || !data.match_id) {
        setMode(false);
        say("No saved match. Choose a table and play the bot.");
        return false;
      }
      jsonBox.value = "";
      [...presetRow.children].forEach(child => child.setAttribute("aria-pressed", "false"));
      show(data);
      setMode(true);
      say(data.game_over ? scoreLine(data) : "Saved match restored. Your shot.");
      return true;
    } catch (err) { setMode(false); say(err.message); return false; }
    finally { setBusy(false); }
  }

  function fit() {
    if (!geometry) return;
    const bounds = canvas.parentElement.getBoundingClientRect();
    const view = geometry.view;
    const aspect = view.w / view.h;
    let width = Math.max(1, bounds.width - 8);
    let height = width / aspect;
    const maxH = Math.min(window.innerHeight * 0.68, 620);
    if (height > maxH) {
      height = maxH;
      width = height * aspect;
    }
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.style.width = width + "px";
    canvas.style.height = height + "px";
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw();
  }

  function sx(x) {
    const view = geometry.view;
    return (x - view.x) / view.w * parseFloat(canvas.style.width);
  }
  function sy(y) {
    const view = geometry.view;
    return (y - view.y) / view.h * parseFloat(canvas.style.height);
  }

  function draw() {
    if (!geometry || !positions) return;
    const w = parseFloat(canvas.style.width);
    const h = parseFloat(canvas.style.height);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = geometry.rail;
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = geometry.felt;
    ctx.fillRect(sx(0), sy(0), sx(geometry.w) - sx(0), sy(geometry.h) - sy(0));
    ctx.strokeStyle = "rgba(255,247,238,.35)";
    ctx.lineWidth = 2;
    ctx.strokeRect(sx(0), sy(0), sx(geometry.w) - sx(0), sy(geometry.h) - sy(0));
    geometry.goals.forEach(function (goal) {
      ctx.fillStyle = goal.side === "A" ? "#7b2943" : "#1d4e89";
      ctx.fillRect(sx(goal.x), sy(goal.y), sx(goal.x + goal.w) - sx(goal.x), sy(goal.y + goal.h) - sy(goal.y));
    });
    geometry.pockets.forEach(function (pocket) {
      ctx.beginPath();
      ctx.fillStyle = "#140e0c";
      ctx.arc(sx(pocket.x), sy(pocket.y), Math.abs(sx(pocket.x + pocket.r) - sx(pocket.x)), 0, Math.PI * 2);
      ctx.fill();
    });
    geometry.bumpers.forEach(function (bumper) {
      ctx.beginPath();
      ctx.fillStyle = "#efc995";
      ctx.arc(sx(bumper.x), sy(bumper.y), Math.abs(sx(bumper.x + bumper.r) - sx(bumper.x)), 0, Math.PI * 2);
      ctx.fill();
    });
    geometry.pieces.forEach(function (piece) {
      const at = positions[piece.id];
      if (!at || at[0] < -100) return;
      const radius = Math.abs(sx(at[0] + piece.r) - sx(at[0]));
      ctx.beginPath();
      ctx.fillStyle = piece.color;
      ctx.arc(sx(at[0]), sy(at[1]), radius, 0, Math.PI * 2);
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = "rgba(255,253,248,.8)";
      ctx.stroke();
    });
    if (geometry.gravity > 0) {
      ctx.fillStyle = "rgba(255,247,238,.75)";
      ctx.font = "12px 'Be Vietnam Pro', sans-serif";
      ctx.fillText("gravity down", 12, h - 12);
    }
    drawAim();
  }

  function drawAim() {
    const origin = aimOrigin();
    if (!origin || !canShoot()) return;
    let tip = null;
    if (aiming) tip = aiming;
    else if (keyboard.on) {
      const rad = keyboard.angle * Math.PI / 180;
      const length = 28 + keyboard.power * 1.15;
      tip = { x: origin.x + Math.cos(rad) * length, y: origin.y + Math.sin(rad) * length, power: keyboard.power };
    }
    if (!tip) return;
    ctx.beginPath();
    ctx.strokeStyle = "#fff7ee";
    ctx.lineWidth = 3;
    ctx.moveTo(sx(origin.x), sy(origin.y));
    ctx.lineTo(sx(tip.x), sy(tip.y));
    ctx.stroke();
  }

  function aimOrigin() {
    if (!geometry || !positions) return null;
    const id = geometry.family === "pot" ? "cue" : "a";
    const at = positions[id];
    return at ? { x: at[0], y: at[1] } : null;
  }

  function worldAt(event) {
    const rect = canvas.getBoundingClientRect();
    const view = geometry.view;
    return {
      x: view.x + (event.clientX - rect.left) / rect.width * view.w,
      y: view.y + (event.clientY - rect.top) / rect.height * view.h,
    };
  }

  function shotFrom(pointer) {
    const origin = aimOrigin();
    if (!origin) return null;
    const dx = pointer.x - origin.x;
    const dy = pointer.y - origin.y;
    const distance = Math.hypot(dx, dy);
    if (distance < 12) return null;
    const reach = geometry.family === "pot" ? geometry.w * 0.34 : geometry.w * 0.28;
    return {
      angle: Math.atan2(dy, dx) * 180 / Math.PI,
      power: Math.max(12, Math.min(100, distance / reach * 100)),
      x: pointer.x,
      y: pointer.y,
    };
  }

  canvas.addEventListener("pointerdown", function (event) {
    if (!geometry || !canShoot() || event.button !== 0 || pointerId !== null) return;
    const origin = aimOrigin();
    const at = worldAt(event);
    const piece = geometry.pieces.find(item => item.id === viewState.aim);
    const hitRadius = Math.max((piece?.r || 28) * 1.8, 24 * geometry.view.w / canvas.getBoundingClientRect().width);
    if (!origin || Math.hypot(at.x - origin.x, at.y - origin.y) > hitRadius) return;
    canvas.focus({ preventScroll: true });
    pointerId = event.pointerId;
    canvas.setPointerCapture(event.pointerId);
    keyboard.on = false;
    aiming = worldAt(event);
    draw();
  });
  canvas.addEventListener("pointermove", function (event) {
    if (!aiming || event.pointerId !== pointerId) return;
    const shot = shotFrom(worldAt(event));
    aiming = shot || worldAt(event);
    draw();
  });
  canvas.addEventListener("pointerup", function (event) {
    if (!aiming || event.pointerId !== pointerId) return;
    const shot = shotFrom(worldAt(event));
    aiming = null;
    pointerId = null;
    draw();
    if (shot) sendShot(shot.angle, shot.power);
  });
  canvas.addEventListener("pointercancel", cancelAim);
  canvas.addEventListener("lostpointercapture", cancelAim);
  canvas.addEventListener("blur", cancelAim);
  window.addEventListener("blur", cancelAim);
  document.addEventListener("visibilitychange", () => { if (document.hidden) cancelAim(); });

  canvas.addEventListener("keydown", function (event) {
    if (event.key === "Escape") { cancelAim(); return; }
    if (!canShoot() || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "ArrowLeft") keyboard.angle -= 5;
    else if (event.key === "ArrowRight") keyboard.angle += 5;
    else if (event.key === "ArrowUp") keyboard.power = Math.min(100, keyboard.power + 4);
    else if (event.key === "ArrowDown") keyboard.power = Math.max(12, keyboard.power - 4);
    else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (event.repeat) return;
      sendShot(keyboard.angle, keyboard.power);
      return;
    } else return;
    event.preventDefault();
    keyboard.on = true;
    keyboard.angle = (keyboard.angle + 360) % 360;
    say("Aim " + Math.round(keyboard.angle) + "°, power " + Math.round(keyboard.power) + ". Enter plays the shot.");
    draw();
  });

  async function sendShot(angle, power) {
    if (!canShoot()) return;
    playing = true;
    setBusy(true);
    say("Shot in. The bot is reading the table.");
    try {
      const data = await post("/api/packs/move", { angle: angle, power: power,
        match_id: viewState.match_id, expected_shot: viewState.shot });
      for (const clip of data.clips || []) {
        await playClip(clip);
      }
      positions = data.bodies;
      viewState = data;
      setMode(true);
      scoreEl.textContent = scoreLine(data);
      draw();
      say(resultLine(data));
    } catch (err) {
      setMode(false);
      say(err.message + " Use Resume saved match before shooting again.");
    } finally {
      playing = false;
      setBusy(false);
      draw();
    }
  }

  function resultLine(data) {
    if (data.game_over) return data.winner === "A" ? "You win." : "The bot wins.";
    const bot = data.bot;
    if (!bot) return "Your shot.";
    const found = bot.found_goal ? " It found a scoring shot." : "";
    return "The bot searched " + bot.sims + " shots in " + bot.ms + " ms." + found;
  }

  function playClip(clip) {
    const frames = clip.frames || [];
    if (!frames.length) return Promise.resolve();
    if (document.hidden || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      positions = frames[frames.length - 1].bodies;
      draw();
      return Promise.resolve();
    }
    const duration = Math.max(frames[frames.length - 1].t * 1000, 180);
    const started = performance.now();
    return new Promise(function (resolve) {
      function tick(now) {
        const t = Math.min(frames[frames.length - 1].t, (now - started) / 1000);
        positions = sample(frames, t);
        draw();
        if (now - started < duration) requestAnimationFrame(tick);
        else finish();
      }
      function finish() {
        const note = clip.scored_by === "A" ? "You scored."
          : clip.scored_by === "B" ? "The bot scored."
          : clip.scratch ? "Scratch. No point." : "";
        if (note) say(note);
        setTimeout(resolve, note ? 380 : 0);
      }
      requestAnimationFrame(tick);
    });
  }

  function sample(frames, t) {
    let next = frames[frames.length - 1];
    let prev = frames[0];
    for (let i = 1; i < frames.length; i++) {
      if (frames[i].t >= t) { next = frames[i]; prev = frames[i - 1]; break; }
    }
    const span = next.t - prev.t || 1;
    const mix = Math.min(1, Math.max(0, (t - prev.t) / span));
    const bodies = {};
    Object.keys(next.bodies).forEach(function (key) {
      const a = prev.bodies[key] || next.bodies[key];
      const b = next.bodies[key];
      bodies[key] = [a[0] + (b[0] - a[0]) * mix, a[1] + (b[1] - a[1]) * mix];
    });
    return bodies;
  }

  family.addEventListener("change", syncFamily);
  form.addEventListener("input", () => {
    setMode(false);
    cancelAim();
    scoreEl.textContent = "Draft changes · Preview to update the table";
    say("Your saved match is unchanged. Preview this draft or start a new match.");
    [...presetRow.children].forEach(child => child.setAttribute("aria-pressed", "false"));
  });
  resumeBtn.addEventListener("click", resumeMatch);
  updateBtn.addEventListener("click", renderTable);
  form.addEventListener("submit", function (event) { event.preventDefault(); startMatch(); });
  window.addEventListener("resize", fit);

  setMode(false);
  setBusy(true);
  post("/api/packs/presets").then(async function (data) {
    (data.presets || []).forEach(function (preset) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = preset.spec.name;
      button.setAttribute("aria-pressed", "false");
      button.addEventListener("click", function () {
        if (busy || playing) return;
        jsonBox.value = "";
        [...presetRow.children].forEach(function (child) { child.setAttribute("aria-pressed", "false"); });
        button.setAttribute("aria-pressed", "true");
        fillForm(preset.spec);
        renderTable();
      });
      presetRow.appendChild(button);
    });
    (data.native || []).forEach(function (game) {
      const link = document.createElement("a");
      link.href = game.href;
      link.textContent = game.name;
      link.title = game.note;
      nativeRow.appendChild(link);
    });
    const first = presetRow.querySelector("button");
    setBusy(false);
    if (!await resumeMatch() && first) first.click();
  }).catch(function () { setBusy(false); say("The table list did not load. You can still preview your own table or resume a saved match."); });
})();
