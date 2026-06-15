"""Bayesian Soccer AI: Gaussian prior around player-to-ball direction."""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W

MODEL_NAME  = "Bayesian"
DESCRIPTION = "Gaussian prior over kick angles centred on the player-to-ball direction."


def get_ai_move(state: dict, is_player_a: bool) -> tuple[int, float, float]:
    players = state["players_a"] if is_player_a else state["players_b"]
    bx, by  = state["ball"]["x"], state["ball"]["y"]
    target  = "A" if is_player_a else "B"
    best_val  = float("-inf")
    best_move = (0, 0.0, 80.0)

    for pidx in range(3):
        p = players[pidx]
        base = math.degrees(math.atan2(by - p["y"], bx - p["x"]))
        for spread in range(-30, 31, 5):
            for power in (88.0, 68.0):
                angle = base + spread
                prior = math.exp(-0.5 * (spread / 12) ** 2)
                traj, scored = simulate_kick(state, pidx, angle, power, is_player_a)
                val = (100.0 if scored == target else (-50.0 if scored else 0.0)) * prior
                if not scored and len(traj) > 1:
                    end = traj[-1]
                    val += ((FIELD_W - end["x"]) if is_player_a else end["x"]) * 0.04 * prior
                elif len(traj) == 1:
                    val -= 30.0 * prior
                if val > best_val:
                    best_val  = val
                    best_move = (pidx, angle, power)

    return best_move
