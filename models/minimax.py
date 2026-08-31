"""Minimax Soccer AI: evaluates moves considering opponent blocking and goal targeting."""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2
from models.common import needs_clear, progress_score, goal_targets, aim_through, dist_to_goal, suggested_powers

MODEL_NAME  = "Minimax"
DESCRIPTION = "Evaluates all players with fine-grained goal-corner search and opponent-awareness."


def _kick_value(state: dict, pidx: int, angle: float, power: float, is_player_a: bool) -> float:
    target = "A" if is_player_a else "B"
    traj, scored = simulate_kick(state, pidx, angle, power, is_player_a)
    if scored == target:
        return 1500.0
    if scored:
        return -500.0
    if len(traj) == 1:
        return -100.0
    end = traj[-1]
    defensive = needs_clear(state, is_player_a)
    val = progress_score(end["x"], is_player_a, defensive)
    # Bonus for ending in goal y-range (on target)
    if GOAL_Y1 <= end["y"] <= GOAL_Y2:
        val += 80.0
    # Penalty for trajectories that bounce off opponents (ball changed direction)
    for i in range(2, len(traj)):
        dx = traj[i]["x"] - traj[i-1]["x"]
        dy = traj[i]["y"] - traj[i-1]["y"]
        px = traj[i-1]["x"] - traj[i-2]["x"]
        py = traj[i-1]["y"] - traj[i-2]["y"]
        if dx * px + dy * py < 0 and (abs(dx) > 5 or abs(dy) > 5):
            val -= 20.0
    return val


def _pick_best_player(players, bx, by, is_player_a):
    best, best_s = 0, float("-inf")
    for i, p in enumerate(players):
        d_ball = math.hypot(p["x"]-bx, p["y"]-by)
        power = (p.get("stats") or {}).get("power", 50)
        s = -d_ball / max(0.6, power/50) - dist_to_goal(p["x"], p["y"], is_player_a)*0.12
        if s > best_s: best, best_s = i, s
    return best

def get_ai_move(state: dict, is_player_a: bool) -> tuple[int, float, float]:
    players = state["players_a"] if is_player_a else state["players_b"]
    bx, by  = state["ball"]["x"], state["ball"]["y"]
    # single best player -> 3x faster, keeps <2s
    best_pidx = _pick_best_player(players, bx, by, is_player_a)
    p = players[best_pidx]
    dist = dist_to_goal(p["x"], p["y"], is_player_a)
    powers = suggested_powers(dist)
    # keep 2 powers only
    powers = [powers[len(powers)//2], powers[-1]] if len(powers)>2 else powers
    goal_tgts = goal_targets(is_player_a)
    base_angles = [aim_through(p["x"], p["y"], bx, by, tx, ty) for tx, ty in goal_tgts]
    best_val  = float("-inf")
    best_move = (best_pidx, 0.0, powers[-1])
    for base in base_angles:
        for off in range(-30, 31, 2):
            for power in powers:
                angle = base + off
                val = _kick_value(state, best_pidx, angle, power, is_player_a)
                if val > best_val:
                    best_val = val
                    best_move = (best_pidx, angle, power)
                if best_val >= 1499:  # early exit on goal
                    return best_move
    return best_move
