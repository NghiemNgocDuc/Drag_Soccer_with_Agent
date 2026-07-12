"""Q-Learning Soccer AI: zone-aware search with aggressive attacking and smart defense."""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2
from models.common import needs_clear, progress_score, goal_targets, aim_through, dist_to_goal, suggested_powers

MODEL_NAME  = "Q-Learning"
DESCRIPTION = "Zone-aware aggressive AI: tight angle search near goal, wide in own half, variable power."

_ZONE_W = FIELD_W // 4


def get_ai_move(state: dict, is_player_a: bool) -> tuple[int, float, float]:
    players = state["players_a"] if is_player_a else state["players_b"]
    bx, by  = state["ball"]["x"], state["ball"]["y"]
    target  = "A" if is_player_a else "B"
    zx      = int(bx // _ZONE_W)
    defensive = needs_clear(state, is_player_a)
    goal_tgts = goal_targets(is_player_a)

    # Zone-based search parameters — much tighter in attacking zone
    in_attack = (is_player_a and zx >= 2) or (not is_player_a and zx < 2)
    if in_attack:
        angle_step = 2
        angle_range = 25
    elif defensive:
        angle_step = 3
        angle_range = 40
    else:
        angle_step = 3
        angle_range = 35

    best_val  = float("-inf")
    best_move = (0, 0.0, 78.0)

    for pidx in range(len(players)):
        p = players[pidx]
        dist = dist_to_goal(p["x"], p["y"], is_player_a)
        powers = suggested_powers(dist)
        base_angles = [aim_through(p["x"], p["y"], bx, by, tx, ty) for tx, ty in goal_tgts]

        for base in base_angles:
            for off in range(-angle_range, angle_range + 1, angle_step):
                angle = base + off
                for power in powers:
                    traj, scored = simulate_kick(state, pidx, angle, power, is_player_a)
                    val = 1200.0 if scored == target else (-400.0 if scored else 0.0)
                    if not scored and len(traj) > 1:
                        end = traj[-1]
                        val += progress_score(end["x"], is_player_a, defensive)
                        if GOAL_Y1 <= end["y"] <= GOAL_Y2:
                            val += 80.0
                    elif len(traj) == 1:
                        val -= 80.0
                    if val > best_val:
                        best_val  = val
                        best_move = (pidx, angle, power)

    return best_move
