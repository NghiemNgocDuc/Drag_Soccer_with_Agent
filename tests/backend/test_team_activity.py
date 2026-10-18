"""Coordinated support must move real pawns on the existing kick timeline."""
from copy import deepcopy
import math

import pytest

from models.soccer_logic import (
    FIELD_W, _setup_penalty_positions, apply_kick, new_soccer_state, simulate_kick,
    team_movement_enabled,
)


def activity_state(mode='aivai'):
    state = new_soccer_state(mode=mode, player_count=5, half_length=0)
    for key, positions in (
        ('players_a', [(90, 480), (350, 150), (350, 700), (600, 200), (645, 437.5)]),
        ('players_b', [(1310, 480), (1050, 150), (1050, 700), (800, 200), (755, 437.5)]),
    ):
        for player, (x, y) in zip(state[key], positions):
            player.update(x=x, y=y)
    return state


def distance(before, after):
    return math.hypot(before['x'] - after['x'], before['y'] - after['y'])


def mirrored(state):
    result = deepcopy(state)
    result['ball']['x'] = FIELD_W - state['ball']['x']
    for own, other in (('players_a', 'players_b'), ('players_b', 'players_a')):
        result[own] = [{**p, 'x': FIELD_W - p['x']} for p in state[other]]
    return result


@pytest.mark.parametrize('side', [True, False])
def test_both_teams_reposition_on_the_same_real_physics_timeline(side):
    state = activity_state() if side else mirrored(activity_state())
    before = deepcopy(state)

    path, goal = simulate_kick(state, 4, 0 if side else 180, 40, side)

    assert goal is None
    assert len(path) > 2
    for key, track in (('players_a', 'a'), ('players_b', 'b')):
        assert len(path[0][track]) == len(path[-1][track]) == 5
        assert all(distance(p, path[0][track][i]) < .2 for i, p in enumerate(state[key]))
        # Support is actual movement of several pawns, not a rendered wobble.
        moved = [i for i, p in enumerate(state[key]) if distance(p, path[-1][track][i]) > 16]
        assert len(moved) >= 3
        assert any(sum(distance(state[key][i], pt[track][i]) > 2 for i in moved) >= 3
                   for pt in path[1:-1])
    assert state == before


def test_prediction_execution_and_authoritative_rosters_agree():
    state = activity_state()
    before = deepcopy(state)

    predicted, expected_goal = simulate_kick(state, 4, 0, 40, True)
    repeated, repeated_goal = simulate_kick(state, 4, 0, 40, True)
    assert state == before
    actual, goal, _, _, _ = apply_kick(state, 4, 0, 40, True)

    assert predicted == repeated == actual
    assert expected_goal == repeated_goal == goal is None
    for key, track in (('players_a', 'a'), ('players_b', 'b')):
        for player, endpoint in zip(state[key], actual[-1][track]):
            assert player['x'] == endpoint['x']
            assert player['y'] == endpoint['y']
    assert state['kick_count'] == 1
    assert not state['is_player_a']
    assert len(state['move_history']) == 1


def test_team_motion_uses_one_shared_physics_world(monkeypatch):
    import pymunk
    space_class = pymunk.Space
    spaces = []

    def counted(*args, **kwargs):
        space = space_class(*args, **kwargs)
        spaces.append(space)
        return space

    monkeypatch.setattr(pymunk, 'Space', counted)
    state = activity_state()
    simulate_kick(state, 4, 0, 40, True)
    assert len(spaces) == 1
    apply_kick(state, 4, 0, 40, True)
    assert len(spaces) == 2


@pytest.mark.parametrize('mode,enabled', [('hvai', True), ('aivai', True), ('hvh', False)])
def test_defaults_preserve_human_control_and_explicit_opt_out(mode, enabled):
    state = new_soccer_state(mode=mode, half_length=0)
    assert team_movement_enabled(state) is enabled
    disabled = new_soccer_state(mode=mode, half_length=0, team_movement=False)
    assert not team_movement_enabled(disabled)
    disabled['team_movement'] = True
    disabled['penalty_shootout'] = True
    assert not team_movement_enabled(disabled)


@pytest.mark.parametrize('opt_out', [True, False])
def test_opt_out_and_human_hotseat_keep_other_discs_stationary(opt_out):
    state = activity_state(mode='aivai' if opt_out else 'hvh')
    if opt_out:
        state['team_movement'] = False

    path, _ = simulate_kick(state, 1, 0, 40, True)

    for key, track in (('players_a', 'a'), ('players_b', 'b')):
        for index, player in enumerate(state[key]):
            if key == 'players_a' and index == 1:
                assert distance(player, path[-1][track][index]) > 16
            else:
                assert all(distance(player, frame[track][index]) < .2 for frame in path)


