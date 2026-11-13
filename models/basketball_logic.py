"""Local, turn-based arcade basketball using the shared game/replay contract.

Each command either throws from the selected ball carrier or launches an
off-ball player. Throws have a real, deterministic arc: only a descending
crossing of a hoop scores. The existing four player stats govern disc contacts,
movement and throwing range. A move runs one bounded pymunk world.
"""
from __future__ import annotations

import math
import time

import pymunk

from models.soccer_logic import (
    DEFAULT_STATS, FIELD_H, FIELD_W, _get_player_friction,
    _get_player_kick_vel, _get_player_mass, _get_player_radius,
    _get_player_stats, normalize_kick,
)

BALL_R = 12.0
HOOP_HEIGHT = 90.0
HOOP_RADIUS = 25.0
THREE_POINT_RADIUS = 360.0
TARGET_SCORE = 21
WIN_BY = 2
HOOPS = {"A": {"x": FIELD_W - 100.0, "y": FIELD_H / 2, "z": HOOP_HEIGHT},
         "B": {"x": 100.0, "y": FIELD_H / 2, "z": HOOP_HEIGHT}}
ROLES = ("Point guard", "Shooting guard", "Small forward", "Power forward", "Center")
_STEP = 1 / 60
_MAX_STEPS = 210                  # At most 3.5 simulated seconds per command.
_MAX_REPLAY_MOVES = 60           # Bound session payloads during long manual games.
_GRAVITY = 980.0
_THROW_Z = 28.0
_THROW_VZ = 520.0
_HELD_Z = 24.0
_CATCH_HEIGHT = 55.0
_WALL_INSET = 25.0
_BALL_DRAG = 190.0


class _PredictionCancelled(Exception):
    """A speculative court world exceeded its request-local deadline."""


def _possession(side: str, index: int) -> dict:
    # ``side`` / ``player_idx`` are canonical; aliases keep renderer callers easy.
    return {"side": side, "player_idx": index, "team": side, "index": index}


def _read_possession(state: dict) -> tuple[str, int] | None:
    possession = state.get("possession")
    if not isinstance(possession, dict):
        return None
    side = possession.get("side", possession.get("team"))
    index = possession.get("player_idx", possession.get("index"))
    if side not in ("A", "B") or not isinstance(index, int):
        return None
    roster = state.get("players_a" if side == "A" else "players_b", [])
    return (side, index) if 0 <= index < len(roster) else None


def _home_positions(side: str, inbound: bool = False) -> tuple:
    # Five basketball positions, without a goalkeeper or soccer formation slots.
    home = ((360.0, 300.0), (470.0, 610.0), (620.0, 205.0),
            (795.0, 650.0), (650.0, FIELD_H / 2))
    if inbound:
        # Compressed backcourt inbound keeps a turn-based match moving: the
        # receiver can attempt a long three or work the ball through teammates.
        home = ((420.0, 300.0), (550.0, 610.0), (710.0, 205.0),
                (850.0, 650.0), (400.0, FIELD_H / 2))
    return home if side == "A" else tuple((FIELD_W - x, y) for x, y in home)


def _place_rosters(state: dict, receiving_side: str | None = None) -> None:
    """Reposition existing player dictionaries so identity and stats survive."""
    for side in ("A", "B"):
        for player, (x, y) in zip(state["players_" + side.lower()],
                                  _home_positions(side, side == receiving_side)):
            player.update(x=x, y=y)
    side = receiving_side or "A"
    state["possession"] = _possession(side, 4)
    carrier = state["players_" + side.lower()][4]
    state["ball"] = {"x": carrier["x"], "y": carrier["y"], "z": _HELD_Z}


