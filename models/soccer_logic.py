"""soccer_logic.py — Soccer game physics engine."""
from __future__ import annotations
import math

FIELD_W: int   = 800
FIELD_H: int   = 500
BALL_R: int    = 12
PLAYER_R: int  = 20
FRICTION: float = 0.93
GOAL_Y1: float  = 185.0
GOAL_Y2: float  = 315.0
POWER_SCALE: float = 1.0
GOALS_TO_WIN: int  = 5
MAX_KICKS: int     = 60   # plenty of room for a 5-goal game

HOME_A: list[tuple[float, float]] = [(150.0, 165.0), (150.0, 250.0), (150.0, 335.0)]
HOME_B: list[tuple[float, float]] = [(650.0, 165.0), (650.0, 250.0), (650.0, 335.0)]

_BOUNCE = 0.82
_PLAYER_TRAVEL = 3.0        # power=100 -> 300 game units forward
_CONTACT = float(PLAYER_R + BALL_R)   # 32 — player-ball hit zone
_P2P    = float(PLAYER_R * 2)         # 40 — player-player minimum distance

# The white boundary line is drawn 20px from each canvas edge.
# All ball bounces happen at that line so the ball never crosses it visually.
_MARGIN = 20
_WALL_T = float(_MARGIN + BALL_R)         # 32  — top/bottom/side bounce position (ball centre)
_WALL_B = float(FIELD_H - _MARGIN - BALL_R)  # 468
_WALL_L = float(_MARGIN + BALL_R)         # 32  — left-wall bounce (outside goal zone)
_WALL_R = float(FIELD_W - _MARGIN - BALL_R)  # 768 — right-wall bounce


def new_soccer_state(
    mode: str = "hvai",
    model_b: str = "greedy",
    model_a: str = "greedy",
) -> dict:
    return {
        "ball":         {"x": 400.0, "y": 250.0},
        "players_a":    [{"x": x, "y": y} for x, y in HOME_A],
        "players_b":    [{"x": x, "y": y} for x, y in HOME_B],
        "score_a":      0,
        "score_b":      0,
        "is_player_a":  True,
        "kick_count":   0,
        "max_kicks":    MAX_KICKS,
        "game_over":    False,
        "winner":       None,
        "move_history": [],
        "snapshots":    [],
        "game_mode":    mode,
        "model_name_a": model_a,
        "model_name_b": model_b,
        "_finalized":   False,
    }


def _seg_pt_dist(ax: float, ay: float, bx: float, by: float, px: float, py: float) -> float:
    """Closest distance from point (px,py) to segment (ax,ay)-(bx,by)."""
    dx, dy = bx - ax, by - ay
    len2 = dx*dx + dy*dy
    if len2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax)*dx + (py - ay)*dy) / len2))
    return math.hypot(ax + t*dx - px, ay + t*dy - py)


def _player_endpoint(
    px0: float, py0: float, fx: float, fy: float,
    blockers: list[dict],
) -> tuple[float, float, float, int]:
    """Move player from (px0,py0) toward (fx,fy) stopping at first player collision.

    Returns (stop_x, stop_y, t_stop, hit_idx) where hit_idx=-1 means no collision
    and t_stop is the fraction of the (px0→fx, py0→fy) segment actually travelled.
    """
    dx, dy = fx - px0, fy - py0
    min_t, hit_idx = 1.0, -1
    for i, bp in enumerate(blockers):
        ox, oy = float(bp["x"]), float(bp["y"])
        cur_dist = math.hypot(px0 - ox, py0 - oy)
        if cur_dist < _P2P:
            # Already overlapping — check if we are moving TOWARD them or AWAY.
            # Dot product of movement with (blocker - kicker) direction:
            # positive = moving toward blocker, negative = moving away.
            toward_x, toward_y = ox - px0, oy - py0
            dot = dx * toward_x + dy * toward_y
            if dot > 0:
                # Moving toward them while overlapping => instant collision at t=0
                if 0.0 < min_t:
                    min_t, hit_idx = 0.0, i
            # If moving away, let them separate — skip
            continue
        A = dx*dx + dy*dy
        if A < 1e-9:
            continue
        B = 2.0 * (dx*(px0 - ox) + dy*(py0 - oy))
        C = (px0 - ox)**2 + (py0 - oy)**2 - _P2P**2
        disc = B*B - 4.0*A*C
        if disc < 0:
            continue
        t_raw = (-B - math.sqrt(disc)) / (2.0*A)
        if t_raw >= -1e-6 and t_raw < min_t:   # >= catches adjacent/touching players (t=0)
            min_t, hit_idx = max(0.0, t_raw), i
    t_stop = max(0.0, min_t - (0.001 if hit_idx >= 0 else 0.0))
    return round(px0 + dx*t_stop, 1), round(py0 + dy*t_stop, 1), t_stop, hit_idx


