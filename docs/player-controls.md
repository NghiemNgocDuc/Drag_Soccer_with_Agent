# Players and controls

Live games and replay views use Canvas 2D. Each team has five numbered players
with stat-sized bodies and distinct goalkeeper rings. In Human vs AI you control
**striker #5**, marked **YOU**, and the teammate AI cannot choose your player.
Penalty shootouts select the placed kicker (index 0).

Play remains turn based. `static/js/game/player-controls.js` defines command
rules; `player-input.js` shares them across mouse, touch, keyboard and form controls.

| Action | Control |
| --- | --- |
| Choose a legal player | Player picker, previous/next buttons, or tap |
| Switch legal players | Q / Shift+Q |
| Aim | Left / right arrows, or Direction field |
| Set power | Up / down arrows, or Power slider |
| Fine adjustment | Shift + arrow key |
| Kick | Kick button, Enter, or Space on the pitch |
| Drag kick | Pull back at least 6 screen pixels, then release |
| Cancel | Escape; Reset aim restores direction and strength 80 |
| Pass type | Pass buttons; N normal, S short, A long, W through |

A tap selects without kicking. Dragging captures one pointer and a fixed origin.
Cancellation, focus loss and turn changes cannot submit a shot. Pass modifiers
and the saved power cap affect the displayed and submitted strength together.
Shortcuts leave form fields, editable text, buttons, links and dialogs alone.

Local Human vs Human and online players may select any member of their active
team. AI-only matches, spectators, game-over states and busy move chains block
manual input. The server independently enforces the striker restriction.

A Human vs AI cycle is striker A, opponent B, teammate A, opponent B, then you
control the striker again. Retry resumes a failed automatic leg; session changes
cancel stale responses and playback. AI and human moves use the selected
animation speed with a four-second cap at normal speed. Ball and player movement
share the recorded timeline, including player contacts and penalties.

New local/online matches use five players even if an older client requests a
different count. Opening an old local match starts a fresh five-a-side match.
Existing online rooms and saved replays retain their actual rosters.

## Verification

```sh
python tools/run_tests.py tests/backend
node --test tests/frontend/*.mjs
python tools/browser/verify_2d.py
```

The browser verifier exercises real mouse, keyboard and touch commands, a full
human/AI cycle, roster visibility during motion, online participants and guest
spectators, replay/highlight/loss playback, hidden-tab effects and idle rendering.
Artifacts are written to the system temporary directory under `agent-soccer-2d`.
The older 3D avatar and control verifiers remain historical reference material.
