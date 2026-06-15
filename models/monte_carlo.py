"""Monte Carlo Soccer AI: randomly samples kicks that can actually reach the ball."""
from __future__ import annotations
import math
import random
from models.soccer_logic import simulate_kick, FIELD_W

MODEL_NAME  = "Monte Carlo"
DESCRIPTION = "Samples random kicks centred on the player-to-ball direction."


def get_ai_move(state: dict, is_player_a: bool) -> tuple[int, float, float]:
    players = state["players_a"] if is_player_a else state["players_b"]
    bx, by  = state["ball"]["x"], state["ball"]["y"]
    target  = "A" if is_player_a else "B"

    best_val  = float("-inf")
    best_move = (0, 0.0, 80.0)

    for pidx in range(3):
        p = players[pidx]
        base = math.degrees(math.atan2(by - p["y"], bx - p["x"]))
        for _ in range(20):
            angle = base + random.uniform(-40, 40)
            power = random.uniform(55, 100)
            traj, scored = simulate_kick(state, pidx, angle, power, is_player_a)
            val = 1000.0 if scored == target else (-250.0 if scored else 0.0)
            if not scored and len(traj) > 1:
                end = traj[-1]
                val += (FIELD_W - end["x"]) if is_player_a else end["x"]
            elif len(traj) == 1:
                val -= 40.0
            if val > best_val:
                best_val  = val
                best_move = (pidx, angle, power)

    return best_move
