# Analytics Dashboard — methodology & findings write-up

Public page: **`/analytics`** (no login). JSON API: **`GET /api/analytics`**.
All numbers are computed over the game's own data — no fabricated data, no
external sources. This document records, per question: the question, the
data it uses, the method, what honesty guards apply, and how to read the
answer.

Dashboard live at: `http://<host>/analytics`

---

## 1. Stat builds: do Size/Power/Weight/Agility allocations correlate with wins?

**Question.** Players buy four stats (Size→radius, Power→kick velocity,
Weight→mass, Agility→pivot force) with a 200-point budget across a 6-player
squad. Does how you spend those points show up in match outcomes?

**Data.** Match summaries (`match_summaries` snapshot): each finished online
match stores both lineups (3×{size, power, weight, agility}), the winner,
and both players' pre-match ratings (ranked matches). A match yields **two
observations** (one per side): that side's lineup archetype and whether it
won.

**Method.**
1. *Archetype bucketing* — each side's lineup is reduced to its mean
   signature (per-stat mean over the 3 players). The archetype is the stat
   with the largest positive deviation from the 50-point baseline, if that
   deviation ≥ 10 points; otherwise `Balanced`. Deterministic and
   explainable — k-means was rejected as unstable and opaque at realistic
   sample sizes. (Buckets: Power-heavy / Agility-heavy / Size-heavy /
   Weight-heavy / Balanced.)
2. *Per-archetype win rate* with a **Wilson 95% CI** (robust at small n).
3. *Homogeneity test* across archetypes: Fisher's exact (best-vs-worst) when
   expected cells are small, otherwise chi-square with Yates correction.
4. *Skill control* — the critical confounder: a build's win rate can reflect
   *who tends to pick it*, not its inherent strength. When ≥ 2×10 rated
   sides exist, the same test is re-run stratified by pre-match rating
   (median split). No causal claim is made without the stratified re-test.

**Guards.** The whole analysis is refused (status `insufficient_data`)
below 10 finished matches. Per-archetype rows carry a confidence label
(`directional only (n < 10 observations)`) until 10 sides of that archetype
exist. A degenerate table (all sides win) yields no p-value rather than a
fake one.

**Reading the answer.** The card shows the test's p-value, per-archetype
win rates with CIs, and — when sample size allows — the same comparison
among low-rated players only and among high-rated players only. If a build
effect survives both strata, that is the strongest claim the data supports.

---

## 2. AI agents: who is strongest, and is there a rock-paper-scissors cycle?

**Question.** The 7 built-in agents (minimax, monte_carlo, greedy, bayesian,
value_iteration, policy_iteration, q_learning) all plan kicks with
`simulate_kick`. Which is empirically strongest — and can a weaker agent
beat a stronger one in a 3-cycle (non-transitivity)?

**Data.** The head-to-head win matrix: every pair plays `n_games` (default
5) simulated matches; wins/losses/draws per pair. This is the **only**
expensive computation (~9 min for 21 pairs × 5 games), so it is built
**on demand** by a background thread (`POST
/api/analytics/matrix/recompute`) and cached for 24 h. Until then the card
honestly shows `not computed`.

**Method.**
- *Strength ranking*: per-agent mean win rate across all 6 opponents; per-pair
  Wilson CIs; sorted descending.
- *Non-transitivity probe*: directed edges where X beat Y by strict majority,
  searched for 3-cycles. Deduplicated by cycle membership.
- *Custom models*: user-submitted leaderboard models are compared against the
  built-in-vs-built-in distribution for each opponent — a custom model's
  win rate vs `minimax` is judged against what the *other 6 built-ins*
  achieve vs `minimax` (mean and range), not against an absolute bar.

**Guards.** Cycles and rankings are labeled **candidates** (per-pair n = 5).
A 3-cycle at this sample size is a reason to run more games, not a law of
nature.

---

## 3. Skill: do players improve, and which rating brackets are hardest?

**Question.** Three sub-questions: (a) do individual players get better over
their ranked careers? (b) at which rating brackets is it hardest to win?
(c) do players return between seasons, and how do ratings move?

**Data.** `ranked_matches` rows (per-match pre-match ratings of both
players + winner) and `rating_history` (per-change log). Season snapshots
are the frozen end-of-season leaderboards (`seasons.leaderboard_snapshot`).

