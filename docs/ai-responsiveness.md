# AI responsiveness and coordination

The default AI favors fast reactions: **120 ms of search and at most 12
speculative physics runs per decision**. Completed candidates are retained when
either limit is reached. This deliberately reduces deep lookahead and ensemble
exploration; it does not establish a higher win rate.

## How decisions work

The catalog includes three additional tactical styles: `possession` prefers safe
short outlets, `counter_attack` values direct forward progress while penalizing
turnovers, and `high_press` rewards outfield players closing down the ball.
`models/tactical_styles.py` shares their geometry and search code. These are
deterministic heuristics, not trained neural networks; they need no additional
dependencies, downloaded weights, remote calls, or persistent storage.

Their scorer is passed through candidate ordering and `SearchBudget.record`,
so a timeout retains the best completed result for that style. They use the
existing 120 ms / 12-prediction limits, keeper safeguards, and support runs.
An already-expired request still returns the generic legal
fallback immediately. Regression checks are in `test_tactical_styles.py`.

`models/tactics.py` builds a short list using the actual roster, player stats,
power cap, ball materials, reachable contact geometry, and open passing lanes.
It considers shots, controlled passes, defensive clearances, and moving toward
the ball when no player can reach. Ordinary Human vs AI opponents can select
their full roster; only shootouts restrict the kicker to the placed index 0.

## Five-level playstyle

Pre-match setup offers five connected dots: **Deep defend, Defend, Balanced,
Attack, All-out attack**. Balanced (level 3) is the default. Lower levels move
the team's lines deeper and make them more compact, with a stronger preference
for controlled possession. Higher levels push the lines forward and widen them,
with more reward for forward play and attacking pressure. Stable player jobs
remain in place: keepers stay home and defenders' support targets remain in
their own half. Existing movement limits and physical collisions still apply.

In Human vs AI the control changes your teammates' playstyle; your striker
remains manual. In AI vs AI each team has its own formation and playstyle.
Selections remain local drafts until `POST /start_match` applies them together
and starts the clock. They are stored in `playstyle_a` / `playstyle_b`, including
across reloads and match resets. Explicit started matches reject changes through
`POST /set_playstyle`; New Game reopens setup. Manual-only matches choose
formations without AI mentality. Online matches and shootouts keep their
existing lifecycle. AI prediction cache keys include both teams' levels.

`models/playstyle.py` reads the two small settings. No extra physics worlds,
network inference, timers or search budget are needed. The shared outcome
scorer applies the preference to completed predictions as well as candidate
ordering; goals and urgent clearances retain priority.

Verify with `python tools/run_tests.py tests/backend/test_playstyle.py` and
`python tools/browser/verify_playstyle.py`. The browser check covers keyboard
selection, kickoff and failure recovery, resets, team selection, session changes,
and desktop/mobile layout.

## Simultaneous team movement

Human vs AI and AI vs AI now launch support runs for **both squads during each
kick**. `models/team_movement.py` follows stable jobs from `models/team_roles.py`.
In the default five-a-side 2-1-1, #1 guards the goal, #2 and #3 defend separate lanes in their own
half, #4 offers short passing outlets, and #5 stays available to attack. Jobs
come from the starting formation and roster slot; possession, collisions and
kickoff resets do not reassign them. Other roster sizes use their formation's
defender, midfield and forward rows. A two-player team uses keeper and attacker.
Keepers track the ball inside the goal-mouth band.
The pre-match formation selector also offers 1-2-1, 2-0-2 and 1-1-2. These change
the number of defenders, support players and attackers using the same cached
formation geometry. The last slot remains the human striker in every shape.
Shared kick scoring also favors each job: defenders retain cover and clear
danger, support players look for controlled receptions, and attackers favor
forward play. These are bounded preferences: goals take priority, urgent
clearances bypass positional penalties, and the nearest eligible player stays
in consideration. The game page lists each player's job beside their number.
The human striker is never automatically launched. An outfielder's planned
free run is bounded to 120 world units and a keeper's to 45, further limited by
actual stats and the match power cap. Contact can change that travel.

