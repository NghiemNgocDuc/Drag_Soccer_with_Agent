"""soccer_logic.py — Soccer game physics engine (pymunk)."""
from __future__ import annotations
import math
import time
import pymunk

FIELD_W: int   = 800
FIELD_H: int   = 500
BALL_R: int    = 12
PLAYER_R: int  = 20
GOAL_Y1: float  = 185.0
GOAL_Y2: float  = 315.0
POWER_SCALE: float = 0.55
GOALS_TO_WIN: int  = 5
_HALFTIME: int      = 135  # 45 min (3s per minute)
_REGULAR_END: int  = 270  # 90 min (3s per minute)
_ET_FIRST_END: int  = 315  # 90 + 15 min
_ET_SECOND_END: int = 360  # 90 + 15 + 15 min
_GOAL_DEPTH: float = 40.0

# Penalty shootout constants
_PENALTY_SPOT_X_A = 630.0   # Team A kicks toward right goal from here
_PENALTY_SPOT_X_B = 170.0   # Team B kicks toward left goal from here
_PENALTY_SPOT_Y   = 250.0
_PENALTY_KICKER_BEHIND = 45.0
_PENALTY_KEEPER_X_A = 755.0
_PENALTY_KEEPER_X_B = 45.0
_PENALTY_KEEPER_DIVE_VEL = 700.0
_PENALTY_KEEPER_DIVE_TARGETS = {"left": 195.0, "center": 250.0, "right": 305.0}
_PENALTY_MAX_KICKS = 10  # 5 each
PLAYER_COUNT: int = 3  # default, override via game state

REFEREE_POS: tuple[float, float] = (400.0, 420.0)

