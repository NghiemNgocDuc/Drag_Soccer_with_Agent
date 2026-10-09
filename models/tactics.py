"""Cheap, shared team decisions before an AI spends its physics-search budget.

Only the selected pawn moves. Receivers and supporting players are evaluated at
their real positions; planning never teleports teammates or mutates match state.
The analytic collision estimate ranks candidates, and the caller can verify a
small shortlist with the authoritative physics engine.
"""
from __future__ import annotations

import math

from models.common import aim_through, needs_clear
from models.soccer_logic import (
    FIELD_W, FIELD_H, GOAL_Y1, GOAL_Y2, _MARGIN, _PM_ELASTICITY_P,
    _ball_physics, _get_player_stats, _get_player_radius, _get_player_mass,
    _get_player_kick_vel, _get_player_friction, _keeper_radius_bonus,
    _keeper_rush_mult, _RALLY_SPEED_BOOST, _RALLY_SPEED_CAP,
)


def _distance(a, b):
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def _progress(x, side):
    return x if side else FIELD_W - x


def _indices(players, allowed):
    if allowed is None:
        return list(range(len(players)))
    return list(dict.fromkeys(i for i in allowed
                              if isinstance(i, int) and 0 <= i < len(players)))


def _profile(state, side, index, material):
    stats = _get_player_stats(state, side, index)
    radius = _get_player_radius(stats)
    resistance = _get_player_friction(stats)
    if index == 0:
        radius += _keeper_radius_bonus(state, side)
        resistance *= _keeper_rush_mult(state, side)
    return radius + material["radius"], resistance, _get_player_kick_vel(stats), _get_player_mass(stats)


def _lane_blocked(start, target, opponents, material):
    """Test actual opponent circles along the interior of a passing lane."""
    dx, dy = target["x"] - start["x"], target["y"] - start["y"]
    length2 = dx * dx + dy * dy
    if length2 < 1.0:
        return False
    for opponent in opponents:
        t = ((opponent["x"] - start["x"]) * dx
             + (opponent["y"] - start["y"]) * dy) / length2
        if 0.02 < t < 0.98:
            radius = _get_player_radius({"size": (opponent.get("stats") or {}).get("size", 50)})
            clearance = math.hypot(opponent["x"] - start["x"] - t * dx,
                                   opponent["y"] - start["y"] - t * dy)
            if clearance < radius + material["radius"] + 8.0:
                return True
    return False


def _impact_geometry(player, ball, angle, contact):
    radians = math.radians(angle)
    ux, uy = math.cos(radians), math.sin(radians)
    dx, dy = ball["x"] - player["x"], ball["y"] - player["y"]
    projection = dx * ux + dy * uy
    perpendicular2 = max(0.0, dx * dx + dy * dy - projection * projection)
    if projection < 0.0 or perpendicular2 >= contact * contact:
        return None
    travel = max(0.0, projection - math.sqrt(contact * contact - perpendicular2))
    cx, cy = player["x"] + ux * travel, player["y"] + uy * travel
    nx, ny = ball["x"] - cx, ball["y"] - cy
    normal_length = math.hypot(nx, ny)
    if normal_length > 1e-9:
        nx, ny = nx / normal_length, ny / normal_length
    else:
        nx, ny = ux, uy
    return travel, cx, cy, nx, ny, max(0.0, ux * nx + uy * ny)


