"""Authored routes must change real support motion without bypassing physics."""
from copy import deepcopy
import math

import pytest

from models.player_plans import normalize_plans, plan_target, advance_plans, cache_key
from models.soccer_logic import FIELD_W, FIELD_H, new_soccer_state, simulate_kick, apply_kick, _reset_players
from models.team_movement import support_moves


def planned_state():
    state = new_soccer_state(mode='aivai', player_count=5, half_length=0)
    for key, positions in (
        ('players_a', [(90,480),(350,200),(350,700),(600,200),(645,437.5)]),
        ('players_b', [(1310,480),(1050,200),(1050,700),(800,200),(755,437.5)]),
    ):
        for player, (x,y) in zip(state[key],positions):
            player.update(x=x,y=y)
    plans = normalize_plans(None)
    plans[1] = {'mode':'route','points':[{'x':.25,'y':200/875},{'x':.25,'y':.12}]}
    state['player_plans_a'] = normalize_plans(plans)
    return state


def test_route_guides_actual_motion_and_predictions_remain_pure():
    state = planned_state()
    before = deepcopy(state)
    prediction, goal = simulate_kick(state,4,0,40,True)
    assert state == before
    actual, actual_goal, *_ = apply_kick(state,4,0,40,True)
    assert actual == prediction and actual_goal == goal is None
    assert state['players_a'][1]['y'] < before['players_a'][1]['y'] - 40
    assert state['player_plan_progress_a'][1] == 1
    assert state['player_plans_a'] == before['player_plans_a']


def test_team_b_mirrors_team_relative_routes():
    state = planned_state()
    state['player_plans_b'] = deepcopy(state['player_plans_a'])
    a, b = plan_target(state,True,1), plan_target(state,False,1)
    assert a['x'] + b['x'] == FIELD_W
    assert a['y'] == b['y']
    assert any(not side and index == 1 for side,index,*_ in support_moves(state,4,0,40,True))


def test_holding_a_reached_point_does_not_launch_a_sidestep():
    state = planned_state()
    state['player_plans_a'][1] = {'mode':'hold','points':[{'x':.25,'y':200/875}]}
    assert not any(side and index == 1 for side,index,*_ in support_moves(state,4,0,40,True))


def test_authored_routes_keep_travel_cap_and_do_not_launch_the_human():
    state = planned_state()
    state['player_plans_a'][1]['points'] = [{'x':.8,'y':.2}]
    state['game_mode'] = 'hvai'
    state['player_plans_a'][4] = {'mode':'route','points':[{'x':.8,'y':.2}]}
    assert plan_target(state,True,4) is None
    moves = support_moves(state,4,0,40,True)
    assert not any(side and index == 4 for side,index,*_ in moves)
    from models.soccer_logic import _get_player_stats, _get_player_friction, _get_player_kick_vel
    for side,index,_,power in moves:
        stats = _get_player_stats(state,side,index)
        travel = (power*_get_player_kick_vel(stats))**2/(2*_get_player_friction(stats))
        assert travel <= (45 if index == 0 else 120) + .01


def test_route_progress_undo_snapshot_and_goal_reset():
    from game.session import push_snapshot
    state = planned_state()
    state['player_plan_progress_a'] = [0,1,0,0,0]
    push_snapshot(state)
    state['player_plan_progress_a'][1] = 0
    assert state['snapshots'][-1]['player_plan_progress_a'][1] == 1
    _reset_players(state)
    assert state['player_plan_progress_a'] == [0]*5
    assert state['player_plans_a'][1]['mode'] == 'route'


def test_cache_keys_include_route_and_cursor_changes():
    state = planned_state()
    initial = cache_key(state)
    state['player_plan_progress_a'] = [0,1,0,0,0]
    assert cache_key(state) != initial
    progressed = cache_key(state)
    state['player_plans_a'][1]['points'][-1]['y'] = .15
    assert cache_key(state) != progressed


@pytest.mark.parametrize('change',[{'sport':'basketball'}, {'penalty_shootout':True},
                                  {'team_movement':False}, {'game_mode':'hvh'}])
def test_other_modes_ignore_authored_routes(change):
    state = planned_state();state.update(change)
    assert plan_target(state,True,1) is None


@pytest.mark.parametrize('raw',[[],{},[None]*5,[{'mode':'fly','points':[]}]*5,
    [{'mode':'hold','points':[]}]*5,
    [{'mode':'route','points':[{'x':float('nan'),'y':.5}]}]*5,
    [{'mode':'route','points':[{'x':True,'y':.5}]}]*5,
    [{'mode':'route','points':[{'x':.1,'y':.5}]*17}]*5,
    [{'mode':'route','points':[{'x':.9,'y':.5}]}]*5])
def test_invalid_or_unbounded_plans_reject(raw):
    with pytest.raises(ValueError):normalize_plans(raw)


def test_corrupt_legacy_plans_fall_back_without_crashing():
    state = planned_state();state['player_plans_a'] = [{'mode':'route','points':None}]*5
    assert plan_target(state,True,1) is None


def test_real_motion_uses_one_physics_world(monkeypatch):
    import pymunk
    original = pymunk.Space
    calls=[]
    def build(*args,**kwargs):
        calls.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(pymunk,'Space',build)
    apply_kick(planned_state(),4,0,40,True)
    assert len(calls) == 1


def test_blocked_route_does_not_force_a_player_through_an_opponent():
    state=planned_state()
    state['player_plans_a'][1]={'mode':'route','points':[{'x':.4,'y':200/875}]}
    state['players_b'][1].update(x=420,y=200)
    assert not any(side and index==1 for side,index,*_ in support_moves(state,4,0,40,True))
