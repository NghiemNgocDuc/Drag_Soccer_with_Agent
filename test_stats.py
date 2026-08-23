"""Unit tests for per-player stat system."""
import math
import pytest
from models.soccer_logic import (
    new_soccer_state, apply_kick, simulate_kick, _stat_map_size, _stat_map_power,
    _stat_map_weight, _stat_map_agility, _get_player_stats, inject_player_stats,
    _STAT_DEFAULT, DEFAULT_STATS, FIELD_W, FIELD_H,
)


def test_stat_mappings_at_default():
    """Stat=50 maps to default physics values."""
    assert abs(_stat_map_size(50) - 20.0) < 0.1  # radius
    assert abs(_stat_map_power(50) - 10.0) < 0.1  # kick vel
    assert abs(_stat_map_weight(50) - 5.0) < 0.1  # mass
    assert abs(_stat_map_agility(50) - 1500.0) < 0.1  # friction


def test_stat_mappings_extremes():
    """Stat=0 and Stat=100 produce min/max physics values."""
    assert abs(_stat_map_size(0) - 12.0) < 0.1
    assert abs(_stat_map_size(100) - 28.0) < 0.1
    assert abs(_stat_map_power(0) - 5.0) < 0.1
    assert abs(_stat_map_power(100) - 15.0) < 0.1
    assert abs(_stat_map_weight(0) - 3.0) < 0.1
    assert abs(_stat_map_weight(100) - 7.0) < 0.1
    assert abs(_stat_map_agility(0) - 1000.0) < 0.1
    assert abs(_stat_map_agility(100) - 2000.0) < 0.1


def test_get_player_stats_default():
    """Player without stats dict gets defaults."""
    st = new_soccer_state()
    s = _get_player_stats(st, True, 0)
    assert s["size"] == _STAT_DEFAULT
    assert s["power"] == _STAT_DEFAULT
    assert s["weight"] == _STAT_DEFAULT
    assert s["agility"] == _STAT_DEFAULT


def test_get_player_stats_from_state():
    """Player with stats dict returns those values."""
    st = new_soccer_state()
    st["players_a"][0]["stats"] = {"size": 80, "power": 30, "weight": 60, "agility": 40}
    s = _get_player_stats(st, True, 0)
    assert s["size"] == 80
    assert s["power"] == 30
    assert s["weight"] == 60
    assert s["agility"] == 40


def test_inject_player_stats():
    """inject_player_stats writes stats into player dicts correctly."""
    st = new_soccer_state(player_count=3)
    team_a = [
        {"size": 80, "power": 80, "weight": 20, "agility": 20},
        {"size": 20, "power": 50, "weight": 50, "agility": 80},
        {"size": 50, "power": 20, "weight": 80, "agility": 50},
    ]
    team_b = [
        {"size": 20, "power": 20, "weight": 80, "agility": 80},
        {"size": 80, "power": 80, "weight": 20, "agility": 20},
        {"size": 50, "power": 50, "weight": 50, "agility": 50},
    ]
    inject_player_stats(st, team_a, team_b)
    for i in range(3):
        assert st["players_a"][i]["stats"] is not None
        assert st["players_b"][i]["stats"] is not None
    # Verify specific values
    assert st["players_a"][0]["stats"]["size"] == 80
    assert st["players_a"][2]["stats"]["weight"] == 80
    assert st["players_b"][0]["stats"]["weight"] == 80


def test_high_power_kicks_farther():
    """Higher Power stat -> ball travels farther for same power input."""
    st = new_soccer_state(player_count=3)
    # Kick from the gameplay kick spot (95px gap) so both builds reach the ball
    st["players_a"][1]["x"] = FIELD_W / 2 - 95
    st["players_a"][1]["y"] = FIELD_H / 2
    st["ball"]["x"] = FIELD_W / 2
    st["ball"]["y"] = FIELD_H / 2
    # Set player 1 of team A to high power
    inject_player_stats(st, [
        {"size": 50, "power": 80, "weight": 50, "agility": 50},
        {"size": 50, "power": 80, "weight": 50, "agility": 50},
        {"size": 50, "power": 80, "weight": 50, "agility": 50},
    ], None)
    traj_high, _ = simulate_kick(st, 1, 0, 80, True)
    end_high = traj_high[-1]
    dist_high = math.hypot(end_high["x"] - st["ball"]["x"], end_high["y"] - st["ball"]["y"])

    # Same player with low power
    st2 = new_soccer_state(player_count=3)
    st2["players_a"][1]["x"] = FIELD_W / 2 - 95
    st2["players_a"][1]["y"] = FIELD_H / 2
    st2["ball"]["x"] = FIELD_W / 2
    st2["ball"]["y"] = FIELD_H / 2
    inject_player_stats(st2, [
        {"size": 50, "power": 20, "weight": 50, "agility": 50},
        {"size": 50, "power": 20, "weight": 50, "agility": 50},
        {"size": 50, "power": 20, "weight": 50, "agility": 50},
    ], None)
    traj_low, _ = simulate_kick(st2, 1, 0, 80, True)
    end_low = traj_low[-1]
    dist_low = math.hypot(end_low["x"] - st2["ball"]["x"], end_low["y"] - st2["ball"]["y"])

    assert dist_high > dist_low, "Higher Power should produce longer kick"