def _estimate(state, side, move, material, profiles):
    """One friction-and-momentum estimate, without creating a Pymunk space."""
    index, angle, power = move
    players = state["players_a" if side else "players_b"]
    player, ball = players[index], state["ball"]
    contact, resistance, velocity, mass = profiles[side][index]
    radians = math.radians(angle)
    ux, uy = math.cos(radians), math.sin(radians)
    speed = power * velocity
    pawn_distance = speed * speed / (2.0 * resistance)
    pawn_end = {"x": max(_MARGIN + contact - material["radius"], min(FIELD_W - _MARGIN - contact + material["radius"], player["x"] + ux * pawn_distance)),
                "y": max(_MARGIN + contact - material["radius"], min(FIELD_H - _MARGIN - contact + material["radius"], player["y"] + uy * pawn_distance))}
    endpoint = {"x": ball["x"], "y": ball["y"]}
    scored = None
    collision = _impact_geometry(player, ball, angle, contact)
    if collision:
        travel, cx, cy, nx, ny, alignment = collision
        impact2 = speed * speed - 2.0 * resistance * travel
        if impact2 > 0:
            transfer = ((1.0 + _PM_ELASTICITY_P * material["restitution"])
                        * mass / (mass + material["mass"]))
            ball_speed = min(_RALLY_SPEED_CAP, math.sqrt(impact2) * alignment * transfer * _RALLY_SPEED_BOOST)
            ball_distance = ball_speed * ball_speed / (2.0 * material["rolling_deceleration"])
            intercepted = False
            # An open lane is worth more than a long estimate through another
            # pawn. Conservatively stop at the first contact instead of running
            # a second physics world just to reject an obviously blocked pass.
            for teammate_side in (side, not side):
                roster = state["players_a" if teammate_side else "players_b"]
                for other_index, other in enumerate(roster):
                    if teammate_side == side and other_index == index:
                        continue
                    rx, ry = other["x"] - ball["x"], other["y"] - ball["y"]
                    along = rx * nx + ry * ny
                    if along <= 0.0:
                        continue
                    other_contact = profiles[teammate_side][other_index][0]
                    across2 = max(0.0, rx * rx + ry * ry - along * along)
                    if across2 >= other_contact * other_contact:
                        continue
                    first_contact = max(0.0, along - math.sqrt(other_contact * other_contact - across2))
                    if first_contact < ball_distance:
                        ball_distance, intercepted = first_contact, True
            endpoint = {"x": ball["x"] + nx * ball_distance,
                        "y": ball["y"] + ny * ball_distance}
            goal_x = FIELD_W - _MARGIN + material["radius"] if side else _MARGIN - material["radius"]
            if not intercepted and nx and _progress(endpoint["x"], side) > _progress(goal_x, side):
                crossing_y = ball["y"] + (goal_x - ball["x"]) * ny / nx
                if GOAL_Y1 + material["radius"] < crossing_y < GOAL_Y2 - material["radius"]:
                    scored = "A" if side else "B"
            endpoint["x"] = max(_MARGIN + material["radius"], min(FIELD_W - _MARGIN - material["radius"], endpoint["x"]))
            endpoint["y"] = max(_MARGIN + material["radius"], min(FIELD_H - _MARGIN - material["radius"], endpoint["y"]))
            # The pawn loses forward momentum on contact; this estimate is only
            # used for ordering, not as an authoritative successor state.
            pawn_end = {"x": cx, "y": cy}
    own = [dict(p) for p in players]
    own[index] = {**own[index], **pawn_end}
    endpoint["a" if side else "b"] = own
    return endpoint, scored


def evaluate_outcome(state, side, move, trajectory, scored):
    """Score progress, safe possession and useful support from real end positions."""
    target = "A" if side else "B"
    if scored:
        return 10000.0 if scored == target else -10000.0
    if not trajectory:
        return -10000.0
    end = trajectory[-1]
    own = state["players_a" if side else "players_b"]
    opponents = state["players_b" if side else "players_a"]
    own_end = end.get("a" if side else "b", own)
    opponent_end = end.get("b" if side else "a", opponents)
    index = move[0]
    progress = _progress(end["x"], side)
    initial_progress = _progress(state["ball"]["x"], side)
    value = (progress - initial_progress) * 0.85
    own_distance = min((_distance(p, end) for p in own_end), default=FIELD_W)
    opponent_distance = min((_distance(p, end) for p in opponent_end), default=FIELD_W)
    # Possession matters especially when a teammate is ready to receive, rather
    # than merely rewarding how far an uncontrolled ball travelled.
    value += max(-90.0, min(90.0, (opponent_distance - own_distance) * 0.45))
    receivers = [p for i, p in enumerate(own_end) if i != index and i != 0]
    if receivers and own_distance < 100.0:
        receiver_distance = min(_distance(p, end) for p in receivers)
        value += max(0.0, 70.0 - receiver_distance) * 0.8
    danger = max(0.0, FIELD_W * 0.2 - progress)
    value -= danger * 0.7
    goal_distance = math.hypot(FIELD_W - progress, end["y"] - (GOAL_Y1 + GOAL_Y2) / 2)
    value += max(0.0, 320.0 - goal_distance) * 0.15
    initial_distance = min((_distance(p, state["ball"]) for p in own), default=FIELD_W)
    value += max(-60.0, min(60.0, (initial_distance - own_distance) * 0.25))
    if index < len(own_end):
        other = [p for i, p in enumerate(own_end) if i != index]
        if other:
            spacing = min(_distance(own_end[index], p) for p in other)
            value -= max(0.0, 55.0 - spacing) * 0.7
        if index == 0 and len(own) > 1 and not needs_clear(state, side):
            value -= 70.0
    return value


