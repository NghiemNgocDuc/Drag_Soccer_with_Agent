"""Five team mentalities change real play without changing jobs or controls."""
from copy import deepcopy
from types import SimpleNamespace
import math

import pytest

from models.playstyle import playstyle_level
from models.soccer_logic import FIELD_W, FIELD_H, new_soccer_state, simulate_kick
from models.team_movement import support_moves
from models.team_roles import role_target, team_roles
from models.tactics import evaluate_outcome


def playstyle_state():
    state = new_soccer_state(mode='aivai', player_count=5, half_length=0)
    for key, positions in (
        ('players_a', [(90, 480), (420, 180), (420, 700), (600, 300), (860, 550)]),
        ('players_b', [(1310, 480), (980, 180), (980, 700), (800, 300), (540, 550)]),
    ):
        for player, (x, y) in zip(state[key], positions):
            player.update(x=x, y=y)
    return state


def mirrored(state):
    result = deepcopy(state)
    result['ball']['x'] = FIELD_W - state['ball']['x']
    for side, other in (('a', 'b'), ('b', 'a')):
        result[f'players_{side}'] = [{**p, 'x': FIELD_W - p['x']} for p in state[f'players_{other}']]
        result[f'playstyle_{side}'] = state.get(f'playstyle_{other}', 3)
        result[f'formation_{side}'] = state.get(f'formation_{other}')
    return result


def distance(a, b):
    return math.hypot(a['x'] - b['x'], a['y'] - b['y'])


@pytest.mark.parametrize('value', [None, True, False, 0, 6, -1, '2', 2.0, [], {}])
def test_invalid_saved_values_fall_back_to_balanced(value):
    state = {'playstyle_a': value, 'playstyle_b': value}
    assert playstyle_level(state, True) == playstyle_level(state, False) == 3


@pytest.mark.parametrize('level', [1, 2, 3, 4, 5])
def test_valid_levels_are_read_independently_for_each_team(level):
    state = {'playstyle_a': level, 'playstyle_b': 6 - level}
    assert playstyle_level(state, True) == level
    assert playstyle_level(state, False) == 6 - level
    assert playstyle_level({}, True) == playstyle_level({}, False) == 3


def test_balanced_keeps_the_previous_five_player_targets_exactly():
    state = new_soccer_state(mode='aivai', player_count=5, half_length=0)
    assert state['playstyle_a'] == state['playstyle_b'] == 3
    expected = [(102.5, 437.5), (460, 329.5), (460, 545.5), (650, 302.5), (880, 522.5)]
    focus = {'x': 700, 'y': 437.5}
    for index, (x, y) in enumerate(expected):
        target = role_target(state, True, index, focus)
        assert target['x'] == pytest.approx(x)
        assert target['y'] == pytest.approx(y)
    state.pop('playstyle_a')
    assert [role_target(state, True, i, focus) for i in range(5)] == [
        {'x': x, 'y': y} for x, y in expected
    ]


def test_levels_progressively_raise_and_widen_the_team_without_changing_jobs():
    state = playstyle_state()
    roles = team_roles(state, True)
    focus = {'x': 700, 'y': FIELD_H / 2}
    targets = []
    opponents = [role_target(state, False, index, focus) for index in range(5)]
    for level in range(1, 6):
        state['playstyle_a'] = level
        targets.append([role_target(state, True, index, focus) for index in range(5)])
        assert team_roles(state, True) == roles
        assert [role_target(state, False, i, focus) for i in range(5)] == opponents
    assert all(row[0] == targets[0][0] for row in targets)
    for index in range(1, 5):
        depths = [row[index]['x'] for row in targets]
        widths = [abs(row[index]['y'] - FIELD_H / 2) for row in targets]
        assert all(a < b for a, b in zip(depths, depths[1:]))
        assert all(a < b for a, b in zip(widths, widths[1:]))
    state['playstyle_a'] = 5
    for index in (1, 2):
        assert role_target(state, True, index, {'x': 1390, 'y': 437.5})['x'] <= FIELD_W * .49


@pytest.mark.parametrize('level', [1, 3, 5])
def test_playstyle_mirrors_real_support_motion_and_keeps_inputs_unchanged(level):
    state = playstyle_state()
    state.update(playstyle_a=level, playstyle_b=6 - level)
    original = deepcopy(state)

    path, goal = simulate_kick(state, 4, 0, 30, True)
    reverse, reverse_goal = simulate_kick(mirrored(state), 4, 180, 30, False)

    assert goal is reverse_goal is None
    for own, other in (('a', 'b'), ('b', 'a')):
        for index, (p, q) in enumerate(zip(path[-1][own], reverse[-1][other])):
            assert p['x'] == pytest.approx(FIELD_W - q['x'], abs=.2)
            assert p['y'] == pytest.approx(q['y'], abs=.2)
            if not (own == 'a' and index == 4):
                assert distance(state[f'players_{own}'][index], p) < (47 if index == 0 else 123)
    assert state == original


