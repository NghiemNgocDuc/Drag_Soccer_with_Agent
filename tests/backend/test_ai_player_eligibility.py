"""Live AI decisions involve the roster while preserving human/penalty controls."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

import app as application
from models import soccer_logic as physics


@pytest.fixture
def live_game(monkeypatch):
    state = physics.new_soccer_state(mode='hvai', player_count=5, half_length=0)
    monkeypatch.setattr(application, 'get_game', lambda _user: state)
    monkeypatch.setattr(application, 'save_game', lambda *_: None)
    monkeypatch.setattr(application, '_auto_save_state', lambda *_: None)
    monkeypatch.setattr(application, '_mem_short', lambda *_: None)
    monkeypatch.setattr(application, '_goal_moment_achievements', lambda *_: None)
    with application.app.test_client() as client:
        with client.session_transaction() as session:
            session['user_id'] = 'dev:ai-player-eligibility'
        yield state, client


@pytest.mark.parametrize('count', [1, 3, 5, 11])
@pytest.mark.parametrize('mode', ['hvai', 'aivai'])
def test_normal_opponent_has_the_full_roster(count, mode):
    state = physics.new_soccer_state(mode=mode, player_count=count, half_length=0)
    assert application._allowed_ai_players(state, False) == tuple(range(count))


@pytest.mark.parametrize('index', [1, 2, 3, 4])
def test_live_opponent_can_execute_each_outfielder_move(live_game, monkeypatch, index):
    state, client = live_game
    state['is_player_a'] = False
    before = deepcopy(state['players_b'][index])
    model = SimpleNamespace(get_ai_move=lambda *_: (index, 90.0, 25.0))
    monkeypatch.setattr(application, '_load_model', lambda _: model)

    response = client.post('/ai_move')

    assert response.status_code == 200
    result = response.get_json()['ai_result']
    assert result['player_idx'] == index
    assert not result.get('timeout_fallback')
    assert state['players_b'][index]['y'] > before['y']
    assert state['move_history'][-1]['player_idx'] == index
    assert state['kick_count'] == 1 and state['is_player_a']


def test_busy_worker_fallback_can_use_a_reachable_outfielder(live_game, monkeypatch):
    state, client = live_game
    state['is_player_a'] = False
    # The central striker can reach the kickoff ball; the home keeper cannot.
    monkeypatch.setattr(application, '_ai_slots', SimpleNamespace(acquire=lambda **_: False))

    response = client.post('/ai_move')

    assert response.status_code == 200
    result = response.get_json()['ai_result']
    assert result['fallback_reason'] == 'busy'
    assert result['player_idx'] != 0
    # A physical rebound off the human striker can send the ball back across
    # halfway. The chosen outfielder must first contact it toward attack.
    assert min(frame['x'] for frame in result['trajectory']) < physics.FIELD_W / 2
    assert (state['ball']['x'], state['ball']['y']) != (physics.FIELD_W / 2, physics.FIELD_H / 2)


@pytest.mark.parametrize('route,key', [('/random_move', 'random_result'), ('/ai_move', 'ai_result')])
def test_teammate_ai_cannot_take_over_the_human_striker(live_game, monkeypatch, route, key):
    state, client = live_game
    striker = physics.human_player_index(state)
    model = SimpleNamespace(get_ai_move=lambda *_: (striker, 90.0, 25.0))
    monkeypatch.setattr(application, '_load_model', lambda _: model)

    response = client.post(route)

    assert response.status_code == 200
    result = response.get_json()[key]
    assert result['player_idx'] != striker
    assert result['fallback_reason'] == 'player_constraint'
    assert state['kick_count'] == 1 and not state['is_player_a']


@pytest.mark.parametrize('mode', ['hvai', 'aivai'])
@pytest.mark.parametrize('side', [True, False])
def test_live_shootout_replans_for_the_placed_kicker(live_game, monkeypatch, mode, side):
    state, client = live_game
    state.update(game_mode=mode, is_player_a=side, penalty_shootout=True)
    physics._setup_penalty_positions(state, side)
    state['penalty_goalkeeper_move'] = 'center'
    model = SimpleNamespace(get_ai_move=lambda *_: (4, 90.0, 25.0))
    monkeypatch.setattr(application, '_load_model', lambda _: model)

    response = client.post('/ai_move')

    assert response.status_code == 200
    assert response.get_json()['ai_result']['player_idx'] == 0
    assert state['penalty_kicks'][-1]['kicker_idx'] == 0
    assert state['penalty_kick_num'] == 1
    assert state['kick_count'] == 0
