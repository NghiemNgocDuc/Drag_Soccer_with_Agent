# Smoother gameplay playback

Current five-a-side 2D playback uses a four-second cap per timed move at normal
speed, including AI moves. Player contacts retain their collision samples and
use linear interpolation beside impacts. The human controls striker #5.

This pass improves motion and transitions in the live game, online matches,
spectator playback, and saved replays. It addresses visible timing discontinuities
while keeping server physics authoritative.

## Research and design choices

| Source | Applied to this game |
| --- | --- |
| [Pymunk: game loop and performance](https://www.pymunk.org/en/latest/overview.html) | Retain the fixed simulation timestep and three contact substeps. Smooth presentation between recorded states. |
| [Glenn Fiedler: snapshot interpolation](https://gafferongames.com/post/snapshot_interpolation/) | Reconstruct motion between authoritative snapshots using their timing; avoid separately predicting a colliding pawn's endpoint. |
| [SciPy: shape-preserving Hermite interpolation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.PchipInterpolator.html) | Estimate tangents from neighbouring timed samples, limit reversals, and keep each coordinate within its adjacent sample values. |
| [Three.js: rendering on demand](https://threejs.org/manual/pages/rendering-on-demand.html) | Keep finite animations and coalesced redraws, with no continuous idle rendering. |
| [Three.js: time-based damping](https://threejs.org/docs/pages/MathUtils.html#damp) | Make the penalty camera respond to elapsed time, matching the existing time-based player camera. |

These sources inform the implementation; this game already receives a complete
trajectory per move, so it does not need an additional network interpolation
buffer. Cubic slopes are estimated locally from the received positions. No new
Python or JavaScript dependency, velocity payload, or simulation step is added.

## What changed

The live kicker previously animated to its endpoint in 310 ms while the ball
played a trajectory lasting up to 2.4 seconds. It skipped its physical roster
track during that interval. Now a recorded kicker moves on the same timeline as
the ball and every teammate. The leg pose is cosmetic and cannot move the pawn
away from its recorded position. Older trajectories without a complete pawn
track retain their existing fallback animation.

`static/js/game/trajectory-playback.js` now supplies a shared, time-aware Hermite
sampler for the ball and players. It replaces the ball's sample-index spline and
the players' piecewise linear interpolation. The sampler preserves every recorded
endpoint, smooths speed changes between ordinary samples, bounds each coordinate,
and uses linear segments next to explicit wall and ground contacts. Duplicate
timestamps, short paths, and older boolean bounce metadata have safe fallbacks.
It never extrapolates past the recorded trajectory.

The live scene no longer rewrites input-control DOM state on every animation
frame. Controls still synchronize when a turn starts, completes, or changes.
Player facing follows the visible ball during playback. The penalty camera uses
elapsed time, and a render after a long idle starts with a normal frame delta
instead of treating the idle gap as camera motion.

Goal replays now celebrate when the scoring trajectory ends. The kickoff reset
slides from the final physical positions, eliminating the jump back to the
pre-kick roster. Updated module URLs prevent a cached older module from missing
the new sampler export.

## Validation

```powershell
node --test (Get-ChildItem tests/frontend -Filter *.mjs).FullName
python tools/browser/verify_game_performance.py --sample-ms 800 --output "$env:TEMP/agent-soccer-smooth-gameplay.json"
python tools/browser/verify_ai_responsiveness.py
python tools/browser/verify_player_controls.py
```

The JavaScript suites pass 62 tests, including constant-speed motion with uneven
timestamps at 10/30/60/120/144 FPS, continuous interior velocity during
deceleration, collision bounds, legacy data, and a restart after a long idle.

The playback browser harness passes 23 checks on the live and replay pages.
Its deterministic frame scheduler tests local, online, and replay player/ball
synchronization at 10/30/60/120 FPS; fixture position error is below 0.001 server
pixels. It also checks goal timing, resets, real goal playback, polling order,
resource reuse, and zero JavaScript exceptions. These simulated frame rates test
timing correctness, not the FPS a particular GPU can sustain.

The AI turn-cycle verifier passes 13 checks, and the pointer/keyboard/touch/player
controls verifier passes 55 checks. All three browser suites report zero
JavaScript exceptions. Real AI cycles return striker control, limb poses reset,
and idle scenes stop scheduling frames.

The online fixtures include membership and revision data. Poll-fixture waits are
bounded. Manual AI penalty checks disable elapsed deadlines within the temporary
test template; the separate playback verifier still exercises the real countdown.

This improves visual continuity rather than raising a guaranteed frame rate.
Actual rendering speed still depends on the GPU, scene, and graphics setting;
4K remains available and the existing graphics preference is preserved.