def test_defensive_and_attacking_settings_change_actual_defender_movement():
    state = playstyle_state()
    endpoints = []
    for level in (1, 5):
        state['playstyle_a'] = level
        path, _ = simulate_kick(state, 4, 0, 30, True)
        endpoints.append(path[-1]['a'][1])
    assert endpoints[0]['x'] < state['players_a'][1]['x']
    assert endpoints[1]['x'] > state['players_a'][1]['x']
    assert endpoints[1]['x'] - endpoints[0]['x'] > 40


def test_setting_changes_invalidate_both_teams_prediction_cache():
    from models.search_budget import SearchBudget, _current
    state = playstyle_state()
    search = SearchBudget(state, True, [4], budget_s=5, max_simulations=4)
    token = _current.set(search)
    try:
        balanced = simulate_kick(state, 4, 0, 30, True)
        assert simulate_kick(state, 4, 0, 30, True) == balanced
        assert search.simulations == 1 and search.cache_hits == 1
        state['playstyle_a'] = 1
        defensive = simulate_kick(state, 4, 0, 30, True)
        assert search.simulations == 2
        assert defensive[0][-1]['a'][1] != balanced[0][-1]['a'][1]
        state['playstyle_b'] = 5
        opponent_attack = simulate_kick(state, 4, 0, 30, True)
        assert search.simulations == 3
        assert opponent_attack[0][-1]['b'][1] != defensive[0][-1]['b'][1]
    finally:
        _current.reset(token)


def test_all_levels_plan_without_allocating_a_physics_world(monkeypatch):
    import pymunk
    state = playstyle_state()

    def forbidden(*_args, **_kwargs):
        raise AssertionError('Playstyle planning must remain analytic')

    monkeypatch.setattr(pymunk, 'Space', forbidden)
    for level in range(1, 6):
        state['playstyle_a'] = level
        assert support_moves(state, 4, 0, 30, True)


def test_human_striker_command_is_unchanged_by_team_playstyle():
    state = playstyle_state()
    state['game_mode'] = 'hvai'
    positions = []
    for level in (1, 3, 5):
        state['playstyle_a'] = level
        path, _ = simulate_kick(state, 4, 0, 30, True)
        positions.append(path[-1]['a'][4])
    assert positions[0] == positions[1] == positions[2]


def test_every_style_keeps_simulation_in_one_physics_world(monkeypatch):
    import pymunk
    real_space = pymunk.Space
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return real_space(*args, **kwargs)

    monkeypatch.setattr(pymunk, 'Space', counted)
    for level in (1, 3, 5):
        state = playstyle_state()
        state.update(playstyle_a=level, playstyle_b=6 - level)
        simulate_kick(state, 4, 0, 30, True)
    assert len(calls) == 3


@pytest.mark.parametrize('side', [True, False])
def test_defensive_prefers_safe_reception_and_attacking_accepts_forward_risk(side):
    state = playstyle_state()
    state['players_a'][3].update(x=645, y=437.5)
    state['players_a'][4].update(x=810, y=350)
    for player, (x, y) in zip(state['players_b'],
                              [(1310, 480), (1200, 150), (1200, 750), (1000, 437.5), (1100, 650)]):
        player.update(x=x, y=y)
    if not side:
        state = mirrored(state)
    safe = {'x': 800 if side else FIELD_W - 800, 'y': 350,
            'a': deepcopy(state['players_a']), 'b': deepcopy(state['players_b'])}
    forward = {**safe, 'x': 1100 if side else FIELD_W - 1100, 'y': 437.5}
    preference = []
    move = (3, 0 if side else 180, 70)

    for level in range(1, 6):
        state['playstyle_a' if side else 'playstyle_b'] = level
        safe_value = evaluate_outcome(state, side, move, [safe], None)
        forward_value = evaluate_outcome(state, side, move, [forward], None)
        preference.append(forward_value - safe_value)
        assert evaluate_outcome(state, side, move, [forward], 'A' if side else 'B') == 10000
        assert evaluate_outcome(state, side, move, [forward], 'B' if side else 'A') == -10000
    assert preference[0] < 0 < preference[-1]
    assert all(a < b for a, b in zip(preference, preference[1:]))


@pytest.mark.parametrize('special', ['danger', 'penalties'])
def test_playstyle_does_not_override_emergency_or_penalty_scoring(special):
    state = playstyle_state()
    if special == 'danger':
        state['ball'].update(x=100, y=437.5)
    else:
        state['penalty_shootout'] = True
    endpoint = {'x': 400, 'y': 600, 'a': state['players_a'], 'b': state['players_b']}
    scores = []
    for level in range(1, 6):
        state['playstyle_a'] = level
        scores.append(evaluate_outcome(state, True, (3, 0, 70), [endpoint], None))
    assert scores == [scores[0]] * 5


