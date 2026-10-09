# Canvas 2D matches

Live games, tournament replays, shared highlights, loss review and live spectators
now use a full-pitch, top-down Canvas 2D view. No WebGL context, Three.js bundle,
3D textures or animated stadium geometry is loaded by these pages.

`/play2d` and `/replay2d/<tid>/<match_id>` are available directly. Existing
`/play3d` and `/replay3d` links still work and serve the same 2D pages. The Flask
endpoint names are retained so existing redirects and saved links keep working.

## Rendering and controls

- `static/js/game/pitch-2d.js` owns drawing, screen-to-pitch coordinates and hit
  testing. The turf, markings, nets and static stands are cached on a second canvas.
- Players use their physical size and team colors, numbered markers and distinct
  keeper rings. The ball's height indicator is cosmetic; coordinates stay in server
  space. Referee positions follow the server and hide when off the pitch.
- Drag back and release, touch selection, keyboard aiming, player selection and
  the direction/power controls use the existing shared input controller.
- `playback-2d.js` uses recorded simulation timestamps and the existing bounded
  interpolation sampler for the ball and rosters. Legacy trajectories still work.
- `render-on-demand.js` coalesces draws, stops when idle, and pauses its animation
  clock while the pitch is hidden or outside the viewport. Goal and whistle
  effects are finite. Online polling continues independently.
- Auto resolution follows device pixel ratio up to 2. Optional 1080p, 1440p and 4K
  use long edges of 1920, 2560 and 3840 pixels. Allocation is capped at 4096 pixels
  per edge and 10 million pixels per canvas. The setting uses its own storage key,
  so an old saved 3D quality preference does not force a large canvas.

SoundManager retains synthesized kicks, bounces, goals, crowd ambience and referee
whistles, with the existing gesture unlock and mute control. Camera controls and
3D-only sky/weather/crowd presets have no effect on this renderer. Pitch, team,
keeper, ball, net, field-line and referee colors are supported; other saved
customization choices remain stored for compatibility.

## Verification

```sh
python tools/run_tests.py
node --test tests/frontend/*.mjs
python tools/browser/verify_2d.py
python tools/browser/capture_readme.py
```

The 2D browser verifier disables WebGL and external integrations. It exercises
real local moves, the human/AI turn cycle, replay and highlight playback, online
participants and a guest spectator, mobile selection, idle draws and resolution.
Screenshots and the report are saved under the system temporary directory in
`agent-soccer-2d`. The README capture writes actual screenshots to
`docs/assets/readme`.

Verified on 2026-10-09: 686 backend tests passed (4 integration-dependent skips),
69 frontend unit tests passed, 41 Canvas browser checks passed, and 23 dialog
checks passed. Canvas browser checks reported zero JavaScript exceptions. Desktop,
mobile and replay screenshots were inspected, and README screenshots refreshed.

The old `index_3d.html`, `replay_3d.html`, Three.js assets and 3D-specific browser
harnesses remain as reference material. They are not used by the game routes;
their historical verification results do not certify the new Canvas views.
