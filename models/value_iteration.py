"""Value Iteration Soccer AI: picks most central player, aims through ball."""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W, FIELD_H

MODEL_NAME  = "Value Iteration"
DESCRIPTION = "Picks most centrally positioned player, searches angles through ball toward goal."


def _player_value(px: float, py: float, is_player_a: bool) -> float:
    cx = FIELD_W * 0.6 if is_player_a else FIELD_W * 0.4
    return -math.hypot(px - cx, py - FIELD_H / 2)


def get_ai_move(state: dict, is_player_a: bool) -> tuple[int, float, float]:
    players = state["players_a"] if is_player_a else state["players_b"]
    bx, by  = state["ball"]["x"], state["ball"]["y"]
    target  = "A" if is_player_a else "B"

    best_pidx = max(range(3), key=lambda i: _player_value(players[i]["x"], players[i]["y"], is_player_a))
    p = players[best_pidx]
    base = math.degrees(math.atan2(by - p["y"], bx - p["x"]))

    best_val  = float("-inf")
    best_move = (best_pidx, base, 85.0)

    for off in range(-30, 31, 5):
        angle = base + off
        traj, scored = simulate_kick(state, best_pidx, angle, 85.0, is_player_a)
        val = 1000.0 if scored == target else (-200.0 if scored else 0.0)
        if not scored and len(traj) > 1:
            end = traj[-1]
            val += (FIELD_W - end["x"]) if is_player_a else end["x"]
        elif len(traj) == 1:
            val -= 40.0
        if val > best_val:
            best_val  = val
            best_move = (best_pidx, angle, 85.0)

    return best_move
