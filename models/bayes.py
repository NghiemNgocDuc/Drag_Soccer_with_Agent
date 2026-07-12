"""Bayesian Soccer AI: multi-target Gaussian prior with likelihood-weighted scoring."""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2
from models.common import needs_clear, progress_score, goal_targets, aim_through, dist_to_goal, suggested_powers

MODEL_NAME  = "Bayesian"
DESCRIPTION = "Multi-target Bayesian inference over goal corners with adaptive prior width."


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
        prior_sigma = 8.0 if dist < 250 else 12.0
        base_angles = [aim_through(p["x"], p["y"], bx, by, tx, ty) for tx, ty in goal_tgts]

        for base in base_angles:
            for off in range(-40, 41, 2):
                angle = base + off
                prior = math.exp(-0.5 * (off / prior_sigma) ** 2)
                for power in powers:
                    traj, scored = simulate_kick(state, pidx, angle, power, is_player_a)
                    val = (200.0 if scored == target else (-100.0 if scored else 0.0)) * prior
                    if not scored and len(traj) > 1:
                        end = traj[-1]
                        val += progress_score(end["x"], is_player_a, defensive) * prior * 0.08
                        if GOAL_Y1 <= end["y"] <= GOAL_Y2:
                            val += 50.0 * prior
                    elif len(traj) == 1:
                        val -= 60.0 * prior
                    if val > best_val:
                        best_val  = val
                        best_move = (pidx, angle, power)

    return best_move
