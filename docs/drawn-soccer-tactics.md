# Drawn soccer tactics

## Customize starting positions

Open **Customize starting positions** before kickoff. The team still has one
goalkeeper and four outfield players. **Defenders**, **Midfielders**, and
**Attackers** each accept at most two players; a group may be empty. Select a
numbered player, choose an available role, then drag or tap inside that role's
marked area in your own half. Arrow keys on the pitch move the selected player.
Escape cancels a drag. Full role groups are disabled, and overlapping starts
are rejected. The goalkeeper keeps its normal starting spot.

Your manually controlled #5 can take any outfield role. AI players use the
assigned jobs and lateral lanes; drawing a run or hold instruction overrides
their ordinary positional target. This does not change who takes manual kicks.
The position preview and drawn-route origins update together.

Positions lock at **Start match**. Goals and halftime restore this layout,
including mirrored Team B positions. New Game retains the editable layout;
**Use formation preset** or selecting a formation restores its standard lineup.
Unsaved edits remain local to the current page. Local two-player and AI-versus-AI
can position both teams; human-versus-AI can position its own team. Online games,
penalties, and basketball keep their existing setup.

The server validates `custom_lineup_a/b` atomically with kickoff: five entries in
jersey order, normalized coordinates, one fixed keeper, at most two per outfield
role, bounded role areas, and at least 86 world units between starting centers.
The internal `support` role is displayed as Midfielder. Roles and positions are
included in prediction cache keys; malformed legacy data falls back to presets.

## Draw movement instructions

**Automatic is already role-specific; no route needs to be drawn.** Goalkeepers
track the ball within the goalmouth. Defenders recover into their own half and
favor clearing danger without abandoning cover. Midfielders offer a passing
outlet and favor passes a teammate can receive. Attackers stay available upfield
and favor forward play. Scoring chances and emergency clearances can take
priority over ordinary positional discipline. These defaults apply to preset
formations and custom role assignments, and to both mirrored teams. The manual
human player always follows your input. The setup editors describe the selected
player's default behavior and update that description when its role changes.

Before kickoff, open **Draw player instructions** below Formation and Playstyle.
Select a numbered player and choose:

- **Automatic tactics:** use the formation role and selected playstyle.
- **Draw a run:** drag from a player to draw a replacement route, or select a
  player and tap waypoints. The run advances across kicks, then holds its endpoint.
- **Hold a position:** tap the place the player should occupy when off the ball.

Undo point and Clear player edit the draft. Arrow keys move the board cursor;
Enter adds a point; Escape cancels a drawing. Mouse, touch, and keyboard work.
Choose the formation first; changing it moves each route's starting player while
keeping the drawn destinations. Both team boards face right. Team B's route is
mirrored when used on the real pitch.

**Start match** saves and locks instructions for the match. The saved movement
plan remains available as a read-only diagram. New Game returns to editable
setup with the previous instructions. Unsaved drawings stay in the current page;
they are not persisted until kickoff.

In human versus AI, draw instructions for your four AI teammates. Your #5 player
follows your kicks. In AI versus AI, both teams and all five players are editable.
Manual-only two-player matches, online matches, penalties, and basketball do not
use this editor. The current kicker always follows its kick command; these plans
guide off-ball movement, not passing or shooting sequences.

## What players do

The editor reduces a freehand stroke to at most 16 waypoints, preserving bends
and endpoints. The server validates each player instruction and uses it as the
positional target in the existing support planner and tactical scoring.

Each eligible AI player receives at most one ordinary physical impulse per kick.
Outfield support travel remains capped at 120 pitch units, and keeper travel at
45, further limited by player stats and power. Sweep checks prevent movement
through players or the ball. A blocked run can make a small sidestep or wait;
it may not complete exactly when opponents interfere. The keeper's editable
area is restricted. These are movement intentions, so collisions still matter.

Progress advances only from real trajectory positions. Predictions do not mutate
routes or cursors. The prediction cache includes both instructions and progress.
Goals and halftime restart the runs; undo restores progress with the physical
state. Replay uses recorded player tracks, so it shows the actual movement.

There is no new physics world, background training process, or perpetual drawing
loop. Saved instructions use `player_plans_a/b`; bounded cursors use
`player_plan_progress_a/b`. Kickoff validation is atomic and rejects changes
after the match starts, invalid coordinates, excessive waypoints, instructions
for an opponent in human play, and manual-striker instructions.

## Research and design decisions

[TacticalPad's official learning guide](https://www.tacticalpad.com/edu/formation.html)
describes player placement, drawing modes, and static/animated tactical boards.
This informed direct player selection and visible movement paths.

The FA's [in-possession principles](https://community.thefa.com/coaching/b/insights-analysis-blogs/posts/crafting-the-attack-the-in-possession-principles-of-play)
describe movement, creating space, and support. Its guidance on
[constraints in coaching](https://www.thefa.com/bootroom/resources/coaching/how-to-use-constraints-in-your-coaching-session)
cautions against treating football as a rigid playbook. The implementation uses
drawn positional intentions while preserving ordinary ball decisions and physical
obstacles. This is a design interpretation, not a claim of trained tactical AI.

## Verification — November 17, 2026

- Backend: 376 checks passed across player plans, match setup, search budget,
  team movement/roles, tactical roles, playstyle, and basketball API regressions.
  The additional blocked-route regression passed in the 22-test plan suite.
- Frontend Node: 26 checks passed across drawing simplification, setup drafts,
  player controls, and clocks.
- Existing formation/setup browser suite: 48 checks passed in Chrome.
- Drawn-tactics browser suite: 28 checks passed in Chrome.
  `python tools/browser/verify_tactics_board.py` exercises mouse drawing,
  cancellation, keyboard and touch entry, kickoff locking, read-only review,
  real defender motion, undo, reload/reset, and responsive layouts. Screenshots
  and the report are written to the temporary `agent-soccer-tactics` directory.

These checks use an isolated local Flask server and temporary sessions. No online
room, production account, GPU training job, or deployment is modified.

Position-editor follow-up: 366 backend checks passed across custom positions,
setup, formations, roles, tactics, team movement, drawn plans, prediction caches,
and basketball APIs; 24 frontend checks passed. The custom-position browser
suite (`tools/browser/verify_custom_lineup.py`) passed 25 checks covering role
capacity, drag/cancel, keyboard, touch, mirroring, persistence and responsive
layouts. The existing drawn-tactics browser suite also passed all 28 checks,
and the formation/setup browser suite passed all 48 checks.

Default-role verification, November 18, 2026: 119 backend checks and 9 frontend
checks passed. A matched-board physical regression changes the same jersey's
assigned role and verifies distinct defensive, midfield and attacking movement
on both sides without drawn plans. Position-editor and drawing browser suites
passed 28 and 29 checks respectively, including the default-role descriptions.
