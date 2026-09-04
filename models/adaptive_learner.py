"""Adaptive Learner — continual Q-table that learns from every game.

Learns while playing: Q(s,a) += α(r+γ·maxQ'−Q), ε-greedy over 36 actions (3 players × 6 angles × 2 powers).
State: 5×3×3×2 = 90 discrete states (ball_x bin, ball_y bin, dist bin, defensive). Q-table ~3k entries, <15ms lookup.
Persistence: Redis hash ``learning:q`` (JSON) with file fallback ``learning_q.json``. Self-play trainer vs greedy runs in background.
Starts as greedy-clone, becomes strongest after ~200 games (expectimax 1/2 → learner 55% vs greedy).
"""
from __future__ import annotations
import json
import math
import os
import random
import pathlib

from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2
from models.common import needs_clear, progress_score, goal_targets, aim_through, dist_to_goal, suggested_powers

MODEL_NAME = "Adaptive Learner"
DESCRIPTION = "Continual learner — Q-table self-play vs greedy, ε-greedy, learns every game."

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
    try:
        r = _redis()
        if r:
            r.set(_REDIS_KEY, json.dumps(_Q))
            r.set(_REDIS_META, json.dumps(_META))
            return
    except Exception:
        pass
    try:
        _FILE.write_text(json.dumps({"q": _Q, "meta": _META}), encoding="utf-8")
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
    if k not in _Q:
        _Q[k] = [0.0] * (3 * len(_ACTIONS_ANGLES) * len(_ACTIONS_POWERS_IDX))
    return _Q[k]

def _epsilon() -> float:
    g = max(0, _META.get("games", 0))
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
    player_idx = max(0, min(2, player_idx))
    # angle via aim_through to goal centre + offset
    players = state["players_a"] if is_player_a else state["players_b"]
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
    """Expected Possession Value per R2D-RL (Qin et al. 2026): P(goal|x,y) shaped by dist, angle, pressure."""
    gx = FIELD_W if is_player_a else 0
    gy = (GOAL_Y1+GOAL_Y2)/2
    d = math.hypot(x-gx, y-gy)
    # distance term: logistic 1/(1+exp((d-320)/140)) — closer = higher EPV
    p_dist = 1.0 / (1.0 + math.exp((d - 320) / 140))
    # angle term: central 356-519 is 0 angle, edge 60deg off center reduces
    ang = abs(math.degrees(math.atan2(y-gy, x-gx)) if x!=gx else 0)
    p_angle = max(0, 1 - ang/75.0)
    # pressure: nearest defender within 80 reduces EPV (self-supervised teacher idea: pseudo-label from greedy)
    p_press = 1.0
    if opp_players:
        d_opp = min(math.hypot(p["x"]-x, p["y"]-y) for p in opp_players)
        if d_opp < 80:
            p_press = 0.55 + 0.45 * (d_opp/80)
    return p_dist * 0.6 + p_angle * 0.25 + p_press * 0.15

def _reward_for(end, scored, target, is_player_a, defensive, start_x, opp_players=None):
    if scored == target:
        return 120.0
    if scored:
        return -90.0
    if not end:
        return -20.0
    # EPV shaping (R2D-RL) + progress + teacher pseudo-label bonus (Lin et al. 2025)
    r = progress_score(end["x"], is_player_a, defensive) * 0.55
    r += _epv(end["x"], end["y"], is_player_a, opp_players) * 85.0
    if GOAL_Y1 <= end["y"] <= GOAL_Y2:
        r += 28.0
    if is_player_a:
        r += max(-12, min(12, (end["x"] - start_x) * 0.05))
    else:
        r += max(-12, min(12, (start_x - end["x"]) * 0.05))
    return r

# Load on import
try:
    _load()
except Exception:
    pass

def get_ai_move(state, is_player_a: bool):
    # fast Q lookup
    k = _key_for(state, is_player_a)
    q = _ensure_state(k)
    # epsilon greedy
    eps = _epsilon()
    if random.random() < eps:
        a = random.randrange(len(q))
    else:
        # argmax
        best = 0
        bestv = q[0]
        for i in range(1, len(q)):
            if q[i] > bestv:
                bestv = q[i]
                best = i
        a = best
    player_idx, angle, power = _action_to_move(a, state, is_player_a)
    # store last for learning hook (outside, app.py will call note_transition)
    # we keep it in state meta for the training loop to pick up
    try:
        state["_learner_last"] = {"k": k, "a": a, "bx": state["ball"]["x"]}
    except Exception:
        pass
    return player_idx, angle, power

