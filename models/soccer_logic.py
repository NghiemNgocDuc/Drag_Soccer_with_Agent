"""soccer_logic.py — Soccer game physics engine (pymunk)."""
from __future__ import annotations
import math
import pymunk

FIELD_W: int   = 800
FIELD_H: int   = 500
BALL_R: int    = 12
PLAYER_R: int  = 20
GOAL_Y1: float  = 185.0
GOAL_Y2: float  = 315.0
POWER_SCALE: float = 0.55
GOALS_TO_WIN: int  = 5
MAX_KICKS: int     = 60

HOME_A: list[tuple[float, float]] = [(150.0, 165.0), (150.0, 250.0), (150.0, 335.0)]
HOME_B: list[tuple[float, float]] = [(650.0, 165.0), (650.0, 250.0), (650.0, 335.0)]

_MARGIN   = 20
_PLAYER_TRAVEL = 3.0
_CONTACT = float(PLAYER_R + BALL_R)
_P2P     = float(PLAYER_R * 2)

# ── Pymunk physics parameters ────────────────────────────────────────────────
_PM_DT        = 1.0 / 60.0
_PM_DAMPING   = 1.0
_PM_MAX_STEPS = 500
_PM_KICK_VEL  = 20.0      # px/s per unit of power (100 -> 2000)
_PM_MASS_P    = 5
_PM_MASS_B    = 1
_PM_ELASTICITY_P = 1.0
_PM_ELASTICITY_B = 1.0
_PM_ELASTICITY_W = 1.0
_PM_FRICTION  = 0.0

# Linear friction deceleration (px/s^2)
_PM_LINEAR_FRICTION_P = 800.0
_PM_LINEAR_FRICTION_B = 1000.0

# Collision categories (bit flags for pymunk ShapeFilter)
_CAT_PLAYER = 1
_CAT_BALL   = 2
_CAT_WALL   = 4
_CAT_GOAL_BARRIER = 8


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
    dx, dy = bx - ax, by - ay
    len2 = dx*dx + dy*dy
    if len2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax)*dx + (py - ay)*dy) / len2))
    return math.hypot(ax + t*dx - px, ay + t*dy - py)


