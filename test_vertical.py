"""Test vertical-axis (z) ball physics extension in soccer_logic.py"""
from models.soccer_logic import (
    new_soccer_state, simulate_kick, apply_kick, _loft_angle, G, _VZ_MIN,
    FIELD_W, FIELD_H,
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
    assert _loft_angle(30) == 0.0
    assert _loft_angle(40) == 0.0
    assert _loft_angle(60) == 10.0
    assert _loft_angle(80) == 20.0
    assert _loft_angle(100) == 30.0
    assert _loft_angle(120) == 30.0  # capped


def test_low_power_stays_on_ground():
    s = new_soccer_state()
    traj, _ = simulate_kick(s, 1, 0.0, 30.0, True)
    for pt in traj:
        assert "z" in pt
        assert pt["z"] == 0.0


def test_high_power_has_loft():
    s = new_soccer_state()
    _kick_spot(s)
    traj, _ = simulate_kick(s, 1, 0.0, 90.0, True)
    max_z = max(pt["z"] for pt in traj)
    assert max_z > 0.0
    assert traj[-1]["z"] >= 0.0


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
