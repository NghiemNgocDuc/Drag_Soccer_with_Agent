"""Q-Learning Soccer AI: zone-aware search centred on player-to-ball direction."""
from __future__ import annotations
import math
from models.soccer_logic import simulate_kick, FIELD_W

MODEL_NAME  = "Q-Learning"
DESCRIPTION = "Zone-aware angle search centred on player-to-ball direction."

_ZONE_W = FIELD_W // 4


def get_ai_move(state: dict, is_player_a: bool) -> tuple[int, float, float]:
    players = state["players_a"] if is_player_a else state["players_b"]
    bx, by  = state["ball"]["x"], state["ball"]["y"]
    target  = "A" if is_player_a else "B"
    zx      = int(bx // _ZONE_W)
    # Tighter angle search in opponent half, wider in own half
    if (is_player_a and zx >= 2) or (not is_player_a and zx < 2):
        offsets = list(range(-20, 21, 5))
    else:
        offsets = list(range(-40, 41, 8))

    best_val  = float("-inf")
    best_move = (0, 0.0, 78.0)

    for pidx in range(3):
        p = players[pidx]
        base = math.degrees(math.atan2(by - p["y"], bx - p["x"]))
        for off in offsets:
            angle = base + off
            traj, scored = simulate_kick(state, pidx, angle, 78.0, is_player_a)
            val = 1000.0 if scored == target else (-250.0 if scored else 0.0)
            if not scored and len(traj) > 1:
                end = traj[-1]
                val += (FIELD_W - end["x"]) if is_player_a else end["x"]
            elif len(traj) == 1:
                val -= 50.0
            if val > best_val:
                best_val  = val
                best_move = (pidx, angle, 78.0)

    return best_move