def _simulate_ball(
    bx: float, by: float, bvx: float, bvy: float,
    blockers: list[dict] | None = None,
) -> tuple[list[dict], str | None]:
    """Simulate ball. blockers = opponents whose bodies the ball bounces off."""
    trajectory = [{"x": round(bx, 1), "y": round(by, 1)}]
    goal_scored: str | None = None

    for _ in range(600):
        prev_bx, prev_by = bx, by

        bx += bvx
        by += bvy
        bvx *= FRICTION
        bvy *= FRICTION

        # ── Top / bottom walls (always bounce at the white boundary line) ──────
        if by < _WALL_T:
            by  = _WALL_T
            bvy = abs(bvy) * _BOUNCE
        elif by > _WALL_B:
            by  = _WALL_B
            bvy = -abs(bvy) * _BOUNCE

        # ── Left wall ──────────────────────────────────────────────────────────
        if bx < _WALL_L:
            if GOAL_Y1 <= by <= GOAL_Y2:
                # Ball entered goal channel — check if it reached the back wall
                if bx - BALL_R <= 0:
                    bx = float(BALL_R)
                    goal_scored = "B"
                    trajectory.append({"x": round(bx, 1), "y": round(by, 1)})
                    break
                # else: ball is inside the goal tunnel, keep going
            else:
                # Outside goal zone: bounce at white line
                bx  = _WALL_L
                bvx = abs(bvx) * _BOUNCE

        # ── Right wall ─────────────────────────────────────────────────────────
        elif bx > _WALL_R:
            if GOAL_Y1 <= by <= GOAL_Y2:
                if bx + BALL_R >= FIELD_W:
                    bx = float(FIELD_W - BALL_R)
                    goal_scored = "A"
                    trajectory.append({"x": round(bx, 1), "y": round(by, 1)})
                    break
            else:
                bx  = _WALL_R
                bvx = -abs(bvx) * _BOUNCE

        # ── Player-body collisions (swept detection to stop tunnelling) ────────
        if blockers:
            for bp in blockers:
                prev_d = math.hypot(prev_bx - bp["x"], prev_by - bp["y"])
                if prev_d < _CONTACT:
                    continue  # already inside this step — skip re-bounce
                swept = _seg_pt_dist(prev_bx, prev_by, bx, by, bp["x"], bp["y"])
                if swept < _CONTACT:
                    # Reflect off the approach side
                    adx = prev_bx - bp["x"]
                    ady = prev_by - bp["y"]
                    ad  = math.hypot(adx, ady)
                    if ad < 0.01:
                        adx, ady, ad = _CONTACT, 0.0, _CONTACT
                    nx = adx / ad
                    ny = ady / ad
                    bx = bp["x"] + nx * _CONTACT
                    by = bp["y"] + ny * _CONTACT
                    dot = bvx*nx + bvy*ny
                    bvx -= 2*dot*nx*0.72
                    bvy -= 2*dot*ny*0.72

        trajectory.append({"x": round(bx, 1), "y": round(by, 1)})
        if abs(bvx) < 0.10 and abs(bvy) < 0.10:
            break

    return trajectory, goal_scored