def tactical_candidates(state, side, allowed_players=None, max_candidates=8):
    """Return a small ordered shortlist of legal shots, passes, clears or support.

    ``allowed_players`` restricts who may kick (for captain-only modes). Reach
    uses the selected pawn's actual stopping distance, size and keeper style.
    """
    players = state["players_a" if side else "players_b"]
    opponents = state["players_b" if side else "players_a"]
    allowed = _indices(players, allowed_players)
    if not allowed:
        raise ValueError("No eligible player can kick")
    cap = max(0.0, min(100.0, float(state.get("power_cap", 100))))
    material = _ball_physics(state)
    # These material/stat values do not change during a decision. Reuse them
    # across all lane estimates instead of remapping every pawn for every ray.
    profiles = {
        roster_side: [_profile(state, roster_side, i, material)
                      for i in range(len(state["players_a" if roster_side else "players_b"]))]
        for roster_side in (True, False)
    }
    ball = state["ball"]
    defensive = needs_clear(state, side)
    distances, reachable = {}, []
    for i in allowed:
        contact, resistance, velocity, _ = profiles[side][i]
        distance = _distance(players[i], ball)
        distances[i] = max(0.0, distance - contact)
        if distances[i] <= (cap * velocity) ** 2 / (2.0 * resistance):
            reachable.append(i)
    outfield = [i for i in reachable if i != 0]
    if outfield and not defensive:
        reachable = outfield
    reachable.sort(key=lambda i: (
        _progress(players[i]["x"], side) > _progress(ball["x"], side) + 15.0,
        round(distances[i], 8), i,
    ))
    ranked, seen = [], set()

    def add(i, angle, power, bonus=0.0):
        move = (i, angle % 360.0, max(0.0, min(cap, power)))
        key = (i, round(move[1], 1), round(move[2], 1))
        if key in seen:
            return
        seen.add(key)
        endpoint, goal = _estimate(state, side, move, material, profiles)
        if goal and _lane_blocked(ball, endpoint, opponents, material):
            goal = None
            bonus -= 170.0
        value = evaluate_outcome(state, side, move, [endpoint], goal) + bonus
        # Resolve genuinely equivalent mirrored moves by insertion order, not
        # by insignificant trigonometric floating-point noise.
        ranked.append((round(value, 8), len(ranked), move))

    if reachable:
        for i in reachable[:3]:
            player = players[i]
            contact, resistance, velocity, mass = profiles[side][i]
            gx = FIELD_W + 30.0 if side else -30.0
            goals = [(gx, (GOAL_Y1 + GOAL_Y2) / 2),
                     (gx, GOAL_Y1 + material["radius"] + 12.0),
                     (gx, GOAL_Y2 - material["radius"] - 12.0)]
            for tx, ty in goals:
                add(i, aim_through(player["x"], player["y"], ball["x"], ball["y"], tx, ty, contact), cap)
            # A direct contact is robust when oblique target estimates disagree
            # with actual collision geometry.
            add(i, math.degrees(math.atan2(ball["y"] - player["y"], ball["x"] - player["x"])), cap, 2.0)
            receivers = []
            for j, receiver in enumerate(players):
                if j in (i, 0) or _distance(receiver, ball) < 45.0:
                    continue
                forward = _progress(receiver["x"], side) - _progress(ball["x"], side)
                if forward < -80.0 and not defensive:
                    continue
                if _lane_blocked(ball, receiver, opponents, material):
                    continue
                pressure = min((_distance(receiver, p) for p in opponents), default=400.0)
                receivers.append((forward * 0.2 + min(150.0, pressure) * 0.4, j, receiver))
            receivers.sort(key=lambda item: (-item[0], item[1]))
            for _, j, receiver in receivers[:2]:
                travel = _distance(receiver, ball)
                transfer = (1.0 + _PM_ELASTICITY_P * material["restitution"]) * mass / (mass + material["mass"])
                angle = aim_through(player["x"], player["y"], ball["x"], ball["y"], receiver["x"], receiver["y"], contact)
                geometry = _impact_geometry(player, ball, angle, contact)
                if not geometry or geometry[-1] < 0.15:
                    continue
                impact_speed = math.sqrt(2.0 * material["rolling_deceleration"] * travel) / (transfer * geometry[-1] * _RALLY_SPEED_BOOST)
                power = math.sqrt(impact_speed * impact_speed + 2.0 * resistance * geometry[0]) / velocity
                add(i, angle, power, 25.0)
            if defensive:
                # Clear toward the open flank rather than through the goal mouth.
                for ty in (FIELD_H * 0.2, FIELD_H * 0.8):
                    tx = ball["x"] + (450.0 if side else -450.0)
                    target = {"x": tx, "y": ty}
                    blocked = _lane_blocked(ball, target, opponents, material)
                    add(i, aim_through(player["x"], player["y"], ball["x"], ball["y"], tx, ty, contact), cap, -40.0 if blocked else 35.0)
    else:
        # No pawn can reach the ball this turn: make a useful legal approach
        # without sacrificing the keeper whenever an outfielder is available.
        support = [i for i in allowed if i != 0] or allowed
        support.sort(key=lambda i: (round(distances[i], 8), i))
        for i in support[:3]:
            player = players[i]
            for offset in (0.0, -45.0, 45.0):
                target_y = max(_MARGIN + 40.0, min(FIELD_H - _MARGIN - 40.0, ball["y"] + offset))
                angle = math.degrees(math.atan2(target_y - player["y"], ball["x"] - player["x"]))
                add(i, angle, cap)
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [move for _, _, move in ranked[:max(1, int(max_candidates))]]


def quick_move(state, side, allowed_players=None):
    """Choose immediately without speculative physics or background work."""
    return tactical_candidates(state, side, allowed_players, max_candidates=1)[0]
