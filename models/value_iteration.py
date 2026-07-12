"""Value Iteration Soccer AI: picks best-positioned player, fine goal-corner search."""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, FIELD_H, GOAL_Y1, GOAL_Y2
from models.common import needs_clear, progress_score, goal_targets, aim_through, dist_to_goal, suggested_powers

MODEL_NAME  = "Value Iteration"
DESCRIPTION = "Position-value player selection with fine-grained goal-corner angle search."


def _player_value(px: float, py: float, bx: float, by: float, is_player_a: bool) -> float:
    goal_angle = abs(math.degrees(math.atan2(
        ((FIELD_W if is_player_a else 0) - px),
        ((GOAL_Y1 + GOAL_Y2) / 2 - py)
    )))
    dist_to_ball = math.hypot(px - bx, py - by)
    centrality = -abs(py - FIELD_H / 2)
    return centrality - dist_to_ball * 0.3 - goal_angle * 2


def get_ai_move(state: dict, is_player_a: bool) -> tuple[int, float, float]:
    players = state["players_a"] if is_player_a else state["players_b"]
    bx, by  = state["ball"]["x"], state["ball"]["y"]
    target  = "A" if is_player_a else "B"
    defensive = needs_clear(state, is_player_a)
    goal_tgts = goal_targets(is_player_a)

    # Score each player
    scored_players = [(i, _player_value(p["x"], p["y"], bx, by, is_player_a)) for i, p in enumerate(players)]
    best_pidx = max(scored_players, key=lambda x: x[1])[0]

    p = players[best_pidx]
    dist = dist_to_goal(p["x"], p["y"], is_player_a)
    powers = suggested_powers(dist)
    base_angles = [aim_through(p["x"], p["y"], bx, by, tx, ty) for tx, ty in goal_tgts]

    best_val  = float("-inf")
    best_move = (best_pidx, 0.0, 85.0)

    for base in base_angles:
        for off in range(-35, 36, 2):
            angle = base + off
            for power in powers:
                traj, scored = simulate_kick(state, best_pidx, angle, power, is_player_a)
                val = 1200.0 if scored == target else (-400.0 if scored else 0.0)
                if not scored and len(traj) > 1:
                    end = traj[-1]
                    val += progress_score(end["x"], is_player_a, defensive)
                    if GOAL_Y1 <= end["y"] <= GOAL_Y2:
                        val += 70.0
                elif len(traj) == 1:
                    val -= 80.0
                if val > best_val:
                    best_val  = val
                    best_move = (best_pidx, angle, power)

    return best_move