def new_basketball_state(
    mode: str = "hvai", model_b: str = "greedy", model_a: str = "greedy",
    player_count: int = 5, half_length: int = 0, win_goal_limit: int = TARGET_SCORE,
    power_cap: int = 100, formation_a: str | None = None,
    formation_b: str | None = None, referee_name: str | None = None,
    team_movement: bool | None = None, **_kwargs,
) -> dict:
    """Return a five-a-side match; basketball always uses 21, win by two.

    Soccer constructor arguments are accepted for route compatibility. Soccer
    clocks, formation choices and penalties do not apply to this local ruleset.
    """
    now = time.time()
    state = {
        "sport": "basketball", "field": {"width": FIELD_W, "height": FIELD_H},
        "basketball": {"hoops": {side: dict(hoop) for side, hoop in HOOPS.items()},
                       "hoop_radius": HOOP_RADIUS, "three_point_radius": THREE_POINT_RADIUS,
                       "target_score": TARGET_SCORE, "win_by": WIN_BY,
                       "roles": list(ROLES)},
        "players_a": [{"x": x, "y": y, "role": ROLES[index], "stats": dict(DEFAULT_STATS)}
                      for index, (x, y) in enumerate(_home_positions("A"))],
        "players_b": [{"x": x, "y": y, "role": ROLES[index], "stats": dict(DEFAULT_STATS)}
                      for index, (x, y) in enumerate(_home_positions("B"))],
        "ball": {}, "possession": None, "last_points": 0,
        "score_a": 0, "score_b": 0, "is_player_a": True, "kick_count": 0,
        "start_time": now, "turn_start_time": now, "game_over": False, "winner": None,
        "move_history": [], "snapshots": [], "game_mode": mode,
        "playstyle_a": 3, "playstyle_b": 3,
        "team_movement": (mode in ("hvai", "aivai")) if team_movement is None else bool(team_movement),
        "model_name_a": model_a, "model_name_b": model_b, "first_kicker": "A",
        "period": "basketball", "player_count": 5, "half_length": 0,
        "win_goal_limit": TARGET_SCORE, "power_cap": power_cap,
        "penalty_shootout": False, "penalty_kick_num": 0,
        "penalty_a_score": 0, "penalty_b_score": 0, "penalty_kicks": [],
        "penalty_goalkeeper_move": None, "keeper_style_a": "default", "keeper_style_b": "default",
        "formation_a": None, "formation_b": None,
        "referee": {"x": FIELD_W / 2, "y": FIELD_H - 65},
        "referee_name": referee_name or "Court official", "_finalized": False,
    }
    _place_rosters(state)
    return state


def human_basketball_player_index(state: dict, is_player_a: bool = True) -> int:
    """Default to this team's carrier; otherwise select its nearest rebounder."""
    side = "A" if is_player_a else "B"
    possession = _read_possession(state)
    if possession and possession[0] == side:
        return possession[1]
    roster = state["players_" + side.lower()]
    ball = state["ball"]
    return min(range(len(roster)), key=lambda i: math.hypot(
        roster[i]["x"] - ball["x"], roster[i]["y"] - ball["y"]))


def _throw_factor(stats: dict) -> float:
    return 8.0 + max(0.0, min(100.0, float(stats.get("power", 50)))) * .04


def _flight_time(height: float) -> float:
    """Time when the fixed arc descends through ``height``."""
    return (_THROW_VZ + math.sqrt(_THROW_VZ ** 2 + 2 * _GRAVITY * (_THROW_Z - height))) / _GRAVITY


def _brake(body: pymunk.Body, deceleration: float, dt: float) -> None:
    speed = body.velocity.length
    if speed:
        body.velocity *= max(0.0, speed - deceleration * dt) / speed


