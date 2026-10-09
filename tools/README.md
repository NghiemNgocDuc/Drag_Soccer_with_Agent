# Game performance verification

For the redesigned pages, [frontend verification](../docs/frontend-design.md)
describes the shared shell, page behavior, and isolated browser commands.
For room lifecycle, Git setup, and the multiplayer browser checks, see
[online play verification](../docs/online-play.md).
For synchronized player/ball motion, interpolation research, camera timing, and
goal transitions, see [gameplay smoothness](../docs/gameplay-smoothness.md).

Run `python tools/run_tests.py` for isolated Python regression checks. External
integrations are disabled before the app is imported; additional pytest
arguments are forwarded, for example `python tools/run_tests.py tests/backend/test_ranked.py`.
The standalone HTTP integration and balance scripts are excluded by default.

The lag fixes remove duplicated stadium rendering, replace replay crowd models
with sprites, update scoreboard textures only when the score changes, preserve
authoritative player positions, serialize online polling, and release replaced
player geometry/materials. Replay playback uses simulation timestamps, retains
wall impacts, and caps each move at 2.4 seconds at normal playback speed. Older
saved replays retain their original cadence.

Physics reads each body position once per snapshot and changes ball resistance
only when its value changes. Prediction and actual play use the same impulse,
contact materials and rolling resistance; [physics details](../docs/physics.md)
describe the redesigned behavior and tuning values.
Bounce metadata no longer overwrites Team B's positions. AI penalties run only
penalty physics. The fixed three substeps per 1/60-second frame are retained,
following [Pymunk's timestep guidance](https://www.pymunk.org/en/latest/pymunk.html#pymunk.Space.step).

AI requests use isolated state copies, four worker slots, and a quick tactical
fallback when the pool is busy. Built-in search stops cooperatively after
120 ms or 12 predictions by default. Arbitrary uploaded Python retains its slot
until execution finishes. [AI responsiveness](../docs/ai-responsiveness.md)
documents coordination, configuration, measured timings, and the new
`tools/benchmarks/profile_ai.py` and `tools/browser/verify_ai_responsiveness.py`
commands. Presence heartbeats skip static assets and run at most
once per user/process every 20 seconds; explicit status changes remain immediate.

## On-demand drawing and 4K

Both 3D views draw only for visual changes, camera movement, and finite gameplay
animations. Idle scenes have no pending animation frame; unchanged live-state
polls do not draw. Hidden or offscreen canvases pause playback and its animation
clock, then resume from the same point. The live match clock updates separately
in the DOM. Decorative crowd, cloud, weather, and LED movement freezes while
idle. This follows [Three.js's on-demand rendering pattern](https://threejs.org/manual/pages/rendering-on-demand.html).

Graphics defaults to 4K and remembers the chosen Auto/1080p/1440p/4K setting.
The live selector is in View Settings; replay has a Graphics selector in its
toolbar. 4K uses a 3840px long edge while retaining the panel's aspect ratio:
3840 by 2160 at 16:9, or 3840 by 1920 for the 2:1 live panel. GPU dimensions and
an 8.3-megapixel limit bound allocation. Auto follows screen density up to 2x.
Higher resolution uses more memory per frame; demand rendering saves idle GPU
work. Sharper procedural textures and supported 2048px shadows add no downloaded
image assets. CSS sizing stays independent of the drawing buffer, following
[Three.js's responsive canvas guidance](https://threejs.org/manual/pages/responsive.html).

Demand-rendering verification passed 38 browser checks,
with zero JavaScript exceptions. The actual live drawing buffer was 3840 by 1920
at a 1060 by 530 CSS size. Idle frame counters remained unchanged on both pages,
including after identical live polls. Hidden playback, camera damping, goals,
quality persistence, and final trajectory positions passed. The combined
JavaScript suites pass 52 tests; hardware allocation fallback is checked using
simulated 2048px limits.

An additional 16 focused checks confirm background clouds stay behind the whole
pitch across player, broadcast, and orbit views without waking an idle renderer.
Five final camera checks confirm the intended 78px captain-follow distance,
stable idle frames, and a clear view after correcting goal-crossbar orientation.

Crowd fans are filtered individually against the camera frustum before GPU
submission. The helper retains original instance matrices and sprite/color
attributes, covers animated poses in its bounds, and avoids buffer uploads when
the visible set is unchanged. Procedural fallback fans use the same filtering. Normal
field/player meshes retain native Three.js culling; moving particle batches
refresh their bounds, and inactive dust is hidden. Off-camera effects still
complete their lifetimes and release their meshes.

Earlier camera visibility verification passed 24 browser checks. A close view submitted
1576/2464 fans in play and 1560/2464 in replay; facing away submitted zero fans.
Returning the camera restored the same instances, and idle rendering stayed
asleep. Eight dedicated JavaScript checks cover attribute alignment, camera return,
screen-edge animation padding, parent/projection changes, and unchanged buffers.

The [stadium redesign](../docs/stadium-experience.md) replaces those original
monochrome fans with detailed seated/cheering spectators and a shared stereo
soundscape. Its verifier passes 48 integrated browser checks and 36 audio
checks, including gesture unlock, master mute, finite celebrations, culling,
and zero idle frames. Five additional JavaScript tests cover audience layout,
shared resources, customization, animated bounds, and camera return.

The [player redesign](../docs/player-controls.md) shares lightweight footballer
models across live play and replay. Event-driven mouse/touch/keyboard controls
use the same turn and aiming rules. Local Human vs Human dispatches the active
team instead of always Team A; Human vs AI keeps captain control. Its browser
harness passes 55 checks covering selected player commands, canceled gestures, input focus,
penalties, online turns, request recovery, mobile layout, and idle resources.

## Measurements from the earlier lag fix

| Measurement | Before | After |
| --- | ---: | ---: |
| Live scene triangles | 311,654 | 53,382 |
| Replay scene triangles | 309,876 | 60,276 |
| Live SwiftShader FPS | 1.44 | 2.84 |
| Replay SwiftShader FPS | 1.88 | 3.53 |

Actual kick computation averaged 27% less time across miss/contact/wall cases,
3/7/11 players, and 50 repetitions per case. All nine authoritative final ball
positions matched the baseline. Rendering measurements use Chromium software
rendering at 1280×800; they measure relative improvement, not expected hardware
FPS. Production Redis/Supabase latency was not measured, and autosaves still run
synchronously.

## Reproduce

```powershell
python tools/benchmarks/profile_physics.py --repeats 50
python tools/benchmarks/profile_physics.py --repeats 20 --profile
python tools/browser/verify_demand_rendering.py --output "$env:TEMP/soccer-demand.json" --screenshots "$env:TEMP/soccer-demand-screenshots"
python tools/browser/verify_demand_rendering.py --polls-only --output "$env:TEMP/soccer-idle-polls.json"
python tools/browser/verify_frustum_culling.py --output "$env:TEMP/soccer-culling.json"
node --test tests/frontend/test_player_controls.mjs tests/frontend/test_player_avatar.mjs tests/frontend/test_stadium_crowd.mjs tests/frontend/test_frustum_culling.mjs tests/frontend/test_render_on_demand.mjs tests/frontend/test_trajectory_playback.mjs
python tools/browser/verify_player_controls.py
python tools/browser/verify_online_controls.py
python tools/browser/verify_online_result.py
python tools/browser/verify_stadium_audio.py
python tools/browser/verify_stadium_experience.py --output "$env:TEMP/agent-soccer-stadium-qa/report.json" --screenshots "$env:TEMP/agent-soccer-stadium-qa"
```

The demand-rendering harness checks at 1080p and performs one actual 4K draw.
Use `--skip-4k` to omit that expensive draw on software rendering. It disables
external integrations and starts a temporary loopback server. Python Playwright
and its Chromium installation are required.

The earlier harness remains available for physics playback and historical scene
comparisons; its idle FPS reading is no longer an active-render throughput
measurement because scenes now intentionally stop drawing:

```powershell
python tools/browser/verify_game_performance.py --baseline --output "$env:TEMP/soccer-before.json"
python tools/browser/verify_game_performance.py --output "$env:TEMP/soccer-after.json"
```

Profiling adds timing overhead; use the first command for timings. The browser
harness disables external integrations and starts a temporary loopback server.
`--baseline` loads HEAD templates with the current backend to isolate rendering
changes. It requires Python Playwright and its Chromium browser installation.

For isolated Python tests without reading live service credentials:

```powershell
python tools/run_tests.py
```

Current isolated verification: 686 Python tests passed with 4 skips, and all 62
JavaScript tests passed. The frontend harness passes 89 browser checks; the
dialog harness passes 23. Focused lesson and loss-analysis checks pass 18 and 8
respectively. Live integration tests and the long balance run are excluded from
the isolated Python command above.

The game playback harness passes 23 browser checks covering idle scoreboard
updates, real goals, 10/30/60 FPS playback, delayed polling, ordered move batches,
penalty countdowns, static player positions, and stable GPU resources after
rebuilds. Removed model IDs are rejected; lesson and loss-analysis defaults now
use supported models. Capped AI matches settle by score while preserving any
existing authoritative result.