def _home_positions(count: int, side: str) -> list[tuple[float, float]]:
    """Generate `count` home positions for one side. Index 0 = GK."""
    if side == "a":
        gk_x, cx = 50.0, 400.0
    else:
        gk_x, cx = 750.0, 400.0
    positions = [(gk_x, 250.0)]
    if count < 2:
        return positions[:count]
    outfield = count - 1
    # Split into defensive (closer to own goal) and attacking (closer to center) rows
    n_def = max(1, outfield // 2)
    n_atk = outfield - n_def
    def_x = gk_x + (cx - gk_x) * 0.25
    atk_x = gk_x + (cx - gk_x) * 0.70
    y_range = min(360, 50 + outfield * 30)
    min_y = 250 - y_range / 2
    max_y = 250 + y_range / 2
    def_ys = [min_y + (i + 0.5) / n_def * y_range for i in range(n_def)]
    atk_ys = [min_y + (i + 0.5) / n_atk * y_range for i in range(n_atk)]
    for y in def_ys:
        positions.append((def_x, y))
    for y in atk_ys:
        positions.append((atk_x, y))
    return positions[:count]

HOME_A: list[tuple[float, float]] = _home_positions(PLAYER_COUNT, "a")
HOME_B: list[tuple[float, float]] = _home_positions(PLAYER_COUNT, "b")

def _reset_players(state: dict) -> None:
    cnt = state.get("player_count", PLAYER_COUNT)
    ha = _home_positions(cnt, "a")
    hb = _home_positions(cnt, "b")
    state["players_a"] = [{"x": float(x), "y": float(y)} for x, y in ha]
    state["players_b"] = [{"x": float(x), "y": float(y)} for x, y in hb]

def _reset_outfield(state: dict, side: str) -> None:
    """Reset all outfield players (index >= 1) for one side to home positions."""
    cnt = state.get("player_count", PLAYER_COUNT)
    ha = _home_positions(cnt, side)
    players = state["players_a"] if side == "a" else state["players_b"]
    for i in range(1, len(players)):
        if i < len(ha):
            players[i] = {"x": float(ha[i][0]), "y": float(ha[i][1])}

_MARGIN   = 20
_PLAYER_TRAVEL = 3.0
_CONTACT = float(PLAYER_R + BALL_R)
_P2P     = float(PLAYER_R * 2)

# ── Pymunk physics parameters ────────────────────────────────────────────────
_PM_DT        = 1.0 / 60.0
_PM_DAMPING   = 1.0
_PM_MAX_STEPS = 500
_PM_KICK_VEL  = 10.0      # px/s per unit of power (100 -> 1000)
_PM_MASS_P    = 5
_PM_MASS_B    = 1
_PM_ELASTICITY_P = 1.0
_PM_ELASTICITY_B = 1.0
_PM_ELASTICITY_W = 1.0
_PM_FRICTION  = 0.0

# Linear friction deceleration (px/s^2)
_PM_LINEAR_FRICTION_P = 1500.0
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
    player_count: int = 3,
) -> dict:
    home_a = _home_positions(player_count, "a")
    home_b = _home_positions(player_count, "b")
    return {
        "ball":         {"x": 400.0, "y": 250.0},
        "players_a":    [{"x": x, "y": y} for x, y in home_a],
        "players_b":    [{"x": x, "y": y} for x, y in home_b],
        "score_a":      0,
        "score_b":      0,
        "is_player_a":  True,
        "kick_count":   0,
        "start_time":   time.time(),
        "game_over":    False,
        "winner":       None,
        "move_history": [],
        "snapshots":    [],
        "game_mode":    mode,
        "model_name_a": model_a,
        "model_name_b": model_b,
        "first_kicker": "A",
        "period":       "regular_first",
        "player_count": player_count,
        "penalty_shootout": False,
        "penalty_kick_num": 0,
        "penalty_a_score": 0,
        "penalty_b_score": 0,
        "penalty_kicks": [],
        "penalty_goalkeeper_move": None,
        "referee":      {"x": REFEREE_POS[0], "y": REFEREE_POS[1]},
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
        pymunk.Segment(static, (m - _GOAL_DEPTH, gy1), (m - _GOAL_DEPTH, gy2), 3),
        pymunk.Segment(static, (m - _GOAL_DEPTH, gy1), (m, gy1), 3),
        pymunk.Segment(static, (m - _GOAL_DEPTH, gy2), (m, gy2), 3),
        pymunk.Segment(static, (fw - m + _GOAL_DEPTH, gy1), (fw - m + _GOAL_DEPTH, gy2), 3),
        pymunk.Segment(static, (fw - m, gy1), (fw - m + _GOAL_DEPTH, gy1), 3),
        pymunk.Segment(static, (fw - m, gy2), (fw - m + _GOAL_DEPTH, gy2), 3),
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

    # Referee — same physics as a player, never dragged by AI
    ref_pos = state.get("referee", {"x": REFEREE_POS[0], "y": REFEREE_POS[1]})
    ref_body = _make_player(ref_pos["x"], ref_pos["y"])

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

    return space, bodies_a, bodies_b, ball_body, ref_body


# ── Penalty shootout ──────────────────────────────────────────────────────────

def _setup_penalty_positions(state: dict, is_player_a: bool) -> None:
    """Place ball and players for a penalty kick."""
    if is_player_a:
        spot_x = _PENALTY_SPOT_X_A
        kicker_x = spot_x - _PENALTY_KICKER_BEHIND
        keeper_x = _PENALTY_KEEPER_X_A
    else:
        spot_x = _PENALTY_SPOT_X_B
        kicker_x = spot_x + _PENALTY_KICKER_BEHIND
        keeper_x = _PENALTY_KEEPER_X_B

    state["ball"] = {"x": spot_x, "y": _PENALTY_SPOT_Y}
    keeper_cy = _PENALTY_KEEPER_DIVE_TARGETS.get("center", _PENALTY_SPOT_Y)
    if is_player_a:
        state["players_a"][0] = {"x": kicker_x, "y": _PENALTY_SPOT_Y}
        _reset_outfield(state, "a")
        state["players_b"][0] = {"x": keeper_x, "y": keeper_cy}
        _reset_outfield(state, "b")
    else:
        state["players_b"][0] = {"x": kicker_x, "y": _PENALTY_SPOT_Y}
        _reset_outfield(state, "b")
        state["players_a"][0] = {"x": keeper_x, "y": keeper_cy}
        _reset_outfield(state, "a")
    state["penalty_goalkeeper_move"] = None


def _build_penalty_space(state: dict, is_player_a: bool):
    """Build a pymunk space for a penalty kick with ball, kicker, and keeper."""
    space = pymunk.Space()
    space.damping = _PM_DAMPING
    static = space.static_body

    m = float(_MARGIN)
    fw, fh = float(FIELD_W), float(FIELD_H)
    gy1, gy2 = float(GOAL_Y1), float(GOAL_Y2)
    pw = 5.0
    br = float(BALL_R)
    pr = float(PLAYER_R)

    wall_filter   = pymunk.ShapeFilter(categories=_CAT_WALL, mask=_CAT_PLAYER | _CAT_BALL)
    goal_filter   = pymunk.ShapeFilter(categories=_CAT_GOAL_BARRIER, mask=_CAT_PLAYER)
    player_filter = pymunk.ShapeFilter(categories=_CAT_PLAYER, mask=_CAT_PLAYER | _CAT_BALL | _CAT_WALL | _CAT_GOAL_BARRIER)
    ball_filter   = pymunk.ShapeFilter(categories=_CAT_BALL, mask=_CAT_PLAYER | _CAT_WALL)

    # Outer walls (simplified — just top/bottom/left/right)
    outer_walls = [
        pymunk.Segment(static, (0, m), (fw, m), pw),
        pymunk.Segment(static, (0, fh - m), (fw, fh - m), pw),
        pymunk.Segment(static, (m, m), (m, gy1 - br), pw),
        pymunk.Segment(static, (m, gy2 + br), (m, fh - m), pw),
        pymunk.Segment(static, (fw - m, m), (fw - m, gy1 - br), pw),
        pymunk.Segment(static, (fw - m, gy2 + br), (fw - m, fh - m), pw),
    ]
    for w in outer_walls:
        w.elasticity = _PM_ELASTICITY_W
        w.friction = _PM_FRICTION
        w.filter = wall_filter
    space.add(*outer_walls)

    # Goal barriers
    goal_barriers = [
        pymunk.Segment(static, (m, gy1), (m, gy2), pw),
        pymunk.Segment(static, (fw - m, gy1), (fw - m, gy2), pw),
    ]
    for gb in goal_barriers:
        gb.elasticity = _PM_ELASTICITY_W
        gb.friction = _PM_FRICTION
        gb.filter = goal_filter
    space.add(*goal_barriers)

    # Back walls
    back_walls = [
        pymunk.Segment(static, (m - _GOAL_DEPTH, gy1), (m - _GOAL_DEPTH, gy2), 3),
        pymunk.Segment(static, (m - _GOAL_DEPTH, gy1), (m, gy1), 3),
        pymunk.Segment(static, (m - _GOAL_DEPTH, gy2), (m, gy2), 3),
        pymunk.Segment(static, (fw - m + _GOAL_DEPTH, gy1), (fw - m + _GOAL_DEPTH, gy2), 3),
        pymunk.Segment(static, (fw - m, gy1), (fw - m + _GOAL_DEPTH, gy1), 3),
        pymunk.Segment(static, (fw - m, gy2), (fw - m + _GOAL_DEPTH, gy2), 3),
    ]
    for bw in back_walls:
        bw.elasticity = _PM_ELASTICITY_W
        bw.friction = _PM_FRICTION
        bw.filter = wall_filter
    space.add(*back_walls)

    def _make_player(x, y):
        body = pymunk.Body(_PM_MASS_P, pymunk.moment_for_circle(_PM_MASS_P, 0, pr))
        body.position = (float(x), float(y))
        shape = pymunk.Circle(body, pr)
        shape.elasticity = _PM_ELASTICITY_P
        shape.friction = _PM_FRICTION
        shape.filter = player_filter
        pivot = pymunk.PivotJoint(space.static_body, body, (0, 0), (0, 0))
        pivot.max_bias = 0
        pivot.max_force = _PM_MASS_P * _PM_LINEAR_FRICTION_P
        space.add(body, shape, pivot)
        return body

    if is_player_a:
        ball_x, ball_y = _PENALTY_SPOT_X_A, _PENALTY_SPOT_Y
        kicker_x = ball_x - _PENALTY_KICKER_BEHIND
        keeper_x = _PENALTY_KEEPER_X_A
        keeper_y = _PENALTY_SPOT_Y
        kicker_body = _make_player(kicker_x, _PENALTY_SPOT_Y)
        keeper_body = _make_player(keeper_x, keeper_y)
    else:
        ball_x, ball_y = _PENALTY_SPOT_X_B, _PENALTY_SPOT_Y
        kicker_x = ball_x + _PENALTY_KICKER_BEHIND
        keeper_x = _PENALTY_KEEPER_X_B
        keeper_y = _PENALTY_SPOT_Y
        kicker_body = _make_player(kicker_x, _PENALTY_SPOT_Y)
        keeper_body = _make_player(keeper_x, keeper_y)

    ball_body = pymunk.Body(_PM_MASS_B, pymunk.moment_for_circle(_PM_MASS_B, 0, br))
    ball_body.position = (ball_x, ball_y)
    ball_shape = pymunk.Circle(ball_body, br)
    ball_shape.elasticity = _PM_ELASTICITY_B
    ball_shape.friction = _PM_FRICTION
    ball_shape.filter = ball_filter
    pivot_b = pymunk.PivotJoint(space.static_body, ball_body, (0, 0), (0, 0))
    pivot_b.max_bias = 0
    pivot_b.max_force = _PM_MASS_B * _PM_LINEAR_FRICTION_B
    space.add(ball_body, ball_shape, pivot_b)

    return space, kicker_body, ball_body, keeper_body


def _sim_penalty(space, kicker_body, ball_body, keeper_body, keeper_dive_dir, max_steps=_PM_MAX_STEPS):
    """Run pymunk simulation for a penalty kick.

    Returns (trajectory, scored).
    """
    # Apply keeper dive velocity
    target_y = _PENALTY_KEEPER_DIVE_TARGETS.get(keeper_dive_dir, 250.0)
    dy = target_y - keeper_body.position.y
    keeper_body.velocity = (0.0, math.copysign(_PENALTY_KEEPER_DIVE_VEL, dy) if abs(dy) > 1 else 0.0)

    trajectory: list[dict] = []
    scored = False

    # Record initial state
    trajectory.append({
        "x": round(ball_body.position.x, 1),
        "y": round(ball_body.position.y, 1),
        "kicker": {"x": round(kicker_body.position.x, 1), "y": round(kicker_body.position.y, 1)},
        "keeper": {"x": round(keeper_body.position.x, 1), "y": round(keeper_body.position.y, 1)},
    })

    for step_i in range(max_steps):
        space.step(_PM_DT / 3.0)
        space.step(_PM_DT / 3.0)
        space.step(_PM_DT / 3.0)

        bx, by = ball_body.position.x, ball_body.position.y
        trajectory.append({
            "x": round(bx, 1),
            "y": round(by, 1),
            "kicker": {"x": round(kicker_body.position.x, 1), "y": round(kicker_body.position.y, 1)},
            "keeper": {"x": round(keeper_body.position.x, 1), "y": round(keeper_body.position.y, 1)},
        })

        # Goal detection — ball edge crosses the goal line
        if GOAL_Y1 <= by <= GOAL_Y2:
            if bx - BALL_R <= _MARGIN:
                scored = True
                break
            if bx + BALL_R >= FIELD_W - _MARGIN:
                scored = True
                break

        # Early exit when settled
        all_settled = True
        if abs(ball_body.velocity.x) >= 0.5 or abs(ball_body.velocity.y) >= 0.5:
            all_settled = False
        elif abs(kicker_body.velocity.x) >= 0.5 or abs(kicker_body.velocity.y) >= 0.5:
            all_settled = False
        elif abs(keeper_body.velocity.x) >= 0.5 or abs(keeper_body.velocity.y) >= 0.5:
            all_settled = False
        if all_settled:
            break

    return trajectory, scored


def apply_penalty_kick(
    state: dict,
    player_idx: int,
    angle_deg: float,
    power: float,
    is_player_a: bool,
) -> tuple[list[dict], bool, str]:
    """Execute a penalty kick.

    Returns (trajectory, scored, description).
    """
    player_idx = max(0, min(2, player_idx))
    keeper_move = state.get("penalty_goalkeeper_move") or "center"

    space, kicker_body, ball_body, keeper_body = _build_penalty_space(state, is_player_a)

    angle_rad = math.radians(angle_deg)
    kicker_body.velocity = (
        math.cos(angle_rad) * power * _PM_KICK_VEL,
        math.sin(angle_rad) * power * _PM_KICK_VEL,
    )

    trajectory, scored = _sim_penalty(space, kicker_body, ball_body, keeper_body, keeper_move)

    # Decimate trajectory
    step = max(1, len(trajectory) // 80)
    traj_out = trajectory[::step] + [trajectory[-1]] if len(trajectory) > step else trajectory

    # Update penalty state
    kick_num = state.get("penalty_kick_num", 0)
    state["penalty_kicks"].append({
        "team": "A" if is_player_a else "B",
        "kicker_idx": player_idx,
        "keeper_move": keeper_move,
        "goal": scored,
    })
    state["penalty_kick_num"] = kick_num + 1

    if scored:
        if is_player_a:
            state["penalty_a_score"] += 1
        else:
            state["penalty_b_score"] += 1

    pa, pb = state["penalty_a_score"], state["penalty_b_score"]
    team_label = "A" if is_player_a else "B"
    desc = f"Penalty {team_label}: {'GOAL' if scored else 'SAVED!'}"

    # Check if shootout is over
    if kick_num + 1 >= _PENALTY_MAX_KICKS:
        if pa != pb and (kick_num + 1) % 2 == 0:
            state["game_over"] = True
            state["winner"] = "A" if pa > pb else "B"
        elif pa == pb:
            # Sudden death — keep going
            pass

    # Setup next penalty (if not game over)
    if not state.get("game_over"):
        state["is_player_a"] = not is_player_a
        state["penalty_goalkeeper_move"] = None
        _setup_penalty_positions(state, not is_player_a)

    return traj_out, scored, desc


def _sim(space, bodies_a, bodies_b, ball_body, ref_body, kicker_idx, is_player_a, max_steps=_PM_MAX_STEPS):
    """Run pymunk simulation and record results.

    Returns (ball_trajectory, scored, kicker_body).
    """
    kicker = (bodies_a if is_player_a else bodies_b)[kicker_idx]
    trajectory: list[dict] = []
    scored: str | None = None
    ball_moved = False
    _was_near_wall = False

    # Record initial state before any physics step
    trajectory.append({
        "x": round(ball_body.position.x, 1),
        "y": round(ball_body.position.y, 1),
        "a": [{"x": round(b.position.x, 1), "y": round(b.position.y, 1)} for b in bodies_a],
        "b": [{"x": round(b.position.x, 1), "y": round(b.position.y, 1)} for b in bodies_b],
        "ref": {"x": round(ref_body.position.x, 1), "y": round(ref_body.position.y, 1)},
    })

    for step_i in range(max_steps):
        # 3 substeps per logic frame to prevent tunneling at high velocities
        space.step(_PM_DT / 3.0)
        space.step(_PM_DT / 3.0)
        space.step(_PM_DT / 3.0)

        bx = ball_body.position.x
        by = ball_body.position.y

        _near = (
            (by <= _MARGIN + BALL_R + 2) or
            (by >= FIELD_H - _MARGIN - BALL_R - 2) or
            (bx <= _MARGIN + BALL_R + 2 and not (GOAL_Y1 <= by <= GOAL_Y2)) or
            (bx >= FIELD_W - _MARGIN - BALL_R - 2 and not (GOAL_Y1 <= by <= GOAL_Y2))
        )
        _bounce = _near and not _was_near_wall
        _was_near_wall = _near

        trajectory.append({
            "x": round(bx, 1), 
            "y": round(by, 1),
            "a": [{"x": round(b.position.x, 1), "y": round(b.position.y, 1)} for b in bodies_a],
            "b": [{"x": round(b.position.x, 1), "y": round(b.position.y, 1)} for b in bodies_b],
            "ref": {"x": round(ref_body.position.x, 1), "y": round(ref_body.position.y, 1)},
        })
        if _bounce:
            trajectory[-1]["b"] = True

        # Goal detection — ball center crosses the goal line
        if GOAL_Y1 <= by <= GOAL_Y2:
            if bx <= _MARGIN:
                scored = "B"
                break
            if bx >= FIELD_W - _MARGIN:
                scored = "A"
                break

        # Early exit: all bodies have settled
        all_settled = True
        if abs(ball_body.velocity.x) >= 0.5 or abs(ball_body.velocity.y) >= 0.5:
            all_settled = False
        else:
            for b in bodies_a + bodies_b + [ref_body]:
                if abs(b.velocity.x) >= 0.5 or abs(b.velocity.y) >= 0.5:
                    all_settled = False
                    break
        
        if all_settled:
            break

    # Post-loop goal check: if ball settled inside the goal area, count it
    if scored is None:
        bx, by = ball_body.position.x, ball_body.position.y
        if GOAL_Y1 <= by <= GOAL_Y2:
            if bx <= _MARGIN:
                scored = "B"
            elif bx >= FIELD_W - _MARGIN:
                scored = "A"

    return trajectory, scored, kicker


def simulate_kick(
    state: dict,
    player_idx: int,
    angle_deg: float,
    power: float,
    is_player_a: bool,
) -> tuple[list[dict], str | None]:
    pc = state.get("player_count", 3)
    player_idx = max(0, min(pc - 1, player_idx))
    space, bodies_a, bodies_b, ball_body, ref_body = _build_space(state)

    kicker = (bodies_a if is_player_a else bodies_b)[player_idx]
    angle_rad = math.radians(angle_deg)
    kicker.velocity = (math.cos(angle_rad) * power * _PM_KICK_VEL,
                       math.sin(angle_rad) * power * _PM_KICK_VEL)

    trajectory, scored, _ = _sim(space, bodies_a, bodies_b, ball_body, ref_body, player_idx, is_player_a)

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
    pc = state.get("player_count", 3)
    player_idx = max(0, min(pc - 1, player_idx))
    space, bodies_a, bodies_b, ball_body, ref_body = _build_space(state)

    kicker = (bodies_a if is_player_a else bodies_b)[player_idx]
    angle_rad = math.radians(angle_deg)
    kicker.velocity = (math.cos(angle_rad) * power * _PM_KICK_VEL,
                       math.sin(angle_rad) * power * _PM_KICK_VEL)

    # Record starting positions for push detection
    start_pos_a = [{"x": p["x"], "y": p["y"]} for p in state["players_a"]]
    start_pos_b = [{"x": p["x"], "y": p["y"]} for p in state["players_b"]]

    trajectory, scored, _ = _sim(space, bodies_a, bodies_b, ball_body, ref_body, player_idx, is_player_a)

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
    # Update referee position from pymunk body
    state["referee"]["x"] = round(ref_body.position.x, 1)
    state["referee"]["y"] = round(ref_body.position.y, 1)

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
        _reset_players(state)
        state["referee"] = {"x": REFEREE_POS[0], "y": REFEREE_POS[1]}
    elif scored == "B":
        state["score_b"] += 1
        state["ball"] = {"x": 400.0, "y": 250.0}
        _reset_players(state)
        state["referee"] = {"x": REFEREE_POS[0], "y": REFEREE_POS[1]}

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
    elapsed = time.time() - state.get("start_time", time.time())
    period = state.get("period", "regular_first")

    if sa >= GOALS_TO_WIN:
        state["game_over"] = True
        state["winner"] = "A"
    elif sb >= GOALS_TO_WIN:
        state["game_over"] = True
        state["winner"] = "B"
    elif elapsed >= _ET_SECOND_END:
        if sa == sb:
            state["penalty_shootout"] = True
            state["period"] = "penalties"
            state["penalty_kick_num"] = 0
            state["penalty_a_score"] = 0
            state["penalty_b_score"] = 0
            state["penalty_kicks"] = []
            state["penalty_goalkeeper_move"] = None
            state["is_player_a"] = True
            # Place ball and players for first penalty
            _setup_penalty_positions(state, True)
        else:
            state["game_over"] = True
            state["winner"] = "A" if sa > sb else "B"
    elif elapsed >= _ET_FIRST_END and period == "et_first":
        state["period"] = "et_second"
        state["ball"] = {"x": 400.0, "y": 250.0}
        _reset_players(state)
        state["referee"] = {"x": REFEREE_POS[0], "y": REFEREE_POS[1]}
        state["is_player_a"] = not state["is_player_a"]
    elif elapsed >= _REGULAR_END and (period == "regular_first" or period == "regular_second"):
        if period == "regular_first":
            # Time jumped past halftime too — do halftime immediately
            state["period"] = "regular_second"
            state["ball"] = {"x": 400.0, "y": 250.0}
            _reset_players(state)
            state["referee"] = {"x": REFEREE_POS[0], "y": REFEREE_POS[1]}
            state["is_player_a"] = state["first_kicker"] != "A"
        # Now handle full-time / extra time
        if sa == sb:
            state["period"] = "et_first"
            state["ball"] = {"x": 400.0, "y": 250.0}
            _reset_players(state)
            state["referee"] = {"x": REFEREE_POS[0], "y": REFEREE_POS[1]}
            state["is_player_a"] = state["first_kicker"] != "A"
        else:
            state["game_over"] = True
            state["winner"] = "A" if sa > sb else "B"
    elif elapsed >= _HALFTIME and period == "regular_first":
        state["period"] = "regular_second"
        state["ball"] = {"x": 400.0, "y": 250.0}
        _reset_players(state)
        state["referee"] = {"x": REFEREE_POS[0], "y": REFEREE_POS[1]}
        state["is_player_a"] = state["first_kicker"] != "A"

    return traj_out, scored, desc, kick_endpoint, push_result