def _build_space(state: dict):
    space = pymunk.Space()
    space.iterations = 12
    bodies = {}
    stats = {}
    for side in ("A", "B"):
        for index, player in enumerate(state["players_" + side.lower()]):
            key = (side, index)
            stat = _get_player_stats(state, side == "A", index)
            radius, mass = _get_player_radius(stat), _get_player_mass(stat)
            body = pymunk.Body(mass, pymunk.moment_for_circle(mass, 0, radius))
            body.position = player["x"], player["y"]
            shape = pymunk.Circle(body, radius)
            shape.elasticity, shape.friction = .18, .4
            shape.filter = pymunk.ShapeFilter(categories=1, mask=7)
            space.add(body, shape)
            bodies[key], stats[key] = body, stat
    ball = pymunk.Body(.6, pymunk.moment_for_circle(.6, 0, BALL_R))
    ball.position = state["ball"]["x"], state["ball"]["y"]
    shape = pymunk.Circle(ball, BALL_R)
    shape.elasticity, shape.friction = .6, .35
    shape.filter = pymunk.ShapeFilter(categories=2, mask=0)
    space.add(ball, shape)
    # Actual solid boundary segments keep collisions and weight interactions
    # physical. The per-step clamp below only removes solver penetration.
    left, top = _WALL_INSET, _WALL_INSET
    right, bottom = FIELD_W - left, FIELD_H - top
    for start, end in (((left, top), (right, top)), ((right, top), (right, bottom)),
                       ((right, bottom), (left, bottom)), ((left, bottom), (left, top))):
        wall = pymunk.Segment(space.static_body, start, end, 0)
        wall.elasticity, wall.friction = .55, .25
        wall.filter = pymunk.ShapeFilter(categories=4, mask=3)
        space.add(wall)
    return space, bodies, stats, ball, shape


def _clamp_body(body, radius):
    pos = body.position
    x = max(_WALL_INSET + radius, min(FIELD_W - _WALL_INSET - radius, pos.x))
    y = max(_WALL_INSET + radius, min(FIELD_H - _WALL_INSET - radius, pos.y))
    if x != pos.x or y != pos.y:
        body.position = x, y


def _launch_player(body, stats, angle, power):
    radians = math.radians(angle)
    speed = power * _get_player_kick_vel(stats)
    body.velocity = math.cos(radians) * speed, math.sin(radians) * speed


def _support_runs(state, bodies, stats, selected, possession, receiver=None):
    if not state.get("team_movement", False):
        return
    focus = bodies[possession].position if possession else pymunk.Vec2d(state["ball"]["x"], state["ball"]["y"])
    lane_y = (280.0, 650.0, 180.0, 630.0, FIELD_H / 2)
    rebounders = ({min((key for key in bodies if key[0] == side),
                       key=lambda key: (bodies[key].position - focus).length)
                  for side in ("A", "B")} if possession is None else set())
    for key, body in bodies.items():
        if key in (selected, possession, receiver):
            continue
        side, index = key
        attack = possession is not None and side == possession[0]
        if key in rebounders:
            # Keep the closest rebounder moving toward a loose ball between
            # commands. Man marking here used to pull that player back on
            # every opposing turn, cancelling low-power recovery progress.
            target = focus
        elif attack:
            progress = focus.x if side == "A" else FIELD_W - focus.x
            depth = max(280.0, min(1100.0, progress + (-180, -50, 130, 220, 60)[index]))
            target = pymunk.Vec2d(depth if side == "A" else FIELD_W - depth, lane_y[index])
        else:
            other = bodies[("B" if side == "A" else "A", index)].position
            own_hoop = HOOPS["B" if side == "A" else "A"]
            # Man-to-man cover, shaded towards the defending basket.
            target = other * .76 + pymunk.Vec2d(own_hoop["x"], own_hoop["y"]) * .24
        delta = target - body.position
        distance = min(70.0, delta.length)
        if distance > 10:
            speed = math.sqrt(2 * _get_player_friction(stats[key]) * distance)
            body.velocity = delta.normalized() * speed


def _frame(bodies, ball, z, elapsed, possession, selected, **events):
    positions = {side.lower(): [{"x": round(bodies[(side, index)].position.x, 1),
                                "y": round(bodies[(side, index)].position.y, 1)}
                               for index in range(5)] for side in ("A", "B")}
    frame = {"x": round(ball.position.x, 1), "y": round(ball.position.y, 1),
             "z": round(max(0.0, z), 2), "t": round(elapsed, 4),
             "a": positions["a"], "b": positions["b"], "basketball": True, "sport": "basketball",
             "possession": _possession(*possession) if possession else None,
             "kicker": {"x": round(bodies[selected].position.x, 1),
                        "y": round(bodies[selected].position.y, 1)}}
    frame.update(events)
    return frame


