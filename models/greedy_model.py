"""Greedy Soccer AI: Turbo — fast + accurate.

Improvements over baseline (keeps latency low, boosts accuracy):
- Single best-player pick (distance + reachability) → 3× fewer simulations
- Coarse 6° sweep + fine 3° sweep, 2 powers per distance → ~80 sims vs 306 before (~4× faster)
- Smarter scoring: goal-line proximity bonus + backward-kick penalty + high-speed bonus
- Early exit on scored: stops searching once a scoring line is found
"""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2
from models.common import needs_clear, progress_score, goal_targets, aim_through, dist_to_goal, suggested_powers

MODEL_NAME  = "Greedy"
DESCRIPTION = "Turbo Greedy — 4× faster, goal-aware scoring, single-player focus."

_COARSE_STEP = 6.0
_FINE_STEP   = 3.0
_FINE_SPAN   = 6.0

def _evaluate(state, pidx, angle, power, is_player_a, target, defensive):
    traj, scored = simulate_kick(state, pidx, angle, power, is_player_a)
    if scored == target:
        return 2000.0  # higher reward to prioritize scoring
    if scored:
        return -600.0
    if len(traj) <= 1:
        return -120.0
    end = traj[-1]
    # progress toward opponent goal
    val = progress_score(end["x"], is_player_a, defensive)
    # on-target bonus (y inside goal mouth)
    if GOAL_Y1 <= end["y"] <= GOAL_Y2:
        val += 120.0
        # proximity to goal line bonus (closer = better)
        gx = FIELD_W if is_player_a else 0
        dist_to_line = abs(end["x"] - gx)
        val += max(0, 100 - dist_to_line * 0.3)
    # penalize backward kicks (ball ends closer to own goal than start)
    bx = state["ball"]["x"]
    if is_player_a and end["x"] < bx - 20:
        val -= 80.0
    if not is_player_a and end["x"] > bx + 20:
        val -= 80.0
    # small bonus for longer ball travel (higher power effective)
    start = traj[0]
    travel = math.hypot(end["x"]-start["x"], end["y"]-start["y"])
    val += min(40, travel * 0.05)
    return val

def _pick_best_player(players, bx, by, is_player_a):
    best, best_score = 0, float("-inf")
    for i, p in enumerate(players):
        d_ball = math.hypot(p["x"]-bx, p["y"]-by)
        d_goal = dist_to_goal(p["x"], p["y"], is_player_a)
        # power helps reach far balls
        power = (p.get("stats") or {}).get("power", 50)
        reach = d_ball / max(0.6, power/50)
        score = -reach*1.2 - d_goal*0.15 - abs(p["y"]- (GOAL_Y1+GOAL_Y2)/2)*0.1
        if score > best_score:
            best, best_score = i, score
    return best

def get_ai_move(state: dict, is_player_a: bool) -> tuple[int, float, float]:
    players = state["players_a"] if is_player_a else state["players_b"]
    bx, by  = state["ball"]["x"], state["ball"]["y"]
    target  = "A" if is_player_a else "B"
    defensive = needs_clear(state, is_player_a)
    goal_tgts = goal_targets(is_player_a)

    # pick single best player → 3× speedup
    best_pidx = _pick_best_player(players, bx, by, is_player_a)
    p = players[best_pidx]
    dist = dist_to_goal(p["x"], p["y"], is_player_a)
    powers_all = suggested_powers(dist)
    # keep only 2 powers: near-mid and max → halves simulations
    powers = [powers_all[len(powers_all)//2], powers_all[-1]] if len(powers_all)>2 else powers_all

    best_val  = float("-inf")
    best_move = (best_pidx, 0.0, powers[-1])
    seen = set()

    def _try(angle, power):
        nonlocal best_val, best_move
        key = (round(angle), round(power))
        if key in seen:
            return False
        seen.add(key)
        val = _evaluate(state, best_pidx, angle, power, is_player_a, target, defensive)
        if val > best_val:
            best_val = val
            best_move = (best_pidx, angle, power)
        # early exit if we found a scoring line
        return val >= 1999.0

    # Stage 1: coarse sweep only for best player
    for tx, ty in goal_tgts:
        base = aim_through(p["x"], p["y"], bx, by, tx, ty)
        for off in range(-30, 31, int(_COARSE_STEP)):
            angle = base + off
            for power in powers:
                if _try(angle, power):
                    return best_move

    # Stage 2: fine around best
    _, bangle, _ = best_move
    for off in range(-int(_FINE_SPAN), int(_FINE_SPAN)+1, int(_FINE_STEP)):
        angle = bangle + off
        for power in powers:
            if _try(angle, power):
                return best_move

    return best_move
