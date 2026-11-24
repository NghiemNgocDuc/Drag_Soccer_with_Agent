# Canvas 2D matches

Live games, tournament replays, shared highlights, loss review and live spectators
now use a full-pitch, top-down Canvas 2D view. No WebGL context, Three.js bundle,
3D textures or animated stadium geometry is loaded by these pages.

`/play2d` and `/replay2d/<tid>/<match_id>` are available directly. Existing
`/play3d` and `/replay3d` links still work and serve the same 2D pages. The Flask
endpoint names are retained so existing redirects and saved links keep working.

## Rendering and controls

- `static/js/game/pitch-2d.js` owns drawing, screen-to-pitch coordinates and hit
  testing. Natural turf shading, markings, nets and the restrained crowd/stand
  details are cached on a second canvas.
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
- Contact particles and trails share that paused clock. The ball remains circular
  while its panels rotate with travel. Particle storage is bounded, and trails expire
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

The turf uses broad, low-contrast fill lighting and muted stands so pitch lines,
players and the ball remain the visual focus. Ball-panel rotation is derived from
recorded movement; it adds no idle animation loop.
## Player artwork update — November 18, 2026

Live matches and replays share small numbered footballers with shirts, shorts,
boots, contrasting keeper kits, and a restrained YOU marker. Saved player/team
colors and keeper colors are respected. The previous metallic disc bevel and
gloss are removed. Artwork still scales with Size, using the same existing hit
targets and physics positions; it adds no idle animation or extra frame loop.

## Game-first match layout — November 23, 2026

The live screen uses a full-width pitch with a compact score/clock/turn row.
New Game, Undo, AI recovery, and enlarged-view controls stay with the game;
direction, power, and Kick sit directly beneath it, including in fullscreen.
The old right-hand control column is replaced by native disclosures for match
settings, the saved game plan, keyboard help, and events. Settings open during
setup, close once at kickoff, then preserve the user's choice during play.
Online connection recovery and Leave match remain outside those disclosures.

`static/css/match-focus.css` owns this live-only presentation. Pitch size remains
width-driven on short screens; the shared replay renderer and physics are
unchanged. New Game exits native or fallback fullscreen before reopening setup.
Kickoff also refreshes the legal player selection, fixing a blank selector and
inactive first button/keyboard kick in human-versus-AI games.

Run `python tools/browser/verify_match_focus.py` for desktop/mobile layout,
disclosure keyboard access, first-kick, and fullscreen-reset checks. Its review
screenshots are in `docs/previews/match-focus/`. The shared 2D browser verifier
also opens the new disclosures when checking the controls inside them.

## Soccer-only cleanup and verification - November 24, 2026

The retired basketball engine, Metro Motion project, table-builder APIs and
unused setup controls are removed. Old page links redirect to the play hub;
retired live saves return to a frozen soccer setup. Soccer sessions, the AI
workshop and stored history remain, including read-only rendering of archived
basketball replays. The larger match layout also keeps its fullscreen button
label correct after a kick updates the HUD.

Validation: 1,254 backend tests passed; four Supabase-dependent chat/voice tests
were skipped because the isolated environment has no Supabase configuration.
All 111 frontend tests passed. Chrome checks passed for the 2D game (89),
match-focus layout/fullscreen (27), lineup editor (26), drawn tactics (27),
match setup (48), opponent picker (47), online reliability (41), login (233), page motion (70), and the warm-theme
checks across desktop/mobile pages.
Checks use isolated development sessions; production integrations are not
exercised by these runs.
