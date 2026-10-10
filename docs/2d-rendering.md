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
- Player markers scale with the Size stat, at 2.1 times the collision radius for
  readability, with matching pointer targets, team colors, numbers and distinct
  keeper rings. The ball's height indicator is cosmetic; coordinates stay in server
  space. Referee positions follow the server and hide when off the pitch.
- Drag back and release, touch selection, keyboard aiming, player selection and
  the direction/power controls use the existing shared input controller.
- `playback-2d.js` uses recorded simulation timestamps and the existing bounded
  interpolation sampler for the ball and rosters. Legacy trajectories still work.
- New matches use five players per team, with the human striker marked YOU.
  Ordinary kicks stay grounded; all ten players remain on the pitch. Human and
  AI moves share the selected animation speed, capped at four seconds per timed
  move at normal speed. Player contacts retain exact impact samples.
- `render-on-demand.js` coalesces draws, stops when idle, and pauses its animation
  clock while the pitch is hidden or outside the viewport. Goal and whistle
  effects are finite. Online polling continues independently.
- Contact particles and trails share that paused clock. The board stays steady
  and the ball stays circular. Particle storage is bounded, and trails expire
  before idle drawing stops. The live pitch fills the panel width at its natural
  aspect ratio. Short IDE panes scroll vertically instead of shrinking the field
  and players into a small area between wide side gutters.
- Dragging shows an elastic pull line, a ghost disc and a strength ring before
  release. The physical disc remains at its authoritative position while aiming.
- Enlarge pitch expands the board using browser fullscreen, with an in-page
  fallback for embedded previews. The exit button or Escape restores the view.
- Auto resolution follows device pixel ratio up to 2. Optional 1080p, 1440p and 4K
  use long edges of 1920, 2560 and 3840 pixels. Allocation is capped at 4096 pixels
  per edge and 10 million pixels per canvas. The setting uses its own storage key,
  so an old saved 3D quality preference does not force a large canvas.

SoundManager uses goal-only audio in live games and replays: no crowd, kick,
bounce or kickoff sounds. A goal plays one quiet whistle at gain 0.055, one-quarter
of the previous whistle gain. Crowd buffers are not generated. Gesture unlock
and mute controls remain available. Camera controls and
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

Verified on 2026-10-09 after the five-a-side update: 710 backend tests passed
(4 integration-dependent skips), 71 frontend unit tests passed and all 55 Canvas
browser checks passed with zero JavaScript exceptions. Coverage includes physics
limits, striker enforcement, complete penalty rosters, impact playback and
hidden-tab effect expiry. Desktop, striker, mobile and replay screenshots are
available from the browser verifier.

The old `index_3d.html`, `replay_3d.html`, Three.js assets and 3D-specific browser
harnesses remain as reference material. They are not used by the game routes;
their historical verification results do not certify the new Canvas views.
