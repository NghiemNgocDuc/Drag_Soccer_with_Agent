"""Regression tests for authoritative kick trajectories and playback metadata."""
import copy

import pytest

from db.highlights import max_speed
from models.soccer_logic import (
    FIELD_H, FIELD_W, _PM_DT, _build_space, _sample_trajectory, _sim,
    apply_kick, inject_player_stats, new_soccer_state, simulate_kick,
)


@pytest.mark.parametrize("count", [3, 7, 11])
@pytest.mark.parametrize("side_a", [True, False])
@pytest.mark.parametrize("power_stat", [20, 80])
def test_prediction_matches_actual_kick(count, side_a, power_stat):
    state = new_soccer_state(player_count=count)
    players = state["players_a"] if side_a else state["players_b"]
    players[-1].update(x=FIELD_W / 2 + (-60 if side_a else 60), y=FIELD_H / 2)
    stats = [{"size": 50, "power": power_stat, "weight": 50, "agility": 50}] * count
    inject_player_stats(state, stats, stats)
    original = copy.deepcopy(state)
    args = (count - 1, 0 if side_a else 180, 100, side_a)
    predicted, predicted_goal = simulate_kick(state, *args)
    assert state == original
    played, actual_goal, *_ = apply_kick(state, *args)
    assert predicted == played
    assert predicted_goal == actual_goal


def test_wall_bounce_keeps_both_teams_and_timestamps():
    state = new_soccer_state(player_count=7)
    state["ball"].update(x=FIELD_W / 2, y=80)
    state["players_a"][-1].update(x=FIELD_W / 2, y=140)
    trajectory, *_ = apply_kick(state, 6, -90, 100, True)
    impacts = [point for point in trajectory if point.get("bounce")]
    assert impacts
    for point in trajectory:
        assert len(point["a"]) == len(point["b"]) == 7
    assert trajectory[0]["t"] == 0
    assert all(p1["t"] > p0["t"] for p0, p1 in zip(trajectory, trajectory[1:]))


@pytest.mark.parametrize("side_a", [True, False])
def test_predicted_goal_matches_play_and_kickoff_reset(side_a):
    state = new_soccer_state()
    bx = FIELD_W - 60 if side_a else 60
    state["ball"].update(x=bx, y=FIELD_H / 2 - 50)
    players = state["players_a"] if side_a else state["players_b"]
    players[-1].update(x=bx + (-60 if side_a else 60), y=state["ball"]["y"])
    args = (len(players)-1, 0 if side_a else 180, 100, side_a)
    predicted, predicted_goal = simulate_kick(state, *args)
    played, actual_goal, *_ = apply_kick(state, *args)
    assert predicted_goal == actual_goal == ("A" if side_a else "B")
    assert predicted == played
    assert state["score_a" if side_a else "score_b"] == 1
    assert state["ball"] == {"x": FIELD_W / 2, "y": FIELD_H / 2, "z": 0.0}


def test_sampling_preserves_impact_neighbors_and_exact_final_frame():
    trajectory = [{"x": i, "y": 100, "z": 0, "t": i * _PM_DT} for i in range(501)]
    trajectory[123]["bounce"] = True
    sampled = _sample_trajectory(trajectory)
    assert trajectory[122] in sampled
    assert trajectory[123] in sampled
    assert sampled[-1] is trajectory[-1]
    assert sum(point is trajectory[-1] for point in sampled) == 1
    assert all(p1["t"] > p0["t"] for p0, p1 in zip(sampled, sampled[1:]))


def test_vertical_flight_is_not_cut_short_when_horizontal_bodies_settle():
    state = new_soccer_state()
    space, a, b, ball, referee, pivot = _build_space(state)
    trajectory, *_ = _sim(space, a, b, ball, referee, 0, True, vz0=120, ball_pivot=pivot)
    assert max(point["z"] for point in trajectory) > 5
    assert trajectory[-1]["z"] == 0
    assert trajectory[-1]["t"] > .2


def test_highlight_speed_uses_actual_sampling_intervals():
    # A retained impact creates a shorter interval than the regular stride.
    trajectory = [{"x": 0, "y": 0, "t": 0},
                  {"x": 60, "y": 0, "t": .1},
                  {"x": 66, "y": 0, "t": .11}]
    assert max_speed(trajectory) == pytest.approx(600)
    legacy = [{"x": 0, "y": 0}, {"x": 10, "y": 0}]
    assert max_speed(legacy) == pytest.approx(600)