@pytest.fixture
def api(monkeypatch):
    import app as application
    from db import customization
    box = {'state': new_soccer_state(mode='hvai', player_count=5, half_length=0)}
    saved = []

    def save(_user, state):
        box['state'] = state
        saved.append(deepcopy(state))

    settings = deepcopy(customization.DEFAULT_CUSTOMIZATION)
    settings['half_length'] = 0
    monkeypatch.setattr(application, 'get_game', lambda _user: box['state'])
    monkeypatch.setattr(application, 'save_game', save)
    monkeypatch.setattr(application, '_ach_toasts', lambda: [])
    monkeypatch.setattr(application, '_track_scene_usage', lambda *_args: None)
    monkeypatch.setattr(application, '_auto_save_state', lambda *_args: None)
    monkeypatch.setattr(application, '_auto_clear_state', lambda *_args: None)
    monkeypatch.setattr(customization, 'get_customization', lambda _user: deepcopy(settings))
    with application.app.test_client() as client:
        with client.session_transaction() as session:
            session['user_id'] = 'dev:playstyle'
        yield SimpleNamespace(client=client, box=box, saved=saved)


@pytest.mark.parametrize('level', [1, 2, 3, 4, 5])
def test_human_can_save_own_playstyle_without_advancing_a_turn(api, level):
    before = deepcopy(api.box['state'])
    response = api.client.post('/set_playstyle', json={'level': level})
    assert response.status_code == 200
    result = response.get_json()
    assert result['playstyle_a'] == level
    assert result['playstyle_b'] == 3
    assert result['kick_count'] == before['kick_count']
    assert result['players_a'] == before['players_a']
    assert result['players_b'] == before['players_b']
    assert api.saved[-1]['playstyle_a'] == level


def test_ai_versus_ai_can_tune_both_teams_independently(api):
    api.box['state']['game_mode'] = 'aivai'
    assert api.client.post('/set_playstyle', json={'side': 'a', 'level': 1}).status_code == 200
    response = api.client.post('/set_playstyle', json={'side': 'b', 'level': 5})
    assert response.status_code == 200
    assert response.get_json()['playstyle_a'] == 1
    assert response.get_json()['playstyle_b'] == 5


def test_human_cannot_change_opponents_playstyle(api):
    before = deepcopy(api.box['state'])
    response = api.client.post('/set_playstyle', json={'side': 'b', 'level': 5})
    assert response.status_code == 403
    assert api.box['state'] == before
    assert not api.saved


@pytest.mark.parametrize('payload', [
    {}, {'level': None}, {'level': True}, {'level': False}, {'level': 0}, {'level': 6},
    {'level': 2.0}, {'level': '2'}, {'level': []}, {'level': {}},
    {'level': 3, 'side': 'c'}, {'level': 3, 'side': None},
])
def test_invalid_playstyle_commands_fail_without_mutation(api, payload):
    before = deepcopy(api.box['state'])
    response = api.client.post('/set_playstyle', json=payload)
    assert response.status_code == 400
    assert api.box['state'] == before
    assert not api.saved


@pytest.mark.parametrize('body', ['[]', 'null', '"invalid"', '{"level":'])
def test_invalid_json_shape_is_rejected(api, body):
    before = deepcopy(api.box['state'])
    response = api.client.post('/set_playstyle', data=body, content_type='application/json')
    assert response.status_code == 400
    assert api.box['state'] == before
    assert not api.saved


@pytest.mark.parametrize('change', [
    {'game_mode': 'hvh'}, {'penalty_shootout': True}, {'game_over': True},
    {'team_movement': False},
])
def test_unavailable_match_modes_reject_playstyle_changes(api, change):
    api.box['state'].update(change)
    before = deepcopy(api.box['state'])
    response = api.client.post('/set_playstyle', json={'level': 5})
    assert response.status_code == 409
    assert api.box['state'] == before
    assert not api.saved


@pytest.mark.parametrize('penalty_mode', [False, True])
def test_both_playstyles_survive_reset_and_mode_changes(api, penalty_mode):
    api.box['state'].update(playstyle_a=1, playstyle_b=5)
    assert api.client.post('/set_mode', json={'mode': 'aivai'}).status_code == 200
    response = api.client.post('/reset', json={'penalty_mode': penalty_mode})
    assert response.status_code == 200
    assert response.get_json()['playstyle_a'] == 1
    assert response.get_json()['playstyle_b'] == 5
    assert api.box['state']['playstyle_a'] == 1
    assert api.box['state']['playstyle_b'] == 5


def test_playstyle_change_requires_authentication():
    import app as application
    with application.app.test_client() as client:
        assert client.post('/set_playstyle', json={'level': 5}).status_code == 401


def test_legacy_malformed_settings_are_balanced_in_api_reset_and_prediction(api):
    from models.search_budget import SearchBudget, _current
    state = api.box['state']
    state.update(playstyle_a={'level': 5}, playstyle_b=True)
    response = api.client.get('/state')
    assert response.status_code == 200
    assert response.get_json()['playstyle_a'] == response.get_json()['playstyle_b'] == 3
    search = SearchBudget(state, True, [4], budget_s=5, max_simulations=2)
    token = _current.set(search)
    try:
        first = simulate_kick(state, 4, 90, 20, True)
        assert simulate_kick(state, 4, 90, 20, True) == first
        assert search.simulations == 1 and search.cache_hits == 1
    finally:
        _current.reset(token)
    response = api.client.post('/reset', json={})
    assert response.status_code == 200
    assert response.get_json()['playstyle_a'] == response.get_json()['playstyle_b'] == 3
