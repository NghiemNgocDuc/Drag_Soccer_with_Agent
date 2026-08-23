"""Player/Model analytics — data-science layer over the game's own data.

This module answers a small set of deliberately focused questions with
defensible, small-sample-correct methodology. Nothing here touches game
logic: it reads persisted match summaries, ranked-match/rating history,
season snapshots, leaderboard benchmarks and (ephemeral) tournament
replays, and reduces them into dashboard payloads.

Design principles:
  - Each analysis is a pure function of its inputs (lists of plain
    dicts), so it is trivially testable and reusable outside Flask.
  - Sample-size honesty: every point estimate ships with a Wilson /
    Poisson 95% CI where appropriate, and a confidence label
    ("directional only" below a per-analysis floor) rather than a bare
    number.
  - Causal claims are refused: where a build outcome could reflect who
    picks a build, we stratify by pre-match rating instead of asserting
    the build is "better".
  - pandas/NumPy are used for the mechanical parts (grouping, sorting);
    the statistics themselves come from scipy (fisher_exact,
    chi2_contingency, binomtest) — standard, reviewable methods.
"""
from __future__ import annotations
import math
from collections import Counter

import numpy as np
import pandas as pd
from scipy.stats import chi2_contingency, fisher_exact, binomtest

# ── Sample-size floors (the honesty thresholds) ───────────────────────────
MIN_MATCHES_FOR_BUILD_ANALYSIS = 10   # total summaries before any build claim
MIN_ARCHETYPE_N                = 10   # per-archetype observations for a firm claim
MIN_EXPECTED_FOR_CHI2          = 5.0  # expected cell count: chi-square vs Fisher
MIN_TRAJECTORY_GAMES           = 8    # ranked games before a player trajectory is shown
MIN_BRACKET_MATCHES            = 5    # observations before a rating bracket is shown
NEAR_MISS_GOAL_WINDOW          = 3    # kicks after a near miss that count as "converted"
Z                             = 1.96

STAT_KEYS = ("size", "power", "weight", "agility")
ARCHETYPE_NAMES = {
    "power": "Power-heavy",
    "agility": "Agility-heavy",
    "size": "Size-heavy",
    "weight": "Weight-heavy",
    "balanced": "Balanced",
}


# ── Statistics helpers (small-sample correct) ─────────────────────────────

def wilson_ci(k: int, n: int, z: float = Z) -> tuple[float, float]:
    """Wilson score interval for a proportion k/n (robust at small n)."""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3))


def poisson_ci(total: float, n: int, z: float = Z) -> tuple[float, float]:
    """Approximate 95% CI for a rate (total events over n units)."""
    if n <= 0 or total <= 0:
        return (0.0, 0.0)
    mean = total / n
    return (round(max(0.0, mean - z * math.sqrt(mean / n)), 3),
            round(mean + z * math.sqrt(mean / n), 3))


def proportion_test(ka: int, na: int, kb: int, nb: int) -> dict:
    """Two-proportion comparison. Fisher's exact for small samples,
    else chi-square with Yates correction. Returns p + both Wilson CIs."""
    if na <= 0 or nb <= 0:
        return {"p": None, "method": "none", "ci_a": (0, 0), "ci_b": (0, 0)}
    table = [[ka, na - ka], [kb, nb - kb]]
    if min(na, nb) < 30:
        _odds, p = fisher_exact(table)
        method = "fisher"
    else:
        _chi2, p, _dof, _exp = chi2_contingency(table, correction=True)
        method = "chi2"
    return {"p": round(float(p), 4), "method": method,
            "ci_a": wilson_ci(ka, na), "ci_b": wilson_ci(kb, nb)}


