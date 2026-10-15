"""Distinct tactical priorities must survive request budgets and API selection."""
from copy import deepcopy
import importlib

import pytest

import app as application
from models import soccer_logic as physics, search_budget as budget, tactics

STYLES = ('possession', 'counter_attack', 'high_press')


def board():
    state = physics.new_soccer_state(mode='hvh', half_length=0)
    state['ball'].update(x=700,y=437.5)
    for roster, points in [('players_a',[(80,437),(400,250),(600,600),(870,420),(640,437)]),
                           ('players_b',[(1320,437),(1050,250),(1000,650),(1030,460),(800,600)])]:
        for pawn, (x,y) in zip(state[roster],points):
            pawn.update(x=x,y=y)
    return state


def test_possession_prefers_safe_receiver_while_counter_prefers_forward_space():
    state=board()
    move=(4,0,80)
    safe=[{'x':855,'y':437.5,'a':state['players_a'],'b':state['players_b']}]
    direct=[{'x':1160,'y':437.5,'a':state['players_a'],'b':state['players_b']}]
    possession=importlib.import_module('models.possession').evaluate_move
    counter=importlib.import_module('models.counter_attack').evaluate_move
    assert possession(state,True,move,safe,None)>possession(state,True,move,direct,None)
    assert counter(state,True,move,direct,None)>counter(state,True,move,safe,None)


def test_press_rewards_closing_the_ball_over_retreating():
    state=board()
    score=importlib.import_module('models.high_press').evaluate_move
    close=deepcopy(state['players_a']);close[1].update(x=660,y=420)
    retreat=deepcopy(state['players_a']);retreat[1].update(x=250,y=250)
    assert score(state,True,(1,0,50),[{**state['ball'],'a':close}],None) > score(
        state,True,(1,180,50),[{**state['ball'],'a':retreat}],None)


@pytest.mark.parametrize('name',STYLES)
def test_goals_dominate_and_mirrored_outcomes_have_same_value(name):
    model=importlib.import_module('models.'+name)
    state=board();end={**state['ball'],'x':820,'a':state['players_a'],'b':state['players_b']}
    value=model.evaluate_move(state,True,(4,0,70),[end],None)
    mirrored=deepcopy(state)
    mirrored['players_a']=[{**p,'x':1400-p['x']} for p in state['players_b']]
    mirrored['players_b']=[{**p,'x':1400-p['x']} for p in state['players_a']]
    mirrored['ball']['x']=1400-state['ball']['x']
    mirrored_end={**end,'x':1400-end['x'],'a':mirrored['players_a'],'b':mirrored['players_b']}
    assert model.evaluate_move(mirrored,False,(4,180,70),[mirrored_end],None)==pytest.approx(value)
    assert model.evaluate_move(state,True,(4,0,70),[end],'A')==10000
    assert model.evaluate_move(state,True,(4,0,70),[end],'B')==-10000


@pytest.mark.parametrize('name',STYLES)
@pytest.mark.parametrize('side',[True,False])
def test_models_keep_constraints_budget_and_input_state(name,side):
    model=importlib.import_module('models.'+name)
    state=board();state['power_cap']=55;before=deepcopy(state)
    diagnostics={}
    move=budget.get_model_move(model,state,side,allowed_players=[1,2,3],budget_s=10,
                               max_simulations=5,diagnostics=diagnostics)
    assert move[0] in [1,2,3] and 0<=move[1]<360 and 0<=move[2]<=55
    assert diagnostics['simulations']<=5
    assert state==before
    assert budget.current_search() is None


@pytest.mark.parametrize('name',STYLES)
def test_style_scoring_selects_best_completed_move_when_budget_ends(name,monkeypatch):
    model=importlib.import_module('models.'+name)
    state=board()
    moves=tactics.tactical_candidates(state,True,max_candidates=3,evaluator=model.evaluate_move)
    expected=max(moves[:2],key=lambda move:model.evaluate_move(state,True,move,*physics.simulate_kick(state,*move,True)))
    monkeypatch.setattr(tactics,'tactical_candidates',lambda *_a,**_k:moves)
    info={}
    actual=budget.get_model_move(model,state,True,budget_s=10,max_simulations=2,diagnostics=info)
    assert actual==expected and info['simulations']==2
    assert info['search_stop']=='simulation_limit'


@pytest.mark.parametrize('name',STYLES)
def test_new_model_is_listed_selectable_and_plays_through_http(name,monkeypatch):
    state=board()
    monkeypatch.setattr(application,'get_game',lambda _:state)
    monkeypatch.setattr(application,'save_game',lambda *_:None)
    with application.app.test_client() as client:
        with client.session_transaction() as session:
            session['user_id']='dev:new-models'
        catalog=client.get('/models').get_json()
        assert any(row['id']==name and row['desc'] for row in catalog)
        assert client.post('/switch_model',json={'model':name,'target':'a'}).status_code==200
        response=client.post('/ai_move').get_json()
        assert 'error' not in response
        assert response['kick_count']==1 and response['ai_result']['trajectory']
