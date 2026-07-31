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
- **Formation push (Path B)**: Team A FWD at x=405, Team B FWD at x=595 (re-tuned from x=300/700). Ball at center has ~95 px clear space — Power=20 can now reach the ball (max travel ~95 px), creating a smooth Power gradient instead of a cliff.

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
- `index_3d.html` — 510-instance InstancedMesh crowd ring around the pitch (billboard sprites on a sprite-sheet shader), palette-driven (`CUST_CROWD_PALETTE`: classic/rainbow/mono/team_a/team_b), bob animation, goal cheer (`triggerCrowdCheer` + `SoundManager.crowdCheer()`), ambient noise via `crowdAmbient()`.
- **Restored (was never wired)**: `createCrowd()` was defined but never called — `crowdData` stayed `null`, so the crowd never rendered. Now called in init after customization load; `frustumCulled = false` set (instances far from the small plane geometry).
- **Billboard shader fix**: original shader computed billboard axes with `normalize(cross(cameraPosition - wp.xyz, vec3(0,1,0)))` in the vertex shader — compiles/links with all uniforms bound but rasterizes ZERO fragments (reproduced headless on SwiftShader; CPU replica of the same math puts 1329/2040 vertices in-frustum). Fixed by feeding camera right/up axes as `uCamRight`/`uCamUp` uniforms (updated per-frame in `animate()` from `camera.matrixWorld` columns) — standard pattern, verified rendering (12k+ pixel A/B diff).

### Routes (`app.py`)
- `/` — redirects to `/play3d`
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
- `index_3d.html` — Three.js 3D game view (human vs AI), stat-aware mesh rendering, spatial audio, goal particles
- `replay_3d.html` — Three.js 3D replay viewer, same rendering + audio/particles
- `customize.html` — Point-buy allocator with player cards, color picker, save button
- `login.html` / `register.html` — Auth pages (canvas animations removed, static CSS backgrounds)

### AI agents
- All 7 agents use `simulate_kick(state, pidx, angle, power)` to evaluate outcomes
- `policy_iteration.py` and `value_iteration.py` account for Power stat in player selection
- AI Arena benchmarks run all agents against each other with win-rate/accuracy/heatmap tables

### Tests (48 passing)
- `test_stats.py` — 11 stat-specific tests (mappings, injection, backward compat, power/weight/agility effects)
- `test_pymunk.py` — 5 pymunk baseline tests
- `test_penalty.py` — 27 penalty shootout tests
- `test_vertical.py` — 5 vertical/loft tests
- `balance_test.py` — Balance test script (not pytest — run directly: `python balance_test.py`)

## Key physics constants

| Constant | Value | Notes |
|----------|-------|-------|
| FIELD_W | 1000 px | Field width |
| FIELD_H | 625 px | Field height |
| _MARGIN | 20 px | Wall inset (goal at 20 and 980) |
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

1. **Formation gap re-tuned (Path C)**: atk_x moved from 300→405 / 700→595, reducing kicker-to-ball gap from 200→95 px. Power=20 now makes contact with the ball (max travel ~95 px). Verified: all Power levels (20–100) from that position reach near goal (max_x ~957–962). The gradient is now continuous rather than a cliff.
2. **Angles ≥10° miss the ball** at all power levels — kicker moves too diagonally, collision normal is vertical, no horizontal momentum transfers to ball.
3. **3D view uses player radius from stats** — confirmed working in both 3D templates.
4. **Referee is created with default stats** (not per-player) — intentional, referee doesn't kick.
5. **Online multiplayer removed** — `/online` and `/join/<room>` redirect to `/play3d`. Backend API endpoints kept for potential 3D online rebuild.

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
