"""Loss analysis — decision tracing, outcome tagging, and "what a stronger
agent would have done" comparison for a user's own models.

Design (confirmed with user):
- Persist one trace row per turn where a *logged-in user's own model* kicked,
  for arena battles, tournament sims, and leaderboard benchmarks. Live hvai
  and anonymous/guest runs are never traced.
- The stored snapshot is the exact pre-kick state the model saw, so replays
  and comparisons are deterministic and honest.
- "What would a stronger agent do?" is answered ON DEMAND per viewed turn by
  running a built-in model's get_ai_move against the reconstructed state —
  one cheap call, nothing pre-stored.
- The physics engine and the AI sandbox contract are untouched.
"""
from __future__ import annotations

import copy
import math

from models.soccer_logic import (
    new_soccer_state, apply_kick, apply_penalty_kick,
    FIELD_W, GOAL_Y1, GOAL_Y2, BALL_R,
)

from db import decision_traces as _traces

# ── Outcome tags ─────────────────────────────────────────────────────────────
TAG_GOAL = "goal"
TAG_CHANCE = "good_chance"
TAG_NEUTRAL = "neutral"
TAG_POOR = "poor"
TAG_OWN_GOAL = "own_goal_risk"

TAG_ORDER = [TAG_GOAL, TAG_CHANCE, TAG_NEUTRAL, TAG_POOR, TAG_OWN_GOAL]

TAG_META = {
    TAG_GOAL: {
        "label": "Goal",
        "emoji": "⚽",
        "cls": "tag-goal",
        "description": "This kick went in. The model's read of the situation worked — learn what made this position good.",
    },
    TAG_CHANCE: {
        "label": "Good chance",
        "emoji": "🎯",
        "cls": "tag-chance",
        "description": "Reached the goal mouth but the keeper just kept it out. The decision was reasonable — the ball ended up on target.",
    },
    TAG_NEUTRAL: {
        "label": "Neutral",
        "emoji": "➖",
        "cls": "tag-neutral",
        "description": "Neither threatening nor harmful. The ball moved but never threatened the goal.",
    },
    TAG_POOR: {
        "label": "Poor",
        "emoji": "📉",
        "cls": "tag-poor",
        "description": "Made no forward progress (or went backwards) — this decision wasted the turn. Compare with a stronger agent below.",
    },
    TAG_OWN_GOAL: {
        "label": "Own-goal risk",
        "emoji": "🆘",
        "cls": "tag-own",
        "description": "This kick went into the model's OWN goal and the opponent scored. If this repeats, the model has a serious direction bug.",
    },
}

GOAL_CENTER_Y = (GOAL_Y1 + GOAL_Y2) / 2
_GOAL_MOUTH_PAD = 25   # px of slop around the goal aperture for "on target"
_LINE_REACH = 30       # px from the goal line counts as "reached the mouth"
_BACKWARD_PX = 30      # px of backwards travel ⇒ poor
_TAP_SPEED = 90        # per-frame speed below this with little progress ⇒ poor
_TAP_PROGRESS = 60


# ── Snapshot building ────────────────────────────────────────────────────────
def build_snapshot(state: dict) -> dict:
    """Deep-copy a state dict minus bulky/irrelevant keys, for storage.

    `state` is the plain physics state dict the model saw (pre-kick), exactly
    as produced by `new_soccer_state()` / a game session. Keeping the whole
    dict (players, ball, score, period, customizations, player_stats, ...)
    means reconstruction is lossless — no fields the agents read are dropped.
    """
    snap = copy.deepcopy(state)
    snap.pop("move_history", None)
    return snap


def reconstruct_state(snapshot: dict) -> dict:
    """Rebuild a working physics state dict from a stored snapshot.

    `new_soccer_state()` first so every key the engine touches has a sane
    default; the snapshot then overlays the exact decision-time situation.
    """
    st = new_soccer_state()
    for k, v in snapshot.items():
        st[k] = copy.deepcopy(v)
    st["move_history"] = []
    st["_finalized"] = False
    return st


