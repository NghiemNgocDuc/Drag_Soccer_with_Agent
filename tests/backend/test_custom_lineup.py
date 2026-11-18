from copy import deepcopy
import pytest

from models.custom_lineup import normalize_lineup, home_positions, cache_key
from models.formations import formation_catalog
from models.soccer_logic import new_soccer_state, _reset_players, _reset_outfield
from models.team_roles import team_roles, role_target


def lineup():
    return normalize_lineup(formation_catalog()[0]['positions'])


@pytest.mark.parametrize('bad', [None, True, 'x', float('nan'), float('inf'), -.1, 1.0])
def test_invalid_coordinate(bad):
    players=lineup();players[1]['x']=bad
    with pytest.raises(ValueError):normalize_lineup(players)


@pytest.mark.parametrize('case', ['count','role','keeper','capacity','overlap','bounds'])
def test_invalid_lineup(case):
    players=lineup()
    if case=='count':players.pop()
    elif case=='role':players[1]['role']=[]
    elif case=='keeper':players[0]['x']=.1
    elif case=='capacity':players[3].update(role='defender',x=.2,y=.8)
    elif case=='overlap':players[2]=deepcopy(players[1])
    else:players[4]['y']=.99
    with pytest.raises(ValueError):normalize_lineup(players)


def test_mirrored_custom_positions_reset_stats_and_roles():
    state=new_soccer_state(mode='aivai',player_count=5)
    players=lineup()
    players[1].update(role='attacker',x=.41,y=.75)
    players[4].update(role='defender',x=.19,y=.25)
    state.update(custom_lineup_a=players,custom_lineup_b=deepcopy(players))
    state['players_a'][1]['stats']={'size':75,'power':25,'weight':50,'agility':50}
    original=deepcopy(state['players_a'][1]['stats'])
    assert team_roles(state,True)==('goalkeeper','attacker','defender','support','defender')
    a,b=home_positions(state,'a'),home_positions(state,'b')
    assert all(ax+bx==pytest.approx(1400) and ay==by for (ax,ay),(bx,by) in zip(a,b))
    _reset_players(state)
    assert state['players_a'][1]['stats']==original
    assert state['players_a'][1]['x']==pytest.approx(.41*1400)
    state['players_b'][4]['x']=999
    _reset_outfield(state,'b')
    assert state['players_b'][4]['x']==pytest.approx(.81*1400)
    focus={'x':700,'y':437.5}
    assert role_target(state,True,1,focus)['x']>700
    assert role_target(state,True,4,focus)['x']<700
    before=cache_key(state)
    state['custom_lineup_a'][1]['y']=.8
    assert cache_key(state)!=before


def test_corrupt_legacy_lineup_falls_back_and_other_sports_ignore_it():
    state=new_soccer_state(player_count=5)
    default=home_positions(state,'a')
    state['custom_lineup_a']=[False]*5
    assert home_positions(state,'a')==default
    state['custom_lineup_a']=lineup();state['custom_lineup_a'][1]['y']=.2
    state['sport']='basketball'
    assert home_positions(state,'a')==default


@pytest.mark.parametrize('roles', [('defender','defender','support','support'),('support','support','attacker','attacker')])
def test_groups_may_be_empty(roles):
    from models.custom_lineup import ROLE_BOUNDS
    players=[{'role':'goalkeeper','x':.062,'y':.5}]
    for i,role in enumerate(roles):
        lo,hi=ROLE_BOUNDS[role]
        players.append({'role':role,'x':(lo+hi)/2,'y':.35 if i%2==0 else .65})
    assert len(normalize_lineup(players))==5


def test_physical_prediction_uses_custom_roles_without_mutation():
    from models.soccer_logic import apply_kick, simulate_kick
    state=new_soccer_state(mode='aivai',player_count=5,half_length=0)
    state['custom_lineup_a']=lineup();state['custom_lineup_a'][1]['y']=.25
    _reset_players(state)
    before=deepcopy(state)
    prediction=simulate_kick(state,4,0,30,True)
    assert state==before
    actual=apply_kick(state,4,0,30,True)
    assert prediction[1]==actual[1]
    for key in ('a','b'):
        for expected,observed in zip(prediction[0][-1][key],actual[0][-1][key]):
            assert expected['x']==pytest.approx(observed['x'])
            assert expected['y']==pytest.approx(observed['y'])
    assert state['custom_lineup_a']==before['custom_lineup_a']


@pytest.mark.parametrize('side', [True,False])
@pytest.mark.parametrize('role', ['defender','support','attacker'])
def test_automatic_physical_movement_follows_assigned_job_not_jersey(side,role):
    from models.soccer_logic import simulate_kick
    state=new_soccer_state(mode='aivai',player_count=5,half_length=0)
    for key,positions in (
        ('players_a',[(90,480),(700,200),(300,700),(400,500),(950,730)]),
        ('players_b',[(1310,480),(1100,150),(1100,750),(1000,500),(850,650)]),
    ):
        for player,(x,y) in zip(state[key],positions):player.update(x=x,y=y)
    custom=lineup()
    if role!='defender':
        other=3 if role=='support' else 4
        custom[1],custom[other]=custom[other],custom[1]
    state['custom_lineup_a']=custom
    # The board is identical in all three cases: only the assigned job changes.
    # No drawn instructions are present; defaults must work without authoring.
    if not side:
        original=deepcopy(state)
        for dst,src in [('a','b'),('b','a')]:
            state['players_'+dst]=[{**p,'x':1400-p['x']} for p in original['players_'+src]]
        state['custom_lineup_b']=state.pop('custom_lineup_a')
    before=deepcopy(state)
    trajectory,goal=simulate_kick(state,4,270,30,side)
    track='a' if side else 'b'
    player=trajectory[-1][track][1]
    depth=player['x'] if side else 1400-player['x']
    assert goal is None and state==before
    if role=='defender':assert depth<650
    # A midfielder settles near the short passing outlet, with a small physical
    # stopping tolerance rather than an artificial exact-position snap.
    elif role=='support':assert abs(depth-650)<5 and player['y']>280
    else:assert depth>750