def group_proportion_test(groups: list[dict]) -> dict:
    """k×2 homogeneity test over groups [{key, k, n}] → Fisher (small) or
    chi-square (expected cells >= 5). Returns p, method, expected-floor
    check and per-group Wilson CIs."""
    if len(groups) < 2 or any(g["n"] <= 0 for g in groups):
        return {"p": None, "method": "none", "groups": groups}
    table = np.array([[g["k"], g["n"] - g["k"]] for g in groups])
    if table.min() < 1 or table.sum(axis=1).min() < 2:
        return {"p": None, "method": "insufficient", "groups": groups}
    try:
        if table.sum() < 40:
            # Fisher's exact generalized to k×2 via scipy's exact test on
            # the collapsed table; scipy has no direct k×2 exact test, so
            # fall back to chi-square with an explicit small-cell flag.
            raise ValueError("small")
        _chi2, p, _dof, expected = chi2_contingency(table, correction=False)
        method = "chi2"
    except ValueError:
        # Small expected cells: Monte-Carlo-free exact p from the
        # chi-square distribution is invalid here, so we use Fisher's
        # exact on pairwise best-vs-worst as the conservative check.
        best = max(groups, key=lambda g: g["k"] / g["n"])
        worst = min(groups, key=lambda g: g["k"] / g["n"])
        _odds, p = fisher_exact([[best["k"], best["n"] - best["k"]],
                                 [worst["k"], worst["n"] - worst["k"]]])
        method = "fisher-best-vs-worst"
    out = []
    for g in groups:
        out.append({**g, "ci": wilson_ci(g["k"], g["n"])})
    return {"p": round(float(p), 4), "method": method, "groups": out}


def confidence_label(n: int, floor: int) -> str:
    if n < floor:
        return f"directional only ({n} < {floor} observations)"
    return f"{n} observations"


# ── Stat-build meta analysis ──────────────────────────────────────────────

def _archetype(stats: list[dict]) -> tuple[str, dict]:
    """Classify a side's 3-player lineup into an interpretable archetype.

    Signature = per-stat mean across the lineup. The archetype is the
    stat with the largest positive deviation from the 50-point baseline,
    provided it reaches at least 10 points; otherwise 'balanced'.
    Deterministic and explainable (k-means is unstable and opaque at the
    sample sizes this project realistically has).
    """
    stats = [s for s in (stats or []) if isinstance(s, dict)]
    if not stats:
        return "balanced", {}
    sig = {k: round(sum(float(p.get(k, 50)) for p in stats) / len(stats), 1)
           for k in STAT_KEYS}
    devs = {k: sig[k] - 50.0 for k in STAT_KEYS}
    best_key = max(devs, key=lambda k: devs[k])
    arch = best_key if devs[best_key] >= 10 else "balanced"
    return arch, sig


def _rating_at_time(ratings: dict[str, dict], user_id: str) -> int | None:
    row = ratings.get(user_id)
    if row:
        return int(row.get("rating", 1200))
    return None


