"""Soccer physics with fixed substeps and timestamped replay trajectories."""
from __future__ import annotations
import math
import time
import pymunk
from models.search_budget import current_search


def _player_positions(bodies, cache=None):
    # Each position property crosses the Python/C boundary. Read it once.
    positions = []
    for body in bodies:
        cached = cache.get(body) if cache is not None else None
        if cached is not None and body.is_sleeping:
            positions.append(cached.copy())
            continue
        pos = body.position
        point = {"x": round(pos.x, 1), "y": round(pos.y, 1)}
        if cache is not None:
            cache[body] = point.copy()
        positions.append(point)
    return positions


def _sample_trajectory(trajectory, target=100):
    """Thin a replay without losing impacts or duplicating the final frame."""
    stride = max(1, (len(trajectory) - 1 + target - 1) // target)
    indices = set(range(0, len(trajectory), stride))
    indices.add(len(trajectory) - 1)
    for i, point in enumerate(trajectory):
        if point.get("bounce") or point.get("contact") or (i and trajectory[i - 1].get("z", 0) > 0
                                   and point.get("z", 0) == 0):
            indices.update((i - 1, i))
    return [trajectory[i] for i in sorted(indices) if i >= 0]

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
PLAYER_COUNT: int = 5

REFEREE_POS: tuple[float, float] = (FIELD_W / 2, FIELD_H - 80.0)  # (~700, ~795)

#  3D vertical-axis physics constants 
G: float = 980.0                 # game-world gravity for optional vertical simulation
_VERTICAL_RESTITUTION: float = 0.5  # bounce when ball lands
_VZ_MIN: float = 5.0                # below this, treat vertical velocity as settled

#  Per-player stats 
_STAT_MIN = 20
_STAT_MAX = 80
_STAT_DEFAULT = 50

def _stat_map_size(stat: int) -> float:
    return 12.0 + (max(0, min(100, stat)) / 100.0) * 16.0

def _stat_map_power(stat: int) -> float:
    return 5.0 + (max(0, min(100, stat)) / 100.0) * 4.0

def _stat_map_weight(stat: int) -> float:
    return 3.0 + (max(0, min(100, stat)) / 100.0) * 4.0

def _stat_map_agility(stat: int) -> float:
    return 700.0 + (max(0, min(100, stat)) / 100.0) * 400.0

DEFAULT_STATS = {"size": _STAT_DEFAULT, "power": _STAT_DEFAULT, "weight": _STAT_DEFAULT, "agility": _STAT_DEFAULT}

#  Keeper PlayStyles (EA FC 25 — Footwork/Rush/Deflector/Cross Claimer/Far Reach/Far Throw) 
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
    # Discs glide after release; cap free travel while preserving pull strength.
    return min(_stat_map_power(stats["power"]),
               math.sqrt(2.0 * _get_player_friction(stats) * 480.0) / 100.0)


def human_player_index(state: dict, is_player_a: bool = True) -> int:
    """The last formation slot is the striker; shootouts use the placed kicker."""
    if state.get("penalty_shootout"):
        return 0
    return max(0, len(state["players_a" if is_player_a else "players_b"]) - 1)

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
        {**(old_a[i] if i < len(old_a) else {}), "x": float(x), "y": float(y)}
        for i, (x, y) in enumerate(ha)
    ]
    state["players_b"] = [
        {**(old_b[i] if i < len(old_b) else {}), "x": float(x), "y": float(y)}
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
            players[i] = {**players[i], "x": float(ha[i][0]), "y": float(ha[i][1])}

_MARGIN   = 20
_PLAYER_TRAVEL = 3.0
_CONTACT = float(PLAYER_R + BALL_R)
_P2P     = float(PLAYER_R * 2)

#  Referee (cosmetic) motion 
# The referee has no collision shape; its deterministic movement is cosmetic.
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

#  Pymunk physics parameters — "ping-pong" tuning
# Crisp rebounds with a heavier ball and enough rolling resistance to make
# shorter passes easier to control. Player launch and glide are independent.
_PM_DT        = 1.0 / 60.0
_PM_DAMPING   = 1.0          # no global damping; friction via pivot joints
_PM_MAX_STEPS = 500
_PM_KICK_VEL  = 10.0      # px/s per unit of power
_PM_MASS_P    = 5
_PM_MASS_B    = 1.5        # heavier ball takes less speed from the same disc contact
# Crisp, tactile table-tennis rebounds with gentle natural decay.
_PM_ELASTICITY_P = 0.90    # player-ball: crisp paddle deflection
_PM_ELASTICITY_B = 0.96    # ball shape default restitution
_PM_ELASTICITY_W = 0.35    # soft boundary: absorb most of the incoming rebound speed
_PM_FRICTION  = 0.0
# Explicit solver settings keep prediction and match simulation identical.
_PM_ITERATIONS = 10
_PM_COLLISION_SLOP = 0.1

# Smooth table glide with controlled strength:
# Glides effortlessly across the pitch without infinite ricochets.
_PM_LINEAR_FRICTION_P = 900.0
_PM_LINEAR_FRICTION_B = 250.0   # shorter rolls with smooth, steady deceleration
_BALL_AIR_FRICTION = 22.0       # light aerodynamic drag

# Keep contact speed bounded without injecting extra energy into rebounds.
_RALLY_SPEED_BOOST  = 1.0      # contacts transfer momentum without an artificial boost
_RALLY_SPEED_CAP    = 850.0    # controlled ceiling so volleys remain readable and tactical

# Rocket League mutators — multipliers applied via customization (ball_type etc.)
_MUTATOR_BALL_TYPE = {
    "normal": {"bounciness": 1.0, "mass": 1.0, "friction": 1.0},
    "beach":  {"bounciness": 1.6, "mass": 0.7, "friction": 0.7},
    "boomer": {"bounciness": 1.4, "mass": 0.6, "friction": 0.6},
    "cube":   {"bounciness": 1.2, "mass": 1.3, "friction": 1.0},
    "puck":   {"bounciness": 0.4, "mass": 0.9, "friction": 0.3},
}
_MUTATOR_BOUNCINESS = {"normal": 1.0, "high": 1.4, "super_high": 1.9}
_BALL_SIZE = {"small": 0.82, "normal": 1.0, "large": 1.18}

# Collision categories (bit flags for pymunk ShapeFilter)
_CAT_PLAYER = 1
_CAT_BALL   = 2
_CAT_WALL   = 4
_CAT_GOAL_BARRIER = 8


def new_soccer_state(
    mode: str = "hvai",
    model_b: str = "greedy",
    model_a: str = "greedy",
    player_count: int = PLAYER_COUNT,
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


def normalize_kick(state, player_idx, angle_deg, power, is_player_a):
    """Validate before mutation and use one command contract for every caller."""
    try:
        angle, strength, index = float(angle_deg), float(power), float(player_idx)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Kick index, angle and power must be finite numbers") from exc
    if not all(math.isfinite(value) for value in (angle, strength, index)):
        raise ValueError("Kick index, angle and power must be finite numbers")
    players = state["players_a" if is_player_a else "players_b"]
    if not players:
        raise ValueError("The kicking team has no players")
    cap = float(state.get("power_cap", 100))
    if not math.isfinite(cap):
        raise ValueError("Power cap must be finite")
    return (max(0, min(len(players) - 1, int(index))), angle % 360.0,
            max(0.0, min(100.0, cap, strength)))


def _ball_physics(state):
    """A shared material for regular play and shootouts; unknown presets default."""
    material = _MUTATOR_BALL_TYPE.get(state.get("ball_type"), _MUTATOR_BALL_TYPE["normal"])
    bounce = _MUTATOR_BOUNCINESS.get(state.get("ball_bounciness"), 1.0)
    return {
        "radius": BALL_R * _BALL_SIZE.get(state.get("ball_size"), 1.0),
        "size": state.get("ball_size") if state.get("ball_size") in _BALL_SIZE else "normal",
        "mass": _PM_MASS_B * material["mass"],
        "restitution": min(0.96, _PM_ELASTICITY_B * material["bounciness"] * bounce),
        "rolling_deceleration": _PM_LINEAR_FRICTION_B * material["friction"],
        "air_deceleration": _BALL_AIR_FRICTION * material["friction"],
    }


def _goal_for_ball(position, radius, z=0.0):
    """IFAB whole-ball rule, including the aperture and the rendered crossbar.

    z is the bottom of the ball above the pitch; the goal is 60 world units high.
    """
    x, y = position
    if not (GOAL_Y1 + radius <= y <= GOAL_Y2 - radius):
        return None
    if z < 0 or z + 2 * radius > _GOAL_DEPTH * 1.2:
        return None
    if x + radius < _MARGIN:
        return "B"
    if x - radius > FIELD_W - _MARGIN:
        return "A"
    return None


def _on_collision(space, a, b, **callbacks):
    # The project supports Pymunk 6.5 as well as the 7.x collision API.
    if hasattr(space, "on_collision"):
        space.on_collision(a, b, **callbacks)
    else:
        handler = space.add_collision_handler(a, b)
        for name, callback in callbacks.items():
            setattr(handler, name, callback)


def _new_physics_space(state):
    """Build field contacts once, shared by both match and penalty simulation."""
    space = pymunk.Space()
    space.damping = _PM_DAMPING
    space.iterations = _PM_ITERATIONS
    space.collision_slop = _PM_COLLISION_SLOP
    # Resting players leave the solver until a real contact or impulse wakes them.
    space.sleep_time_threshold = 0.1
    space.idle_speed_threshold = 0.5
    space._soccer_ball = _ball_physics(state)
    space._soccer_events = {"bounce": False, "contact": False}
    static = space.static_body
    m, fw, fh = float(_MARGIN), float(FIELD_W), float(FIELD_H)
    gy1, gy2 = float(GOAL_Y1), float(GOAL_Y2)
    wall_filter = pymunk.ShapeFilter(categories=_CAT_WALL, mask=_CAT_PLAYER | _CAT_BALL)
    goal_filter = pymunk.ShapeFilter(categories=_CAT_GOAL_BARRIER, mask=_CAT_PLAYER)
    # Segment end caps are the physical posts. Do not enlarge the aperture by R.
    segments = [
        ((0, m), (fw, m)), ((0, fh - m), (fw, fh - m)),
        ((m, m), (m, gy1)), ((m, gy2), (m, fh - m)),
        ((fw - m, m), (fw - m, gy1)), ((fw - m, gy2), (fw - m, fh - m)),
    ]
    net_segments = [
        ((m - _GOAL_DEPTH, gy1), (m - _GOAL_DEPTH, gy2)),
        ((m - _GOAL_DEPTH, gy1), (m, gy1)), ((m - _GOAL_DEPTH, gy2), (m, gy2)),
        ((fw - m + _GOAL_DEPTH, gy1), (fw - m + _GOAL_DEPTH, gy2)),
        ((fw - m, gy1), (fw - m + _GOAL_DEPTH, gy1)),
        ((fw - m, gy2), (fw - m + _GOAL_DEPTH, gy2)),
    ]
    for endpoints, radius, restitution, shape_filter, collision_type in (
        (segments, 5.0, _PM_ELASTICITY_W, wall_filter, _CAT_WALL),
        (net_segments, 3.0, 0.2, wall_filter, _CAT_WALL),
        ([((m, gy1), (m, gy2)), ((fw - m, gy1), (fw - m, gy2))],
         5.0, _PM_ELASTICITY_W, goal_filter, _CAT_GOAL_BARRIER),
    ):
        for a, b in endpoints:
            shape = pymunk.Segment(static, a, b, radius)
            shape.elasticity, shape.friction = restitution, _PM_FRICTION
            shape.filter, shape.collision_type = shape_filter, collision_type
            space.add(shape)

    def wall_impact(arbiter, collision_space, _data):
        # Real contact impulses, not proximity, drive replay/audio impact events.
        if arbiter.is_first_contact and arbiter.total_impulse.length_squared > 1.0:
            collision_space._soccer_events["bounce"] = True

    _on_collision(space, _CAT_BALL, _CAT_WALL, post_solve=wall_impact)
    def player_impact(arbiter, collision_space, _data):
        if arbiter.is_first_contact and arbiter.total_impulse.length_squared > 1.0:
            collision_space._soccer_events["contact"] = True
            # Preserve solver momentum, limiting only excessive contact speed.
            for shape in arbiter.shapes:
                if shape.collision_type == _CAT_BALL:
                    body = shape.body
                    speed = body.velocity.length
                    if speed > 1.0:
                        boost = min(_RALLY_SPEED_BOOST, _RALLY_SPEED_CAP / speed)
                        body.velocity = body.velocity * boost
                    break
    _on_collision(space, _CAT_BALL, _CAT_PLAYER, post_solve=player_impact)
    return space


def _make_player(space, position, stats=None, radius_bonus=0.0, rush_mult=1.0,
                 restitution=_PM_ELASTICITY_P):
    stats = DEFAULT_STATS if stats is None else {**DEFAULT_STATS, **stats}
    radius = _get_player_radius(stats) + radius_bonus
    mass = _get_player_mass(stats)
    body = pymunk.Body(mass, pymunk.moment_for_circle(mass, 0, radius))
    edge = _MARGIN + 5.0 + radius
    body.position = (max(edge, min(FIELD_W-edge, position[0])),
                     max(edge, min(FIELD_H-edge, position[1])))
    body._pitch_edge = edge
    shape = pymunk.Circle(body, radius)
    shape.elasticity, shape.friction = restitution, _PM_FRICTION
    shape.filter = pymunk.ShapeFilter(categories=_CAT_PLAYER,
                                     mask=_CAT_PLAYER | _CAT_BALL | _CAT_WALL | _CAT_GOAL_BARRIER)
    shape.collision_type = _CAT_PLAYER
    pivot = pymunk.PivotJoint(space.static_body, body, (0, 0), (0, 0))
    pivot.max_bias = 0
    pivot.max_force = mass * _get_player_friction(stats) * rush_mult
    space.add(body, shape, pivot)
    return body


def _contain_players(bodies):
    """Enforce the solid player boundary, including both goal mouths.

    The contact solver handles normal bounces. This guard repairs old saved
    out-of-bounds positions and prevents any residual penetration escaping it.
    """
    for body in bodies:
        edge = body._pitch_edge
        x, y = body.position
        nx, ny = max(edge, min(FIELD_W-edge, x)), max(edge, min(FIELD_H-edge, y))
        if nx == x and ny == y:
            continue
        vx, vy = body.velocity
        body.position = nx, ny
        body.velocity = (-vx * .5 if (x-nx)*vx > 0 else vx,
                         -vy * .5 if (y-ny)*vy > 0 else vy)
        body.space.reindex_shapes_for_body(body)


def _make_ball(space, position):
    material = space._soccer_ball
    radius, mass = material["radius"], material["mass"]
    body = pymunk.Body(mass, pymunk.moment_for_circle(mass, 0, radius))
    body.position = position
    shape = pymunk.Circle(body, radius)
    shape.elasticity, shape.friction = material["restitution"], _PM_FRICTION
    shape.filter = pymunk.ShapeFilter(categories=_CAT_BALL, mask=_CAT_PLAYER | _CAT_WALL)
    shape.collision_type = _CAT_BALL
    pivot = pymunk.PivotJoint(space.static_body, body, (0, 0), (0, 0))
    pivot.max_bias = 0
    pivot.max_force = mass * material["rolling_deceleration"]
    space.add(body, shape, pivot)
    return body, pivot


def _build_space(state: dict):
    """Create an isolated deterministic world from authoritative match positions."""
    space = _new_physics_space(state)
    teams = []
    for side in (True, False):
        players = state["players_a" if side else "players_b"]
        teams.append([
            _make_player(space, (float(p["x"]), float(p["y"])), p.get("stats"),
                         _keeper_radius_bonus(state, side) if i == 0 else 0.0,
                         _keeper_rush_mult(state, side) if i == 0 else 1.0)
            for i, p in enumerate(players)
        ])
    ref_pos = state.get("referee", {"x": REFEREE_POS[0], "y": REFEREE_POS[1]})
    # No shape or joint: the referee remains entirely cosmetic.
    ref_body = pymunk.Body(_PM_MASS_P, pymunk.moment_for_circle(_PM_MASS_P, 0, PLAYER_R))
    ref_body.position = (float(ref_pos["x"]), float(ref_pos["y"]))
    space.add(ref_body)
    ball_body, ball_pivot = _make_ball(space, (float(state["ball"]["x"]), float(state["ball"]["y"])))
    return space, teams[0], teams[1], ball_body, ref_body, ball_pivot


#  Penalty shootout 

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
        state["players_a"][0].update(x=kicker_x, y=_PENALTY_SPOT_Y)
        state["players_b"][0].update(x=keeper_x, y=keeper_cy)
    else:
        state["players_b"][0].update(x=kicker_x, y=_PENALTY_SPOT_Y)
        state["players_a"][0].update(x=keeper_x, y=keeper_cy)

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
            players[i].update(x=center_x + x_off, y=y_spacing * outfield_pos)
            outfield_pos += 1

    state["referee"] = {"x": -100, "y": -100}
    state["penalty_goalkeeper_move"] = None


def _build_penalty_space(state: dict, is_player_a: bool, player_idx=0):
    """Only the selected kicker, opposing keeper and ball are active in a shootout."""
    space = _new_physics_space(state)
    space._soccer_penalty_target = "A" if is_player_a else "B"
    ball_x = _PENALTY_SPOT_X_A if is_player_a else _PENALTY_SPOT_X_B
    kicker_x = ball_x + (-_PENALTY_KICKER_BEHIND if is_player_a else _PENALTY_KICKER_BEHIND)
    keeper_x = _PENALTY_KEEPER_X_A if is_player_a else _PENALTY_KEEPER_X_B
    kicker = _make_player(space, (kicker_x, _PENALTY_SPOT_Y),
                          _get_player_stats(state, is_player_a, player_idx))
    keeper_side = not is_player_a
    effects = _keeper_effect(state, keeper_side)
    # Deflector loses more contact energy. A save never teleports the endpoint.
    restitution = _PM_ELASTICITY_P
    if effects.get("safe_deflect"):
        restitution *= max(0.0, min(1.0, float(effects.get("deflect_speed_mult", 0.6))))
    keeper = _make_player(space, (keeper_x, _PENALTY_SPOT_Y),
                          _get_player_stats(state, keeper_side, 0),
                          _keeper_radius_bonus(state, keeper_side),
                          _keeper_rush_mult(state, keeper_side), restitution)
    ball, _ = _make_ball(space, (ball_x, _PENALTY_SPOT_Y))
    return space, kicker, ball, keeper


def _sim_penalty(space, kicker_body, ball_body, keeper_body, keeper_dive_dir,
                 max_steps=_PM_MAX_STEPS, dive_mult=1.0, search=None):
    """Use the same substeps, contact materials and whole-ball scoring as a match."""
    target_y = _PENALTY_KEEPER_DIVE_TARGETS.get(keeper_dive_dir, _PENALTY_SPOT_Y)
    dy = target_y - keeper_body.position.y
    speed = _PENALTY_KEEPER_DIVE_VEL * float(dive_mult)
    keeper_body.apply_impulse_at_local_point((0.0, keeper_body.mass *
                                             (math.copysign(speed, dy) if abs(dy) > 1 else 0.0)))
    trajectory = []
    scored = False
    goal = None
    elapsed = 0.0
    radius = space._soccer_ball["radius"]

    def frame():
        ball, kicker, keeper = ball_body.position, kicker_body.position, keeper_body.position
        point = {"x": round(ball.x, 1), "y": round(ball.y, 1), "z": 0.0, "t": elapsed,
                 "kicker": {"x": round(kicker.x, 1), "y": round(kicker.y, 1)},
                 "keeper": {"x": round(keeper.x, 1), "y": round(keeper.y, 1)}}
        if not trajectory:
            point["ball_size"] = space._soccer_ball["size"]
        if space._soccer_events["bounce"]:
            point["bounce"] = True
        if space._soccer_events["contact"]:
            point["contact"] = True
        trajectory.append(point)

    frame()
    for step in range(max_steps):
        if search is not None and step % 8 == 0:
            search.checkpoint()
        space._soccer_events["bounce"] = False
        space._soccer_events["contact"] = False
        for _ in range(3):
            space.step(_PM_DT / 3.0)
            _contain_players((kicker_body, keeper_body))
            elapsed += _PM_DT / 3.0
            goal = _goal_for_ball(ball_body.position, radius)
            if goal:
                scored = goal == space._soccer_penalty_target
                break
        frame()
        if goal or all(body.velocity.length_squared < 0.25
                         for body in (ball_body, kicker_body, keeper_body)):
            break
    return trajectory, scored


def _penalty_trajectory(state, player_idx, angle_deg, power, is_player_a, search=None):
    """Prediction and execution share the same selected kicker and keeper dive."""
    space, kicker, ball, keeper = _build_penalty_space(state, is_player_a, player_idx)
    _launch_kicker(state, kicker, player_idx, angle_deg, power, is_player_a)
    effects = _keeper_effect(state, not is_player_a)
    dive_mult = float(effects.get("dive_speed_mult", 1.0)) * float(effects.get("rush_speed_mult", 1.0))
    trajectory, scored = _sim_penalty(space, kicker, ball, keeper,
                                     state.get("penalty_goalkeeper_move") or "center",
                                     dive_mult=dive_mult, search=search)
    sampled = _sample_trajectory(trajectory, target=80)
    for point in sampled:
        point['a'] = [dict(p) for p in state['players_a']]
        point['b'] = [dict(p) for p in state['players_b']]
        point['a' if is_player_a else 'b'][player_idx].update(point['kicker'])
        point['b' if is_player_a else 'a'][0].update(point['keeper'])
        point['ref'] = {'x': -100, 'y': -100}
    return sampled, scored


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
    player_idx, angle_deg, power = normalize_kick(state, player_idx, angle_deg, power, is_player_a)
    keeper_move = state.get("penalty_goalkeeper_move") or "center"
    traj_out, scored = _penalty_trajectory(state, player_idx, angle_deg, power, is_player_a)

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
    """Normal commands are ground passes; vertical simulation remains opt-in."""
    return 0.0


def _referee_step(ref_body, ball_body, dt: float, ball_pos=None, ball_vel=None) -> None:
    """Deterministic ambient referee motion: flow-field patrol + ball dodging.

    Purely cosmetic — the referee body has no collision shape, so its
    position only affects rendering (via trajectory ``ref`` frames and the
    game-state ``referee`` field). Movement is a deterministic function of
    (referee position, ball position/velocity), so AI lookahead via
    ``simulate_kick`` stays reproducible.
    """
    rx, ry = ref_body.position
    bx, by = ball_body.position if ball_pos is None else ball_pos
    bvx, bvy = ball_body.velocity if ball_vel is None else ball_vel
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


def _sim(space, bodies_a, bodies_b, ball_body, ref_body, kicker_idx, is_player_a,
         max_steps=_PM_MAX_STEPS, vz0=0.0, ball_pivot=None, search=None):
    """Advance one deterministic world, recording real contacts and physical time."""
    kicker = (bodies_a if is_player_a else bodies_b)[kicker_idx]
    trajectory = []
    scored = None
    ball_z, ball_vz = 0.0, vz0
    players = bodies_a + bodies_b
    sub_dt = _PM_DT / 3.0
    material = space._soccer_ball
    ground_force = ball_body.mass * material["rolling_deceleration"]
    air_force = ball_body.mass * material["air_deceleration"]
    friction_force = None
    elapsed = 0.0
    position_cache = {}

    def frame(ball_pos, ref_pos):
        point = {"x": round(ball_pos.x, 1), "y": round(ball_pos.y, 1),
                 "z": round(ball_z, 1), "t": elapsed,
                 "a": _player_positions(bodies_a, position_cache),
                 "b": _player_positions(bodies_b, position_cache),
                 "ref": {"x": round(ref_pos.x, 1), "y": round(ref_pos.y, 1)}}
        if not trajectory:
            point["ball_size"] = material["size"]
        if space._soccer_events["bounce"]:
            point["bounce"] = True
        if space._soccer_events["contact"]:
            point["contact"] = True
        trajectory.append(point)

    frame(ball_body.position, ref_body.position)
    for step in range(max_steps):
        if search is not None and step % 8 == 0:
            search.checkpoint()
        space._soccer_events["bounce"] = False
        space._soccer_events["contact"] = False
        frame_dt = 0.0
        for _ in range(3):
            # Choose resistance before the step, including the initial takeoff.
            if ball_pivot is not None:
                force = air_force if ball_z > 0.0 or ball_vz > 0.0 else ground_force
                if force != friction_force:
                    ball_pivot.max_force = force
                    friction_force = force
            space.step(sub_dt)
            _contain_players(players)
            elapsed += sub_dt
            frame_dt += sub_dt
            if ball_z > 0.0 or ball_vz != 0.0:
                ball_vz -= G * sub_dt
                ball_z += ball_vz * sub_dt
                if ball_z <= 0.0:
                    ball_z = 0.0
                    ball_vz = -ball_vz * _VERTICAL_RESTITUTION if abs(ball_vz) > _VZ_MIN else 0.0
            # Test every contact substep before a fast ball can rebound off the net.
            scored = _goal_for_ball(ball_body.position, material["radius"], ball_z)
            if scored:
                break
        ball_pos, ball_vel = ball_body.position, ball_body.velocity
        if search is None:
            _referee_step(ref_body, ball_body, frame_dt, ball_pos, ball_vel)
            frame(ball_pos, ref_body.position)
        if scored:
            if search is not None:
                frame(ball_pos, ref_body.position)
            break
        all_settled = (ball_vel.length_squared < 0.25 and ball_z == 0.0 and ball_vz == 0.0)
        if all_settled:
            for body in players:
                if body.velocity.length_squared >= 0.25:
                    all_settled = False
                    break
        # AI lookahead needs real contacts and the final roster, but not a
        # cosmetic referee or a player snapshot for every rendered frame.
        if search is not None and (step % 4 == 0 or all_settled
                                   or step == max_steps - 1
                                   or space._soccer_events["bounce"] or space._soccer_events["contact"]):
            frame(ball_pos, ref_body.position)
        if all_settled:
            break
    # Ensure subsequent moves never inherit a stale airborne resistance value.
    if ball_pivot is not None and ball_z == 0.0 and ball_vz == 0.0:
        ball_pivot.max_force = ground_force
    return trajectory, scored, kicker


def _launch_kicker(state, kicker, player_idx, angle_deg, power, is_player_a):
    """Launch the selected pawn with a momentum impulse; contacts provide recoil."""
    stats = _get_player_stats(state, is_player_a, player_idx)
    speed = power * _get_player_kick_vel(stats)
    angle = math.radians(angle_deg)
    desired = pymunk.Vec2d(math.cos(angle) * speed, math.sin(angle) * speed)
    kicker.apply_impulse_at_local_point((desired - kicker.velocity) * kicker.mass)
    # This is a ground game: only actual contact moves the ball. In particular,
    # a missed kick must not launch the ball vertically from a distance.
    return 0.0


def simulate_kick(
    state: dict,
    player_idx: int,
    angle_deg: float,
    power: float,
    is_player_a: bool,
) -> tuple[list[dict], str | None]:
    player_idx, angle_deg, power = normalize_kick(state, player_idx, angle_deg, power, is_player_a)
    search = current_search()
    move = (player_idx, angle_deg, power)
    if search is not None:
        cached = search.cached(state, move, is_player_a)
        if cached is not None:
            return cached
        search.begin()
    if state.get("penalty_shootout"):
        trajectory, scored = _penalty_trajectory(state, player_idx, angle_deg, power, is_player_a, search=search)
        result = trajectory, ("A" if is_player_a else "B") if scored else None
        if search is not None:
            search.record(state, move, is_player_a, result)
        return result
    space, bodies_a, bodies_b, ball_body, ref_body, ball_pivot = _build_space(state)

    kicker = (bodies_a if is_player_a else bodies_b)[player_idx]
    vz0 = _launch_kicker(state, kicker, player_idx, angle_deg, power, is_player_a)

    trajectory, scored, _ = _sim(space, bodies_a, bodies_b, ball_body, ref_body, player_idx, is_player_a,
                               vz0=vz0, ball_pivot=ball_pivot, search=search)

    # Misses still contain the launched pawn: prediction and playback agree.
    result = _sample_trajectory(trajectory), scored
    if search is not None:
        search.record(state, move, is_player_a, result)
    return result


def apply_kick(
    state: dict,
    player_idx: int,
    angle_deg: float,
    power: float,
    is_player_a: bool,
) -> tuple[list[dict], str | None, str, dict, None]:
    player_idx, angle_deg, power = normalize_kick(state, player_idx, angle_deg, power, is_player_a)
    space, bodies_a, bodies_b, ball_body, ref_body, ball_pivot = _build_space(state)

    kicker = (bodies_a if is_player_a else bodies_b)[player_idx]
    vz0 = _launch_kicker(state, kicker, player_idx, angle_deg, power, is_player_a)

    trajectory, scored, _ = _sim(space, bodies_a, bodies_b, ball_body, ref_body, player_idx, is_player_a, vz0=vz0, ball_pivot=ball_pivot)

    #  Decimate trajectory 
    traj_out = _sample_trajectory(trajectory)

    #  Update state 
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

    #  Push result 
    push_result = None  # Legacy, now handled completely via full trajectory syncing

    #  Description 
    player_label = "A" if is_player_a else "B"
    ball_hit = any(pt["x"] != trajectory[0]["x"] or pt["y"] != trajectory[0]["y"] for pt in trajectory)
    miss_text = " (missed!)" if not ball_hit else ""
    scored_text = f" GOAL for {scored}!" if scored else ""
    desc = (
        f"Team {player_label} player {player_idx}: "
        f"angle={round(angle_deg)}{chr(176)} power={round(power)}{scored_text}{miss_text}"
    )

    #  Score handling 
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
    elif hl <= 0:
        pass  # Untimed flick soccer: only the goal target ends the match.
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
