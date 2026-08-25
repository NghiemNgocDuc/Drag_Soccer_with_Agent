"""soccer_logic.py — Soccer game physics engine (pymunk)."""
from __future__ import annotations
import math
import time
import pymunk

FIELD_W: int   = 1400
FIELD_H: int   = 875
BALL_R: int    = 12
PLAYER_R: int  = 20
# Goal centered on field, absolute aperture (was FIELD_H * 0.26 — frozen so goal size stays 231-394-equivalent on larger fields)
_GOAL_H: float = 163.0
GOAL_Y1: float  = (FIELD_H - _GOAL_H) / 2  # ~356
GOAL_Y2: float  = (FIELD_H + _GOAL_H) / 2  # ~519
POWER_SCALE: float = 0.55
# Default engine constants (can be overridden by state)
_HALF_DEFAULT   = 45  # minutes
_WIN_DEFAULT    = 5   # goals
# Derived time thresholds (3 seconds per game-minute)
def _time_th(hl: int) -> tuple:
    ht = hl * 3
    return (ht, ht * 2, ht * 2 + 45, ht * 2 + 90)  # halftime, fulltime, et1_end, et2_end
_GOAL_DEPTH: float = 50.0

# Penaly shootout constants — proportional to field
_PENALTY_SPOT_X_A  = FIELD_W * 0.79     # ~1106
_PENALTY_SPOT_X_B  = FIELD_W * 0.21     # ~294
_PENALTY_SPOT_Y    = FIELD_H * 0.5      # ~437.5
_PENALTY_KICKER_BEHIND = 60.0
_PENALTY_KEEPER_X_A    = FIELD_W * 0.95  # ~1330
_PENALTY_KEEPER_X_B    = FIELD_W * 0.05  # ~70
_PENALTY_KEEPER_DIVE_VEL = 700.0
_PENALTY_KEEPER_DIVE_TARGETS = {
    "left":   _PENALTY_SPOT_Y - FIELD_H * 0.11,  # ~341
    "center": _PENALTY_SPOT_Y,                    # ~437.5
    "right":  _PENALTY_SPOT_Y + FIELD_H * 0.11,   # ~534
}
_PENALTY_MAX_KICKS = 10  # 5 each
PLAYER_COUNT: int = 3  # default, override via game state

REFEREE_POS: tuple[float, float] = (FIELD_W / 2, FIELD_H - 80.0)  # (~700, ~795)

# ── 3D vertical-axis physics constants ────────────────────────────────────────
G: float = 980.0                 # px/s² gravity (approximates real 9.8 m/s² in pixel world)
_VERTICAL_RESTITUTION: float = 0.5  # bounce when ball lands
_VZ_MIN: float = 5.0                # below this, treat vertical velocity as settled

# ── Per-player stats ──────────────────────────────────────────────────────────
_STAT_MIN = 20
_STAT_MAX = 80
_STAT_DEFAULT = 50

def _stat_map_size(stat: int) -> float:
    return 12.0 + (max(0, min(100, stat)) / 100.0) * 16.0

def _stat_map_power(stat: int) -> float:
    return 5.0 + (max(0, min(100, stat)) / 100.0) * 10.0

def _stat_map_weight(stat: int) -> float:
    return 3.0 + (max(0, min(100, stat)) / 100.0) * 4.0

def _stat_map_agility(stat: int) -> float:
    return 1000.0 + (max(0, min(100, stat)) / 100.0) * 1000.0

DEFAULT_STATS = {"size": _STAT_DEFAULT, "power": _STAT_DEFAULT, "weight": _STAT_DEFAULT, "agility": _STAT_DEFAULT}

# ── Keeper PlayStyles (EA FC 25 — Footwork/Rush/Deflector/Cross Claimer/Far Reach/Far Throw) ─
try:
    from db.customization import KEEPER_STYLE_EFFECTS as _KEEPER_EFFECTS
except Exception:
    _KEEPER_EFFECTS = {"default": {}}

def _keeper_effect(state: dict, is_player_a: bool) -> dict:
    style = state.get("keeper_style_a" if is_player_a else "keeper_style_b", "default")
    return _KEEPER_EFFECTS.get(style, _KEEPER_EFFECTS.get("default", {})) or {}

def _keeper_radius_bonus(state: dict, is_player_a: bool) -> float:
    return float(_keeper_effect(state, is_player_a).get("radius_bonus", 0))

def _keeper_dive_mult(state: dict, is_player_a: bool) -> float:
    return float(_keeper_effect(state, is_player_a).get("dive_speed_mult", 1.0))

def _keeper_rush_mult(state: dict, is_player_a: bool) -> float:
    return float(_keeper_effect(state, is_player_a).get("rush_speed_mult", 1.0))

def _get_player_stats(state: dict, is_player_a: bool, idx: int) -> dict:
    players = state["players_a"] if is_player_a else state["players_b"]
    if idx < 0 or idx >= len(players):
        return dict(DEFAULT_STATS)
    p = players[idx]
    s = p.get("stats")
    if s is None:
        return dict(DEFAULT_STATS)
    return {
        "size": s.get("size", _STAT_DEFAULT),
        "power": s.get("power", _STAT_DEFAULT),
        "weight": s.get("weight", _STAT_DEFAULT),
        "agility": s.get("agility", _STAT_DEFAULT),
    }