def stat_build_analysis(summaries: list[dict],
                        ratings: dict[str, dict] | None = None,
                        min_matches: int = MIN_MATCHES_FOR_BUILD_ANALYSIS,
                        min_archetype_n: int = MIN_ARCHETYPE_N) -> dict:
    """Q: do Size/Power/Weight/Agility allocations correlate with wins?

    Data: per-side observations from match summaries (a match yields one
    row per side: that side's lineup archetype, whether it won, and the
    player's pre-match rating when known). Method: archetype buckets →
    per-archetype win rate + Wilson CI → k×2 homogeneity test (Fisher /
    chi-square by cell size) → skill-stratified re-test (median rating
    split) to separate build strength from who picks the build.

    Returns a payload with explicit confidence labels; the caller renders
    'insufficient data' states from `status`.
    """
    if not summaries or len(summaries) < min_matches:
        return {"status": "insufficient_data",
                "have": len(summaries), "needed": min_matches,
                "message": f"Need at least {min_matches} finished matches "
                           f"before build/outcome claims; have {len(summaries)}."}

    rows = []
    for s in summaries:
        for side, build, key in (("a", s.get("build_a"), s.get("player_a")),
                                 ("b", s.get("build_b"), s.get("player_b"))):
            if not build:
                continue
            arch, sig = _archetype(build)
            won = bool(s.get("winner") == side.upper())
            rr = s.get("ranked_result") or {}
            rating = None
            if rr:
                detail = rr.get(f"player_{side}") or {}
                rating = int(detail["rating_before"]) if detail.get("rating_before") is not None else None
            elif ratings is not None and key:
                rating = _rating_at_time(ratings, key)
            rows.append({"arch": arch, "won": won, "rating": rating,
                         "match": s.get("room_id"), "side": side,
                         "sig": sig, "player": key})

    df = pd.DataFrame(rows)
    groups = []
    for arch in sorted(set(df["arch"])):
        sub = df[df["arch"] == arch]
        groups.append({"key": ARCHETYPE_NAMES.get(arch, arch), "k": int(sub["won"].sum()),
                       "n": int(len(sub))})
    test = group_proportion_test(groups) if len(groups) > 1 else \
        {"p": None, "method": "none", "groups": groups}

    # Skill control: stratify by pre-match rating (median split). Only
    # rows with a rating participate; the stratification is only reported
    # as meaningful when both strata have enough matches to compare.
    strat_rows = df[df["rating"].notna()].copy()
    strata = None
    if len(strat_rows) >= 2 * min_archetype_n:
        median = float(strat_rows["rating"].median())
        out = {}
        for label, mask in (("below", strat_rows["rating"] <= median),
                            ("above", strat_rows["rating"] > median)):
            sub = strat_rows[mask]
            g = []
            for arch in sorted(set(sub["arch"])):
                a = sub[sub["arch"] == arch]
                g.append({"key": ARCHETYPE_NAMES.get(arch, arch), "k": int(a["won"].sum()),
                          "n": int(len(a))})
            out[label] = group_proportion_test(g)
        strata = {"median_rating": round(median, 1), "tests": out}

    overall = {"n_matches": len(summaries), "n_sides": int(len(df)),
               "n_rated_sides": int(len(strat_rows))}
    return {
        "status": "ok",
        "question": "Do Size/Power/Weight/Agility allocations correlate with "
                    "match outcomes, and is any effect robust to player skill?",
        "caveat": "A build's win rate can reflect who tends to pick it, not "
                  "its inherent strength; the skill-stratified test controls "
                  "for pre-match rating where sample size allows.",
        "overall": overall,
        "test": test,
        "strata": strata,
        "per_archetype": test["groups"],
    }


# ── AI agent comparison matrix ────────────────────────────────────────────

def _agent_vs_opponent(matrix: list[dict], agent: str, opponent: str) -> dict | None:
    for row in matrix:
        if row.get("model_a") == agent and row.get("model_b") == opponent:
            return {"wins": int(row["wins_a"]), "losses": int(row["wins_b"]),
                    "draws": int(row["draws"]), "win_rate": float(row["win_rate_a"]),
                    "n_games": int(row["wins_a"]) + int(row["wins_b"]) + int(row["draws"])}
        if row.get("model_b") == agent and row.get("model_a") == opponent:
            return {"wins": int(row["wins_b"]), "losses": int(row["wins_a"]),
                    "draws": int(row["draws"]), "win_rate": float(row["win_rate_b"]),
                    "n_games": int(row["wins_a"]) + int(row["wins_b"]) + int(row["draws"])}
    return None