def _run_move(state, player_idx, angle, power, is_player_a, *, prediction_deadline=None):
    if prediction_deadline is not None and time.perf_counter() >= prediction_deadline:
        raise _PredictionCancelled
    space, bodies, stats, ball, ball_shape = _build_space(state)
    selected = ("A" if is_player_a else "B", player_idx)
    possession = _read_possession(state)
    if possession:
        ball.position = bodies[possession].position
    z = _HELD_Z if possession else max(0.0, float(state["ball"].get("z", 0)))
    z = min(z, _HELD_Z)       # Completed turns never retain a ball in flight.
    trajectory = [_frame(bodies, ball, z, 0, possession, selected)]
    throwing = selected == possession and power > 0
    thrower = selected if throwing else None
    # A steal is an approach into contact, rather than alternating ownership
    # forever between two discs already touching after the previous steal.
    can_steal = bool(possession and selected[0] != possession[0] and
                     (bodies[selected].position - bodies[possession].position).length >
                     _get_player_radius(stats[selected]) + _get_player_radius(stats[possession]) + 12)
    launch = tuple(ball.position)
    receiver = None
    vz = 0.0
    if throwing:
        direction = pymunk.Vec2d(math.cos(math.radians(angle)), math.sin(math.radians(angle)))
        ball.velocity = direction * power * _throw_factor(stats[selected])
        z, vz, possession = _THROW_Z, _THROW_VZ, None
        # A pass recipient holds their lane while the other players support.
        catch_point = ball.position + ball.velocity * _flight_time(_CATCH_HEIGHT)
        candidates = [key for key in bodies if key[0] == selected[0] and key != selected]
        nearest = min(candidates, key=lambda key: (bodies[key].position - catch_point).length)
        if (bodies[nearest].position - catch_point).length < 85:
            receiver = nearest
    else:
        _launch_player(bodies[selected], stats[selected], angle, power)
    _support_runs(state, bodies, stats, selected, _read_possession(state), receiver)
    scored, points = None, 0
    for step in range(1, _MAX_STEPS + 1):
        if prediction_deadline is not None and time.perf_counter() >= prediction_deadline:
            raise _PredictionCancelled
        elapsed = step * _STEP
        for key, body in bodies.items():
            _brake(body, _get_player_friction(stats[key]), _STEP)
        previous_pos, previous_z = ball.position, z
        if possession:
            ball.position, ball.velocity = bodies[possession].position, (0, 0)
            z, vz = _HELD_Z, 0.0
        elif z > 0 or vz > 0:
            next_z = z + vz * _STEP - .5 * _GRAVITY * _STEP ** 2
            vz -= _GRAVITY * _STEP
            z = max(0.0, next_z)
            if z == 0:
                vz = 0.0
        else:
            _brake(ball, _BALL_DRAG, _STEP)
        airborne = z > _CATCH_HEIGHT or (throwing and vz > 0)
        ball_shape.filter = pymunk.ShapeFilter(categories=2, mask=0 if possession else (4 if airborne else 5))
        space.step(_STEP)
        for key, body in bodies.items():
            _clamp_body(body, _get_player_radius(stats[key]))
        _clamp_body(ball, BALL_R)
        events = {}
        if throwing and not possession and previous_z >= HOOP_HEIGHT > z and vz < 0:
            fraction = (previous_z - HOOP_HEIGHT) / (previous_z - z)
            crossing = previous_pos + (ball.position - previous_pos) * fraction
            for side, hoop in HOOPS.items():
                if (crossing - pymunk.Vec2d(hoop["x"], hoop["y"])).length <= HOOP_RADIUS:
                    scored = side
                    distance = math.hypot(launch[0] - hoop["x"], launch[1] - hoop["y"])
                    points = 3 if side == selected[0] and distance >= THREE_POINT_RADIUS else 2
                    ball.position, ball.velocity, z = crossing, (0, 0), HOOP_HEIGHT
                    trajectory.append(_frame(bodies, ball, z, elapsed, None, selected,
                                             scored=side, points=points))
                    break
            if scored:
                break
        if possession:
            # Only the commanded defender can steal a held ball by reaching
            # contact. Passive support cannot silently take a user's dribble.
            if can_steal and selected[0] != possession[0]:
                carrier, defender = bodies[possession], bodies[selected]
                reach = _get_player_radius(stats[possession]) + _get_player_radius(stats[selected]) + 7
                if (defender.position - carrier.position).length <= reach:
                    possession = selected
                    events["contact"] = True
            ball.position = bodies[possession].position
            ball.velocity, z = (0, 0), _HELD_Z
        elif z <= _CATCH_HEIGHT and (not throwing or vz <= 0):
            # A released arc must clear the clustered discs around its
            # thrower. Catching it during the first rising frames otherwise
            # turns short passes and layups into instant, unavoidable steals.
            # Defenders can still steal a held ball on their commanded run,
            # and either team can catch a descending pass or a loose rebound.
            catches = []
            for key, body in bodies.items():
                if key == thrower and elapsed < .38:
                    continue
                distance = (body.position - ball.position).length
                if distance <= _get_player_radius(stats[key]) + BALL_R + 10:
                    catches.append((distance, key))
            if catches:
                _, possession = min(catches)
                ball.position, ball.velocity, z, vz = bodies[possession].position, (0, 0), _HELD_Z, 0
                events["contact"] = True
                throwing = False
        if previous_z > 0 and z == 0:
            events["bounce"] = True
        if step % 2 == 0 or events:
            trajectory.append(_frame(bodies, ball, z, elapsed, possession, selected, **events))
        settled = all(body.velocity.length < 2 for body in bodies.values())
        if settled and (possession or (z == 0 and ball.velocity.length < 3)) and elapsed >= .15:
            break
    # The hard time bound is authoritative; no hidden movement continues later.
    if not possession:
        z = 0.0 if not scored else z
    final = _frame(bodies, ball, z, elapsed, possession, selected)
    if trajectory[-1]["t"] != final["t"]:
        trajectory.append(final)
    elif not scored:
        trajectory[-1] = {**trajectory[-1], **final}
    return trajectory, scored, points, possession