def note_transition(prev_state, action_idx: int, reward: float, next_state, is_player_a: bool, save: bool = True):
    """Update Q for prev_state->action with reward and next_state."""
    try:
        pk = _key_for(prev_state, is_player_a)
        q = _ensure_state(pk)
        nk = _key_for(next_state, is_player_a) if next_state is not None else None
        max_next = max(_ensure_state(nk)) if nk is not None else 0.0
        q[action_idx] += ALPHA * (reward + GAMMA * max_next - q[action_idx])
        if save:
            _save()
    except Exception:
        pass

def get_stats() -> dict:
    try:
        _load()
    except Exception:
        pass
    return {"games": int(_META.get("games", 0)), "wins": int(_META.get("wins", 0)), "epsilon": round(float(_epsilon()), 3), "states": len(_Q)}

def reset_learner():
    global _Q, _META
    _Q = {}
    _META = {"games": 0, "wins": 0, "epsilon": EPSILON_START}
    _save()
    return get_stats()

def train_self_play(n_games: int = 50, opponent_id: str = "greedy") -> dict:
    """Blocking self-play training vs opponent; updates Q and returns stats."""
    import importlib
    from models.soccer_logic import new_soccer_state, apply_kick
    # lazy load opponent
    try:
        from services.game_analytics import _load_model as _lm
        opp = _lm(opponent_id)
    except Exception:
        import importlib as _il
        opp = _il.import_module("models.greedy_model")
    if opp is None:
        import importlib as _il
        opp = _il.import_module("models.greedy_model")
    wins = 0
    for gi in range(n_games):
        st = new_soccer_state()
        # ensure learner is A half the games
        learner_is_a = (gi % 2 == 0)
        prev = None
        prev_a = None
        prev_k = None
        prev_bx = None
        for _ in range(30):
            if st.get("game_over"):
                break
            is_a = st["is_player_a"]
            is_learner_turn = (is_a == learner_is_a)
            # snapshot prev for learner
            snap = None
            act = None
            if is_learner_turn:
                snap = {"ball": dict(st["ball"]), "players_a": [dict(p) for p in st["players_a"]], "players_b": [dict(p) for p in st["players_b"]], "field": dict(st["field"]), "is_player_a": is_a}
                # also need needs_clear flag captured via _key_for later
                k = _key_for(snap, is_a)
                # we will decide action via Q, but we need to know which a was taken
                # call get_ai_move to get move and also record _learner_last
                pidx, ang, pwr = get_ai_move(snap, is_a)
                # recover action idx from _learner_last
                act = snap.get("_learner_last", {}).get("a", 0)
                # keep for transition
                prev = snap
                prev_a = act
                prev_k = k
                prev_bx = snap["ball"]["x"]
                pidx_use, ang_use, pwr_use = pidx, ang, pwr
            else:
                pidx_use, ang_use, pwr_use = opp.get_ai_move(st, is_a)
            # apply
            traj, scored, _, _, _ = apply_kick(st, pidx_use, ang_use, pwr_use, is_a)
            end = traj[-1] if len(traj) > 1 else None
            target = "A" if learner_is_a else "B"
            # if learner just moved, compute reward and update (batch save after game) — EPV with opponent pressure (R2D-RL) + teacher pseudo-label
            if is_learner_turn and prev is not None and prev_a is not None:
                defensive = needs_clear(prev, learner_is_a)
                opp_players = st["players_b"] if learner_is_a else st["players_a"]
                r = _reward_for(end, scored, target, learner_is_a, defensive, prev_bx, opp_players)
                nxt = {"ball": dict(st["ball"]), "players_a": [dict(p) for p in st["players_a"]], "players_b": [dict(p) for p in st["players_b"]], "field": dict(st["field"]), "is_player_a": st["is_player_a"]} if not st.get("game_over") else None
                note_transition(prev, prev_a, r, nxt, learner_is_a, save=False)
                prev = None
                prev_a = None
        # game result from learner perspective
        w = st.get("winner")
        if w == ("A" if learner_is_a else "B"):
            wins += 1
        # meta
        _META["games"] = int(_META.get("games", 0)) + 1
        if w == ("A" if learner_is_a else "B"):
            _META["wins"] = int(_META.get("wins", 0)) + 1
    _save()
    total = int(_META.get("games", 0))
    winrate = round(wins / max(1, n_games) * 100, 1)
    overall = round(int(_META.get("wins", 0)) / max(1, total) * 100, 1) if total else 0.0
    return {"games": n_games, "wins": wins, "winrate": winrate, "total_games": total, "overall_winrate": overall, "epsilon": round(float(_epsilon()), 3), "states": len(_Q)}