# ── Outcome tagging ──────────────────────────────────────────────────────────
def tag_outcome(snapshot: dict, decision: dict, scored: str | None, trajectory: list | None) -> str:
    """Classify one traced kick from the traced model's perspective.

    `scored` is the side that scored ("A"/"B") or None. Only rows where the
    traced model kicked are stored, so `scored != my side` is an own-goal.
    """
    is_player_a = bool(snapshot.get("is_player_a"))
    my_side = "A" if is_player_a else "B"

    if scored == my_side:
        return TAG_GOAL
    if scored is not None:
        return TAG_OWN_GOAL
    if not trajectory or len(trajectory) < 2:
        return TAG_NEUTRAL

    start, end = trajectory[0], trajectory[-1]
    goal_line = FIELD_W - 20 if is_player_a else 20  # 1380 vs 20 (_MARGIN)
    progress = (end["x"] - start["x"]) if is_player_a else (start["x"] - end["x"])

    if progress <= -_BACKWARD_PX:
        return TAG_POOR

    mouth_dy = abs(end["y"] - GOAL_CENTER_Y)
    reached_line = (end["x"] >= goal_line - _LINE_REACH) if is_player_a else (end["x"] <= goal_line + _LINE_REACH)
    if reached_line and mouth_dy <= (GOAL_Y2 - GOAL_Y1) / 2 + _GOAL_MOUTH_PAD:
        return TAG_CHANCE

    speeds = []
    for i in range(1, len(trajectory)):
        speeds.append(math.hypot(trajectory[i]["x"] - trajectory[i - 1]["x"],
                                 trajectory[i]["y"] - trajectory[i - 1]["y"]))
    max_speed = max(speeds) if speeds else 0
    if progress < _TAP_PROGRESS and max_speed < _TAP_SPEED:
        return TAG_POOR

    return TAG_NEUTRAL


# ── Persistence helper (called from sim call sites) ──────────────────────────
def save_traced_turn(
    *,
    owner_id: str,
    model_id: str,
    model_label: str,
    match_id: str,
    opponent: str,
    result: str,            # win | loss | draw (traced model's perspective)
    score_for: int,
    score_against: int,
    turn: int,
    mover: str,             # "a" | "b"
    pre_state: dict,
    decision: dict,         # {player_idx, angle, power}
    scored: str | None,
    trajectory: list | None,
) -> None:
    """Snapshot + tag + persist one traced turn.

    Must be called with `pre_state` BEFORE apply_kick mutates it (the deep
    copy happens here synchronously). Failures are swallowed — tracing must
    never break the match itself.
    """
    try:
        snapshot = build_snapshot(pre_state)
        tag = tag_outcome(snapshot, decision, scored, trajectory)
        _traces.save_trace({
            "owner_id": owner_id,
            "match_id": match_id,
            "model_id": model_id,
            "model_label": model_label,
            "opponent": opponent,
            "result": result,
            "score_for": score_for,
            "score_against": score_against,
            "turn": turn,
            "mover": mover,
            "decision": decision,
            "state_snapshot": snapshot,
            "outcome_tag": tag,
        })
    except Exception:
        pass


# ── On-demand comparison ─────────────────────────────────────────────────────
def builtin_decision(snapshot: dict, model_key: str) -> dict | None:
    """What would built-in `model_key` have done at this exact turn?

    Runs the built-in's get_ai_move against the reconstructed decision-time
    state. Built-ins are loaded directly (never through the sandbox — only
    user code goes through user_models/runner.py). Returns a comparison
    payload or None on failure.
    """
    from services import game_analytics as ga

    mod = ga._load_model(model_key)
    if mod is None:
        return None
    name = next((m["name"] for m in ga.MODEL_CATALOG if m["id"] == model_key), model_key)
    try:
        st = reconstruct_state(snapshot)
        pidx, ang, pwr = mod.get_ai_move(st, bool(snapshot.get("is_player_a")))
    except Exception:
        return None
    return {
        "model": model_key,
        "name": name,
        "player_idx": int(pidx),
        "angle": round(float(ang), 1),
        "power": round(float(pwr), 1),
    }


def default_comparison_model() -> str:
    return "minimax"