def simulate_basketball_move(state, player_idx, angle_deg, power, is_player_a):
    """Predict without mutating state, returning the soccer prediction tuple."""
    player_idx, angle_deg, power = normalize_kick(state, player_idx, angle_deg, power, is_player_a)
    trajectory, scored, _, _ = _run_move(state, player_idx, angle_deg, power, is_player_a)
    return trajectory, scored


def apply_basketball_move(state, player_idx, angle_deg, power, is_player_a):
    """Apply one throw/run and return trajectory, score, description, endpoint, None."""
    player_idx, angle_deg, power = normalize_kick(state, player_idx, angle_deg, power, is_player_a)
    if state.get("game_over"):
        raise ValueError("This basketball match has finished")
    trajectory, scored, points, possession = _run_move(state, player_idx, angle_deg, power, is_player_a)
    final = trajectory[-1]
    for side in ("a", "b"):
        for player, point in zip(state["players_" + side], final[side]):
            player.update(x=point["x"], y=point["y"])
    state["ball"] = {key: final[key] for key in ("x", "y", "z")}
    state["possession"] = _possession(*possession) if possession else None
    side = "A" if is_player_a else "B"
    state["last_points"] = points
    endpoint = dict(final["kicker"])
    desc = f"Team {side} #{player_idx + 1}: "
    if scored:
        state["score_" + scored.lower()] += points
        desc += f"{points}-point basket for {scored}!"
    elif possession:
        desc += f"{possession[0]} #{possession[1] + 1} has possession"
    else:
        desc += "loose ball"
    state["kick_count"] = state.get("kick_count", 0) + 1
    state["is_player_a"] = not is_player_a
    state["turn_start_time"] = time.time()
    state["_finalized"] = False
    state.setdefault("move_history", []).append({
        "desc": desc, "player": side, "player_idx": player_idx,
        "angle": round(angle_deg, 1), "power": round(power, 1),
        "scored": scored, "points": points, "sport": "basketball",
        "trajectory": trajectory, "mover": side.lower(), "kick_endpoint": dict(endpoint),
    })
    sa, sb = state["score_a"], state["score_b"]
    if max(sa, sb) >= TARGET_SCORE and abs(sa - sb) >= WIN_BY:
        state.update(game_over=True, winner="A" if sa > sb else "B", clock_stopped_at=0)
    if scored:
        receiving_side = "B" if scored == "A" else "A"
        _place_rosters(state, receiving_side)
        # A scoring turnover gives the conceding team the inbound regardless
        # of whether a player happened to score at the other end.
        state["is_player_a"] = receiving_side == "A"
        state["move_history"][-1]["reset"] = {
            "ball": dict(state["ball"]), "possession": dict(state["possession"]),
            "a": [{"x": player["x"], "y": player["y"]} for player in state["players_a"]],
            "b": [{"x": player["x"], "y": player["y"]} for player in state["players_b"]],
        }
    if len(state["move_history"]) > _MAX_REPLAY_MOVES:
        del state["move_history"][:-_MAX_REPLAY_MOVES]
    return trajectory, scored, desc, endpoint, None


