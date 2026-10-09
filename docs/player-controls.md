# Players and controls

The game and replay viewer share `static/js/game/player-avatar.js`: athletic player
models with numbered kits, shorts, socks, boots, natural skin/hair variants,
and distinct goalkeeper gloves. Each athlete uses seven visible meshes and
1,030–1,054 triangles. Geometry, materials, shirt-number textures, and soft
shadows are cached across players and team rebuilds. Shared resources carry
`userData.playerShared`; `static/js/game/scene-resources.js` preserves them when removing
a team. Existing private resources still dispose normally.

The physical Size stat controls the footprint: 12 px at Size 0, 20 px at 50,
and 28 px at 100. Selecting a player shows that footprint with a ring and
keeps their root scale unchanged. Brief kicking and limb poses run within the
existing finite move animation; standing players start no animation loop.
The launch animation now advances smoothly without its old backward jumps.

Play remains turn based. `static/js/game/player-controls.js` defines the command rules
and aiming math; `static/js/game/player-input.js` handles mouse, touch, keyboard, and
the visible Player/Direction/Power/Kick controls using those same rules.

| Action | Control |
| --- | --- |
| Choose a legal player | Player picker, previous/next buttons, or tap the player |
| Switch players on the pitch | Q / Shift+Q |
| Aim | Left / right arrows, or Direction field |
| Set power | Up / down arrows, or Power slider |
| Make fine adjustments | Hold Shift with an arrow key |
| Kick | Kick button, Enter, or Space on the pitch |
| Drag kick | Pull a player back at least 6 screen pixels, then release |
| Cancel | Escape; Reset aim restores the default direction and power |
| Pass type | Visible pass buttons; N normal, S short, A long, W through |

Arrow keys adjust the preview without submitting a move. Short/long/through
modifiers and the saved power cap affect the displayed power and submitted
command together. The preview maps server y to negative world z, matching the
actual kick direction. A tap selects without kicking, including on a player's
head. Dragging captures one pointer and a fixed origin. Right-click camera
movement, a second finger, cancellation, focus loss, and a turn change cannot
finish a shot. Penalty aiming leaves the authoritative ball stationary.

Controls respect match rules: Human vs AI permits captain 0; local Human vs
Human permits the active team's players; online players can select their own
team on their turn. AI vs AI, spectators, game-over states, and busy move chains
block manual input. Keyboard shortcuts leave form fields, editable text,
buttons, links, dialogs, and browser modifier shortcuts alone. Mobile controls
fit at 375 px with 44 px minimum targets, and the Assistant button stays in
page flow instead of covering the player picker.

Local Human vs Human previously always applied Team A's command, including on
Team B's turn. `/move` now normalizes and applies the active team's roster and
stats for normal kicks and penalties. AI-only matches reject manual commands.
The captain rule for Human vs AI remains enforced on the server. Busy state
covers the full captain A / opponent B / teammate A / opponent B cycle, returning
control to the captain. One-player squads skip the teammate pair. Retry resumes
the failed automatic leg, and session changes cancel stale responses and
playback. Local automated moves play at twice the selected animation speed.
Checked responses and `finally` cleanup restore input after request failures.
Goalkeeper buttons are connected to the
module's handler, and a timed-out goalkeeper chooses the center before the AI
shot. After a human penalty, keeper selection takes place before the AI shoots.

## Verification

```powershell
node --test tests/frontend/test_player_controls.mjs tests/frontend/test_player_avatar.mjs tests/frontend/test_stadium_crowd.mjs tests/frontend/test_frustum_culling.mjs tests/frontend/test_render_on_demand.mjs tests/frontend/test_trajectory_playback.mjs
python tools/browser/verify_player_controls.py
```

The JavaScript suites contain 59 checks. Isolated Python verification passes
686 tests with 4 skips, including side-specific hotseat moves and penalties,
unchanged captain enforcement, and rejected AI-only manual commands.
The browser harness passes 55 checks and uses trusted pointer, keyboard, and touch interactions,
temporary isolated match data, mocked responses for command-contract checks,
and real server physics for both the hotseat path and a complete Human vs AI
captain/opponent/teammate/opponent sequence. It verifies failed-request recovery, visible
goalkeeper buttons, busy state throughout the whole move chain, zero poses and
zero idle frames after playback, and stable GPU resources across five rebuilds.
Screenshots and its report are
written to `$env:TEMP/agent-soccer-player-controls`. Python Playwright and its
Chromium installation are required. External integrations are disabled.
