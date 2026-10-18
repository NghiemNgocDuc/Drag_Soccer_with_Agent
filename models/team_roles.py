"""Stable formation jobs and cheap positional targets for each roster slot."""
from functools import lru_cache

from models.playstyle import playstyle_level
from models.soccer_logic import FIELD_W, FIELD_H, GOAL_Y1, GOAL_Y2, _home_positions


@lru_cache(maxsize=64)
def _layout(count, formation):
    # Derive jobs from the starting formation, never from live positions or
    # possession. Collisions, goals and turn changes cannot swap players' jobs.
    home = _home_positions(count, 'a', formation)
    roles = []
    for index, (x, _) in enumerate(home):
        if index == 0:
            role = 'goalkeeper'
        elif count == 2 or x >= FIELD_W / 2 - 95 - .01:
            role = 'attacker'
        elif x <= FIELD_W * .162 + .01:
            role = 'defender'
        else:
            role = 'support'
        roles.append(role)
    return tuple(roles), tuple(home)


def team_roles(state, side):
    """Return stable jobs in jersey order; five-a-side is GK, DEF, DEF, MID, FWD."""
    roster = state['players_a' if side else 'players_b']
    formation = _formation(state, side)
    return _layout(len(roster), formation)[0]


def _formation(state, side):
    formation = state.get('formation_a' if side else 'formation_b')
    return formation if isinstance(formation, str) else None


def role_target(state, side, index, focus, *, radius=40.0):
    """A role keeps its zone regardless of which team is taking the kick.

    Targets describe intentions. The movement planner still checks the swept
    path, limits travel and launches an ordinary physical impulse.
    """
    roster = state['players_a' if side else 'players_b']
    roles, home = _layout(len(roster), _formation(state, side))
    role = roles[index]
    progress = focus['x'] if side else FIELD_W - focus['x']
    across = focus['y'] - FIELD_H / 2
    lane = (home[index][1] - FIELD_H / 2) * 2.4
    if role == 'goalkeeper':
        depth = max(85.0, min(115.0, 85.0 + progress * .025))
        target_y = max(GOAL_Y1 + radius, min(GOAL_Y2 - radius, FIELD_H / 2 + across * .35))
    elif role == 'defender':
        # Both defenders protect their own half, each in a separate lane.
        depth = max(150.0, min(FIELD_W * .44, progress - 240.0))
        target_y = FIELD_H / 2 + lane + across * .18
    elif role == 'support':
        # Offer a short outlet beside the play, leaving the central shot lane.
        depth = max(FIELD_W * .28, min(FIELD_W * .70, progress - 50.0))
        if abs(lane) < 10:
            lane = -135.0 if index % 2 else 135.0
        target_y = FIELD_H / 2 + lane + across * .30
    else:
        # Stay available upfield even on the other team's kick.
        depth = max(FIELD_W * .50, min(FIELD_W - 120.0, progress + 180.0))
        if abs(lane) < 10:
            lane = -85.0 if index % 2 else 85.0
        target_y = FIELD_H / 2 + lane + across * .20
    mentality = playstyle_level(state, side) - 3
    if mentality and role != 'goalkeeper':
        # Change the team's line and width, never a player's stable job. The
        # balanced path above is unchanged, including its exact old targets.
        if role == 'defender':
            depth = max(125.0, min(FIELD_W * .49, depth + mentality * 35.0))
        elif role == 'support':
            depth = max(FIELD_W * .22, min(FIELD_W * .78, depth + mentality * 50.0))
        else:
            depth = max(FIELD_W * .42, min(FIELD_W - 90.0, depth + mentality * 55.0))
        target_y = FIELD_H / 2 + (target_y - FIELD_H / 2) * (1.0 + mentality * .08)
    return {'x': depth if side else FIELD_W - depth, 'y': target_y}