The planner tries at most three swept paths per pawn and reserves destinations
to reduce crowding. A blocked or already-settled player can hold position;
there is no random movement just to make every disc appear busy. Each accepted
run uses a single momentum impulse. Existing friction, collisions and settling
govern the result, in the same Pymunk world as the chosen kick. No extra physics
world, search budget, persistent state or background animation loop is added.
Predictions and actual play use exactly the same plans and physics.

A local five-a-side check on 2026-10-16 (20 repetitions of the same spread
fixture, excluding imports and state copying) measured 0.29 ms median planning,
8.31 ms prediction, and 8.59 ms execution with support enabled. The same fixture
with support disabled took 4.36 ms and 4.49 ms for prediction and execution.
Both modes created exactly one physics world per prediction or execution;
planning created none. Three-repeat bounded attack checks measured 28.1 ms
median for Team Coordinated and 24.1 ms for High Press, each using three
predictions. These are local fixture measurements, not deployment guarantees.
After adding fixed roles and role-aware scoring, the same three-repeat bounded
attack check measured 20.2 ms median for Team Coordinated and 21.1 ms for High
Press, each with three predictions and four cache hits. This small sample showed
no material latency regression; it does not establish a speedup.

Human vs AI now completes **your kick, one opponent reply, then your turn**.
No separate teammate turn is needed. Hotseat and online human-only games retain
manual turns by default. Shootouts retain their kicker/keeper-only simulation.
An explicit `team_movement=False` preserves the legacy four-leg human cycle and
single-pawn physics for saved practice fixtures and physics experiments.

For those legacy games, `models/team_support.py` proposes forward
support runs when a teammate has the safer position near the ball, including
when that teammate is the human striker excluded from the AI's turn.

Support players look for open lanes 100–460 pixels from the ball, keep separation
from teammates and defenders, and use both flanks. One outfielder stays behind
the ball as cover while the nearest player remains available to play it. Each
eligible outfielder checks at most twelve destinations and contributes at most
two runs. Their stopping power follows actual Power/Agility and the match cap;
the entire path must clear the ball and other players. Already useful receivers
can hold position. Loose balls, urgent clearances and scoring chances still take
priority. Goalkeepers stay home when an outfielder can reach during an attack.
In that opt-out mode, a turn still moves only its selected player.

Support checks are deterministic geometry calculations and create no extra
physics worlds. The same positional improvement score is used by the quick
fallback and verified search, within the existing 120 ms / 12-prediction budget.

## Bounded search

`models/search_budget.py` verifies up to four tactical seeds, then lets the
selected agent refine them within the remaining shared budget. The best
completed physical outcome wins. Ensemble experts and opponent predictions
consume the same budget. Repeated predictions of the same board and command are
cached within that decision; coordinates, team membership, stats, and physical
settings are part of the cache key.

The shared aiming helper now returns an absolute world angle, including for
Team B. Progress rewards the correct attacking direction. These fixes prevent
many misses and backward choices in the older strategies.

The Q learner keeps its zero-prediction lookup and existing 36-action table. It
selects only existing pawns for small squads; if a striker/teammate constraint
requires a fallback, the unused learning action is discarded. The `langchain`
agent uses its local strategy with budgets below 1000 ms, avoiding remote
inference during ordinary play. A 1000 ms budget permits its original optional
LLM path; network work can outlive the request wait.

## Physics and request lifecycle

Lookahead uses the same solver, contacts, three substeps, settling rules, and
goal detection as actual play. It saves time by skipping cosmetic referee motion
and recording fewer intermediate player snapshots. Final ball positions, player
positions, and scoring remain identical for a completed prediction. Actual moves
still produce their normal trajectories.

Cancellation is checked every eight simulation frames. Each built-in decision
has its own context, cache, and cancellation event. The live request wrapper
admits four workers per process, copies current state without accumulated replay
history for built-ins, and waits the configured budget plus 100 ms. Excess
requests receive an immediate legal tactical choice instead of entering a queue.
This is a cooperative limit, not a strict HTTP latency guarantee: scheduling,
state storage, actual move physics, and rendering add time.