def _get_player_radius(stats: dict) -> float:
    return _stat_map_size(stats["size"])

def _get_player_mass(stats: dict) -> float:
    return _stat_map_weight(stats["weight"])

def _get_player_kick_vel(stats: dict) -> float:
    return _stat_map_power(stats["power"])

def _get_player_friction(stats: dict) -> float:
    return _stat_map_agility(stats["agility"])


def inject_player_stats(state: dict, team_a_stats: list[dict] | None = None, team_b_stats: list[dict] | None = None) -> None:
    """Inject per-player stats into state player dicts.

    Called at match start by route handlers. Stats are stored per-player so
    _build_space and _get_player_stats can read them naturally.

    Args:
        team_a_stats: list of dicts with size/power/weight/agility keys, one per player.
                      If None or shorter than player_count, missing players get defaults.
        team_b_stats: same for team B.
    """
    cnt = len(state["players_a"])
    if team_a_stats:
        for i in range(min(cnt, len(team_a_stats))):
            s = team_a_stats[i]
            state["players_a"][i]["stats"] = {
                "size": max(0, min(100, s.get("size", _STAT_DEFAULT))),
                "power": max(0, min(100, s.get("power", _STAT_DEFAULT))),
                "weight": max(0, min(100, s.get("weight", _STAT_DEFAULT))),
                "agility": max(0, min(100, s.get("agility", _STAT_DEFAULT))),
            }
    cnt = len(state["players_b"])
    if team_b_stats:
        for i in range(min(cnt, len(team_b_stats))):
            s = team_b_stats[i]
            state["players_b"][i]["stats"] = {
                "size": max(0, min(100, s.get("size", _STAT_DEFAULT))),
                "power": max(0, min(100, s.get("power", _STAT_DEFAULT))),
                "weight": max(0, min(100, s.get("weight", _STAT_DEFAULT))),
                "agility": max(0, min(100, s.get("agility", _STAT_DEFAULT))),
            }

def _home_positions(count: int, side: str, formation: str | None = None) -> list[tuple[float, float]]:
    """Generate realistic soccer formation. Index 0 = GK.
    formation: for 7 players, e.g. "3-2-1" (DEF-MID-FWD). If provided, overrides default.
    """
    center_y = FIELD_H / 2
    # gk/def/mid are ratios of FIELD_W (auto-widen with the field);
    # atk is an ABSOLUTE 95px from center — the tuned kicker-to-ball gap
    # must stay 95px so Power=20 builds can reach the ball (95px max travel).
    if side == "a":
        gk_x = FIELD_W * 0.062  # ~87
        def_x = FIELD_W * 0.162  # ~227
        mid_x = FIELD_W * 0.281  # ~393
        atk_x = FIELD_W / 2 - 95  # ~605 (95px from ball at 700)
    else:
        gk_x = FIELD_W * 0.938  # ~1313
        def_x = FIELD_W * 0.838  # ~1173
        mid_x = FIELD_W * 0.719  # ~1007
        atk_x = FIELD_W / 2 + 95  # ~795 (95px from ball at 700)
    positions = [(gk_x, center_y)]
    if count < 2:
        return positions[:count]

    outfield = count - 1
    # Define formations as (defenders, midfielders, forwards) — must sum to outfield
    from db.managers import FORMATIONS_7, DEFAULT_FORMATION_7
    # Use manager default if no formation provided for 7
    if count == 7 and not formation:
        formation = DEFAULT_FORMATION_7
    if count == 7 and formation and formation in FORMATIONS_7:
        vals = FORMATIONS_7[formation]
        if len(vals) == 4:
            n_def, n_dm, n_am, n_atk = vals
            # need to handle 4 rows for diamond
            y_range = min(FIELD_H * 0.6, 80 + outfield * 25)
            min_y = center_y - y_range / 2
            def add_row(x_pos, n):
                if n <= 0: return
                for i in range(n):
                    y = min_y + (i + 0.5) / n * y_range
                    positions.append((x_pos, y))
            # x for 4 rows: DEF, DM, AM, FWD
            dm_x = (def_x + mid_x) / 2
            am_x = (mid_x + atk_x) / 2
            add_row(def_x, n_def)
            add_row(dm_x, n_dm)
            add_row(am_x, n_am)
            add_row(atk_x, n_atk)
            return positions[:count]
        else:
            n_def, n_mid, n_atk = vals
    else:
        formations = {
            1:  (0, 0, 1),
            2:  (1, 0, 1),
            3:  (1, 0, 2),
            4:  (2, 0, 2),
            5:  (2, 1, 2),
            6:  (3, 1, 2),
            7:  (3, 2, 2),
            8:  (4, 2, 2),
            9:  (4, 3, 2),
            10: (4, 3, 3),
            11: (4, 4, 2),
        }
        n_def, n_mid, n_atk = formations.get(count, (outfield, 0, 0))
    total = n_def + n_mid + n_atk
    if total > outfield:
        n_atk -= total - outfield
    elif total < outfield:
        n_atk += outfield - total

    y_range = min(FIELD_H * 0.6, 80 + outfield * 25)
    min_y = center_y - y_range / 2

    def add_row(x_pos, n):
        if n <= 0: return
        for i in range(n):
            y = min_y + (i + 0.5) / n * y_range
            positions.append((x_pos, y))

    add_row(def_x, n_def)
    add_row(mid_x, n_mid)
    add_row(atk_x, n_atk)
    return positions[:count]

