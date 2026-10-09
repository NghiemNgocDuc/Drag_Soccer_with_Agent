# AI responsiveness and coordination

The default AI favors fast reactions: **120 ms of search and at most 12
speculative physics runs per decision**. Completed candidates are retained when
either limit is reached. This deliberately reduces deep lookahead and ensemble
exploration; it does not establish a higher win rate.

## How decisions work

`models/tactics.py` builds a short list using the actual roster, player stats,
power cap, ball materials, reachable contact geometry, and open passing lanes.
It considers shots, controlled passes, defensive clearances, and moving toward
the ball when no player can reach. Goalkeepers stay home when an outfielder can
reach during an attack. A turn still moves only its selected player.

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

Human vs AI completes striker A, opponent B, teammate A, opponent B, then returns
control to the striker. The teammate uses the coordinated policy instead of a
random rush. One-player squads omit the teammate pair. Retry continues the
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
