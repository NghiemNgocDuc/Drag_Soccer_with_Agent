"""All four arcade corner bumpers share actual play and AI physics."""
import copy

import pytest

from models import soccer_logic as physics


@pytest.mark.parametrize('right,bottom', [(False,False),(False,True),(True,False),(True,True)])
@pytest.mark.parametrize('crowded', [False,True])
def test_corner_returns_ball_to_play_without_teleport(right, bottom, crowded):
    state = physics.new_soccer_state(half_length=0)
    def point(x,y):
        return {'x':physics.FIELD_W-x if right else x,'y':physics.FIELD_H-y if bottom else y}
    state['ball'].update(point(39,39))
    if crowded:
        for side,index,x,y in [('a',0,47,78),('b',0,78,47),('a',4,98,60)]:
            state['players_'+side][index].update(point(x,y))
    original=copy.deepcopy(state)
    predicted, scored=physics.simulate_kick(state,1,90,0,True)
    trajectory,*_=physics.apply_kick(state,1,90,0,True)
    assert scored is None
    assert predicted == trajectory
    assert trajectory[0]['x'] == original['ball']['x']
    assert trajectory[0]['y'] == original['ball']['y']
    assert any(p.get('bounce') for p in trajectory[:4])
    last=trajectory[-1]
    assert min(last['x'],physics.FIELD_W-last['x']) > 65
    assert min(last['y'],physics.FIELD_H-last['y']) > 65
    assert all(p['z']==0 for p in trajectory)
    for a,b in zip(trajectory,trajectory[1:]):
        # Replay decimation keeps impact samples; travel still takes physical time.
        dt=b['t']-a['t']
        assert ((b['x']-a['x'])**2+(b['y']-a['y'])**2)**.5 < 850*dt+1


def test_bumper_does_not_affect_ordinary_wall_or_goal_mouth():
    state=physics.new_soccer_state()
    for position in [(40,437.5),(700,39),(700,437.5)]:
        space=physics._new_physics_space(state)
        ball,_=physics._make_ball(space,position)
        ball.velocity=(-20,-30)
        physics._release_corner_ball(space,ball)
        assert tuple(ball.velocity)==(-20,-30)


def test_corner_incoming_velocity_redirects_immediately():
    space=physics._new_physics_space(physics.new_soccer_state())
    ball,_=physics._make_ball(space,(42,42))
    ball.velocity=(-300,-200)
    physics._release_corner_ball(space,ball)
    assert ball.velocity.x > 0 and ball.velocity.y > 0
    assert tuple(ball.position)==(42,42)