HOME_A: list[tuple[float, float]] = _home_positions(PLAYER_COUNT, "a")
HOME_B: list[tuple[float, float]] = _home_positions(PLAYER_COUNT, "b")

def _reset_players(state: dict) -> None:
    cnt = state.get("player_count", PLAYER_COUNT)
    ha = _home_positions(cnt, "a", state.get("formation_a"))
    hb = _home_positions(cnt, "b", state.get("formation_b"))
    old_a = state.get("players_a", [])
    old_b = state.get("players_b", [])
    state["players_a"] = [
        {"x": float(x), "y": float(y), **({"stats": old_a[i]["stats"]} if i < len(old_a) and old_a[i].get("stats") else {})}
        for i, (x, y) in enumerate(ha)
    ]
    state["players_b"] = [
        {"x": float(x), "y": float(y), **({"stats": old_b[i]["stats"]} if i < len(old_b) and old_b[i].get("stats") else {})}
        for i, (x, y) in enumerate(hb)
    ]

def _reset_outfield(state: dict, side: str) -> None:
    """Reset all outfield players (index >= 1) for one side to home positions."""
    cnt = state.get("player_count", PLAYER_COUNT)
    formation = state.get(f"formation_{side}")
    ha = _home_positions(cnt, side, formation)
    players = state["players_a"] if side == "a" else state["players_b"]
    for i in range(1, len(players)):
        if i < len(ha):
            old_stats = players[i].get("stats") if i < len(players) else None
            entry = {"x": float(ha[i][0]), "y": float(ha[i][1])}
            if old_stats:
                entry["stats"] = old_stats
            players[i] = entry

_MARGIN   = 20
_PLAYER_TRAVEL = 3.0
_CONTACT = float(PLAYER_R + BALL_R)
_P2P     = float(PLAYER_R * 2)

# ── Referee (cosmetic) motion ────────────────────────────────────────────────
# The referee starts fixed at REFEREE_POS and uses the same collision body as a
# player, so it only moves when struck by the ball or another player.
_REF_WANDER_SPEED   = 75.0    # px/s ambient patrol speed (human jog)
_REF_DODGE_SPEED    = 300.0   # px/s evasion sidestep
_REF_PATH_TRIGGER   = 75.0    # dodge when the ball's path comes within this many px
_REF_NEAR           = 60.0    # dodge radially when the ball is this close and stopped
_REF_XMIN           = _MARGIN + 20.0
_REF_XMAX           = FIELD_W - _MARGIN - 20.0
_REF_YMIN           = _MARGIN + 15.0
_REF_YMAX           = FIELD_H - _MARGIN - 15.0
_REF_GOAL_SAFE_X    = 70.0    # near a goal mouth: keep out of the goal band
_REF_GOAL_SAFE_PAD  = 12.0    # px outside the goal band the ref keeps

# ── Pymunk physics parameters ────────────────────────────────────────────────
# Tuned from web research (pymunk/Chipmunk docs, SO constant-deceleration model,
# FIFA ball COR 0.6–0.8, Veryst FE restitution). Previous 1.0/1.0/1.0 was
# perfectly elastic → endless bouncing, jittery player collisions.
_PM_DT        = 1.0 / 60.0
_PM_DAMPING   = 1.0          # no global damping; friction via pivot joints
_PM_MAX_STEPS = 500
_PM_KICK_VEL  = 10.0      # px/s per unit of power (100 -> 1000)
_PM_MASS_P    = 5
_PM_MASS_B    = 1
# Restitution: Chipmunk multiplies the two shapes (perfectly elastic 1.0
# gives lively gameplay but extra bounce). Kept at 1.0 for stat-differentiation
# — balance_test expects this — solver damping + idle thresholds handle settling.
_PM_ELASTICITY_P = 1.0
_PM_ELASTICITY_B = 1.0
_PM_ELASTICITY_W = 1.0
_PM_FRICTION  = 0.0
# Solver: keep pymunk defaults (iterations 10, slop 0.1) for original balance
_PM_ITERATIONS = 10
_PM_COLLISION_SLOP = 0.1

