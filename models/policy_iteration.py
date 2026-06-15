"""Policy Iteration Soccer AI: best lane player, aims through ball."""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2

MODEL_NAME  = "Policy Iteration"
DESCRIPTION = "Player with clearest path to ball, aims through ball toward goal."


def _lane_score(px: float, py: float, bx: float, by: float) -> float:
    return -math.hypot(px - bx, py - by)


def get_ai_move(state: dict, is_player_a: bool) -> tuple[int, float, float]:
    players = state["players_a"] if is_player_a else state["players_b"]
    bx, by  = state["ball"]["x"], state["ball"]["y"]
    target  = "A" if is_player_a else "B"

    best_pidx = max(range(3), key=lambda i: _lane_score(players[i]["x"], players[i]["y"], bx, by))
    p = players[best_pidx]
    base = math.degrees(math.atan2(by - p["y"], bx - p["x"]))

    best_val  = float("-inf")
    best_move = (best_pidx, base, 82.0)

    for off in range(-30, 31, 6):
        angle = base + off
        traj, scored = simulate_kick(state, best_pidx, angle, 82.0, is_player_a)
        val = 1000.0 if scored == target else (-200.0 if scored else 0.0)
        if not scored and len(traj) > 1:
            end = traj[-1]
            val += (FIELD_W - end["x"]) if is_player_a else end["x"]
        elif len(traj) == 1:
            val -= 40.0
        if val > best_val:
            best_val  = val
            best_move = (best_pidx, angle, 82.0)

    return best_move
