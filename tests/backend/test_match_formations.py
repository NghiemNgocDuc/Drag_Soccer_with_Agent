"""Setup formations retain their jobs and placement throughout a match."""
from copy import deepcopy

import pytest

from models import soccer_logic as physics
from models.formations import FORMATIONS_5, formation_catalog, formation_id
from models.team_roles import team_roles


@pytest.mark.parametrize("side", ["a", "b"])
def test_default_formation_preserves_original_five_player_geometry(side):
    width, center = physics.FIELD_W, physics.FIELD_H / 2
    if side == "a":
        xs = (width * .062, width * .162, width * .281, width / 2 - 95)
    else:
        xs = (width * .938, width * .838, width * .719, width / 2 + 95)
    expected = [(xs[0], center), (xs[1], center - 45), (xs[1], center + 45),
                (xs[2], center), (xs[3], center)]
    assert physics._home_positions(5, side) == expected
    assert physics._home_positions(5, side, "2-1-1") == expected


@pytest.mark.parametrize("formation", FORMATIONS_5)
def test_formation_preview_matches_physics_and_stable_jobs(formation):
    preview = next(item for item in formation_catalog() if item["id"] == formation)
    state = physics.new_soccer_state(formation_a=formation, formation_b=formation)
    expected_roles = tuple(point["role"] for point in preview["positions"])
    assert team_roles(state, True) == team_roles(state, False) == expected_roles
    assert physics.human_player_index(state) == 4
    assert expected_roles[-1] == "attacker"
    assert len(preview["positions"]) == 5
    for index, (a, b, point) in enumerate(zip(state["players_a"], state["players_b"], preview["positions"])):
        assert point["number"] == index + 1
        assert a["x"] == pytest.approx(point["x"] * physics.FIELD_W)
        assert a["y"] == pytest.approx(point["y"] * physics.FIELD_H)
        assert a["x"] + b["x"] == pytest.approx(physics.FIELD_W)
        assert a["y"] == b["y"]
    # Live positions cannot reassign jobs.
    state["players_a"].reverse()
    assert team_roles(state, True) == expected_roles


def test_all_formations_are_distinct_and_catalog_is_not_mutable_shared_state():
    shapes = {tuple(physics._home_positions(5, "a", key)) for key in FORMATIONS_5}
    assert len(shapes) == len(FORMATIONS_5) == 4
    preview = formation_catalog()
    preview[0]["rows"].clear()
    preview[0]["positions"][0]["role"] = "attacker"
    assert formation_catalog()[0]["rows"] == [2, 1, 1]
    assert formation_catalog()[0]["positions"][0]["role"] == "goalkeeper"


@pytest.mark.parametrize("invalid", [None, "", "4-4-2", [], {}, True, 3])
def test_invalid_legacy_five_player_formation_falls_back_safely(invalid):
    assert formation_id(invalid) == "2-1-1"
    state = physics.new_soccer_state(formation_a=invalid, formation_b=invalid)
    assert state["formation_a"] == state["formation_b"] == "2-1-1"
    assert physics._home_positions(5, "a", invalid) == physics.HOME_A


@pytest.mark.parametrize("formation", FORMATIONS_5)
def test_reset_and_halftime_keep_formation_and_player_stats(formation):
    state = physics.new_soccer_state(formation_a=formation, formation_b="1-2-1")
    for side in ("a", "b"):
        for index, player in enumerate(state[f"players_{side}"]):
            player.update(name=f"{side}{index}", stats={**physics.DEFAULT_STATS, "size": 25 + index * 10})
    home = {side: deepcopy(state[f"players_{side}"]) for side in ("a", "b")}
    for side in ("a", "b"):
        for player in state[f"players_{side}"]:
            player.update(x=300, y=200)
    physics._reset_players(state)
    assert state["players_a"] == home["a"]
    assert state["players_b"] == home["b"]
    state["players_a"][4].update(x=800, y=200)
    physics._reset_outfield(state, "a")
    assert state["players_a"] == home["a"]
    state["players_b"][4].update(x=400, y=200)
    state["start_time"] = 1000
    assert physics.advance_match_clock(state, 1135)
    assert state["players_a"] == home["a"]
    assert state["players_b"] == home["b"]
    assert state["formation_a"] == formation and state["formation_b"] == "1-2-1"


@pytest.mark.parametrize("formation", FORMATIONS_5)
def test_goal_returns_both_squads_to_selected_formation(formation):
    state = physics.new_soccer_state(formation_a=formation, formation_b="1-1-2",
                                    half_length=0, team_movement=False)
    state["players_a"][4]["stats"] = {**physics.DEFAULT_STATS, "power": 70}
    home_a, home_b = deepcopy(state["players_a"]), deepcopy(state["players_b"])
    state["ball"].update(x=1367, y=physics.FIELD_H / 2)
    state["players_a"][4].update(x=1320, y=physics.FIELD_H / 2)
    state["players_b"][0].update(y=200)
    _, scored, *_ = physics.apply_kick(state, 4, 0, 80, True)
    assert scored == "A"
    assert state["score_a"] == 1
    assert state["players_a"] == home_a
    assert state["players_b"] == home_b


def test_unstarted_match_clock_and_periods_stay_frozen_until_explicit_start():
    state = physics.new_soccer_state()
    state.update(match_started=False, start_time=1000)
    before = deepcopy(state)
    assert not physics.advance_match_clock(state, 10000)
    clock = physics.match_clock(state, 10000)
    assert not clock["clock_running"] and clock["next_period_at"] is None
    assert clock["match_elapsed"] == clock["match_clock_seconds"] == 0
    assert state == before
    state.update(match_started=True, start_time=10000)
    assert physics.match_clock(state, 10003)["match_clock_seconds"] == 60
    assert physics.advance_match_clock(state, 10135)
    assert state["period"] == "regular_second"


def test_missing_setup_flag_keeps_simulations_and_legacy_matches_running():
    state = physics.new_soccer_state()
    assert "match_started" not in state
    state["start_time"] = 1000
    assert physics.match_clock(state, 1003)["match_clock_seconds"] == 60
    assert physics.advance_match_clock(state, 1135)


def test_five_player_ids_do_not_override_seven_player_formations():
    state = physics.new_soccer_state(player_count=7, formation_a="3-3-0", formation_b="2-1-2-1")
    assert state["formation_a"] == "3-3-0"
    assert state["formation_b"] == "2-1-2-1"
    assert team_roles(state, True) == ("goalkeeper",) + ("defender",) * 3 + ("support",) * 3
    assert team_roles(state, False) == ("goalkeeper",) + ("defender",) * 2 + ("support",) * 3 + ("attacker",)
