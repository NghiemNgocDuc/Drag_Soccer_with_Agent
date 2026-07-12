"""Monte Carlo Soccer AI: focused random sampling with adaptive distribution."""
from __future__ import annotations
import math
import random
from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2
from models.common import needs_clear, progress_score, goal_targets, aim_through, dist_to_goal, suggested_powers

MODEL_NAME  = "Monte Carlo"
DESCRIPTION = "Focused random sampling around best goal-corner angles with adaptive spread."


def get_ai_move(state: dict, is_player_a: bool) -> tuple[int, float, float]:
    players = state["players_a"] if is_player_a else state["players_b"]
    bx, by  = state["ball"]["x"], state["ball"]["y"]
    target  = "A" if is_player_a else "B"
    defensive = needs_clear(state, is_player_a)
    goal_tgts = goal_targets(is_player_a)

    best_val  = float("-inf")
    best_move = (0, 0.0, 80.0)

    for pidx in range(len(players)):
        p = players[pidx]
        dist = dist_to_goal(p["x"], p["y"], is_player_a)
        powers = suggested_powers(dist)
        base_angles = [aim_through(p["x"], p["y"], bx, by, tx, ty) for tx, ty in goal_tgts]

        for base in base_angles:
            spread = 30 if dist > 400 else 20
            for _ in range(30):
                angle = base + random.gauss(0, spread / 2)
                angle = max(-180, min(180, angle))
                power = random.choice(powers) + random.uniform(-5, 5)
                power = max(40, min(100, power))
                traj, scored = simulate_kick(state, pidx, angle, power, is_player_a)
                val = 1200.0 if scored == target else (-400.0 if scored else 0.0)
                if not scored and len(traj) > 1:
                    end = traj[-1]
                    val += progress_score(end["x"], is_player_a, defensive)
                    if GOAL_Y1 <= end["y"] <= GOAL_Y2:
                        val += 60.0
                elif len(traj) == 1:
                    val -= 60.0
                if val > best_val:
                    best_val  = val
                    best_move = (pidx, angle, power)

    return best_move