def _build_space(state: dict):
    """Create a pymunk Space with field, players, and ball at current state positions."""
    space = pymunk.Space()
    space.damping = _PM_DAMPING
    static = space.static_body

    m = float(_MARGIN)
    fw, fh = float(FIELD_W), float(FIELD_H)
    gy1, gy2 = float(GOAL_Y1), float(GOAL_Y2)
    br = float(BALL_R)
    pw = 5.0

    wall_filter   = pymunk.ShapeFilter(categories=_CAT_WALL,   mask=_CAT_PLAYER | _CAT_BALL)
    goal_filter   = pymunk.ShapeFilter(categories=_CAT_GOAL_BARRIER, mask=_CAT_PLAYER)
    player_filter = pymunk.ShapeFilter(categories=_CAT_PLAYER, mask=_CAT_PLAYER | _CAT_BALL | _CAT_WALL | _CAT_GOAL_BARRIER)
    ball_filter   = pymunk.ShapeFilter(categories=_CAT_BALL,   mask=_CAT_PLAYER | _CAT_WALL)

    # Outer walls (top/bottom) — collide with players and ball
    outer_walls = [
        pymunk.Segment(static, (0, m), (fw, m), pw),                    # top
        pymunk.Segment(static, (0, fh - m), (fw, fh - m), pw),         # bottom
        pymunk.Segment(static, (m, m), (m, gy1 - br), pw),             # left upper
        pymunk.Segment(static, (m, gy2 + br), (m, fh - m), pw),       # left lower
        pymunk.Segment(static, (fw - m, m), (fw - m, gy1 - br), pw),  # right upper
        pymunk.Segment(static, (fw - m, gy2 + br), (fw - m, fh - m), pw), # right lower
    ]
    for w in outer_walls:
        w.elasticity = _PM_ELASTICITY_W
        w.friction = _PM_FRICTION
        w.filter = wall_filter
    space.add(*outer_walls)

    # Goal barriers — cover the goal mouth so players can't exit but ball passes through
    goal_barriers = [
        pymunk.Segment(static, (m, gy1), (m, gy2), pw),
        pymunk.Segment(static, (fw - m, gy1), (fw - m, gy2), pw),
    ]
    for gb in goal_barriers:
        gb.elasticity = _PM_ELASTICITY_W
        gb.friction = _PM_FRICTION
        gb.filter = goal_filter
    space.add(*goal_barriers)

    # Goal back walls — stop anything that enters the goal (collide with everything)
    back_walls = [
        pymunk.Segment(static, (0, gy1), (0, gy2), 3),
        pymunk.Segment(static, (fw, gy1), (fw, gy2), 3),
    ]
    for bw in back_walls:
        bw.elasticity = _PM_ELASTICITY_W
        bw.friction = _PM_FRICTION
        bw.filter = wall_filter
    space.add(*back_walls)

    def _make_player(x, y):
        body = pymunk.Body(_PM_MASS_P, pymunk.moment_for_circle(_PM_MASS_P, 0, PLAYER_R))
        body.position = (float(x), float(y))
        shape = pymunk.Circle(body, PLAYER_R)
        shape.elasticity = _PM_ELASTICITY_P
        shape.friction = _PM_FRICTION
        shape.filter = player_filter
        
        pivot = pymunk.PivotJoint(space.static_body, body, (0, 0), (0, 0))
        pivot.max_bias = 0
        pivot.max_force = _PM_MASS_P * _PM_LINEAR_FRICTION_P
        space.add(body, shape, pivot)
        return body

    bodies_a = [_make_player(p["x"], p["y"]) for p in state["players_a"]]
    bodies_b = [_make_player(p["x"], p["y"]) for p in state["players_b"]]

    ball_body = pymunk.Body(_PM_MASS_B, pymunk.moment_for_circle(_PM_MASS_B, 0, BALL_R))
    ball_body.position = (float(state["ball"]["x"]), float(state["ball"]["y"]))
    ball_shape = pymunk.Circle(ball_body, BALL_R)
    ball_shape.elasticity = _PM_ELASTICITY_B
    ball_shape.friction = _PM_FRICTION
    ball_shape.filter = ball_filter
    
    pivot_b = pymunk.PivotJoint(space.static_body, ball_body, (0, 0), (0, 0))
    pivot_b.max_bias = 0
    pivot_b.max_force = _PM_MASS_B * _PM_LINEAR_FRICTION_B
    space.add(ball_body, ball_shape, pivot_b)

    return space, bodies_a, bodies_b, ball_body


def _sim(space, bodies_a, bodies_b, ball_body, kicker_idx, is_player_a, max_steps=_PM_MAX_STEPS):
    """Run pymunk simulation and record results.

    Returns (ball_trajectory, scored, kicker_body).
    """
    kicker = (bodies_a if is_player_a else bodies_b)[kicker_idx]
    trajectory: list[dict] = []
    scored: str | None = None
    ball_moved = False

    for step_i in range(max_steps):
        # 3 substeps per logic frame to prevent tunneling at high velocities
        space.step(_PM_DT / 3.0)
        space.step(_PM_DT / 3.0)
        space.step(_PM_DT / 3.0)

        bx = ball_body.position.x
        by = ball_body.position.y
        trajectory.append({
            "x": round(bx, 1), 
            "y": round(by, 1),
            "a": [{"x": round(b.position.x, 1), "y": round(b.position.y, 1)} for b in bodies_a],
            "b": [{"x": round(b.position.x, 1), "y": round(b.position.y, 1)} for b in bodies_b]
        })

        # Goal detection — ball edge crosses the goal mouth (MARGIN from field edge)
        if GOAL_Y1 <= by <= GOAL_Y2:
            if bx - BALL_R <= _MARGIN:
                scored = "B"
                break
            if bx + BALL_R >= FIELD_W - _MARGIN:
                scored = "A"
                break

        # Early exit: all bodies have settled
        all_settled = True
        if abs(ball_body.velocity.x) >= 0.5 or abs(ball_body.velocity.y) >= 0.5:
            all_settled = False
        else:
            for b in bodies_a + bodies_b:
                if abs(b.velocity.x) >= 0.5 or abs(b.velocity.y) >= 0.5:
                    all_settled = False
                    break
        
        if all_settled:
            break

    return trajectory, scored, kicker


