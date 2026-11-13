"""Deterministic tactical play with support for a legacy 36-action Q-table.

Modern squads and unseen boards use the shared full-roster tactical policy.
Exploration is confined to explicit offline self-play. Its legacy table is
trained privately and published after the batch, never action by action.
Online experience learning is handled separately by the shared outcome policy.
"""
from __future__ import annotations
import json
import math
import random
import pathlib
import copy
import threading
from contextvars import ContextVar

from models.soccer_logic import FIELD_W, GOAL_Y1, GOAL_Y2
from models.common import needs_clear, aim_through, dist_to_goal, suggested_powers

MODEL_NAME = "Adaptive Learner"
DESCRIPTION = "Deterministic full-squad tactics informed by validated match experience; legacy offline Q-table support."

# Discretization
_X_BINS = [280, 560, 840, 1120]  # 5 zones
_Y_BINS = [356, 519]  # inside goal mouth = 1 else 0 + top/mid/bottom handled via y bucket
_DIST_BINS = [60, 150]  # close/medium/far

_ACTIONS_ANGLES = [-30, -18, -6, 6, 18, 30]
_ACTIONS_POWERS_IDX = [0, 1]  # 0=mid power, 1=max

# Q-learning params
ALPHA = 0.22
GAMMA = 0.95
EPSILON_START = 0.32
EPSILON_MIN = 0.05
EPSILON_DECAY_GAMES = 400  # anneal over 400 games

_FILE = pathlib.Path(__file__).parent / "learning_q.json"
_REDIS_KEY = "learning:q"
_REDIS_META = "learning:meta"  # {"games": int, "wins": int}

_Q: dict[str, list[float]] = {}
_META: dict = {"games": 0, "wins": 0, "epsilon": EPSILON_START}
_STATE_LOCK = threading.RLock()
_TRAIN_LOCK = threading.Lock()
_training: ContextVar[tuple[dict, dict] | None] = ContextVar("legacy_q_training", default=None)


def _tables():
    return _training.get() or (_Q, _META)

def _redis():
    try:
        from db.redis_client import r as _r
        return _r
    except Exception:
        return None

def _load():
    global _Q, _META
    # try Redis
    try:
        r = _redis()
        if r:
            raw = r.get(_REDIS_KEY)
            if raw:
                s = raw.decode() if isinstance(raw, bytes) else raw
                _Q = json.loads(s)
            rawm = r.get(_REDIS_META)
            if rawm:
                s = rawm.decode() if isinstance(rawm, bytes) else rawm
                _META.update(json.loads(s))
            if _Q:
                return
    except Exception:
        pass
    # file fallback
    try:
        if _FILE.exists():
            j = json.loads(_FILE.read_text(encoding="utf-8"))
            _Q = j.get("q", {})
            _META.update(j.get("meta", {}))
    except Exception:
        pass

def _save():
    with _STATE_LOCK:
        payload = json.dumps({"q": _Q, "meta": _META})
    saved = json.loads(payload)
    try:
        r = _redis()
        if r:
            r.set(_REDIS_KEY, json.dumps(saved["q"]))
            r.set(_REDIS_META, json.dumps(saved["meta"]))
            return
    except Exception:
        pass
    try:
        _FILE.write_text(payload, encoding="utf-8")
    except Exception:
        pass

def _key_for(state, is_player_a: bool) -> str:
    bx, by = state["ball"]["x"], state["ball"]["y"]
    # x bin
    xb = 0
    for b in _X_BINS:
        if bx > b:
            xb += 1
    # y bin: 0=top,1=goal mouth,2=bottom but we bucket as inside/outside + top/bottom
    if GOAL_Y1 <= by <= GOAL_Y2:
        yb = 1
    elif by < GOAL_Y1:
        yb = 0
    else:
        yb = 2
    # dist to ball from nearest my player
    players = state["players_a"] if is_player_a else state["players_b"]
    d = min(math.hypot(p["x"] - bx, p["y"] - by) for p in players)
    db = 0
    for b in _DIST_BINS:
        if d > b:
            db += 1
    defensive = 1 if needs_clear(state, is_player_a) else 0
    return f"{xb}-{yb}-{db}-{defensive}-{'A' if is_player_a else 'B'}"

def _ensure_state(k: str):
    table, _ = _tables()
    if k not in table:
        table[k] = [0.0] * (3 * len(_ACTIONS_ANGLES) * len(_ACTIONS_POWERS_IDX))
    return table[k]

def _epsilon() -> float:
    _, meta = _tables()
    g = max(0, meta.get("games", 0))
    # linear anneal
    prog = min(1.0, g / EPSILON_DECAY_GAMES)
    return max(EPSILON_MIN, EPSILON_START * (1 - prog) + EPSILON_MIN * prog)