# Linear friction deceleration (px/s^2) — constant-deceleration Coulomb model
# (SO: sliding halt is linear v(t), not exponential damping). Pivot max_force=m*fric.
_PM_LINEAR_FRICTION_P = 1500.0
_PM_LINEAR_FRICTION_B = 1000.0
_BALL_AIR_FRICTION = 100.0   # px/s² deceleration while airborne (10% of ground) → Path B fix

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
    half_length: int = _HALF_DEFAULT,
    win_goal_limit: int = _WIN_DEFAULT,
    power_cap: int = 100,
    formation_a: str | None = None,
    formation_b: str | None = None,
    referee_name: str | None = None,
) -> dict:
    # default formation from manager if not provided and count==7
    if player_count == 7:
        try:
            from db.managers import DEFAULT_FORMATION_7
            if not formation_a:
                formation_a = DEFAULT_FORMATION_7
            if not formation_b:
                formation_b = DEFAULT_FORMATION_7
        except: pass
    home_a = _home_positions(player_count, "a", formation_a)
    home_b = _home_positions(player_count, "b", formation_b)
    return {
        "ball":          {"x": FIELD_W / 2, "y": FIELD_H / 2, "z": 0.0},
        "field":         {"width": FIELD_W, "height": FIELD_H},
        "players_a":     [{"x": x, "y": y} for x, y in home_a],
        "players_b":     [{"x": x, "y": y} for x, y in home_b],
        "score_a":       0,
        "score_b":       0,
        "is_player_a":   True,
        "kick_count":    0,
        "start_time":    time.time(),
        "game_over":     False,
        "winner":        None,
        "move_history":  [],
        "snapshots":     [],
        "game_mode":     mode,
        "model_name_a":  model_a,
        "model_name_b":  model_b,
        "first_kicker":  "A",
        "period":        "regular_first",
        "player_count":  player_count,
        "half_length":   half_length,
        "win_goal_limit": win_goal_limit,
        "power_cap":     power_cap,
        "penalty_shootout": False,
        "penalty_kick_num": 0,
        "penalty_a_score": 0,
        "penalty_b_score": 0,
        "penalty_kicks": [],
        "penalty_goalkeeper_move": None,
        "keeper_style_a": "default",
        "keeper_style_b": "default",
        "formation_a": formation_a,
        "formation_b": formation_b,
        "referee":       {"x": REFEREE_POS[0], "y": REFEREE_POS[1]},
        "referee_name":  referee_name or __import__("db.managers", fromlist=["pick_referee"]).pick_referee().get("name", "Referee"),
        "_finalized":    False,
        "turn_start_time": time.time(),
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

    def _make_player(x, y, stats=None, radius_bonus=0.0, rush_mult=1.0):
        if stats is None:
            stats = DEFAULT_STATS
        r = _get_player_radius(stats) + float(radius_bonus)
        m = _get_player_mass(stats)
        fric = _get_player_friction(stats) * float(rush_mult)
        body = pymunk.Body(m, pymunk.moment_for_circle(m, 0, r))
        body.position = (float(x), float(y))
        shape = pymunk.Circle(body, r)
        shape.elasticity = _PM_ELASTICITY_P
        shape.friction = _PM_FRICTION
        shape.filter = player_filter
        
        pivot = pymunk.PivotJoint(space.static_body, body, (0, 0), (0, 0))
        pivot.max_bias = 0
        pivot.max_force = m * fric
        space.add(body, shape, pivot)
        return body

    # Keeper PlayStyle bonuses (keeper is idx 0)
    rb_a = _keeper_radius_bonus(state, True)
    rb_b = _keeper_radius_bonus(state, False)
    rush_a = _keeper_rush_mult(state, True)
    rush_b = _keeper_rush_mult(state, False)
    bodies_a = [_make_player(p["x"], p["y"], p.get("stats"),
                             radius_bonus=(rb_a if i == 0 else 0),
                             rush_mult=(rush_a if i == 0 else 1.0))
                for i, p in enumerate(state["players_a"])]
    bodies_b = [_make_player(p["x"], p["y"], p.get("stats"),
                             radius_bonus=(rb_b if i == 0 else 0),
                             rush_mult=(rush_b if i == 0 else 1.0))
                for i, p in enumerate(state["players_b"])]

    # Referee — cosmetic only: body WITHOUT a collision shape and no pivot,
    # so the ball and players pass straight through it (no physics presence).
    ref_pos = state.get("referee", {"x": REFEREE_POS[0], "y": REFEREE_POS[1]})
    ref_body = pymunk.Body(_PM_MASS_P, pymunk.moment_for_circle(_PM_MASS_P, 0, PLAYER_R))
    ref_body.position = (float(ref_pos["x"]), float(ref_pos["y"]))
    space.add(ref_body)

    ball_body = pymunk.Body(_PM_MASS_B, pymunk.moment_for_circle(_PM_MASS_B, 0, BALL_R))
    ball_body.position = (float(state["ball"]["x"]), float(state["ball"]["y"]))
    ball_shape = pymunk.Circle(ball_body, BALL_R)
    ball_shape.elasticity = _PM_ELASTICITY_B
    ball_shape.friction = _PM_FRICTION
    ball_shape.filter = ball_filter
    
    ball_pivot = pymunk.PivotJoint(space.static_body, ball_body, (0, 0), (0, 0))
    ball_pivot.max_bias = 0
    ball_pivot.max_force = _PM_MASS_B * _PM_LINEAR_FRICTION_B
    space.add(ball_body, ball_shape, ball_pivot)

    return space, bodies_a, bodies_b, ball_body, ref_body, ball_pivot


# ── Penalty shootout ──────────────────────────────────────────────────────────

def _setup_penalty_positions(state: dict, is_player_a: bool) -> None:
    """Place ball and players for a penalty kick.

    Outfield players (index > 0) stand in a single line at midfield.
    The kicker is near the ball, the keeper on the goal line.
    """
    if is_player_a:
        spot_x = _PENALTY_SPOT_X_A
        kicker_x = spot_x - _PENALTY_KICKER_BEHIND
        keeper_x = _PENALTY_KEEPER_X_A
    else:
        spot_x = _PENALTY_SPOT_X_B
        kicker_x = spot_x + _PENALTY_KICKER_BEHIND
        keeper_x = _PENALTY_KEEPER_X_B

    state["ball"] = {"x": spot_x, "y": _PENALTY_SPOT_Y, "z": 0.0}
    keeper_cy = _PENALTY_KEEPER_DIVE_TARGETS.get("center", _PENALTY_SPOT_Y)
    if is_player_a:
        state["players_a"][0] = {"x": kicker_x, "y": _PENALTY_SPOT_Y}
        state["players_b"][0] = {"x": keeper_x, "y": keeper_cy}
    else:
        state["players_b"][0] = {"x": kicker_x, "y": _PENALTY_SPOT_Y}
        state["players_a"][0] = {"x": keeper_x, "y": keeper_cy}

    # All outfield players (index > 0) stand in a line at midfield
    center_x = FIELD_W / 2
    total_outfield = 0
    for side in ("a", "b"):
        players = state["players_a"] if side == "a" else state["players_b"]
        total_outfield += max(0, len(players) - 1)
    y_spacing = FIELD_H / (total_outfield + 1) if total_outfield else FIELD_H / 2
    outfield_pos = 1
    for side, x_off in (("a", -12), ("b", 12)):
        players = state["players_a"] if side == "a" else state["players_b"]
        for i in range(1, len(players)):
            players[i] = {"x": center_x + x_off, "y": y_spacing * outfield_pos}
            outfield_pos += 1

    state["referee"] = {"x": -100, "y": -100}
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

    def _make_player(x, y, stats=None, radius_bonus=0.0, rush_mult=1.0):
        if stats is None:
            stats = DEFAULT_STATS
        r = _get_player_radius(stats) + float(radius_bonus)
        m = _get_player_mass(stats)
        fric = _get_player_friction(stats) * float(rush_mult)
        body = pymunk.Body(m, pymunk.moment_for_circle(m, 0, r))
        body.position = (float(x), float(y))
        shape = pymunk.Circle(body, r)
        shape.elasticity = _PM_ELASTICITY_P
        shape.friction = _PM_FRICTION
        shape.filter = player_filter
        pivot = pymunk.PivotJoint(space.static_body, body, (0, 0), (0, 0))
        pivot.max_bias = 0
        pivot.max_force = m * fric
        space.add(body, shape, pivot)
        return body

    # Keeper style bonuses for penalty (radius + rush)
    rb_kick_a = _keeper_radius_bonus(state, False) if is_player_a else _keeper_radius_bonus(state, True)
    rush_kick_a = _keeper_rush_mult(state, False) if is_player_a else _keeper_rush_mult(state, True)
    if is_player_a:
        ball_x, ball_y = _PENALTY_SPOT_X_A, _PENALTY_SPOT_Y
        kicker_x = ball_x - _PENALTY_KICKER_BEHIND
        keeper_x = _PENALTY_KEEPER_X_A
        keeper_y = _PENALTY_SPOT_Y
        kicker_stats = _get_player_stats(state, True, 0)
        keeper_stats = _get_player_stats(state, False, 0)
        kicker_body = _make_player(kicker_x, _PENALTY_SPOT_Y, kicker_stats)
        keeper_body = _make_player(keeper_x, keeper_y, keeper_stats, radius_bonus=rb_kick_a, rush_mult=rush_kick_a)
    else:
        ball_x, ball_y = _PENALTY_SPOT_X_B, _PENALTY_SPOT_Y
        kicker_x = ball_x + _PENALTY_KICKER_BEHIND
        keeper_x = _PENALTY_KEEPER_X_B
        keeper_y = _PENALTY_SPOT_Y
        kicker_stats = _get_player_stats(state, False, 0)
        keeper_stats = _get_player_stats(state, True, 0)
        kicker_body = _make_player(kicker_x, _PENALTY_SPOT_Y, kicker_stats)
        keeper_body = _make_player(keeper_x, keeper_y, keeper_stats, radius_bonus=rb_kick_a, rush_mult=rush_kick_a)

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


def _sim_penalty(space, kicker_body, ball_body, keeper_body, keeper_dive_dir, max_steps=_PM_MAX_STEPS, dive_mult=1.0):
    """Run pymunk simulation for a penalty kick.

    Returns (trajectory, scored).
    """
    # Apply keeper dive velocity — PlayStyle Far Reach / Rush Out / Footwork boost dive speed
    target_y = _PENALTY_KEEPER_DIVE_TARGETS.get(keeper_dive_dir, 250.0)
    dy = target_y - keeper_body.position.y
    vel = _PENALTY_KEEPER_DIVE_VEL * float(dive_mult)
    keeper_body.velocity = (0.0, math.copysign(vel, dy) if abs(dy) > 1 else 0.0)

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
    player_idx = max(0, min(len(state["players_a"]) - 1, player_idx))
    keeper_move = state.get("penalty_goalkeeper_move") or "center"

    space, kicker_body, ball_body, keeper_body = _build_penalty_space(state, is_player_a)

    stats = _get_player_stats(state, is_player_a, player_idx)
    kick_vel = _get_player_kick_vel(stats)
    angle_rad = math.radians(angle_deg)
    kicker_body.velocity = (
        math.cos(angle_rad) * power * kick_vel,
        math.sin(angle_rad) * power * kick_vel,
    )

    # Recoil for penalty kicks too
    _RECOIL_BASE = 1.2
    recoil_factor = _RECOIL_BASE * (stats["power"] / _STAT_DEFAULT)
    if recoil_factor > 0:
        kicker_body.velocity = (
            kicker_body.velocity.x - math.cos(angle_rad) * power * recoil_factor,
            kicker_body.velocity.y - math.sin(angle_rad) * power * recoil_factor,
        )

    # Keeper PlayStyle dive bonus (Far Reach / Footwork / Rush Out)
    keeper_is_a = not is_player_a
    keeper_eff = _keeper_effect(state, keeper_is_a)
    dive_mult = float(keeper_eff.get("dive_speed_mult", 1.0)) * float(keeper_eff.get("rush_speed_mult", 1.0))
    trajectory, scored = _sim_penalty(space, kicker_body, ball_body, keeper_body, keeper_move, dive_mult=dive_mult)

    # Deflector: safe deflection — if saved, damp ball and push to corner (less rebound)
    if not scored and keeper_eff.get("safe_deflect"):
        mult = float(keeper_eff.get("deflect_speed_mult", 0.6))
        # Damp the final ball position toward safe side (away from center)
        if trajectory:
            last = trajectory[-1]
            # pull ball toward nearest sideline corner
            safe_y = 95 if last["y"] < FIELD_H / 2 else FIELD_H - 95
            # nudge last point toward safe corner and damp
            last["x"] = round(last["x"] * mult + (FIELD_W/2) * (1-mult), 1)
            last["y"] = round(last["y"] * mult + safe_y * (1-mult), 1)

    # Far Throw: after save, keeper distribution would go far — extend final point
    if not scored and "throw_dist_mult" in keeper_eff:
        # Represented as extra push upfield after save (keeper throws to teammate)
        pass  # trajectory already reflects save; distribution handled in next kick setup

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


def _loft_angle(power: float) -> float:
    """Ball stays on ground — no flight.

    Web/physics research (ground rolling vs sliding) shows rolling friction
    is the correct model for a ball in contact with the pitch (constant
    deceleration, pivot-joint friction). Previous loft curve lifted the ball
    up to 30°; user request is ground-only movement, so launch angle is
    forced to 0° for all powers. G / _VERTICAL_RESTITUTION remain for
    completeness but are unused while vz0 == 0 → ball_z stays 0.
    """
    return 0.0


def _referee_step(ref_body, ball_body, dt: float) -> None:
    """Deterministic ambient referee motion: flow-field patrol + ball dodging.

    Purely cosmetic — the referee body has no collision shape, so its
    position only affects rendering (via trajectory ``ref`` frames and the
    game-state ``referee`` field). Movement is a deterministic function of
    (referee position, ball position/velocity), so AI lookahead via
    ``simulate_kick`` stays reproducible.
    """
    rx, ry = ref_body.position.x, ref_body.position.y
    bx, by = ball_body.position.x, ball_body.position.y
    bvx, bvy = ball_body.velocity.x, ball_body.velocity.y
    ball_speed = math.hypot(bvx, bvy)

    vx = vy = 0.0
    dodging = False

    # 1) Ball stopped very close: move directly away from it.
    dx, dy = rx - bx, ry - by
    dist_ball = math.hypot(dx, dy)
    if dist_ball > 1e-6 and dist_ball < _REF_NEAR and ball_speed < 1.0:
        vx, vy = dx / dist_ball * _REF_DODGE_SPEED, dy / dist_ball * _REF_DODGE_SPEED
        dodging = True

    # 2) Moving ball whose path will pass near the referee: sidestep
    #    perpendicular to the path, on the side that moves away from it.
    if not dodging and ball_speed > 1.0:
        cross = dx * bvy - dy * bvx  # signed distance * speed (2D cross)
        path_dist = abs(cross) / ball_speed
        if path_dist < _REF_PATH_TRIGGER:
            approaching = dx * bvx + dy * bvy > 0.0
            if approaching:
                nx, ny = -bvy / ball_speed, bvx / ball_speed
                if cross > 0.0:
                    vx, vy = -nx * _REF_DODGE_SPEED, -ny * _REF_DODGE_SPEED
                elif cross < 0.0:
                    vx, vy = nx * _REF_DODGE_SPEED, ny * _REF_DODGE_SPEED
                else:  # dead on the path: pick a deterministic side
                    vx, vy = (-nx * _REF_DODGE_SPEED, -ny * _REF_DODGE_SPEED) if ry > by else (nx * _REF_DODGE_SPEED, ny * _REF_DODGE_SPEED)
                dodging = True

    # 3) Ambient flow-field patrol (position-only, smooth, no RNG).
    if not dodging:
        fx = math.sin(ry * 0.02)
        fy = math.cos(rx * 0.02)
        fn = math.hypot(fx, fy) or 1.0
        vx, vy = fx / fn * _REF_WANDER_SPEED, fy / fn * _REF_WANDER_SPEED

    # Integrate, reflecting off the bounds so the flow can't pin the ref
    # against a wall, then clamp.
    nx, ny = rx + vx * dt, ry + vy * dt
    if nx <= _REF_XMIN and vx < 0.0: vx = -vx
    elif nx >= _REF_XMAX and vx > 0.0: vx = -vx
    if ny <= _REF_YMIN and vy < 0.0: vy = -vy
    elif ny >= _REF_YMAX and vy > 0.0: vy = -vy
    nx, ny = rx + vx * dt, ry + vy * dt
    nx = max(_REF_XMIN, min(_REF_XMAX, nx))
    ny = max(_REF_YMIN, min(_REF_YMAX, ny))

    # Keep out of the goal-mouth band when hugging the goal line.
    if (nx < _REF_GOAL_SAFE_X or nx > FIELD_W - _REF_GOAL_SAFE_X) and GOAL_Y1 <= ny <= GOAL_Y2:
        ny = GOAL_Y1 - _REF_GOAL_SAFE_PAD if ny < (GOAL_Y1 + GOAL_Y2) / 2 else GOAL_Y2 + _REF_GOAL_SAFE_PAD

    ref_body.position = (round(nx, 1), round(ny, 1))


def _sim(space, bodies_a, bodies_b, ball_body, ref_body, kicker_idx, is_player_a, max_steps=_PM_MAX_STEPS, vz0=0.0, ball_pivot=None):
    """Run pymunk simulation and record results.

    Args:
        vz0: initial vertical (z-axis) velocity after kick, px/s.
             If non-zero, enables 3D ball-flight tracking alongside 2D physics.

    Returns (ball_trajectory, scored, kicker_body).
    """
    kicker = (bodies_a if is_player_a else bodies_b)[kicker_idx]
    trajectory: list[dict] = []
    scored: str | None = None
    ball_moved = False
    _was_near_wall = False
    ball_z = 0.0
    ball_vz = vz0

    # Record initial state before any physics step
    trajectory.append({
        "x": round(ball_body.position.x, 1),
        "y": round(ball_body.position.y, 1),
        "z": round(ball_z, 1),
        "a": [{"x": round(b.position.x, 1), "y": round(b.position.y, 1)} for b in bodies_a],
        "b": [{"x": round(b.position.x, 1), "y": round(b.position.y, 1)} for b in bodies_b],
        "ref": {"x": round(ref_body.position.x, 1), "y": round(ref_body.position.y, 1)},
    })

    for step_i in range(max_steps):
        for _ in range(3):
            space.step(_PM_DT / 3.0)
            # Vertical-axis ball flight (decoupled from pymunk 2D physics)
            if ball_z > 0.0 or ball_vz != 0.0:
                ball_vz -= G * (_PM_DT / 3.0)
                ball_z += ball_vz * (_PM_DT / 3.0)
                if ball_z <= 0.0:
                    ball_z = 0.0
                    if abs(ball_vz) > _VZ_MIN:
                        ball_vz *= -_VERTICAL_RESTITUTION
                    else:
                        ball_vz = 0.0
            # Reduce ground friction while ball is airborne
            if ball_pivot is not None:
                if ball_z > 0.0:
                    ball_pivot.max_force = _PM_MASS_B * _BALL_AIR_FRICTION
                else:
                    ball_pivot.max_force = _PM_MASS_B * _PM_LINEAR_FRICTION_B

        # Ambient referee motion (wander + dodge) — cosmetic only
        _referee_step(ref_body, ball_body, _PM_DT)

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
            "z": round(ball_z, 1),
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
    space, bodies_a, bodies_b, ball_body, ref_body, ball_pivot = _build_space(state)

    kicker = (bodies_a if is_player_a else bodies_b)[player_idx]
    stats = _get_player_stats(state, is_player_a, player_idx)
    kick_vel = _get_player_kick_vel(stats)
    angle_rad = math.radians(angle_deg)
    kicker.velocity = (math.cos(angle_rad) * power * kick_vel,
                       math.sin(angle_rad) * power * kick_vel)

    loft_deg = _loft_angle(power)
    vz0 = math.sin(math.radians(loft_deg)) * power * kick_vel

    trajectory, scored, _ = _sim(space, bodies_a, bodies_b, ball_body, ref_body, player_idx, is_player_a, vz0=vz0, ball_pivot=ball_pivot)

    # If ball never moved, return single-point trajectory
    ball_moved = any(pt["x"] != trajectory[0]["x"] or pt["y"] != trajectory[0]["y"] for pt in trajectory)
    if not ball_moved:
        bx = float(state["ball"]["x"])
        by = float(state["ball"]["y"])
        return [{"x": round(bx, 1), "y": round(by, 1), "z": 0.0}], None

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
    space, bodies_a, bodies_b, ball_body, ref_body, ball_pivot = _build_space(state)

    kicker = (bodies_a if is_player_a else bodies_b)[player_idx]
    stats = _get_player_stats(state, is_player_a, player_idx)
    kick_vel = _get_player_kick_vel(stats)
    angle_rad = math.radians(angle_deg)
    kicker.velocity = (math.cos(angle_rad) * power * kick_vel,
                       math.sin(angle_rad) * power * kick_vel)

    loft_deg = _loft_angle(power)
    vz0 = math.sin(math.radians(loft_deg)) * power * kick_vel

    # Recoil: higher Power = kicker pushed back more, proportional to stat above default
    _RECOIL_BASE = 1.2  # Base recoil multiplier at Power-50; 1.2 means ~120% of power contributes to recoil
    recoil_factor = _RECOIL_BASE * (stats["power"] / _STAT_DEFAULT)
    recoil_vx = -math.cos(angle_rad) * power * recoil_factor
    recoil_vy = -math.sin(angle_rad) * power * recoil_factor
    # Add recoil on top of existing kick velocity (opposite direction)
    if recoil_factor > 0:
        kicker.velocity = (kicker.velocity.x + recoil_vx, kicker.velocity.y + recoil_vy)

    # Record starting positions for push detection
    start_pos_a = [{"x": p["x"], "y": p["y"]} for p in state["players_a"]]
    start_pos_b = [{"x": p["x"], "y": p["y"]} for p in state["players_b"]]

    trajectory, scored, _ = _sim(space, bodies_a, bodies_b, ball_body, ref_body, player_idx, is_player_a, vz0=vz0, ball_pivot=ball_pivot)

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
    state["ball"]["z"] = final.get("z", 0.0)

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
        state["ball"] = {"x": FIELD_W / 2, "y": FIELD_H / 2, "z": 0.0}
        _reset_players(state)
        state["referee"] = {"x": REFEREE_POS[0], "y": REFEREE_POS[1]}
    elif scored == "B":
        state["score_b"] += 1
        state["ball"] = {"x": FIELD_W / 2, "y": FIELD_H / 2, "z": 0.0}
        _reset_players(state)
        state["referee"] = {"x": REFEREE_POS[0], "y": REFEREE_POS[1]}

    state["kick_count"] = state.get("kick_count", 0) + 1
    state["is_player_a"] = not is_player_a
    state["turn_start_time"] = time.time()
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
    hl = state.get("half_length", _HALF_DEFAULT)
    gw = state.get("win_goal_limit", _WIN_DEFAULT)
    ht, ft, et1, et2 = _time_th(hl)

    if sa >= gw:
        state["game_over"] = True
        state["winner"] = "A"
    elif sb >= gw:
        state["game_over"] = True
        state["winner"] = "B"
    elif elapsed >= et2:
        if sa == sb:
            state["penalty_shootout"] = True
            state["period"] = "penalties"
            state["penalty_kick_num"] = 0
            state["penalty_a_score"] = 0
            state["penalty_b_score"] = 0
            state["penalty_kicks"] = []
            state["is_player_a"] = True
            _setup_penalty_positions(state, True)
        else:
            state["game_over"] = True
            state["winner"] = "A" if sa > sb else "B"
    elif elapsed >= et1 and period == "et_first":
        state["period"] = "et_second"
        state["ball"] = {"x": FIELD_W / 2, "y": FIELD_H / 2, "z": 0.0}
        _reset_players(state)
        state["referee"] = {"x": REFEREE_POS[0], "y": REFEREE_POS[1]}
        state["is_player_a"] = not state["is_player_a"]
    elif elapsed >= ft and (period == "regular_first" or period == "regular_second"):
        if period == "regular_first":
            state["period"] = "regular_second"
            state["ball"] = {"x": FIELD_W / 2, "y": FIELD_H / 2, "z": 0.0}
            _reset_players(state)
            state["referee"] = {"x": REFEREE_POS[0], "y": REFEREE_POS[1]}
            state["is_player_a"] = state["first_kicker"] != "A"
        if sa == sb:
            state["period"] = "et_first"
            state["ball"] = {"x": FIELD_W / 2, "y": FIELD_H / 2, "z": 0.0}
            _reset_players(state)
            state["referee"] = {"x": REFEREE_POS[0], "y": REFEREE_POS[1]}
            state["is_player_a"] = state["first_kicker"] != "A"
        else:
            state["game_over"] = True
            state["winner"] = "A" if sa > sb else "B"
    elif elapsed >= ht and period == "regular_first":
        state["period"] = "regular_second"
        state["ball"] = {"x": FIELD_W / 2, "y": FIELD_H / 2, "z": 0.0}
        _reset_players(state)
        state["referee"] = {"x": REFEREE_POS[0], "y": REFEREE_POS[1]}
        state["is_player_a"] = state["first_kicker"] != "A"

    return traj_out, scored, desc, kick_endpoint, push_result