def _move_to(state, side, index, target):
    player = state["players_" + side.lower()][index]
    dx, dy = target[0] - player["x"], target[1] - player["y"]
    stats = _get_player_stats(state, side == "A", index)
    speed = math.sqrt(max(0, 2 * _get_player_friction(stats) * math.hypot(dx, dy)))
    power = min(100.0, float(state.get("power_cap", 100)), speed / _get_player_kick_vel(stats))
    return index, math.degrees(math.atan2(dy, dx)) % 360, power


def _recovery_move(state, side, possession, cap):
    """Rank actual stat-based reach, then check at most three physical paths."""
    roster = state["players_" + side.lower()]
    target = (state["players_" + possession[0].lower()][possession[1]]
              if possession else state["ball"])
    target_radius = (_get_player_radius(_get_player_stats(state, possession[0] == "A", possession[1])) + 7
                     if possession else BALL_R + 10)
    options = []
    touching = []
    for index, player in enumerate(roster):
        stats = _get_player_stats(state, side == "A", index)
        radius = _get_player_radius(stats)
        dx, dy = target["x"] - player["x"], target["y"] - player["y"]
        distance = math.hypot(dx, dy)
        if possession and distance <= radius + target_radius + 5:
            touching.append((distance, index, radius))
            continue
        speed = cap * _get_player_kick_vel(stats)
        friction = _get_player_friction(stats)
        # Braking precedes each physics step, so continuous stopping distance
        # would overestimate a weak player's reach by half a step of travel.
        travel = max(0.0, speed * speed / (2 * friction) - speed * _STEP / 2)
        needed = max(0.0, distance - radius - target_radius)
        blocked = 0.0
        if distance:
            for other_side in ("A", "B"):
                for other_index, other in enumerate(state["players_" + other_side.lower()]):
                    if (other_side, other_index) in ((side, index), possession):
                        continue
                    projection = ((other["x"] - player["x"]) * dx +
                                  (other["y"] - player["y"]) * dy) / (distance * distance)
                    if 0 < projection < 1:
                        clearance = math.hypot(other["x"] - player["x"] - projection * dx,
                                               other["y"] - player["y"] - projection * dy)
                        other_radius = _get_player_radius(_get_player_stats(state, other_side == "A", other_index))
                        blocked = max(blocked, radius + other_radius - clearance)
        options.append(((needed > travel + 1, blocked > 0, needed / max(1, travel), distance, index),
                        _move_to(state, side, index, (target["x"], target["y"]))))
    if not options:
        # Contact alone cannot trigger a second steal. Create a real approach
        # on the next command rather than issuing an endless zero-length move.
        _, index, radius = min(touching)
        player = roster[index]
        dx, dy = player["x"] - target["x"], player["y"] - target["y"]
        distance = math.hypot(dx, dy)
        if not distance:
            dx, dy, distance = (1.0 if target["x"] < FIELD_W / 2 else -1.0), 0.0, 1.0
        offset = radius + target_radius + 75
        margin = _WALL_INSET + radius
        directions = ((dx / distance, dy / distance), (0, 1), (0, -1), (1, 0), (-1, 0))
        retreats = [(target["x"] + ux * offset, target["y"] + uy * offset) for ux, uy in directions]
        retreat = next((point for point in retreats
                        if margin <= point[0] <= FIELD_W - margin and margin <= point[1] <= FIELD_H - margin),
                       (FIELD_W / 2, FIELD_H / 2))
        return _move_to(state, side, index, retreat)
    ordered = sorted(options)
    best_move, best_score = ordered[0][1], None
    deadline = time.perf_counter() + .080
    for _, move in ordered[:3]:
        # One bounded world per candidate; leave margin under the shared local
        # decision target and never build a tree of speculative simulations.
        if time.perf_counter() >= deadline:
            break
        try:
            trajectory, scored, _, final_possession = _run_move(
                state, *move, side == "A", prediction_deadline=deadline)
        except _PredictionCancelled:
            break
        endpoint = trajectory[-1]
        point = endpoint[side.lower()][move[0]]
        final_target = (endpoint[final_possession[0].lower()][final_possession[1]]
                        if final_possession else endpoint)
        remaining = math.hypot(point["x"] - final_target["x"], point["y"] - final_target["y"])
        gained = bool(final_possession and final_possession[0] == side)
        score = (scored == side, gained, -remaining)
        if best_score is None or score > best_score:
            best_move, best_score = move, score
        if gained or scored == side:
            break
    return best_move


