"""Greedy Soccer AI: nearest player aims through ball toward goal."""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2

MODEL_NAME  = "Greedy"
DESCRIPTION = "Picks the player nearest the ball, aims through it toward goal."


def get_ai_move(state: dict, is_player_a: bool) -> tuple[int, float, float]:
    players = state["players_a"] if is_player_a else state["players_b"]
    bx, by  = state["ball"]["x"], state["ball"]["y"]
    goal_cx = float(FIELD_W) if is_player_a else 0.0
    goal_cy = (GOAL_Y1 + GOAL_Y2) / 2
    target  = "A" if is_player_a else "B"

    best_val  = float("-inf")
    best_move = (0, 0.0, 85.0)

    for pidx in range(3):
        p = players[pidx]
        # Aim angle = player -> ball direction (so player physically reaches ball)
        base = math.degrees(math.atan2(by - p["y"], bx - p["x"]))
        for off in range(-30, 31, 6):
            angle = base + off
            for power in (90.0, 75.0):
                traj, scored = simulate_kick(state, pidx, angle, power, is_player_a)
                val = 1000.0 if scored == target else (-300.0 if scored else 0.0)
                if not scored and len(traj) > 1:
                    end = traj[-1]
                    val += (FIELD_W - end["x"]) if is_player_a else end["x"]
                elif len(traj) == 1:
                    val -= 50.0  # missed ball penalty
                if val > best_val:
                    best_val  = val
                    best_move = (pidx, angle, power)

    return best_move
