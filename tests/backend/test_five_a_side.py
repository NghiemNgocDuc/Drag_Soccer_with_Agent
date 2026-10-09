"""Five-a-side control and bounded, planar arcade physics regressions."""
import copy
import math
import random

import pytest

from models import soccer_logic as physics
from game.session import new_game_state


def test_new_matches_have_five_players_and_a_central_striker():
    for state in (new_game_state(), physics.new_soccer_state()):
        assert state['player_count'] == 5
        for side in ('a', 'b'):
            assert len(state['players_'+side]) == 5
            assert state['players_'+side][4]['y'] == physics.FIELD_H / 2
        assert physics.human_player_index(state) == 4
        state['penalty_shootout'] = True
        assert physics.human_player_index(state) == 0


@pytest.mark.parametrize('power', [20, 60, 100])
def test_missed_kick_cannot_move_or_lift_ball(power):
    state = physics.new_soccer_state(half_length=9999)
    start = dict(state['ball'])
    trajectory, scored, *_ = physics.apply_kick(state, 4, 90, power, True)
    assert scored is None
    assert all((p['x'], p['y'], p['z']) == (start['x'], start['y'], 0) for p in trajectory)
    assert all(len(p[side]) == 5 for p in trajectory for side in ('a', 'b'))


@pytest.mark.parametrize('power_stat,agility', [(0, 0), (50, 50), (100, 0), (100, 100)])
def test_maximum_power_approach_is_bounded_and_grounded(power_stat, agility):
    state = physics.new_soccer_state(half_length=9999)
    state['players_a'][4].update(x=650, y=180, stats={**physics.DEFAULT_STATS, 'power':power_stat, 'agility':agility})
    trajectory, *_ = physics.apply_kick(state, 4, 0, 100, True)
    assert 0 < state['players_a'][4]['x'] - 650 <= 224  # one fixed substep of integration tolerance
    assert all(p['z'] == 0 for p in trajectory)


def test_wall_reflects_the_ball_without_changing_tangent_direction_or_adding_energy():
    state = physics.new_soccer_state()
    state['ball'].update(x=500, y=80)
    space, _, _, ball, _, _ = physics._build_space(state)
    ball.velocity = 200, -400
    for _ in range(90):
        before = ball.velocity
        space.step(physics._PM_DT/3)
        after = ball.velocity
        if after.y > 0:
            assert after.x > 0
            assert after.length < before.length
            assert abs(after.y / before.y) == pytest.approx(physics._PM_ELASTICITY_B * physics._PM_ELASTICITY_W, abs=.02)
            assert after.x == pytest.approx(before.x, abs=2)
            break
    else:
        pytest.fail('No wall rebound')


def test_many_extreme_kicks_keep_every_player_visible_and_replay_complete():
    rng = random.Random(26)
    state = physics.new_soccer_state(half_length=9999, win_goal_limit=999)
    for side in ('a', 'b'):
        for p in state['players_'+side]:
            p['stats'] = {'size':rng.choice([0,100]), 'power':100, 'weight':rng.choice([0,100]), 'agility':0}
    for turn in range(64):
        side = turn % 2 == 0
        index = rng.randrange(5)
        trajectory, *_ = physics.apply_kick(state,index,rng.uniform(0,360),100,side)
        for frame in trajectory:
            assert frame['z'] == 0
            for team in ('a','b'):
                assert len(frame[team]) == 5
                for i,p in enumerate(frame[team]):
                    radius = physics._get_player_radius(state['players_'+team][i]['stats'])
                    edge = physics._MARGIN + 5 + radius
                    assert math.isfinite(p['x']) and math.isfinite(p['y'])
                    assert edge-.11 <= p['x'] <= physics.FIELD_W-edge+.11
                    assert edge-.11 <= p['y'] <= physics.FIELD_H-edge+.11


def test_player_contact_is_recorded_and_prediction_matches_play():
    state = physics.new_soccer_state(half_length=9999)
    before = copy.deepcopy(state)
    predicted, goal = physics.simulate_kick(state,4,0,100,True)
    assert state == before
    actual, scored, *_ = physics.apply_kick(state,4,0,100,True)
    assert predicted == actual and goal == scored
    assert any(p.get('contact') for p in actual)
    assert all(p['z'] == 0 for p in actual)


def test_hard_player_contact_caps_ball_speed():
    state = physics.new_soccer_state()
    state['players_a'][4].update(x=660, y=437.5)
    space, team_a, _, ball, _, _ = physics._build_space(state)
    team_a[4].velocity = 1600, 0
    for _ in range(30):
        space.step(physics._PM_DT / 3)
        if space._soccer_events['contact']:
            assert 0 < ball.velocity.length <= physics._RALLY_SPEED_CAP + .001
            break
    else:
        pytest.fail('No player contact')


def test_default_striker_strength_reaches_kickoff_ball():
    state = physics.new_soccer_state(half_length=9999)
    trajectory, *_ = physics.apply_kick(state, 4, 0, 80, True)
    assert any(p.get('contact') for p in trajectory)
    assert state['ball']['x'] > physics.FIELD_W / 2 + 20


def test_penalty_frames_keep_the_full_roster_and_track_the_kicker():
    state = physics.new_soccer_state()
    state['penalty_shootout'] = True
    physics._setup_penalty_positions(state,True)
    trajectory, *_ = physics.apply_penalty_kick(state,0,0,100,True)
    assert all(len(p['a']) == len(p['b']) == 5 for p in trajectory)
    assert trajectory[-1]['a'][0]['x'] != trajectory[0]['a'][0]['x']
    assert all(p['a'][0]['x'] == p['kicker']['x'] and p['b'][0]['y'] == p['keeper']['y'] for p in trajectory)


def test_goal_resets_keep_player_identity_and_stats():
    state = physics.new_soccer_state()
    state['players_a'][4].update(name='You',color='#abcdef',stats=dict(physics.DEFAULT_STATS))
    physics._reset_players(state)
    assert state['players_a'][4]['name'] == 'You'
    assert state['players_a'][4]['color'] == '#abcdef'
    assert state['players_a'][4]['stats'] == physics.DEFAULT_STATS