def playback_turn(snapshot: dict, decision: dict) -> dict:
    """Re-run the recorded kick against the stored pre-kick state.

    Returns the same shape the live match produces: trajectory + scored side
    + description + the tag for this kick. Used by the analysis viewer's
    "play this kick" and by tests to sanity-check the stored decision.
    """
    st = reconstruct_state(snapshot)
    is_player_a = bool(snapshot.get("is_player_a"))
    pidx = int(decision["player_idx"])
    ang = float(decision["angle"])
    pwr = float(decision["power"])
    if snapshot.get("penalty_shootout"):
        traj, scored, desc = apply_penalty_kick(st, pidx, ang, pwr, is_player_a)
        scored_side = "A" if scored else None
    else:
        traj, scored, desc, _kep, _push = apply_kick(st, pidx, ang, pwr, is_player_a)
        scored_side = scored
    return {
        "trajectory": traj,
        "scored": scored_side,
        "desc": desc,
        "outcome_tag": tag_outcome(snapshot, decision, scored_side, traj),
    }


# ── Aggregate patterns ───────────────────────────────────────────────────────
def aggregate_patterns(rows: list[dict]) -> dict:
    """Roll up a model's traced turns into loss patterns for the summary page.

    Honest-by-design: output always comes with caveats (only the traced
    model's turns are recorded; retention is 30 days / ~200 matches).
    """
    matches: dict[str, dict] = {}
    turn_counts: dict[str, int] = {}
    for r in rows:
        mid = r["match_id"]
        turn_counts[mid] = turn_counts.get(mid, 0) + 1
        m = matches.setdefault(mid, {
            "match_id": mid,
            "opponent": r.get("opponent", ""),
            "result": r.get("result", ""),
            "score_for": r.get("score_for", 0),
            "score_against": r.get("score_against", 0),
            "model_label": r.get("model_label", ""),
            "outcomes": {t: 0 for t in TAG_ORDER},
        })
        m["outcomes"][r.get("outcome_tag", TAG_NEUTRAL)] = \
            m["outcomes"].get(r.get("outcome_tag", TAG_NEUTRAL), 0) + 1

    record = {"wins": 0, "losses": 0, "draws": 0}
    outcomes = {t: 0 for t in TAG_ORDER}
    per_opponent: dict[str, dict] = {}
    by_third = {
        "early": {t: 0 for t in TAG_ORDER},
        "middle": {t: 0 for t in TAG_ORDER},
        "late": {t: 0 for t in TAG_ORDER},
    }

    for mid, m in matches.items():
        record["wins" if m["result"] == "win" else "losses" if m["result"] == "loss" else "draws"] += 1
        opp = per_opponent.setdefault(m["opponent"] or "Unknown", {
            "opponent": m["opponent"] or "Unknown",
            "matches": 0, "wins": 0, "losses": 0, "draws": 0,
            "outcomes": {t: 0 for t in TAG_ORDER},
        })
        opp["matches"] += 1
        opp["wins" if m["result"] == "win" else "losses" if m["result"] == "loss" else "draws"] += 1

    # Second pass over rows for per-turn data (outcome thirds per match).
    for r in rows:
        tag = r.get("outcome_tag", TAG_NEUTRAL)
        outcomes[tag] += 1
        total = turn_counts.get(r["match_id"], 1) or 1
        early_cut, mid_cut = total / 3, 2 * total / 3
        if r["turn"] < early_cut:
            by_third["early"][tag] += 1
        elif r["turn"] < mid_cut:
            by_third["middle"][tag] += 1
        else:
            by_third["late"][tag] += 1
        po = per_opponent.get(r.get("opponent", "") or "Unknown")
        if po:
            po["outcomes"][tag] += 1

    n_turns = len(rows)
    non_goal = sum(outcomes[t] for t in (TAG_POOR, TAG_NEUTRAL, TAG_OWN_GOAL))
    return {
        "n_matches": len(matches),
        "n_turns": n_turns,
        "record": record,
        "outcomes": outcomes,
        "poor_rate": round(non_goal / n_turns * 100, 1) if n_turns else 0.0,
        "per_opponent": sorted(per_opponent.values(),
                               key=lambda o: (-o["matches"], o["opponent"])),
        "by_third": by_third,
        "own_goal_match_ids": [m["match_id"] for m in matches.values()
                               if m["outcomes"].get(TAG_OWN_GOAL, 0) > 0],
        "caveats": [
            "Traces only cover turns where your model kicked (opponent turns are not stored).",
            "Traces expire after 30 days, capped at ~200 recent matches per model.",
            "Outcome tags are heuristic (goal-mouth proximity), not ground truth.",
        ],
    }
