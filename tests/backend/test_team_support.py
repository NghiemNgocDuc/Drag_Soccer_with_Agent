"""Useful off-ball runs must survive actual physics and the shared search budget."""
from copy import deepcopy
import math

import pytest

from models import team_coordinated
from models.search_budget import get_model_move
from models.soccer_logic import FIELD_W, new_soccer_state, simulate_kick
from models.tactics import quick_move, tactical_candidates


def support_state():
    state = new_soccer_state(player_count=5, half_length=0)
    state['ball'].update(x=700, y=437.5)
    for key, positions in (
        ('players_a', [(90, 437.5), (360, 437.5), (570, 220), (570, 655), (645, 437.5)]),
        ('players_b', [(1320, 437.5), (1150, 160), (1150, 715), (1100, 310), (1100, 565)]),
    ):
        for player, (x, y) in zip(state[key], positions):
            player.update(x=x, y=y)
    return state


def mirror(state):
    result = deepcopy(state)
    result['ball']['x'] = FIELD_W - state['ball']['x']
    for key, other in (('players_a', 'players_b'), ('players_b', 'players_a')):
        result[key] = [{**player, 'x': FIELD_W - player['x']} for player in state[other]]
    return result


@pytest.mark.parametrize('side', [True, False])
def test_teammate_opens_forward_space_without_touching_ball_or_other_players(side):
    state = support_state() if side else mirror(support_state())
    original = deepcopy(state)
    move = quick_move(state, side, allowed_players=[0, 1, 2, 3])
    path, goal = simulate_kick(state, *move, side)
    own_key, track = ('players_a', 'a') if side else ('players_b', 'b')
    assert move[0] in (2, 3)
    assert goal is None
    assert path[-1]['x'] == pytest.approx(state['ball']['x'])
    assert path[-1]['y'] == pytest.approx(state['ball']['y'])
    receiver = path[-1][track][move[0]]
    assert (receiver['x'] - state['ball']['x']) * (1 if side else -1) > 100
    assert abs(receiver['y'] - state['ball']['y']) > 150
    for i, player in enumerate(state[own_key]):
        if i != move[0]:
            assert path[-1][track][i]['x'] == pytest.approx(player['x'])
            assert path[-1][track][i]['y'] == pytest.approx(player['y'])
    assert state == original


@pytest.mark.parametrize('blocked_y,receiver', [(327, 3), (547, 2)])
def test_opponent_in_a_passing_lane_sends_support_to_the_other_flank(blocked_y, receiver):
    state = support_state()
    state['players_b'][1].update(x=766, y=blocked_y)
    assert quick_move(state, True, [0, 1, 2, 3])[0] == receiver


def test_existing_receiver_leaves_the_other_flank_for_next_teammate():
    state = support_state()
    state['players_a'][2].update(x=840, y=267.5)
    move = get_model_move(team_coordinated, state, True, allowed_players=[0, 1, 2, 3],
                          budget_s=1, max_simulations=8)
    path, _ = simulate_kick(state, *move, True)
    assert move[0] == 3
    assert path[-1]['a'][3]['y'] > state['ball']['y'] + 150
    assert math.hypot(path[-1]['a'][3]['x'] - 840, path[-1]['a'][3]['y'] - 267.5) > 200


def test_blocked_run_does_not_push_through_a_teammate_to_reach_empty_space():
    state = support_state()
    state['players_a'][1].update(x=660, y=215)
    move = quick_move(state, True, [2, 3])
    path, _ = simulate_kick(state, *move, True)
    assert move[0] == 3
    assert path[-1]['a'][1]['x'] == pytest.approx(660)
    assert path[-1]['a'][1]['y'] == pytest.approx(215)


def test_cover_advances_behind_ball_and_keeper_remains_home():
    state = support_state()
    move = quick_move(state, True, [1])
    path, _ = simulate_kick(state, *move, True)
    assert state['players_a'][1]['x'] < path[-1]['a'][1]['x'] < state['ball']['x'] - 100
    assert path[-1]['a'][0]['x'] == pytest.approx(state['players_a'][0]['x'])


def test_immediate_danger_is_cleared_before_any_support_run():
    state = support_state()
    state['ball'].update(x=180, y=437.5)
    state['players_a'][1].update(x=125, y=437.5)
    move = quick_move(state, True, [0, 1, 2, 3])
    path, goal = simulate_kick(state, *move, True)
    assert move[0] == 1
    assert goal != 'B'
    assert path[-1]['x'] > 400


def test_support_shortlist_mirrors_and_respects_stats_and_power_cap():
    state = support_state()
    state['power_cap'] = 65
    state['players_a'][2]['stats'] = dict(size=100, power=0, weight=50, agility=100)
    moves = tactical_candidates(state, True, [1, 2, 3])
    reflected = tactical_candidates(mirror(state), False, [1, 2, 3])
    assert len(moves) == len(reflected)
    for move, other in zip(moves, reflected):
        assert move[0] in (1, 2, 3) and 0 <= move[2] <= 65
        assert other[0] == move[0]
        assert other[2] == pytest.approx(move[2])
        assert abs((other[1] - (180 - move[1]) + 180) % 360 - 180) < 1e-7


def test_support_fallback_never_allocates_a_physics_world(monkeypatch):
    import pymunk
    state = support_state()
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Support positioning must stay analytic')
    monkeypatch.setattr(pymunk, 'Space', forbidden)
    assert quick_move(state, True, [0, 1, 2, 3])[0] in (2, 3)


def test_physics_verified_coordinator_keeps_support_with_existing_prediction_limit():
    state = support_state()
    diagnostics = {}
    move = get_model_move(team_coordinated, state, True, allowed_players=[0, 1, 2, 3],
                          budget_s=1, max_simulations=4, diagnostics=diagnostics)
    path, _ = simulate_kick(state, *move, True)
    assert move[0] in (2, 3)
    assert path[-1]['x'] == pytest.approx(state['ball']['x'])
    assert diagnostics['simulations'] <= 4


def test_live_teammate_endpoint_opens_space_and_advances_only_one_turn(monkeypatch):
    import app as application
    state = support_state()
    state['game_mode'] = 'hvai'
    original = deepcopy(state)
    saved = []
    monkeypatch.setattr(application, 'get_game', lambda _user: state)
    monkeypatch.setattr(application, 'save_game', lambda _user, current: saved.append(current))
    with application.app.test_client() as client:
        with client.session_transaction() as session:
            session['user_id'] = 'dev:support-test'
        response = client.post('/random_move')
    assert response.status_code == 200
    result = response.get_json()
    move = result['random_result']
    assert move['player_idx'] in (2, 3)
    assert result['kick_count'] == original['kick_count'] + 1
    assert not result['is_player_a']
    assert result['players_a'][4]['x'] == original['players_a'][4]['x']
    assert result['ball']['x'] == pytest.approx(original['ball']['x'])
    assert move['trajectory'][-1]['a'][move['player_idx']]['x'] > original['ball']['x'] + 100
    assert saved