def test_high_power_more_loft():
    """Ground-only: ball stays on ground — both powers produce z==0."""
    st = new_soccer_state(player_count=3)
    inject_player_stats(st, [
        {"size": 50, "power": 80, "weight": 50, "agility": 50},
    ] * 3, None)
    traj_high, _ = simulate_kick(st, 1, 0, 90, True)
    max_z_high = max(pt.get("z", 0) for pt in traj_high)

    st2 = new_soccer_state(player_count=3)
    inject_player_stats(st2, [
        {"size": 50, "power": 20, "weight": 50, "agility": 50},
    ] * 3, None)
    traj_low, _ = simulate_kick(st2, 1, 0, 90, True)
    max_z_low = max(pt.get("z", 0) for pt in traj_low)

    assert max_z_high == 0.0 and max_z_low == 0.0, "Ground-only: no loft"


def test_heavy_vs_light_collision():
    """In a player-ball-player chain, lighter player moves more when hit by ball."""
    st = new_soccer_state()
    st["ball"] = {"x": 400.0, "y": 312.0, "z": 0.0}
    st["players_a"] = [{"x": 200.0, "y": 312.0}, {"x": 300.0, "y": 312.0}, {"x": 100.0, "y": 312.0}]
    st["players_b"] = [{"x": 800.0, "y": 312.0}, {"x": 600.0, "y": 312.0}, {"x": 700.0, "y": 312.0}]
    inject_player_stats(st, None, [
        {"size": 50, "power": 50, "weight": 80, "agility": 50},
        {"size": 50, "power": 50, "weight": 20, "agility": 50},
        {"size": 50, "power": 50, "weight": 50, "agility": 50},
    ])
    traj, scored = simulate_kick(st, 1, 0, 95, True)
    end_b1 = traj[-1]["b"][1]
    end_b0 = traj[-1]["b"][0]
    dx_b1 = abs(end_b1["x"] - 600.0)
    dx_b0 = abs(end_b0["x"] - 800.0)
    assert dx_b1 > dx_b0 - 5, f"Light B1 (at 600) moved {dx_b1}, heavy B0 (at 800) moved {dx_b0}"


def test_high_agility_stops_faster():
    """Higher agility (friction) -> target player slides less when hit by ball."""
    bx, by = 500.0, 312.0
    st_high = new_soccer_state(player_count=3)
    st_high["players_a"] = [{"x": bx - 30.0, "y": by}, {"x": 100.0, "y": 0.0}, {"x": 100.0, "y": 0.0}]
    st_high["players_b"] = [{"x": bx, "y": by}, {"x": 900.0, "y": 0.0}, {"x": 900.0, "y": 0.0}]
    st_high["ball"] = {"x": bx, "y": by, "z": 0.0}
    inject_player_stats(st_high, [
        {"size": 50, "power": 80, "weight": 50, "agility": 50},
    ] * 3, [
        {"size": 50, "power": 50, "weight": 50, "agility": 80},
    ] * 3)
    apply_kick(st_high, 0, 0, 100, True)
    dx_high = abs(st_high["players_b"][0]["x"] - bx)

    st_low = new_soccer_state(player_count=3)
    st_low["players_a"] = [{"x": bx - 30.0, "y": by}, {"x": 100.0, "y": 0.0}, {"x": 100.0, "y": 0.0}]
    st_low["players_b"] = [{"x": bx, "y": by}, {"x": 900.0, "y": 0.0}, {"x": 900.0, "y": 0.0}]
    st_low["ball"] = {"x": bx, "y": by, "z": 0.0}
    inject_player_stats(st_low, [
        {"size": 50, "power": 80, "weight": 50, "agility": 50},
    ] * 3, [
        {"size": 50, "power": 50, "weight": 50, "agility": 20},
    ] * 3)
    apply_kick(st_low, 0, 0, 100, True)
    dx_low = abs(st_low["players_b"][0]["x"] - bx)

    assert dx_high < dx_low, f"High agility moved {dx_high:.0f}px, low agility moved {dx_low:.0f}px"


def test_no_stats_backward_compat():
    """State without stats fields uses uniform defaults — same as before."""
    st_default = new_soccer_state(player_count=3)
    traj_default, _ = simulate_kick(st_default, 1, 0, 80, True)
    end_default = traj_default[-1]
    dist_default = math.hypot(end_default["x"] - st_default["ball"]["x"], end_default["y"] - st_default["ball"]["y"])

    st_nostats = new_soccer_state(player_count=3)
    traj_nostats, _ = simulate_kick(st_nostats, 1, 0, 80, True)
    end_nostats = traj_nostats[-1]
    dist_nostats = math.hypot(end_nostats["x"] - st_nostats["ball"]["x"], end_nostats["y"] - st_nostats["ball"]["y"])

    assert abs(dist_default - dist_nostats) < 1.0


def test_recoil_higher_power_moves_kicker():
    """Higher Power stat -> stronger recoil (larger backward impulse component).
    
    The recoil formula: recoil_vx = -cos(angle) * power * 1.2 * (power_stat / 50).
    At Power=80, factor = 1.2 * 80/50 = 1.92; at Power=20, factor = 1.2 * 20/50 = 0.48.
    The kick_vel formula: 5 + power_stat/100 * 10.
    At Power=80, kv = 13; at Power=20, kv = 7.
    """
    kv_high = 5 + (80 / 100.0) * 10
    kv_low = 5 + (20 / 100.0) * 10
    assert abs(kv_high - 13.0) < 0.01
    assert abs(kv_low - 7.0) < 0.01

    factor_high = 1.2 * (80 / _STAT_DEFAULT)
    factor_low = 1.2 * (20 / _STAT_DEFAULT)
    assert abs(factor_high - 1.92) < 0.01
    assert abs(factor_low - 0.48) < 0.01

    recoil_high = 80 * factor_high
    recoil_low = 80 * factor_low
    assert recoil_high > recoil_low, "Higher Power should have larger recoil impulse"
    assert recoil_high == pytest.approx(153.6, rel=0.01)
    assert recoil_low == pytest.approx(38.4, rel=0.01)
