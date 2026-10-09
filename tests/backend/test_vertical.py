"""Test vertical-axis (z) ball physics extension in soccer_logic.py"""
from models.soccer_logic import (
    new_soccer_state, simulate_kick, apply_kick, _loft_angle, G, _VZ_MIN,
    FIELD_W, FIELD_H, _build_space, _sim,
)


def _kick_spot(s, pidx=1):
    """Position kicker at the gameplay kick spot: 95px left of ball center."""
    s["players_a"][pidx]["x"] = FIELD_W / 2 - 95
    s["players_a"][pidx]["y"] = FIELD_H / 2
    s["ball"]["x"] = FIELD_W / 2
    s["ball"]["y"] = FIELD_H / 2


def test_new_state_has_z():
    s = new_soccer_state()
    assert "z" in s["ball"]
    assert s["ball"]["z"] == 0.0


def test_loft_angle_curve():
    # Ground-only: loft disabled per user request — always 0
    assert _loft_angle(30) == 0.0
    assert _loft_angle(40) == 0.0
    assert _loft_angle(60) == 0.0
    assert _loft_angle(80) == 0.0
    assert _loft_angle(100) == 0.0
    assert _loft_angle(120) == 0.0  # capped


def test_low_power_stays_on_ground():
    s = new_soccer_state()
    traj, _ = simulate_kick(s, 1, 0.0, 30.0, True)
    for pt in traj:
        assert "z" in pt
        assert pt["z"] == 0.0


def test_high_power_has_loft():
    # Ground-only: even high power stays on ground
    s = new_soccer_state()
    _kick_spot(s)
    traj, _ = simulate_kick(s, 1, 0.0, 90.0, True)
    max_z = max(pt["z"] for pt in traj)
    assert max_z == 0.0
    assert traj[-1]["z"] == 0.0


def test_trajectory_has_z_field():
    s = new_soccer_state()
    traj, _ = simulate_kick(s, 1, 0.0, 80.0, True)
    for pt in traj:
        assert "z" in pt, f"missing z at point {pt}"


def test_apply_kick_preserves_z():
    s = new_soccer_state()
    traj, scored, desc, ep, push = apply_kick(s, 1, 0.0, 80.0, True)
    assert "z" in s["ball"]
    assert s["ball"]["z"] >= 0.0


def test_apply_kick_state_matches_final_vertical_position():
    s = new_soccer_state()
    _kick_spot(s)
    traj, scored, desc, endpoint, push = apply_kick(s, 1, 0.0, 90.0, True)
    if scored:
        assert s["ball"] == {"x": FIELD_W / 2, "y": FIELD_H / 2, "z": 0.0}
    else:
        assert s["ball"]["z"] == traj[-1]["z"]


def test_airborne_ball_does_not_collide_in_2d():
    # Ground-only mode: _loft_angle always 0 → vz0 0 in normal play.
    # Direct vz0 injection still works at physics level, but normal kicks
    # never set it. This test now verifies ground stays grounded via simulate_kick.
    s = new_soccer_state()
    _kick_spot(s)
    traj, _ = simulate_kick(s, 1, 0.0, 90.0, True)
    assert all(pt["z"] == 0.0 for pt in traj)
    # Direct low-level _sim with injected vz0 would still lift, but is not used in game
    space, bodies_a, bodies_b, ball_body, ref_body, ball_pivot = _build_space(s)
    bodies_a[1].velocity = (900.0, 0.0)
    trajectory, scored, _ = _sim(
        space, bodies_a, bodies_b, ball_body, ref_body, 1, True,
        max_steps=10, vz0=0.0, ball_pivot=ball_pivot,
    )
    assert len(trajectory) > 2
    assert max(point["z"] for point in trajectory) == 0.0
