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


def get_ai_move(state: dict, is_player_a: bool) -> tuple[int, float, float]:
    players = state["players_a"] if is_player_a else state["players_b"]
    bx, by  = state["ball"]["x"], state["ball"]["y"]
    best_val  = float("-inf")
    best_move = (0, 0.0, 85.0)
    goal_tgts = goal_targets(is_player_a)

    for pidx in range(len(players)):
        p = players[pidx]
        dist = dist_to_goal(p["x"], p["y"], is_player_a)
        powers = suggested_powers(dist)
        base_angles = [aim_through(p["x"], p["y"], bx, by, tx, ty) for tx, ty in goal_tgts]

        for base in base_angles:
            for off in range(-30, 31, 2):
                for power in powers:
                    angle = base + off
                    val = _kick_value(state, pidx, angle, power, is_player_a)
                    if val > best_val:
                        best_val  = val
                        best_move = (pidx, angle, power)

    return best_move