def basketball_ai_move(state: dict, is_player_a: bool) -> tuple[int, float, float]:
    """Deterministic legal policy: shoot, advance by pass, steal, or rebound.

    Throws use the shared launch equations. Recovery checks at most three
    stat-aware paths in the actual physics world, without soccer assumptions.
    """
    side = "A" if is_player_a else "B"
    roster = state["players_" + side.lower()]
    possession = _read_possession(state)
    cap = min(100.0, max(0.0, float(state.get("power_cap", 100))))
    if possession and possession[0] == side:
        index = possession[1]
        player, hoop = roster[index], HOOPS[side]
        stats = _get_player_stats(state, is_player_a, index)
        dx, dy = hoop["x"] - player["x"], hoop["y"] - player["y"]
        distance = math.hypot(dx, dy)
        shot_power = max(.01, distance / (_throw_factor(stats) * _flight_time(HOOP_HEIGHT)))
        if shot_power <= cap:
            return index, math.degrees(math.atan2(dy, dx)) % 360, shot_power
        # Prefer an unguarded, forward receiver over a blind full-court heave.
        opponents = state["players_b" if is_player_a else "players_a"]
        options = []
        for target_index, target in enumerate(roster):
            if target_index == index:
                continue
            target_distance = math.hypot(target["x"] - hoop["x"], target["y"] - hoop["y"])
            pass_distance = math.hypot(target["x"] - player["x"], target["y"] - player["y"])
            pass_power = pass_distance / (_throw_factor(stats) * _flight_time(_CATCH_HEIGHT))
            clearance = min(math.hypot(target["x"] - other["x"], target["y"] - other["y"]) for other in opponents)
            if pass_power <= cap and target_distance < distance - 50 and clearance > 140:
                options.append((target_distance - min(clearance, 150) * .25, target_index, pass_power))
        if options:
            _, target_index, pass_power = min(options)
            target = roster[target_index]
            return index, math.degrees(math.atan2(target["y"] - player["y"], target["x"] - player["x"])) % 360, pass_power
        # A covered backcourt offense can still pass into space and chase the
        # rebound on its next command; ball carriers do not move by throwing.
        return index, math.degrees(math.atan2(dy, dx)) % 360, cap
    return _recovery_move(state, side, possession, cap)