def simulate_kick(
    state: dict,
    player_idx: int,
    angle_deg: float,
    power: float,
    is_player_a: bool,
) -> tuple[list[dict], str | None]:
    players = state["players_a"] if is_player_a else state["players_b"]
    player_idx = max(0, min(2, player_idx))
    p = players[player_idx]

    bx = float(state["ball"]["x"])
    by = float(state["ball"]["y"])
    opp = state["players_b"] if is_player_a else state["players_a"]

    angle_rad = math.radians(angle_deg)
    travel = power * _PLAYER_TRAVEL
    raw_fx = max(float(PLAYER_R), min(float(FIELD_W - PLAYER_R), p["x"] + math.cos(angle_rad) * travel))
    raw_fy = max(float(PLAYER_R), min(float(FIELD_H - PLAYER_R), p["y"] + math.sin(angle_rad) * travel))

    # Stop player at first player-player collision
    teammates = [q for q in players if q is not p]
    fx, fy, _, _ = _player_endpoint(p["x"], p["y"], raw_fx, raw_fy, teammates + list(opp))

    # Ball only moves if player's rocket path passes through the ball
    path_dist = _seg_pt_dist(p["x"], p["y"], fx, fy, bx, by)
    if path_dist > _CONTACT:
        return [{"x": round(bx, 1), "y": round(by, 1)}], None

    contact_quality = max(0.2, 1.0 - path_dist / _CONTACT)
    bvx = math.cos(angle_rad) * power * POWER_SCALE * contact_quality
    bvy = math.sin(angle_rad) * power * POWER_SCALE * contact_quality

    traj, goal = _simulate_ball(bx, by, bvx, bvy, opp)
    step = max(1, len(traj) // 100)
    return traj[::step] + [traj[-1]], goal


def apply_kick(
    state: dict,
    player_idx: int,
    angle_deg: float,
    power: float,
    is_player_a: bool,
) -> tuple[list[dict], str | None, str]:
    trajectory, scored = simulate_kick(state, player_idx, angle_deg, power, is_player_a)

    final = trajectory[-1]
    state["ball"]["x"] = final["x"]
    state["ball"]["y"] = final["y"]

    players = state["players_a"] if is_player_a else state["players_b"]
    player_idx = max(0, min(2, player_idx))
    player = players[player_idx]
    px0, py0 = player["x"], player["y"]
    angle_rad = math.radians(angle_deg)
    travel = power * _PLAYER_TRAVEL
    raw_fx = max(float(PLAYER_R), min(float(FIELD_W - PLAYER_R), px0 + math.cos(angle_rad) * travel))
    raw_fy = max(float(PLAYER_R), min(float(FIELD_H - PLAYER_R), py0 + math.sin(angle_rad) * travel))

    # Build a tagged list of all other players so we can identify who was hit
    all_others: list[tuple[str, int, dict]] = []
    for i, q in enumerate(state["players_a"]):
        if q is not player:
            all_others.append(("a", i, q))
    for i, q in enumerate(state["players_b"]):
        if q is not player:
            all_others.append(("b", i, q))
    blockers_list = [q for _, _, q in all_others]

    new_px, new_py, t_stop, hit_idx = _player_endpoint(px0, py0, raw_fx, raw_fy, blockers_list)
    player["x"] = new_px
    player["y"] = new_py
    kick_endpoint = {"x": new_px, "y": new_py}

    # Billiard-style push: hit player gets launched along the collision normal
    push_result = None
    if hit_idx >= 0:
        hit_team, hit_p_idx, bp = all_others[hit_idx]
        # Normal: from kicker stop pos → hit player centre
        nx, ny = bp["x"] - new_px, bp["y"] - new_py
        nd = math.hypot(nx, ny)
        if nd > 0.01:
            nx /= nd; ny /= nd
        else:
            nx, ny = math.cos(angle_rad), math.sin(angle_rad)
        # Push energy = remaining fraction of the intended travel
        seg_len = math.hypot(raw_fx - px0, raw_fy - py0)
        push_dist = max(_P2P * 1.5, (1.0 - t_stop) * seg_len * 0.75)
        push_tx = max(float(PLAYER_R), min(float(FIELD_W - PLAYER_R), bp["x"] + nx * push_dist))
        push_ty = max(float(PLAYER_R), min(float(FIELD_H - PLAYER_R), bp["y"] + ny * push_dist))
        # Secondary collision: pushed player may hit yet another player
        sec_blockers = [q for _, _, q in all_others if q is not bp] + [player]
        push_fx, push_fy, _, _ = _player_endpoint(bp["x"], bp["y"], push_tx, push_ty, sec_blockers)
        from_pos = {"x": bp["x"], "y": bp["y"]}
        bp["x"] = push_fx
        bp["y"] = push_fy
        push_result = {
            "team":       hit_team,
            "player_idx": hit_p_idx,
            "from":       from_pos,
            "to":         {"x": push_fx, "y": push_fy},
        }

    player_label = "A" if is_player_a else "B"
    ball_hit = len(trajectory) > 1
    miss_text = " (missed!)" if not ball_hit else ""
    scored_text = f" GOAL for {scored}!" if scored else ""
    desc = (
        f"Team {player_label} player {player_idx}: "
        f"angle={round(angle_deg)}{chr(176)} power={round(power)}{scored_text}{miss_text}"
    )

    if scored == "A":
        state["score_a"] += 1
        state["ball"] = {"x": 400.0, "y": 250.0}
        state["players_a"] = [{"x": float(x), "y": float(y)} for x, y in HOME_A]
        state["players_b"] = [{"x": float(x), "y": float(y)} for x, y in HOME_B]
    elif scored == "B":
        state["score_b"] += 1
        state["ball"] = {"x": 400.0, "y": 250.0}
        state["players_a"] = [{"x": float(x), "y": float(y)} for x, y in HOME_A]
        state["players_b"] = [{"x": float(x), "y": float(y)} for x, y in HOME_B]

    state["kick_count"] = state.get("kick_count", 0) + 1
    state["is_player_a"] = not is_player_a
    state["_finalized"] = False

    state["move_history"].append({
        "desc":       desc,
        "player":     player_label,
        "player_idx": player_idx,
        "angle":      round(angle_deg, 1),
        "power":      round(power, 1),
        "scored":     scored,
    })

    sa, sb = state["score_a"], state["score_b"]
    if sa >= GOALS_TO_WIN:
        state["game_over"] = True
        state["winner"] = "A"
    elif sb >= GOALS_TO_WIN:
        state["game_over"] = True
        state["winner"] = "B"
    elif state["kick_count"] >= state.get("max_kicks", MAX_KICKS):
        state["game_over"] = True
        state["winner"] = "A" if sa > sb else ("B" if sb > sa else "Draw")

    return trajectory, scored, desc, kick_endpoint, push_result