def simulate_kick(
    state: dict,
    player_idx: int,
    angle_deg: float,
    power: float,
    is_player_a: bool,
) -> tuple[list[dict], str | None]:
    player_idx = max(0, min(2, player_idx))
    space, bodies_a, bodies_b, ball_body = _build_space(state)

    kicker = (bodies_a if is_player_a else bodies_b)[player_idx]
    angle_rad = math.radians(angle_deg)
    kicker.velocity = (math.cos(angle_rad) * power * _PM_KICK_VEL,
                       math.sin(angle_rad) * power * _PM_KICK_VEL)

    trajectory, scored, _ = _sim(space, bodies_a, bodies_b, ball_body, player_idx, is_player_a)

    # If ball never moved, return single-point trajectory
    ball_moved = any(pt["x"] != trajectory[0]["x"] or pt["y"] != trajectory[0]["y"] for pt in trajectory)
    if not ball_moved:
        bx = float(state["ball"]["x"])
        by = float(state["ball"]["y"])
        return [{"x": round(bx, 1), "y": round(by, 1)}], None

    step = max(1, len(trajectory) // 100)
    return trajectory[::step] + [trajectory[-1]] if len(trajectory) > step else trajectory, scored


def apply_kick(
    state: dict,
    player_idx: int,
    angle_deg: float,
    power: float,
    is_player_a: bool,
) -> tuple[list[dict], str | None, str]:
    player_idx = max(0, min(2, player_idx))
    space, bodies_a, bodies_b, ball_body = _build_space(state)

    kicker = (bodies_a if is_player_a else bodies_b)[player_idx]
    angle_rad = math.radians(angle_deg)
    kicker.velocity = (math.cos(angle_rad) * power * _PM_KICK_VEL,
                       math.sin(angle_rad) * power * _PM_KICK_VEL)

    # Record starting positions for push detection
    start_pos_a = [{"x": p["x"], "y": p["y"]} for p in state["players_a"]]
    start_pos_b = [{"x": p["x"], "y": p["y"]} for p in state["players_b"]]

    trajectory, scored, _ = _sim(space, bodies_a, bodies_b, ball_body, player_idx, is_player_a)

    # ── Decimate trajectory ─────────────────────────────────────────────────
    if len(trajectory) > 1:
        step = max(1, len(trajectory) // 100)
        traj_out = trajectory[::step] + [trajectory[-1]]
    else:
        traj_out = trajectory

    # ── Update state ────────────────────────────────────────────────────────
    final = traj_out[-1]
    state["ball"]["x"] = final["x"]
    state["ball"]["y"] = final["y"]

    # Update player positions from pymunk bodies
    for i, body in enumerate(bodies_a):
        state["players_a"][i]["x"] = round(body.position.x, 1)
        state["players_a"][i]["y"] = round(body.position.y, 1)
    for i, body in enumerate(bodies_b):
        state["players_b"][i]["x"] = round(body.position.x, 1)
        state["players_b"][i]["y"] = round(body.position.y, 1)

    kick_endpoint = {"x": round(kicker.position.x, 1), "y": round(kicker.position.y, 1)}

    # ── Push result ─────────────────────────────────────────────────────────
    push_result = None  # Legacy, now handled completely via full trajectory syncing

    # ── Description ─────────────────────────────────────────────────────────
    player_label = "A" if is_player_a else "B"
    ball_hit = any(pt["x"] != trajectory[0]["x"] or pt["y"] != trajectory[0]["y"] for pt in trajectory)
    miss_text = " (missed!)" if not ball_hit else ""
    scored_text = f" GOAL for {scored}!" if scored else ""
    desc = (
        f"Team {player_label} player {player_idx}: "
        f"angle={round(angle_deg)}{chr(176)} power={round(power)}{scored_text}{miss_text}"
    )

    # ── Score handling ──────────────────────────────────────────────────────
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

    return traj_out, scored, desc, kick_endpoint, push_result
