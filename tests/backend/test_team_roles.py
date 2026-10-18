"""Player jobs stay stable while the whole team reacts to the play."""
from copy import deepcopy
import math

import pytest

from models.soccer_logic import (
    FIELD_W, FIELD_H, GOAL_Y1, GOAL_Y2, _reset_players, _reset_outfield,
    apply_kick, new_soccer_state, simulate_kick,
)
from models.team_movement import support_moves
from models.team_roles import role_target, team_roles


FIVE_ROLES = ('goalkeeper', 'defender', 'defender', 'support', 'attacker')


def role_state(ball_x=1200, mode='aivai'):
    state = new_soccer_state(mode=mode, player_count=5, half_length=0)
    state['ball'].update(x=ball_x, y=437.5)
    for key, positions in (
        ('players_a', [(90, 480), (800, 300), (800, 600), (600, 200), (500, 730)]),
        ('players_b', [(1310, 480), (1200, 150), (1200, 750), (1100, 200), (650, 650)]),
    ):
        for player, (x, y) in zip(state[key], positions):
            player.update(x=x, y=y)
    return state


def mirrored(state):
    result = deepcopy(state)
    result['ball']['x'] = FIELD_W - state['ball']['x']
    for own, other in (('a', 'b'), ('b', 'a')):
        result[f'players_{own}'] = [
            {**player, 'x': FIELD_W - player['x']} for player in state[f'players_{other}']
        ]
        result[f'formation_{own}'] = state.get(f'formation_{other}')
    return result


def distance(a, b):
    return math.hypot(a['x'] - b['x'], a['y'] - b['y'])


@pytest.mark.parametrize('count,expected', [
    (1, ('goalkeeper',)),
    (2, ('goalkeeper', 'attacker')),
    (3, ('goalkeeper', 'defender', 'attacker')),
    (5, FIVE_ROLES),
    (11, ('goalkeeper',) + ('defender',) * 4 + ('support',) * 4 + ('attacker',) * 2),
])
def test_default_jobs_match_roster_slots_in_small_and_large_squads(count, expected):
    state = new_soccer_state(player_count=count, half_length=0)
    assert team_roles(state, True) == expected
    assert team_roles(state, False) == expected


@pytest.mark.parametrize('formation,expected', [
    ('2-3-1', ('goalkeeper',) + ('defender',) * 2 + ('support',) * 3 + ('attacker',)),
    ('3-2-1', ('goalkeeper',) + ('defender',) * 3 + ('support',) * 2 + ('attacker',)),
    ('2-1-2-1', ('goalkeeper',) + ('defender',) * 2 + ('support',) * 3 + ('attacker',)),
    ('3-3-0', ('goalkeeper',) + ('defender',) * 3 + ('support',) * 3),
])
def test_custom_formations_keep_their_defensive_midfield_and_forward_rows(formation, expected):
    state = new_soccer_state(player_count=7, formation_a=formation, formation_b=formation,
                             half_length=0)
    assert team_roles(state, True) == team_roles(state, False) == expected


def test_roles_follow_each_actual_roster_and_its_own_formation():
    state = new_soccer_state(player_count=7, formation_a='3-3-0', formation_b='1-2-3',
                             half_length=0)
    assert team_roles(state, True).count('attacker') == 0
    assert team_roles(state, False).count('attacker') == 3
    state['players_a'] = state['players_a'][:2]
    assert state['player_count'] == 7  # Legacy metadata need not match both rosters.
    assert team_roles(state, True) == ('goalkeeper', 'attacker')
    assert len(team_roles(state, False)) == 7


def test_positions_turn_changes_and_resets_cannot_swap_jobs():
    state = role_state()
    for key in ('players_a', 'players_b'):
        locations = [(p['x'], p['y']) for p in reversed(state[key])]
        for player, (x, y) in zip(state[key], locations):
            player.update(x=x, y=y)
    state['is_player_a'] = False
    assert team_roles(state, True) == team_roles(state, False) == FIVE_ROLES

    apply_kick(state, 3, 270, 30, True)
    assert team_roles(state, True) == team_roles(state, False) == FIVE_ROLES
    _reset_outfield(state, 'a')
    assert team_roles(state, True) == FIVE_ROLES
    _reset_players(state)
    assert team_roles(state, True) == team_roles(state, False) == FIVE_ROLES


@pytest.mark.parametrize('progress', [50, 700, 1350])
@pytest.mark.parametrize('side', [True, False])
def test_roles_keep_distinct_depth_zones_in_attack_and_defense(progress, side):
    state = role_state()
    focus = {'x': progress if side else FIELD_W - progress, 'y': FIELD_H / 2}
    targets = [role_target(state, side, index, focus) for index in range(5)]
    depths = [p['x'] if side else FIELD_W - p['x'] for p in targets]

    assert 85 <= depths[0] <= 115
    assert all(150 <= depths[index] <= FIELD_W * .44 for index in (1, 2))
    assert FIELD_W * .28 - 1e-8 <= depths[3] <= FIELD_W * .70 + 1e-8
    assert FIELD_W * .50 <= depths[4] <= FIELD_W - 120
    assert targets[1]['y'] < targets[2]['y']
    assert abs(targets[3]['y'] - focus['y']) >= 100