def test_zero_power_does_not_move_the_teams_for_free():
    state = activity_state()

    path, _ = simulate_kick(state, 4, 0, 0, True)

    for key, track in (('players_a', 'a'), ('players_b', 'b')):
        assert all(distance(player, frame[track][index]) < .2
                   for frame in path for index, player in enumerate(state[key]))


def test_support_runs_and_keepers_have_bounded_physical_travel():
    state = activity_state()

    path, _ = simulate_kick(state, 4, 0, 40, True)

    for key, track in (('players_a', 'a'), ('players_b', 'b')):
        # These players have clear lanes and no ball contacts in this fixture.
        assert 16 < distance(state[key][0], path[-1][track][0]) < 47
        for index in (1, 2, 3):
            assert 16 < distance(state[key][index], path[-1][track][index]) < 123


def test_support_respects_low_power_and_high_agility_stats():
    from models.team_movement import support_moves
    state = activity_state()
    state['power_cap'] = 30
    state['players_a'][1]['stats'] = dict(size=100, power=0, weight=50, agility=100)

    moves = support_moves(state, 4, 0, 30, True)
    path, _ = simulate_kick(state, 4, 0, 30, True)

    assert any(side and index == 1 for side, index, *_ in moves)
    assert all(0 < strength <= 30 for _, _, _, strength in moves)
    assert 8 < distance(state['players_a'][1], path[-1]['a'][1]) < 13


def test_prediction_budget_keeps_exact_outcomes_and_rechecks_movement_setting():
    from models.search_budget import SearchBudget, _current
    state = activity_state()
    search = SearchBudget(state, True, [4], budget_s=5, max_simulations=4)
    token = _current.set(search)
    try:
        predicted, goal = simulate_kick(state, 4, 0, 40, True)
        cached, cached_goal = simulate_kick(state, 4, 0, 40, True)
        assert cached == predicted and cached_goal == goal
        assert search.simulations == 1 and search.cache_hits == 1
        state['team_movement'] = False
        solo, _ = simulate_kick(state, 4, 0, 40, True)
        assert search.simulations == 2
        assert solo[-1]['a'][1] != predicted[-1]['a'][1]
    finally:
        _current.reset(token)

    state['team_movement'] = True
    actual, actual_goal, _, _, _ = apply_kick(state, 4, 0, 40, True)
    assert actual_goal == goal
    for key in ('x', 'y', 'z', 'a', 'b'):
        assert actual[-1][key] == predicted[-1][key]


def test_support_motion_is_symmetric_when_attacking_direction_is_reversed():
    state = activity_state()
    reflected = mirrored(state)

    original, goal = simulate_kick(state, 4, 0, 40, True)
    reverse, other_goal = simulate_kick(reflected, 4, 180, 40, False)

    assert goal is other_goal is None
    assert original[-1]['x'] == pytest.approx(FIELD_W - reverse[-1]['x'], abs=.2)
    for own, other in (('a', 'b'), ('b', 'a')):
        for p, q in zip(original[-1][own], reverse[-1][other]):
            assert p['x'] == pytest.approx(FIELD_W - q['x'], abs=.2)
            assert p['y'] == pytest.approx(q['y'], abs=.2)


def test_human_striker_is_not_automatically_launched_on_teammate_turn():
    state = activity_state(mode='hvai')
    original = dict(state['players_a'][4])

    path, _ = simulate_kick(state, 1, 0, 40, True)

    assert all(distance(original, frame['a'][4]) < .2 for frame in path)
    assert any(distance(state['players_a'][i], path[-1]['a'][i]) > 16 for i in (0, 2, 3))


@pytest.mark.parametrize('side', [True, False])
def test_penalties_keep_outfield_players_stationary(side):
    state = activity_state()
    state['penalty_shootout'] = True
    _setup_penalty_positions(state, side)
    state['penalty_goalkeeper_move'] = 'left'

    path, _ = simulate_kick(state, 0, 0 if side else 180, 60, side)

    for key, track in (('players_a', 'a'), ('players_b', 'b')):
        assert all(distance(state[key][i], point[track][i]) < .2
                   for point in path for i in range(1, 5))


def test_undo_restores_the_whole_moving_roster(monkeypatch):
    import app as application
    from game.session import push_snapshot

    state = activity_state()
    before = deepcopy(state)
    push_snapshot(state)
    apply_kick(state, 4, 0, 40, True)
    monkeypatch.setattr(application, 'get_game', lambda _user: state)
    monkeypatch.setattr(application, 'save_game', lambda *_args: None)
    monkeypatch.setattr(application, '_auto_save_state', lambda *_args: None)

    with application.app.test_client() as client:
        with client.session_transaction() as session:
            session['user_id'] = 'dev:activity-undo'
        response = client.post('/undo')

    assert response.status_code == 200
    assert response.get_json()['undone']
    assert state['players_a'] == before['players_a']
    assert state['players_b'] == before['players_b']
    assert state['ball'] == before['ball']
    assert state['kick_count'] == before['kick_count']
