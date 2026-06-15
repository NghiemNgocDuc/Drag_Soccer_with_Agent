"""Minimax Soccer AI: searches player/angle/power with opponent blocking in mind."""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2

MODEL_NAME  = "Minimax"
DESCRIPTION = "Evaluates moves considering opponent block positions (ball bounces off them)."


def _kick_value(state: dict, pidx: int, angle: float, power: float, is_player_a: bool) -> float:
    target = "A" if is_player_a else "B"
    traj, scored = simulate_kick(state, pidx, angle, power, is_player_a)
    if scored == target:
        return 1000.0
    if scored:
        return -300.0
    if len(traj) == 1:
        return -60.0  # missed ball
    end = traj[-1]
    return (FIELD_W - end["x"]) if is_player_a else end["x"]


def get_ai_move(state: dict, is_player_a: bool) -> tuple[int, float, float]:
    players = state["players_a"] if is_player_a else state["players_b"]
    bx, by  = state["ball"]["x"], state["ball"]["y"]
    best_val  = float("-inf")
    best_move = (0, 0.0, 85.0)

    for pidx in range(3):
        p = players[pidx]
        base = math.degrees(math.atan2(by - p["y"], bx - p["x"]))
        for off in range(-35, 36, 7):
            for power in (92.0, 75.0, 58.0):
                angle = base + off
                val = _kick_value(state, pidx, angle, power, is_player_a)
                if val > best_val:
                    best_val  = val
                    best_move = (pidx, angle, power)

    return best_move
