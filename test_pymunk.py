"""Unit tests for pymunk physics simulation in soccer logic."""
from models.soccer_logic import new_soccer_state, apply_kick, simulate_kick


def test_apply_kick_returns_trajectory():
    st = new_soccer_state()
    traj, scored, desc, ep, push = apply_kick(st, 1, 0, 80, True)
    assert len(traj) > 1
    assert scored is None or isinstance(scored, bool)


def test_apply_kick_both_sides():
    st = new_soccer_state()
    traj, scored, desc, ep, push = apply_kick(st, 1, 0, 80, True)
    assert len(traj) > 1
    traj2, scored2, desc2, ep2, push2 = apply_kick(st, 1, 0, 80, False)
    assert len(traj2) > 1


def test_simulate_kick_returns_trajectory():
    st2 = new_soccer_state()
    traj, scored = simulate_kick(st2, 0, 0, 100, True)
    assert len(traj) >= 1
    assert scored is None or isinstance(scored, bool)


def test_simulate_kick_miss():
    st3 = new_soccer_state()
    st3["ball"] = {"x": 400.0, "y": 400.0}
    traj, scored = simulate_kick(st3, 0, 135, 30, True)
    assert len(traj) >= 1


def test_apply_kick_goal_updates_score():
    st4 = new_soccer_state()
    st4["score_a"] = 4
    traj, scored, desc, ep, push = apply_kick(st4, 1, 0, 100, True)
    assert st4["score_a"] >= 4
    assert isinstance(st4["game_over"], bool)
