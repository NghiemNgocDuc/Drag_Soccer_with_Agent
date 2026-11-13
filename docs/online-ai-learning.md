# Learning from online games

Completed online soccer matches now provide experience for a small learned
position-value policy. It advises the existing tactical candidate rankings.
It does not replace the physics engine, player eligibility, formations,
scoring rules, or the 120 ms / 12-prediction search budget.

## Collection

The server captures both teams' views immediately before and after each
accepted online move. A record contains ten bounded board features, the moving
perspective, whether a goal occurred, the final match result, and an opaque
match grouping key. Names, account IDs, invitation codes, chat, voice, code,
and full trajectories are excluded from the training corpus.

Only naturally completed, non-shootout five-a-side soccer matches with normal
ball materials and a 100-point power cap qualify. They must contain at least
eight observed moves and two distinct participants. Forfeits, cancellations,
rejected/retried moves, incomplete games, penalties and other rule profiles
do not train the policy. Basketball currently has local play only, so soccer
experience never trains its AI.

Each room retains at most 240 feature rows (both perspectives of 120 moves).
The shared Redis corpus keeps the latest 200 qualifying matches, with 30-day
record expiry and duplicate completion protection. Collection uses the existing
room write; saving the completed corpus and training run in a bounded background
worker. Training does not run inside a human move or physics prediction.

Existing online games saved before this capture was added do not contain the
required complete features and are not retroactively treated as training data.

## Validation and promotion

After at least 20 completed matches and 100 valid feature rows, the worker
attempts training; later attempts follow every ten new matches. A deterministic
match hash permanently assigns whole games to training or holdout sets. Both
teams and each game receive balanced weight, so long matches cannot dominate.

Ridge regression fits position value from the final result and modest measured
progress/goal signals. The holdout set must contain at least four independent
games and 20 rows. A candidate must improve held-out prediction error over both
the fixed baseline and the current deployed policy, with improvement across at
least 60% of held-out games and a bounded worst-game regression. Coefficient,
finite-value and monotonic safety checks also apply.

Before publication, ten mirrored physical scenarios check opening play,
shooting, defensive clearances, passes and weak-player stats. The fixed tactical
scorer measures actual pymunk results, without the candidate grading itself.
Own goals, a missed previously available goal, excessive per-scenario loss, a
negative aggregate change, or a validation timeout reject the update. Candidate
evaluation uses a thread-local model and cannot change another live decision.

Passing both gates publishes one versioned immutable policy. A Redis lease and
atomic ownership check prevent an expired trainer overwriting a newer update
or operator reset. Other workers refresh their cached policy asynchronously,
at most once every 30 seconds. A decision snapshots one model for its entire
search. Storage failure or corrupt model data retains the last healthy model.

Live inference adds at most 40 tactical points to non-scoring outcomes. Goals,
own goals, penalties, basketball and incompatible rules bypass it. Without
validated data, the existing tactics remain in use. No live random exploration
or additional physics simulation is introduced by the learned ranker.

**Held-out prediction error and these physical scenario scores are validation
metrics, not a measured improvement in human-match win rate.** Real deployment
starts collecting data after this version is running; it does not ship a model
claimed to have learned from matches that were unavailable during development.

## Status and operation

`GET /api/learning/status` includes an `experience` object with stored-match
count, minimum evidence, active policy version and the latest validation report.
It exposes aggregate metrics rather than training rows or match grouping IDs.

Training runs automatically. A server operator can also use the same Redis
environment from the command line:

```powershell
python tools/train_online_ai.py
python tools/train_online_ai.py --train
python tools/train_online_ai.py --reset
```

Reset removes the deployed learned policy and keeps the bounded corpus. The
next automatic attempt waits for ten additional completed matches.

`POST /api/learning/train` with `{}` queues a bounded background attempt;
`POST /api/learning/reset` resets deployment. In production, these global
mutations require the server's `AI_TRAINING_TOKEN` in an
`X-AI-Training-Token` header. The token stays on the server. Signed-in local
development sessions may use the endpoints without it. Anonymous callers and
ordinary production accounts cannot mutate the shared policy.

## Existing adaptive and basketball AI

The Adaptive Learner no longer explores randomly during live play or defaults
to a goalkeeper action for unseen states. Modern squads use full-roster tactics
and the validated learned advice. Small-roster legacy Q-table choices remain
deterministic when a usable learned row exists. The legacy offline self-play
helper uses isolated candidate tables, symmetric delta rewards and transitions
that include the opponent response; it is separate from online training.

The basketball AI uses player power, agility, radius and the current power cap
to select reachable defenders/rebounders, checks blocked recovery paths with
at most three bounded predictions, avoids an under-hoop zero-power throw, and
can retreat out of crowded contact before another steal. The reproducible
benchmark fixes eight targeted defect cases (baseline 0/8; improved 8/8) and
includes 48 seeded legality/physics stress scenarios. These are scenario
results, not online basketball results.

## Verification

```powershell
python tools/run_tests.py tests/backend/test_experience_policy.py tests/backend/test_online_learning.py tests/backend/test_learning_fitness.py tests/backend/test_adaptive_learner.py tests/backend/test_ai_search_budget.py tests/backend/test_basketball.py tests/backend/test_basketball_api.py
python tools/benchmarks/benchmark_basketball_ai.py
```
