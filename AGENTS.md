# Agent Soccer — Project State

## One-line
Browser-based 2v2–11v11 3D soccer game where human/AI players take turns kicking, powered by pymunk physics. Players have 4 point-buy stats (Size/Power/Weight/Agility) that meaningfully differentiate gameplay.

## Architecture

- **Backend**: Flask (Python), pymunk physics engine, SQLite + SQLAlchemy
- **Frontend**: Three.js 3D rendering via HTML templates, shared SoundManager (`static/sound.js`)
- **AI models**: 7 agents (greedy, minimax, bayes, monte_carlo, q_learning, value_iteration, policy_iteration) — all use `simulate_kick` for lookahead evaluation
- **Auth**: Flask-Login, custom user model with registration/login
- **3D-only**: All 2D game templates deleted (`index.html`, `online.html`, `replay.html`). Route `/` redirects to `/play3d`.

## What's in place

### Core engine (`models/soccer_logic.py`)
- Per-player stats system: Size→radius, Power→kick_vel, Weight→mass, Agility→player_friction (pivot joint max_force)
- `inject_player_stats(state, team_a, team_b)` called at match start
- Stats persist through `_reset_players` and `_reset_outfield` (goal/half resets)
- Recoil formula: `recoil_vx = -cos(angle) * power * 1.2 * (power_stat/50)`
- Loft/vertical: `_loft_angle(power)` → 0 deg below power=40, (power-40)*0.5 capped at 30 deg above
- **Airborne friction fix (Path B)**: `ball_pivot.max_force` reduced to 10% (1000→100) while `ball_z > 0`, restores on touchdown. This lets lofted passes cover full field distance.
- **Formation push (Path B→C→resize)**: Team A FWD at x=605, Team B FWD at x=795 — **absolute 95px from center** (`FIELD_W/2 ∓ 95`), NOT a width ratio, so the tuned kicker-to-ball gap survives the 1400-wide field. GK/DEF/MID remain width ratios (0.062/0.162/0.281 etc.) and auto-widened with the field — the gap between lines grew (more open space) while the kicker gap stayed 95px (Power=20 floor preserved). Power=20 can still reach the ball (max travel ~95px).

### Formation system
- `_home_positions(count, side)` generates realistic soccer formations with GK, DEF, MID, FWD rows
- Supports 1–11 players, spreads outfield across y-range proportionally
- Index 0 = GK always; indices 1+ = outfield by row (defenders, then midfielders, then forwards)

### 3D Sound & Particles (fully synthesized, zero asset files)
- `static/sound.js` — SoundManager singleton, **all sounds procedurally synthesized** via WebAudio (no audio assets): goal = bass + triangle arpeggio + sine sparkle (2 key variants), whistle = dual detuned squares + vibrato, crowd ambient = stereo formant loop, cheer = stereo roar that **ducks ambient to 35% and restores**. Core API: `attach(ctx)`, `resume()`, `makeImpactBuffer()`, `makeKickBuffer()` (0.16 s), `makeBounceBuffer()` (0.10 s), `goal()`, `whistle()`, `crowdAmbient()`, `crowdCheer()`, `toggleMute()` (covers everything).
- **Gesture unlock IIFE (both 3D templates)**: first `pointerdown`/`keydown`/`touchstart` resumes the context, starts ambient, fires kickoff whistle once (`window._whistlePlayed` guard). Fixes silent-on-load browser gesture lock.
- **Bounce-sound guard**: trajectory frames carry `b` as the *player-position array*; only bounce frames have `b === true` (set in `_sim`). Bounce audio must check `ptA.b === true`, or it fires on every animation frame.
- `index_3d.html` — Three.js `PositionalAudio` for spatial kick/bounce sounds (kick 0.7, bounce 0.45), goal celebration particles, crowd cheer on all goal sites (incl. `autoPenaltyKick`/`triggerAI`).
- `replay_3d.html` — Same sound + particle system in replay viewer (kick 0.7, bounce 0.5, pointerdown-only unlock). Replay data from tournament sims interleaves empty placeholder moves (no trajectory) — page plays them as instant no-ops.
- Verified headless (CDP + `--autoplay-policy=no-user-gesture-required`): unlock flow, buffer durations, whistle-once, ambient gain 0.10, cheer duck/restore, mute behavior, and full playthroughs on both pages — zero console exceptions.

