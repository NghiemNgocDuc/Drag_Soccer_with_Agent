"""Bounded simultaneous support runs using the kick's existing physics world.

Each AI pawn gets at most one launch impulse. Its ordinary mass, friction and
collisions govern the whole run; no teleports, continuous motors or extra sims.
"""
from __future__ import annotations

import math

from models.soccer_logic import (
    FIELD_W, FIELD_H, _MARGIN,
    _ball_physics, _get_player_stats, _get_player_radius,
    _get_player_friction, _get_player_kick_vel, _keeper_radius_bonus,
    _keeper_rush_mult, human_player_index, team_movement_enabled,
)
from models.team_support import distance, path_clear
from models.team_roles import role_target

MAX_SUPPORT_TRAVEL = 120.0
MAX_KEEPER_TRAVEL = 45.0


def support_moves(state, kicker_idx, angle, power, kicking_side):
    """Plan both teams in O(players²), with at most three lanes per pawn."""
    if not team_movement_enabled(state) or power <= 0:
        return []
    cap = max(0.0, min(100.0, float(state.get('power_cap', 100))))
    ball = state['ball']
    material = _ball_physics(state)
    rosters = {True: state['players_a'], False: state['players_b']}
    profiles, circles = {}, {}
    for side, roster in rosters.items():
        for index, player in enumerate(roster):
            stats = _get_player_stats(state, side, index)
            radius, braking = _get_player_radius(stats), _get_player_friction(stats)
            if index == 0:
                radius += _keeper_radius_bonus(state, side)
                braking *= _keeper_rush_mult(state, side)
            profiles[side, index] = radius, braking, _get_player_kick_vel(stats)
            circles[side, index] = {**player, 'radius': radius}

    # Anticipate a little of the play only if the selected disc is approaching
    # the ball. A missed kick cannot drag the entire formation downfield.
    kicker = rosters[kicking_side][kicker_idx]
    ux, uy = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    dx, dy = ball['x'] - kicker['x'], ball['y'] - kicker['y']
    radius, braking, velocity = profiles[kicking_side, kicker_idx]
    approach = dx * ux + dy * uy
    reach = (power * velocity) ** 2 / (2 * braking)
    contact = radius + material['radius']
    anticipating = 0 <= approach <= reach + contact and abs(dx * uy - dy * ux) < contact
    lead = min(120.0, power * 1.2) if anticipating else 0.0
    focus = {'x': max(80.0, min(FIELD_W - 80.0, ball['x'] + ux * lead)),
             'y': max(80.0, min(FIELD_H - 80.0, ball['y'] + uy * lead))}
    moves, destinations = [], []
    # Acting side first makes planning symmetric when teams/field are mirrored.
    for side in (kicking_side, not kicking_side):
        roster = rosters[side]
        if len(roster) < 2:
            continue
        for index, player in enumerate(roster):
            if (side == kicking_side and index == kicker_idx) or (
                side and state.get('game_mode') == 'hvai' and index == human_player_index(state)):
                continue
            radius, braking, velocity = profiles[side, index]
            destination = role_target(state, side, index, focus, radius=radius)
            max_travel = MAX_KEEPER_TRAVEL if index == 0 else MAX_SUPPORT_TRAVEL
            obstacles = ([p for key, p in circles.items() if key != (side, index)]
                         + [{'x': ball['x'], 'y': ball['y'], 'radius': material['radius']}]
                         + destinations)
            reach = min(max_travel, (cap * velocity) ** 2 / (2 * braking))
            for sidestep in ((0,) if index == 0 else (0, -65, 65)):
                target = {'x': max(_MARGIN + radius + 8, min(FIELD_W - _MARGIN - radius - 8, destination['x'])),
                          'y': max(_MARGIN + radius + 8, min(FIELD_H - _MARGIN - radius - 8, destination['y'] + sidestep))}
                travel = distance(player, target)
                if travel < 8 or reach < 8:
                    continue
                fraction = min(1.0, reach / travel)
                endpoint = {axis: player[axis] + (target[axis] - player[axis]) * fraction for axis in ('x', 'y')}
                if not path_clear(player, endpoint, obstacles, radius):
                    continue
                heading = math.degrees(math.atan2(endpoint['y'] - player['y'], endpoint['x'] - player['x'])) % 360
                strength = min(cap, math.sqrt(2 * braking * distance(player, endpoint)) / velocity)
                moves.append((side, index, heading, strength))
                destinations.append({**endpoint, 'radius': radius})
                break
    return moves