Custom models keep their full isolated state contract and a two-second request
wait. Running arbitrary Python cannot be stopped by cancelling its future, so it
retains its worker slot until it finishes. Uploaded execution retains its
separate five-second limit. This follows Python's
[Future cancellation behavior](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Future.cancel).

Human vs AI completes striker A, then opponent B, and returns control to the
striker. Explicit legacy matches add teammate A and opponent B before returning
control; one-player squads omit that teammate pair. Retry continues the
pending automatic leg; duplicate clicks are blocked, and joining another session
invalidates old responses and animations. Local AI moves use the
selected animation speed, with a four-second cap at normal speed in Canvas 2D.
Slow rendering or a hidden canvas can extend elapsed wall time.

## Configuration

Set these in `.env`, then restart the app:

```dotenv
AI_DECISION_BUDGET_MS=120
AI_MAX_SIMULATIONS=12
```

The supported ranges are 20–1000 ms and 1–64 predictions. Lower limits favor
responsiveness; higher limits allow more exploration and use more CPU. Local
matches, tournaments, built-in benchmark opponents, loss analysis, tutorial
opponents, and learner training opponents use the shared runner.

## Local measurements

A quiet local run covered 12 agents, 3/7/11-player squads, and kickoff/attacking
positions, with three decisions per case. All 72 cases completed. Comparing the
70 completed matching historical cases gave:

| Measurement | Before | After |
| --- | ---: | ---: |
| Median decision time across matching cases | 83.7 ms | 25.6 ms |
| Hybrid, 11 players, attacking position | 1722.9 ms | 33.4 ms |
| Hybrid physics runs in that position | 123 | 12 |
| Cases whose move displaced the ball by more than 1 px | 38/70 | 66/70 |

The historical sample used a different run and fewer repetitions; two interrupted
cases were excluded. These figures measure local decision work, exclude imports,
and are not production latency or match-strength guarantees. Saved historical
moves were evaluated with the current ordinary match physics for the displacement
comparison. Random learner choices can vary between runs.

A separate burst of 12 simultaneous `/ai_move` requests using Expectimax with
11 players returned 12 HTTP 200 responses, all legal, each advancing exactly one
turn. Four requests searched and eight used the busy fallback. Median endpoint
time was 245 ms and maximum was 292 ms; all four worker slots were available
afterward.

## Reproduce and verify

```powershell
python tools/run_tests.py
node --test (Get-ChildItem tests/frontend -Filter *.mjs).FullName
python tools/browser/verify_ai_responsiveness.py
python tools/browser/verify_player_controls.py
python tools/benchmarks/profile_ai.py --sides a --scenarios kickoff attack --repeats 3 --output "$env:TEMP/agent-soccer-ai-profile.json" --probe-model expectimax
```

The profiler disables external services, runs decisions in disposable processes,
counts real physics spaces, and records outcomes plus wall/CPU timings. Add
`--compare path/to/prior.json` for matching-case comparisons. The default scenario
set also includes defensive positions and both sides; `--decision-mode service`
includes the live worker wrapper. Run timing checks without concurrent browser
or test workloads. Browser tools use isolated local data and require Playwright
with Chromium.

Regression coverage includes simulation caps, mid-prediction cancellation,
worker recovery, concurrent cache isolation, material invalidation, both-side
physical parity, roster legality, open passes, defensive clearances, and striker
constraints. The full Python suite passes 686 tests with 4 skips; the JavaScript
suites pass 59 tests. The focused browser verifier passed 13 checks; the existing player
controls verifier passed 55, both without JavaScript exceptions.

`tests/backend/test_team_support.py` checks forward runs with a stationary ball,
mirrored teams, blocked passing lanes and run paths, distinct flank receivers,
defensive cover, urgent clearances, stat/power limits, and support choices under
the existing prediction cap. The older counts above describe the original
responsiveness release.