**Method.**
- *Trajectories*: per-player rating-after series, shown **only** for players
  with ≥ 8 ranked matches (otherwise honest `trajectory_note`).
- *Brackets*: win rate by 100-point pre-match rating bucket. Bucket
  membership uses the rating that existed **before** the match — a match
  can't retroactively move its own bracket. Brackets shown only with ≥ 5
  observations; k×2 test across brackets when ≥ 2 qualify.
- *Season-over-season*: adjacent seasons compared via their frozen
  snapshots — retention (share of season-N players present in N+1) and mean
  rating change for returning players (soft-reset compression is visible
  here: `new = 1200 + (old − 1200) × 0.5`).

**Guards.** Anything below the floors is labeled rather than plotted;
bracket tests need ≥ 2 brackets with ≥ 5 matches each.

---

## 4. Match dynamics: goals, first-goal timing, near-miss conversion

**Question.** How many goals per match (and how variable)? How quickly does
the first goal arrive? Do near misses predict goals?

**Data.** Two sources, both honest about their durability:
- *Online matches* (durable): summary snapshots carry final scores and the
  kick index of the first goal.
- *Tournament replays* (ephemeral — 24 h Redis TTL): per-kick trajectories
  with scored flags. The dashboard therefore states that near-miss/first-goal
  statistics only cover recent tournament matches.

**Method.**
- *Goals/match*: mean + **Poisson 95% CI** (count data) + full distribution.
- *First goal*: 1-based kick index of the first scored move; binned into a
  survival-style "share of matches without a goal by kick k" curve.
- *Near-miss → goal conversion*: a near miss is a real shot (peak speed ≥
  400 px/s) that reached the goal line within 100 px and came within 100 px
  of the goal mouth — **the exact heuristics the highlight detector uses**
  (`db.highlights` constants), so this dashboard and the highlights agree by
  construction. A near miss is "converted" if a goal follows within the next
  3 kicks. Conversion rate carries a Wilson CI.

**Guards.** `score_a/score_b` missing rows are never treated as 0-0; near
misses that are themselves goals are excluded from the denominator
(they're goals, not near misses).

---

## Caching, privacy, and cost

- `GET /api/analytics` computes the cheap reductions on first request and
  serves from Redis for 24 h (`cached: true` in the response; warm reads
  ~3 ms vs ~5 s cold). The cache is invalidated whenever an online match
  finishes (`_an_clear("an:data")` in `_save_match_summary`), so the page is
  at most one match stale.
- The 7-agent matrix is **never** computed on a page/JSON request — only via
  the explicit recompute button (409 while a run is in progress). No cron,
  no scheduler: on-demand + cache, mirroring the leaderboard benchmark
  pattern.
- The page is public (like `/spectate` and `/match/<id>/summary`). It
  exposes **aggregates only** — archetype names and usernames, never
  per-player lineups or trajectories keyed to accounts. Match summaries
  store no trajectories, so none can leak.
- Everything degrades gracefully in dev without Supabase/Redis: the
  in-memory fallbacks back every read.

## Verification

- `test_analytics.py` — 25 tests: Wilson/Poisson CI math, Fisher-vs-chi2
  branch selection, archetype classification (all 5 buckets + balanced
  fallback), build analysis (insufficient-data state, homogeneity,
  degenerate-table honesty, skill-strata gating, effect detection),
  matrix strength/cycle detection (transitive vs rock-paper-scissors),
  custom-vs-builtins baselines, rating qualification (≥ 8 games), bracket
  bucketing (pre-match ratings), season retention/deltas, near-miss
  detection (reuses highlight rules, speed floor, attacking-direction
  check), dynamics conversion, DB read-all paths, public API + caching
  (`cached` flag), matrix recompute 409 guard, page smoke.
- Manual verification (`%TEMP%\opencode\analytics_verify.py`, dev path):
  41 checks — seeded 12 summaries + 10 ranked matches + 2 seasons + 1
  leaderboard model + 1 tournament replay; page render, every payload
  section, strata, trajectory, season pair, dynamics conversion, matrix
  analysis + cycle + customs, cold/warm cache timing, 409 guard, and the
  fully-empty state.
- Full suite: **239 passed, 4 skipped**.