def _action_to_move(action_idx: int, state, is_player_a: bool):
    # 36 actions: player 0..2 * 6 angles *2 powers
    n_ang = len(_ACTIONS_ANGLES)
    n_pow = len(_ACTIONS_POWERS_IDX)
    per_player = n_ang * n_pow
    player_idx = action_idx // per_player
    rem = action_idx % per_player
    ang_idx = rem // n_pow
    pow_idx = rem % n_pow
    # angle via aim_through to goal centre + offset
    players = state["players_a"] if is_player_a else state["players_b"]
    player_idx = max(0, min(2, len(players) - 1, player_idx))
    bx, by = state["ball"]["x"], state["ball"]["y"]
    p = players[player_idx]
    gx = state["field"]["width"] if is_player_a else 0.0
    gy = (GOAL_Y1 + GOAL_Y2) / 2
    base = aim_through(p["x"], p["y"], bx, by, gx, gy)
    angle = base + _ACTIONS_ANGLES[ang_idx]
    # power
    dist = dist_to_goal(p["x"], p["y"], is_player_a)
    pw_all = suggested_powers(dist)
    powers = [pw_all[len(pw_all)//2], pw_all[-1]] if len(pw_all) > 2 else pw_all
    # ensure pow_idx within powers
    powers = powers[:2] if len(powers) >= 2 else powers + [powers[-1]]
    power = powers[pow_idx if pow_idx < len(powers) else -1]
    return player_idx, angle, power

def _epv(x, y, is_player_a, opp_players=None):
    """A mirrored position heuristic, not a calibrated scoring probability."""
    gx = FIELD_W if is_player_a else 0
    gy = (GOAL_Y1+GOAL_Y2)/2
    d = math.hypot(x-gx, y-gy)
    # distance term: logistic 1/(1+exp((d-320)/140)) — closer = higher EPV
    p_dist = 1.0 / (1.0 + math.exp((d - 320) / 140))
    # Use distance along the attacking axis so mirrored A/B boards agree.
    ang = abs(math.degrees(math.atan2(y - gy, abs(x - gx))))
    p_angle = max(0, 1 - ang/75.0)
    # Pressure near the ball reduces the heuristic.
    p_press = 1.0
    if opp_players:
        d_opp = min(math.hypot(p["x"]-x, p["y"]-y) for p in opp_players)
        if d_opp < 80:
            p_press = 0.55 + 0.45 * (d_opp/80)
    return p_dist * 0.6 + p_angle * 0.25 + p_press * 0.15

def _reward_for(end, scored, target, is_player_a, defensive, start_x, opp_players=None,
                *, start_y=None, start_opp_players=None):
    if scored == target:
        return 120.0
    if scored:
        return -120.0
    if not end:
        return -20.0
    # Reward improvement, rather than paying for the same position every turn.
    forward = (end["x"] - start_x) * (1 if is_player_a else -1)
    before = _epv(start_x, end["y"] if start_y is None else start_y,
                  is_player_a, start_opp_players)
    after = _epv(end["x"], end["y"], is_player_a, opp_players)
    return max(-90.0, min(90.0, forward * .15 + (after - before) * 85.0))

# Load on import
try:
    _load()
except Exception:
    pass

def get_ai_move(state, is_player_a: bool):
    from models.tactics import quick_move

    state.pop("_learner_last", None)
    k = _key_for(state, is_player_a)
    players = state["players_a"] if is_player_a else state["players_b"]
    action_count = min(3, len(players)) * len(_ACTIONS_ANGLES) * len(_ACTIONS_POWERS_IDX)
    offline = _training.get() is not None
    table, _ = _tables()
    q = _ensure_state(k) if offline else table.get(k)
    # Legacy actions only represent the first three players. Modern teams use
    # their full roster, including the striker, instead of inheriting that cap.
    if not offline:
        values = q[:action_count] if isinstance(q, list) and len(q) >= action_count else []
        if (len(players) > 3 or not values or
                not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values) or
                max(values) <= 0 or max(values) - min(values) < 1e-9):
            return quick_move(state, is_player_a)
    if offline and random.random() < _epsilon():
        a = random.randrange(action_count)
    else:
        # argmax
        best = 0
        bestv = q[0]
        for i in range(1, action_count):
            if q[i] > bestv:
                bestv = q[i]
                best = i
        a = best
    player_idx, angle, power = _action_to_move(a, state, is_player_a)
    # Metadata names only an executed legacy action, never a tactical fallback.
    try:
        state["_learner_last"] = {"k": k, "a": a, "bx": state["ball"]["x"]}
    except Exception:
        pass
    return player_idx, angle, power

def note_transition(prev_state, action_idx: int, reward: float, next_state, is_player_a: bool, save: bool = True):
    """Update a legacy action; bootstrap only at the learner's next decision."""
    try:
        pk = _key_for(prev_state, is_player_a)
        q = _ensure_state(pk)
        nk = (_key_for(next_state, is_player_a)
              if next_state is not None and not next_state.get("game_over")
              and next_state.get("is_player_a") == is_player_a else None)
        action_count = min(3, len(prev_state["players_a" if is_player_a else "players_b"])) * 12
        max_next = max(_ensure_state(nk)[:action_count]) if nk is not None else 0.0
        q[action_idx] += ALPHA * (reward + GAMMA * max_next - q[action_idx])
        if save:
            _save()
    except Exception:
        pass

def get_stats() -> dict:
    # Reads must not reload persisted data over an in-flight training batch.
    with _STATE_LOCK:
        return {"games": int(_META.get("games", 0)), "wins": int(_META.get("wins", 0)),
                "epsilon": 0.0, "training_epsilon": round(float(_epsilon()), 3),
                "states": len(_Q), "policy": "deterministic_tactical",
                "training": _TRAIN_LOCK.locked()}

def reset_learner():
    global _Q, _META
    with _STATE_LOCK:
        _Q = {}
        _META = {"games": 0, "wins": 0, "epsilon": EPSILON_START}
    _save()
    return get_stats()

def train_self_play(n_games: int = 50, opponent_id: str = "greedy") -> dict:
    """Offline legacy training; live requests never explore or see partial rows.

Each transition spans the learner's move and the opponent's response. Matches
are bounded to 30 commands; unfinished matches count as draws in this report.
This helper trains three-player legacy tables, not the modern outcome policy.
"""
    global _Q, _META
    from models.soccer_logic import new_soccer_state, apply_kick
    from models.search_budget import get_model_move
    if not _TRAIN_LOCK.acquire(blocking=False):
        raise RuntimeError("Legacy training already running")
    with _STATE_LOCK:
        candidate = copy.deepcopy(_Q), dict(_META)
    token = _training.set(candidate)
    try:
        try:
            from services.game_analytics import _load_model
            opp = _load_model(opponent_id)
        except Exception:
            opp = None
        if opp is None:
            from models import greedy_model as opp
        n_games = max(1, min(50, int(n_games)))
        table, meta = candidate
        wins = 0
        completed = 0
        for gi in range(n_games):
            st = new_soccer_state(player_count=3, half_length=0)
            learner_is_a = gi % 2 == 0
            target = "A" if learner_is_a else "B"
            pending = None
            for _ in range(30):
                if st.get("game_over"):
                    break
                is_a = st["is_player_a"]
                own_turn = is_a == learner_is_a
                # Retain settings/stats needed by movement and reward evaluation,
                # but not accumulated replay data in the candidate snapshots.
                before = copy.deepcopy({k: v for k, v in st.items()
                                        if k not in ("move_history", "history", "snapshots")})
                if own_turn:
                    move = get_ai_move(before, is_a)
                    pending = (before, before["_learner_last"]["a"], 0.0)
                else:
                    move = get_model_move(opp, st, is_a)
                trajectory, scored, _, _, _ = apply_kick(st, *move, is_a)
                if pending is not None:
                    opponents = "players_b" if learner_is_a else "players_a"
                    reward = _reward_for(
                        trajectory[-1] if trajectory else None, scored, target,
                        learner_is_a, needs_clear(before, learner_is_a),
                        before["ball"]["x"], st[opponents],
                        start_y=before["ball"]["y"], start_opp_players=before[opponents],
                    )
                    prev, action, prior_reward = pending
                    pending = (prev, action, prior_reward + (1 if own_turn else GAMMA) * reward)
                    if not own_turn or st.get("game_over"):
                        note_transition(prev, action, pending[2],
                                        None if st.get("game_over") else st,
                                        learner_is_a, save=False)
                        pending = None
            if pending is not None:
                # At the command cap no successor decision was observed.
                note_transition(pending[0], pending[1], pending[2], None, learner_is_a, save=False)
            won = st.get("winner") == target
            wins += int(won)
            completed += int(bool(st.get("game_over")))
            meta["games"] = int(meta.get("games", 0)) + 1
            meta["wins"] = int(meta.get("wins", 0)) + int(won)
        with _STATE_LOCK:
            _Q, _META = table, meta
        _save()
        total = int(meta.get("games", 0))
        return {"games": n_games, "completed_games": completed, "wins": wins,
                "winrate": round(wins / n_games * 100, 1), "total_games": total,
                "overall_winrate": round(int(meta.get("wins", 0)) / max(1, total) * 100, 1),
                "epsilon": 0.0, "training_epsilon": round(float(_epsilon()), 3),
                "states": len(table)}
    finally:
        _training.reset(token)
        _TRAIN_LOCK.release()