### Stadium Crowd (3D)
- `index_3d.html` — InstancedMesh crowd ring around the pitch (**instance count derived from field size — ~726 on 1400×875**, was 510 on 1000×625; billboard sprites on a sprite-sheet shader), palette-driven (`CUST_CROWD_PALETTE`: classic/rainbow/mono/team_a/team_b), bob animation, goal cheer (`triggerCrowdCheer` + `SoundManager.crowdCheer()`), ambient noise via `crowdAmbient()`.
- **Restored (was never wired)**: `createCrowd()` was defined but never called — `crowdData` stayed `null`, so the crowd never rendered. Now called in init after customization load; `frustumCulled = false` set (instances far from the small plane geometry).
- **Billboard shader fix**: original shader computed billboard axes with `normalize(cross(cameraPosition - wp.xyz, vec3(0,1,0)))` in the vertex shader — compiles/links with all uniforms bound but rasterizes ZERO fragments (reproduced headless on SwiftShader; CPU replica of the same math puts 1329/2040 vertices in-frustum). Fixed by feeding camera right/up axes as `uCamRight`/`uCamUp` uniforms (updated per-frame in `animate()` from `camera.matrixWorld` columns) — standard pattern, verified rendering (12k+ pixel A/B diff).

### Cosmetic Referee (wandering, ball-dodging, zero physics)
- **Server motion** (`models/soccer_logic.py`): `_referee_step(ref_body, ball_body, dt)` — deterministic (no RNG, so `simulate_kick` lookahead stays reproducible): (1) ball stopped within 60px → radial dodge away at 300px/s; (2) moving ball → perpendicular sidestep away from ball's path (dead-center → deterministic side by `ry > by`); (3) else flow patrol `(sin(ry*0.02), cos(rx*0.02))` at 75px/s. Bounces off `_REF_*MIN/MAX` bounds, goal-mouth band guard pulls it out, clamp after each step. Constants: `_REF_WANDER_SPEED=75`, `_REF_DODGE_SPEED=300`, `_REF_PATH_TRIGGER=75`, `_REF_NEAR=60`, `_REF_GOAL_SAFE_X=70`, `_REF_GOAL_SAFE_PAD=12`.
- **No physics presence**: referee is a shape-less `pymunk.Body` (no Circle shape, no pivot) — the ball/players can never collide with it (previously built via `_make_player` so the ball could bounce off the invisible referee).
- **Trajectory sync**: every trajectory frame carries `"ref": {x, y}` (init frame + per-step); `_sim` steps the referee after each 3×`space.step`; state synced in `apply_kick`; sent off-field `(-100,-100)` during penalty shootouts; `game/session.py::push_snapshot` snapshots it (undo/restore).
- **Rendering**: dark slate capsule + white chest band + head + soft shadow, rendered in both `index_3d.html` and `replay_3d.html`. Live game: positioned from `/state` referee (init/reset/undo/setMode/post-move) and lerped from `ref` frames during trajectory animation. Replay: positioned from `ref` frames (init scans for the first *real* trajectory — tournament sims interleave empty placeholder moves — and autoplay now continues past empty moves, a pre-existing bug).
- **Testability hooks** (module-scope is invisible to CDP): `window.triggerAI` (also fixes the latent aivai-button bug — inline `onclick` can't see module-scope functions), `window.__refereePos()`, `window.__replayMeta()`.
- Verified headless (CDP): mesh exists + positioned == `serverToWorld(state.referee)`, wanders during AI-vs-AI and replay autoplay, hidden during penalties, zero console exceptions.

### Sky & Environment (scene presets, both 3D templates)
- Driven by existing `bg_scene` customization field (db/customization.py, values: **night/sunset/cloudy/day/cyber** — `night` is the default and the pre-existing hardcoded look). Was fully ignored by the 3D renderer (third instance after `crowd_palette`; replay_3d had no customization fetch at all — one added).
- Both `index_3d.html` and `replay_3d.html` share a `SCENE_PRESETS` table + `applyScene(name, floodColor)` (module-scope; `window.__setScene` hook for CDP): per-preset sky gradient (procedural canvas texture on an upper-half BackSide sphere radius 3000, `fog:false`), `scene.background`/`fog` color = horizon color, sun color/intensity/position, ambient+hemisphere intensities (night/cloudy boost ambient to keep gameplay visible — visibility floor over realism), 400-point star field (night/cyber only), 4 floodlight masts with emissive panels + non-shadow `PointLight`s (meshes always visible; **lights only when dark**: night 400 / cyber 380 / sunset 150 / day+cloudy 0; tint from `floodlight_color` warm/cool/red/purple), 320-point rain stream (cloudy only; per-frame fall+slant tick in `animate()`).
- Customization read at init (same single fetch — `bg_scene` + `floodlight_color`); changes require page reload (matches existing system: customize.html says "They will apply on the game page").
- Verified headless (CDP): all 5 presets' values exact, pairwise screenshot-distinct, rain animates, floodlight gating per preset, crowd/net/referee/camera hooks intact, cloudy+rain ≥ 70% of day fps (SwiftShader ~44), zero console errors on both pages.
- **Known follow-up (orphaned customization fields still ignored by 3D)**: `grass_shade`, `pitch_pattern`, `field_line_color`, `corner_flag_style`, `stadium_vignette`, `bg_color`, `ball_color`, `ref_color`, `keeper_color_a/b`, `ball_design`, `highlight_style`, `shirt_font`, `goal_effect`, `trail_color`, `power_bar_style` — all saved/settable but unconsumed by the 3D renderer (2D-era fields).

### Routes (`app.py`)
- `/` — **landing page for logged-out visitors** (Phase 0 redesign); redirects logged-in users to `/play3d` (was `@login_required` redirect only)
- `/play3d` — Main 3D game (Three.js)
- `/replay3d/<tid>/<match_id>` — 3D match replay
- `/tournaments/<tid>/watch/<match_id>` — redirects to `/replay3d/...`
- `/online` — redirects to `/play3d` (online 2D removed)
- `/customize` — point-buy stat assignment page (6 player cards, 4 sliders each, 200-pt budget)
- `/reset` — injects saved stats via `inject_player_stats`
- `/my-models` — AI model selection/management
- AI Arena — benchmarking page
- All API routes (move, ai_move, state, etc.) unchanged

### Templates
- `index_3d.html` — Three.js 3D game view (human vs AI), stat-aware mesh rendering, spatial audio, goal particles, cosmetic referee
- `replay_3d.html` — Three.js 3D replay viewer, same rendering + audio/particles + referee
- `customize.html` — Point-buy allocator with player cards, color picker, save button
- `landing.html` / `login.html` / `register.html` — **Phase 0 redesign**: built only on `static/design-system.css` (drop-in replacement for style.css on these pages); auth pages still use the old inline-styled layout classes only where the system didn't replace them
- `login.html` / `register.html` — Auth pages (canvas animations removed, static CSS backgrounds)

### Frontend redesign Phase 0 (design system)
- `static/design-system.css` — single source of truth for the visual language: tokens (brand sky `#0ea5e9` / orange / purple / slate neutrals / semantic colors), one glass recipe (`rgba(255,255,255,.72)` + 20px blur + border `rgba(255,255,255,.65)` + diffused shadows), type scale (Be Vietnam Pro body + Space Grotesk display; **Inter import dropped**), 8px spacing scale, 5 radii, components: `.btn` (primary/secondary/ghost/danger/success/warning, sm/lg/block, shine sweep on solid), `.glass-card`, `.input` + `.input-error`/`.field-error`, `.navbar` + mobile hamburger (`.nav-toggle`/`.nav-links.open`), `.badge` variants, `.modal-overlay`/`.modal`, `.flash` (error/success/info), `.toast`, `.footer`, `.auth-layout` (visual panel + centered card, stacks on mobile).
- Landing page: hero (headline + CTAs + **pure-SVG night-scene mini pitch** — stars, floodlight masts with warm panels, crowd dots, team A/B dots, ball + kick arrow; matches the 3D sky system), 4 feature glass cards (Build Your Own AI / 3D Match Simulation / Tournaments / Custom Stats — inline SVG icons), CTA band, minimal footer. Nav shows Log in/Sign up for guests, Play + avatar for logged-in (route passes `username`).
- Auth pages rebuilt on the system, **all functionality preserved**: login (email/password POST `/auth/login`, demo-account box with copy buttons + toast, `/register` + `/forgot-password` links, flash errors); register (username/email/password/confirm POST `/auth/register`, min-6 hint).
- **Bug fixed (pre-existing)**: register form had no `confirm` field but the server requires it (`if password != confirm: "Passwords do not match."`) — registration could never succeed via the form. Added `<input id="confirm" name="confirm">` (markup-only, no backend change).
- Verified headless (Playwright, port 5090): 34 checks — guest landing, all form fields/actions/links preserved, demo copy toast, register + login flows redirect to `/play3d`, logged-in `/` → `/play3d`, empty-cred login flash renders, nav guest/logged-in branches, hamburger open + aria-expanded, breakpoints (2-col features at 768, 1-col ≤640, hero stacks), zero console errors. Screenshots in `%TEMP%\opencode\redesign_verify\`.
- **Known follow-up**: `forgot_password.html` / `reset_password.html` still use style.css + inline styles (same broken `--text`/`--muted` vars); later phases migrate remaining 11 templates onto design-system.css.

### AI agents
- All 7 agents use `simulate_kick(state, pidx, angle, power)` to evaluate outcomes
- `policy_iteration.py` and `value_iteration.py` account for Power stat in player selection
- AI Arena benchmarks run all agents against each other with win-rate/accuracy/heatmap tables

### Tests (48 passing)
- `test_stats.py` — 11 stat-specific tests (mappings, injection, backward compat, power/weight/agility effects)
- `test_pymunk.py` — 5 pymunk baseline tests
- `test_penalty.py` — 27 penalty shootout tests
- `test_vertical.py` — 5 vertical/loft tests
- `referee_test.py` — 8 referee checks (temp file: `%TEMP%\opencode\referee_test.py`) — determinism, wandering, dodge, zero physics, bounds, state sync
- `balance_test.py` — Balance test script (not pytest — run directly: `python balance_test.py`)

## Key physics constants

| Constant | Value | Notes |
|----------|-------|-------|
| FIELD_W | 1400 px | Field width (was 1000; resized 40% in "field enlarge" task) |
| FIELD_H | 875 px | Field height (was 625; same 1.6:1 aspect) |
| _MARGIN | 20 px | Wall inset (goal at 20 and 1380) |
| _GOAL_H | 163.0 px | **Absolute** goal aperture (frozen when field grew — was `FIELD_H*0.26`; GOAL_Y1=356, GOAL_Y2=519) |
| _GOAL_DEPTH | 50 px | Absolute (unchanged) |
| _PM_LINEAR_FRICTION_P | 1500 | Player friction (Agility-derived: 1000 + stat/100*1000) |
| _PM_LINEAR_FRICTION_B | 1000 | Ball ground friction |
| _BALL_AIR_FRICTION | 100 | Ball airborne friction (10% of ground) |
| PM_MASS_B | 1 | Ball mass |
| PLAYER_R | 20 px | Base player radius (overshadowed by stat-driven) |
| BALL_R | 12 px | Ball radius |

## Stat→physics maps

```
Size    → radius:      12 + (stat/100)*16      → 15.2–24.8 px
Power   → kick_vel:     5 + (stat/100)*10      → 7.0–13.0 px/s/unit
Weight  → mass:         3 + (stat/100)*4        → 3.8–6.2
Agility → friction:  1000 + (stat/100)*1000    → 1200–1800
```

All linear, 1.5–1.86× range from min(20) to max(80).

## Known issues / edges

1. **Formation gap re-tuned (Path C→resize)**: atk_x held at an absolute 95px from center (605/795 on the 1400-wide field; was 405/595 on 1000) — the kicker-to-ball gap stays 95px so the Power=20 "can reach the ball" floor survives the field enlargement. GK/DEF/MID are width ratios and widened with the field. Verify note: the "max_x ~957–962" figure from the old 1000-wide field no longer applies; re-verified on 1400 (see field-resize task: Power=20 from atk_x still makes contact, travel unchanged ~95px+).
2. **Angles ≥10° miss the ball** at all power levels — kicker moves too diagonally, collision normal is vertical, no horizontal momentum transfers to ball.
3. **3D view uses player radius from stats** — confirmed working in both 3D templates.
4. **Referee is created with default stats** (not per-player) — intentional, referee doesn't kick.
5. **Online multiplayer removed** — `/online` and `/join/<room>` redirect to `/play3d`. Backend API endpoints kept for potential 3D online rebuild.
6. **Module-scope vs inline handlers**: game scripts are `<script type="module">`, so inline `onclick` attributes cannot call module functions (the aivai button relied on this). Fixed by `window.triggerAI` export; other inline handlers were audited.
7. **Tournament replays interleave empty placeholder moves** (no trajectory) — replay init scans for the first real trajectory and autoplay skips/continues past empties (fixed).
8. **Field resized 1000×625 → 1400×875 (40%, 1.6:1 kept)**: goal aperture frozen to absolute 163px (GOAL_Y1/Y2 now 356/519, was `FIELD_H*0.26`), atk_x frozen to absolute 95px from center (605/795) so the Power=20 floor survives; GK/DEF/MID, penalty spots, referee bounds, crowd ring are width/height-derived and widened automatically. Camera fixed independently: FOV 65, default pos (-1500, 790, 320), fog 1500–4500, shadow ±900, min/max distance 300/2600 (old camera had near touchline ~8.5× outside frame). `serverToWorld`/`worldToServer` derive from W/2, H/2. **User-model sandbox**: `user_models/runner.py` TEMPLATE + `my_models.html` tutorial updated to 1400×875/goals 356–519/center 700,437.5 — but user-submitted models that hardcoded 1000/312 aim at the old goal line and will miss on the new field (documented, unfixable without user updates).

## Deleted files
- `templates/index.html` — old 2D Canvas game
- `templates/online.html` — old 2D online multiplayer
- `templates/replay.html` — old 2D replay viewer

## Recent git history (top 8)
```
(HEAD)   Formation re-tune: atk_x 300→405 / 700→595 (95px gap) so Power=20 can reach ball
3a73608 Path B fix: push formations back + airborne friction fix for stat differentiation
c2a6ff9 Update README: add new services, research hub, AI Arena, feedback system, and project structure
c03adce Production hardening: add auth services, feedback system, research hub, paper search, analytics, error monitoring
c2e6a68 Add AI Arena — model benchmarking with win rates, shot accuracy, heatmaps, head-to-head matrix
97ebc8d Redesign profile and My Models pages, add soccer AI tutorial, fix field dimensions, clean up unused files
b540ff0 Thread customization (half_length, win_goal_limit, power_cap) through to engine
fd1ef13 Implement all customization features in game canvas
9d5c9f4 Move customization to its own standalone page at /customize with server-side persistence
```

## What would be next

- **Online multiplayer 3D** — rebuild `/online` with Three.js rendering
- **Air friction re-tuning**: `_BALL_AIR_FRICTION=100` is 10% of ground. If lofted passes feel too floaty, raise it. If still short of goal, lower it.
- **Weight/Agility independence**: Currently multiplied into same `max_force` term (`m*fric`). They could become independent levers by splitting the formula.
- **More AI models** that leverage stats in their evaluation.
- **Stat effects on keeper** (currently keeper uses default stats always).
