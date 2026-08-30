"""Monte Carlo Soccer AI: focused random sampling with adaptive distribution."""
from __future__ import annotations
import math
import random
from models.soccer_logic import simulate_kick, FIELD_W, GOAL_Y1, GOAL_Y2
from models.common import needs_clear, progress_score, goal_targets, aim_through, dist_to_goal, suggested_powers

MODEL_NAME  = "Monte Carlo"
DESCRIPTION = "Focused random sampling around best goal-corner angles with adaptive spread."


def _pick_best_player(players, bx, by, is_player_a):
    import math
    from models.common import dist_to_goal
    best, best_s = 0, float("-inf")
    for i,p in enumerate(players):
        d_ball = math.hypot(p["x"]-bx, p["y"]-by)
        power=(p.get("stats") or {}).get("power",50)
        s = -d_ball/max(0.6,power/50) - dist_to_goal(p["x"],p["y"],is_player_a)*0.1
        if s>best_s: best, best_s = i, s
    return best

def get_ai_move(state: dict, is_player_a: bool) -> tuple[int, float, float]:
    players = state["players_a"] if is_player_a else state["players_b"]
    bx, by  = state["ball"]["x"], state["ball"]["y"]
    target  = "A" if is_player_a else "B"
    defensive = needs_clear(state, is_player_a)
    goal_tgts = goal_targets(is_player_a)

    # single best player → 3× faster
    best_pidx = _pick_best_player(players, bx, by, is_player_a)
    p = players[best_pidx]
    dist = dist_to_goal(p["x"], p["y"], is_player_a)
    powers = suggested_powers(dist)
    # keep 2 powers only
    powers = [powers[len(powers)//2], powers[-1]] if len(powers)>2 else powers
    base_angles = [aim_through(p["x"], p["y"], bx, by, tx, ty) for tx, ty in goal_tgts]

    best_val  = float("-inf")
    best_move = (best_pidx, 0.0, powers[-1])

    for base in base_angles:
        spread = 24 if dist > 400 else 16
        for _ in range(12):  # 12 vs 30 → 2.5× faster
            angle = base + random.gauss(0, spread/2)
            angle = max(-180, min(180, angle))
            power = random.choice(powers) + random.uniform(-4, 4)
            power = max(40, min(100, power))
            traj, scored = simulate_kick(state, best_pidx, angle, power, is_player_a)
            if scored == target:
                return (best_pidx, angle, power)
            val = -500.0 if scored else 0.0
            if len(traj) > 1:
                end = traj[-1]
                val += progress_score(end["x"], is_player_a, defensive)
                if GOAL_Y1 <= end["y"] <= GOAL_Y2:
                    val += 90.0
                    gx = 1400 if is_player_a else 0
                    val += max(0, 80 - abs(end["x"]-gx)*0.25)
                # long travel bonus
                import math as _m
                travel = _m.hypot(end["x"]-traj[0]["x"], end["y"]-traj[0]["y"])
                val += min(30, travel*0.04)
            else:
                val -= 80.0
            if val > best_val:
                best_val = val
                best_move = (best_pidx, angle, power)

    return best_move