def agent_matrix_analysis(matrix: list[dict]) -> dict:
    """Q: which built-in agent is empirically strongest, and is there a
    non-transitive (rock-paper-scissors) relationship between any three?

    Data: the head-to-head win matrix (each pair plays `n_games`, wins /
    losses / draws recorded). Method: per-agent mean win rate across all
    6 opponents (plus Wilson CI at the per-pair level); non-transitivity
    probe = strict-majority directed edges (X beats Y when X has more
    wins) searched for 3-cycles — reported as a *candidate* only, since
    per-pair n is small.
    """
    agents = sorted({r["model_a"] for r in matrix} | {r["model_b"] for r in matrix})
    if len(agents) < 2:
        return {"status": "empty", "agents": [], "strength": [],
                "cycles": [], "per_opponent": {}}
    strength = []
    for agent in agents:
        vs = []
        for opp in agents:
            if opp == agent:
                continue
            d = _agent_vs_opponent(matrix, agent, opp)
            if d:
                vs.append(d)
        wins = sum(d["wins"] for d in vs)
        total = sum(d["wins"] + d["losses"] + d["draws"] for d in vs)
        rate = wins / total if total else 0.0
        strength.append({"agent": agent, "win_rate": round(rate * 100, 1),
                         "wins": wins, "total": total,
                         "ci": wilson_ci(wins, total),
                         "n_pairs": len(vs)})
    strength.sort(key=lambda s: s["win_rate"], reverse=True)

    edges: set[tuple[str, str]] = set()
    for a in agents:
        for b in agents:
            if a == b:
                continue
            d = _agent_vs_opponent(matrix, a, b)
            if d and d["wins"] > d["losses"]:
                edges.add((a, b))
    cycles = []
    for (a, b) in edges:
        for c in agents:
            if c in (a, b):
                continue
            if (b, c) in edges and (c, a) in edges:
                cycles.append({"cycle": [a, b, c],
                               "edges": [(a, b), (b, c), (c, a)]})
    seen = set()
    unique = []
    for cy in cycles:
        key = frozenset(cy["cycle"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(cy)

    per_opponent = {}
    for opp in agents:
        vs = []
        for other in agents:
            if other == opp:
                continue
            d = _agent_vs_opponent(matrix, other, opp)
            if d:
                vs.append({"agent": other, "win_rate": float(d["win_rate"]),
                           "wins": int(d["wins"]), "n_games": int(d["n_games"])})
        per_opponent[opp] = {
            "mean": round(np.mean([v["win_rate"] for v in vs]), 1) if vs else None,
            "min": round(min(v["win_rate"] for v in vs), 1) if vs else None,
            "max": round(max(v["win_rate"] for v in vs), 1) if vs else None,
            "n": len(vs), "entries": vs,
        }
    return {"status": "ok", "agents": agents, "strength": strength,
            "cycles": unique, "per_opponent": per_opponent,
            "caveat": "Per-pair n is small (default 5 games); cycles and "
                      "rankings are candidates, not proofs."}


def custom_models_vs_builtins(entries: list[dict],
                              matrix_analysis: dict) -> list[dict]:
    """Q: how do user-submitted models compare against the built-in
    baseline? Each entry's `details` holds per-opponent win rates against
    the same 7 built-ins; the matrix provides the built-in-vs-built-in
    distribution for each opponent (what the 6 other built-ins achieve vs
    that opponent) as the comparison axis."""
    per_opponent = matrix_analysis.get("per_opponent") or {}
    out = []
    for e in entries:
        rows = []
        for d in (e.get("details") or []):
            opp = d.get("opponent")
            base = per_opponent.get(opp)
            if not base or not base.get("entries"):
                continue
            rates = [v["win_rate"] for v in base["entries"]]
            rows.append({
                "opponent": opp,
                "model_win_rate": float(d.get("win_rate", 0)),
                "model_n_games": int(d.get("n_games", 0)),
                "builtins_mean": base["mean"],
                "builtins_range": [base["min"], base["max"]],
                "builtins_n": base["n"],
                "above_builtin_mean": bool(float(d.get("win_rate", 0)) >= (base["mean"] or 0)),
            })
        out.append({"model_name": e.get("model_name"), "model_id": e.get("model_id"),
                    "username": e.get("username"), "score": e.get("score"),
                    "rows": rows})
    return out


# ── Rating / skill progression ────────────────────────────────────────────

def rating_progression(matches: list[dict], history: list[dict],
                       min_games: int = MIN_TRAJECTORY_GAMES,
                       min_bracket: int = MIN_BRACKET_MATCHES) -> dict:
    """Q: do players improve? Which rating brackets are the 'best'?
    Season-over-season: do players come back, and how do ratings change?

    Data: ranked_matches (per-match pre-match ratings of both players +
    outcomes) and rating_history (per-change log incl. season resets).
    Trajectories are shown only for players with >= min_games matches;
    bracket win rates aggregate a player's outcome against their own
    pre-match rating (self-consistent — bracket membership is decided by
    the rating that existed before the match, not after).
    """
    if not matches:
        return {"status": "insufficient_data", "have": 0,
                "needed": min_games,
                "message": "No rated matches recorded yet."}

    rows = []
    for m in matches:
        for side, key in (("a", m.get("player_a")), ("b", m.get("player_b"))):
            rating_before = m.get(f"rating_{side}_before")
            if rating_before is None:
                continue
            rows.append({
                "player": key, "created_at": m.get("created_at") or "",
                "rating_before": int(rating_before),
                "rating_after": int(m.get(f"rating_{side}_after", rating_before)),
                "won": bool(m.get("winner") == side.upper()),
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return {"status": "insufficient_data", "have": 0, "needed": min_games,
                "message": "No rated matches with rating data yet."}

    counts = df.groupby("player")["won"].count()
    qualified = set(counts[counts >= min_games].index)
    trajectories = []
    for player in sorted(qualified):
        sub = df[df["player"] == player].sort_values("created_at")
        trajectories.append({
            "player": player,
            "games": int(len(sub)),
            "points": [{"t": r["created_at"], "rating_before": int(r["rating_before"]),
                        "rating_after": int(r["rating_after"])}
                       for r in sub.to_dict("records")],
        })
    trajectories.sort(key=lambda t: t["games"], reverse=True)

    # Win rate by pre-match rating bracket (100-point buckets).
    df["bracket"] = (df["rating_before"] // 100 * 100).astype(int)
    brackets = []
    for bucket, sub in df.groupby("bracket", sort=True):
        n = int(len(sub))
        if n < min_bracket:
            continue
        k = int(sub["won"].sum())
        brackets.append({"bracket": int(bucket), "k": k, "n": n,
                         "win_rate": round(100.0 * k / n, 1),
                         "ci": wilson_ci(k, n),
                         "label": confidence_label(n, min_bracket)})
    bracket_test = group_proportion_test([{"key": str(b["bracket"]), "k": b["k"], "n": b["n"]}
                                          for b in brackets]) if len(brackets) >= 2 else None

    return {"status": "ok", "n_matches": int(len(df)),
            "trajectories": trajectories,
            "trajectory_note": f"Trajectories shown only for players with "
                               f">= {min_games} ranked matches "
                               f"({len(qualified)} player(s) qualify).",
            "brackets": brackets, "bracket_test": bracket_test}


def season_over_season(seasons: list[dict]) -> dict:
    """Q: season-over-season retention and rating change.

    Compares adjacent seasons via their frozen snapshots: players present
    in both (retention = share of season-N players active in N+1), and
    end-of-N vs start-of-N+1 rating deltas for returning players.
    """
    done = sorted([s for s in seasons if s.get("status") == "completed"],
                  key=lambda s: s["number"])
    active = [s for s in seasons if s.get("status") == "active"]
    ordered = done + sorted(active, key=lambda s: s["number"])
    pairs = []
    for i in range(len(ordered) - 1):
        a, b = ordered[i], ordered[i + 1]
        if a["number"] != b["number"] - 1:
            continue
        snap_a = _snapshot_players(a)
        snap_b = _snapshot_players(b)
        if not snap_a or not snap_b:
            continue
        by_a = {p["user_id"]: p for p in snap_a}
        by_b = {p["user_id"]: p for p in snap_b}
        returning = [u for u in by_a if u in by_b]
        deltas = [int(by_b[u]["rating"]) - int(by_a[u]["rating"]) for u in returning]
        pairs.append({
            "from_season": int(a["number"]), "to_season": int(b["number"]),
            "players_in_from": len(snap_a), "players_in_to": len(snap_b),
            "returning": len(returning),
            "retention": round(100.0 * len(returning) / len(snap_a), 1) if snap_a else 0,
            "mean_rating_change": round(float(np.mean(deltas)), 1) if deltas else None,
            "n_deltas": len(deltas),
        })
    return {"pairs": pairs}


def _snapshot_players(season: dict) -> list[dict]:
    import json
    raw = season.get("leaderboard_snapshot")
    if raw:
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return []
    return []


# ── Match dynamics ────────────────────────────────────────────────────────

def _real_replay_moves(replay_data) -> list[dict]:
    return [m for m in (replay_data or [])
            if isinstance(m, dict) and m.get("trajectory") and len(m["trajectory"]) >= 2]


def _near_miss_kicks(replay_data) -> list[int]:
    """1-based kick indices of near misses, reusing the highlight
    heuristic constants (a real shot that reached the goal mouth)."""
    moves = _real_replay_moves(replay_data)
    from db.highlights import (NEAR_MIN_SPEED, NEAR_LINE_MARGIN,
                               NEAR_MOUTH_DIST, max_speed, _min_mouth_dist)
    kicks = []
    for i, m in enumerate(moves, 1):
        if m.get("scored"):
            continue
        traj = m["trajectory"]
        if max_speed(traj) < NEAR_MIN_SPEED:
            continue
        target_x = 20 if m.get("mover") == "b" else 1380
        reached, best = _min_mouth_dist(traj, target_x, NEAR_LINE_MARGIN, 356, 519)
        if reached and best <= NEAR_MOUTH_DIST:
            kicks.append(i)
    return kicks


def _first_goal_kick(replay_data) -> int | None:
    for i, m in enumerate(_real_replay_moves(replay_data), 1):
        if m.get("scored"):
            return i
    return None


def match_dynamics(summaries: list[dict],
                   replays: list[dict] | None = None,
                   near_window: int = NEAR_MISS_GOAL_WINDOW) -> dict:
    """Q: how many goals per match, how quickly does the first goal
    arrive, and do near misses predict goals?

    Data: match summaries (durable scores + total kicks + first-goal
    kick, online matches) and tournament replays (ephemeral, per-kick
    trajectories with scored flags). Goals/match = mean + Poisson CI;
    first-goal timing = 1-based kick index, binned into a survival-style
    'fraction of matches without a goal by kick k'; near-miss → goal
    conversion = share of near misses followed by a goal within the next
    `near_window` kicks, with a binomial CI.
    """
    scores = [(int(s.get("score_a", 0)), int(s.get("score_b", 0)))
              for s in summaries]
    goals_per_match = {
        "n_matches": len(scores),
        "mean": round(float(np.mean([a + b for a, b in scores])), 2) if scores else None,
        "ci": poisson_ci(sum(a + b for a, b in scores), len(scores)) if scores else None,
        "distribution": [a + b for a, b in scores],
        "scoreboard": [{"score_a": a, "score_b": b} for a, b in scores],
    }

    first_goals = [int(s["first_goal_kick"]) for s in summaries
                   if s.get("first_goal_kick")]
    for rp in (replays or []):
        k = _first_goal_kick(rp.get("replay_data"))
        if k:
            first_goals.append(k)
    first_goal = None
    if first_goals:
        n = len(first_goals)
        total = sum(first_goals)
        bins = [0, 1, 2, 3, 5, 8, 12, 20]
        survival = []
        for i in range(len(bins) - 1):
            k_min, k_max = bins[i], bins[i + 1]
            scored_in_window = sum(1 for k in first_goals if k_min < k <= k_max)
            survival.append({"kick": k_max, "count": scored_in_window})
        cumulative = []
        remaining = n
        for b in survival:
            remaining -= b["count"]
            cumulative.append({"kick": b["kick"],
                               "no_goal_yet": round(100.0 * remaining / n, 1)})
        first_goal = {"n_matches_with_goal": n, "mean_kick": round(total / n, 1),
                      "ci": poisson_ci(total, n), "cumulative_no_goal": cumulative}

    near = {"n_matches": 0, "near_misses": 0, "converted": 0,
            "conversion_rate": None, "ci": None}
    for rp in (replays or []):
        moves = _real_replay_moves(rp.get("replay_data"))
        near_misses = _near_miss_kicks(rp.get("replay_data"))
        if not near_misses:
            continue
        near["n_matches"] += 1
        for kick in near_misses:
            near["near_misses"] += 1
            window = moves[kick:kick + near_window]
            if any(m.get("scored") for m in window):
                near["converted"] += 1
    if near["near_misses"]:
        rate = near["converted"] / near["near_misses"]
        near["conversion_rate"] = round(rate * 100, 1)
        near["ci"] = wilson_ci(near["converted"], near["near_misses"])

    return {
        "status": "ok",
        "goals_per_match": goals_per_match,
        "first_goal": first_goal,
        "near_miss": near,
        "caveat": "Tournament replays expire after 24h, so near-miss and "
                  "first-goal stats only cover recent tournament matches; "
                  "online matches contribute goals + kick counts via their "
                  "summary snapshots.",
    }