@pytest.mark.parametrize('count,formation', [(1, None), (2, None), (5, None), (7, '2-1-2-1')])
def test_role_targets_mirror_without_changing_jobs_or_turn_preferences(count, formation):
    state = new_soccer_state(mode='aivai', player_count=count, formation_a=formation,
                             formation_b=formation, half_length=0)
    reflected = mirrored(state)
    focus = {'x': 1090, 'y': 170}
    other_focus = {**focus, 'x': FIELD_W - focus['x']}

    for index in range(count):
        target = role_target(state, True, index, focus)
        opposite = role_target(reflected, False, index, other_focus)
        assert target['x'] == pytest.approx(FIELD_W - opposite['x'])
        assert target['y'] == pytest.approx(opposite['y'])
        state['is_player_a'] = not state['is_player_a']
        assert role_target(state, True, index, focus) == target


@pytest.mark.parametrize('side', [True, False])
@pytest.mark.parametrize('across', [0, FIELD_H])
def test_keeper_role_tracks_the_ball_inside_the_goal_mouth(side, across):
    state = role_state()
    target = role_target(state, side, 0, {'x': FIELD_W / 2, 'y': across}, radius=35)
    assert GOAL_Y1 + 35 <= target['y'] <= GOAL_Y2 - 35
    assert 85 <= (target['x'] if side else FIELD_W - target['x']) <= 115


@pytest.mark.parametrize('ball_x', [200, 1200])
@pytest.mark.parametrize('side', [True, False])
@pytest.mark.parametrize('own_kick', [True, False])
def test_real_support_motion_recovers_defenders_and_keeps_attacker_upfield(ball_x, side, own_kick):
    state = role_state(ball_x)
    if not side:
        state = mirrored(state)
    before = deepcopy(state)
    own, track = ('players_a', 'a') if side else ('players_b', 'b')

    path, scored = simulate_kick(state, 3, 270, 30, side if own_kick else not side)

    assert scored is None
    for index in (1, 2):
        assert (path[-1][track][index]['x'] - state[own][index]['x']) * (1 if side else -1) < -50
    assert (path[-1][track][4]['x'] - state[own][4]['x']) * (1 if side else -1) > 50
    assert path[-1]['x'] == pytest.approx(state['ball']['x'])
    assert path[-1]['y'] == pytest.approx(state['ball']['y'])
    assert state == before


def test_role_planning_stays_analytic_and_does_not_mutate_the_match(monkeypatch):
    import pymunk
    state = role_state()
    before = deepcopy(state)

    def forbidden(*_args, **_kwargs):
        raise AssertionError('Role planning must not create a physics world')

    monkeypatch.setattr(pymunk, 'Space', forbidden)
    moves = support_moves(state, 3, 270, 30, True)
    assert any(side and index == 1 for side, index, *_ in moves)
    assert any(side and index == 4 for side, index, *_ in moves)
    assert state == before


def test_manual_striker_keeps_its_job_and_is_not_automatically_launched():
    state = role_state(mode='hvai')
    before = deepcopy(state['players_a'][4])

    moves = support_moves(state, 3, 270, 30, True)
    path, _ = simulate_kick(state, 3, 270, 30, True)

    assert team_roles(state, True)[4] == 'attacker'
    assert not any(side and index == 4 for side, index, *_ in moves)
    assert all(distance(before, frame['a'][4]) < .2 for frame in path)


def test_manual_striker_launch_still_uses_the_players_requested_angle():
    state = role_state(mode='hvai')
    original = deepcopy(state['players_a'][4])

    path, _ = simulate_kick(state, 4, 180, 35, True)

    assert path[-1]['a'][4]['x'] < original['x'] - 20
    assert path[-1]['a'][4]['y'] == pytest.approx(original['y'], abs=.2)
    assert team_roles(state, True)[4] == 'attacker'


@pytest.mark.parametrize('formation', [{'bad': 1}, ['3-2-1'], 42])
@pytest.mark.parametrize('count', [5, 7])
def test_legacy_invalid_formation_metadata_cannot_break_state_or_prediction(formation, count):
    import app as application
    from models.search_budget import SearchBudget, _current

    state = new_soccer_state(mode='aivai', player_count=count, half_length=0)
    expected_a, expected_b = team_roles(state, True), team_roles(state, False)
    state['formation_a'] = deepcopy(formation)
    state['formation_b'] = deepcopy(formation)

    with application.app.test_request_context('/state'):
        result = application._full_state(state)
        assert result['player_roles_a'] == expected_a
        assert result['player_roles_b'] == expected_b

    search = SearchBudget(state, True, [count - 1], budget_s=5, max_simulations=2)
    token = _current.set(search)
    try:
        predicted = simulate_kick(state, count - 1, 90, 30, True)
        cached = simulate_kick(state, count - 1, 90, 30, True)
        assert predicted == cached
        assert search.simulations == 1 and search.cache_hits == 1
    finally:
        _current.reset(token)

    _reset_players(state)
    assert team_roles(state, True) == expected_a
    assert team_roles(state, False) == expected_b
