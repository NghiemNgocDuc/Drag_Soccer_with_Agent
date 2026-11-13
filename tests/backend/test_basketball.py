"""Basketball rules, shared playback shape and deterministic physical moves."""
import copy
import math
import random

import pytest

from models import basketball_logic as basketball


def _carrier_at(state, side, x, y, index=4):
    state["players_" + side.lower()][index].update(x=x, y=y)
    state["possession"] = {"side": side, "player_idx": index}
    state["ball"] = {"x": x, "y": y, "z": 24}


def _shot(state, side=True):
    index, angle, power = basketball.basketball_ai_move(state, side)
    return basketball.apply_basketball_move(state, index, angle, power, side)


def test_constructor_uses_basketball_rules_despite_soccer_route_defaults():
    state = basketball.new_basketball_state(player_count=11, half_length=45, win_goal_limit=5)
    assert state["sport"] == "basketball"
    assert state["half_length"] == 0 and state["win_goal_limit"] == 21
    assert not state["penalty_shootout"]
    assert state["player_count"] == 5
    assert state["basketball"]["target_score"] == 21
    assert len(state["players_a"]) == len(state["players_b"]) == 5
    assert len({player["role"] for player in state["players_a"]}) == 5
    assert "goalkeeper" not in {player["role"].lower() for player in state["players_a"]}
    assert state["possession"]["side"] == "A"
    assert basketball.human_basketball_player_index(state) == 4


@pytest.mark.parametrize("side", [True, False])
@pytest.mark.parametrize("distance,points", [(230, 2), (360, 3), (650, 3)])
def test_real_descending_shots_score_two_or_three_from_both_ends(side, distance, points):
    state = basketball.new_basketball_state(team_movement=False)
    team = "A" if side else "B"
    x = 1300 - distance if side else 100 + distance
    _carrier_at(state, team, x, basketball.FIELD_H / 2)
    trajectory, scored, desc, endpoint, push = _shot(state, side)
    assert scored == team
    assert state["score_" + team.lower()] == points
    assert state["last_points"] == points
    assert f"{points}-point" in desc
    assert trajectory[-1]["z"] == basketball.HOOP_HEIGHT
    assert trajectory[-1]["points"] == points
    assert max(frame["z"] for frame in trajectory) > basketball.HOOP_HEIGHT
    assert trajectory[-2]["z"] > trajectory[-1]["z"]
    assert endpoint == trajectory[-1]["kicker"]
    assert push is None
    assert state["possession"]["side"] == ("B" if side else "A")
    assert state["is_player_a"] != side


def test_shot_aim_and_power_matter_and_ground_crossing_does_not_score():
    for angle, power in ((30, 70), (0, 15), (0, 45)):
        state = basketball.new_basketball_state(team_movement=False)
        _, scored, *_ = basketball.apply_basketball_move(state, 4, angle, power, True)
        assert scored is None
        assert state["score_a"] == state["score_b"] == 0
    state = basketball.new_basketball_state(team_movement=False)
    state["possession"] = None
    state["ball"] = {"x": 1300, "y": 437.5, "z": 0}
    _, scored, *_ = basketball.apply_basketball_move(state, 0, 180, 30, True)
    assert scored is None


def test_prediction_matches_live_and_does_not_mutate_input():
    state = basketball.new_basketball_state()
    before = copy.deepcopy(state)
    move = basketball.basketball_ai_move(state, True)
    predicted, goal = basketball.simulate_basketball_move(state, *move, True)
    assert state == before
    actual, scored, *_ = basketball.apply_basketball_move(state, *move, True)
    assert predicted == actual and goal == scored == "A"
    assert all(frame["sport"] == "basketball" and len(frame["a"]) == len(frame["b"]) == 5 for frame in actual)
    assert all(actual[i]["t"] < actual[i + 1]["t"] for i in range(len(actual) - 1))
    assert actual[0]["possession"]["side"] == "A"
    assert any(frame["possession"] is None for frame in actual)


def test_off_ball_command_moves_player_and_collects_loose_ball():
    state = basketball.new_basketball_state(team_movement=False)
    state["possession"] = None
    state["players_a"][0].update(x=400, y=300)
    state["ball"] = {"x": 520, "y": 300, "z": 0}
    trajectory, scored, *_ = basketball.apply_basketball_move(state, 0, 0, 70, True)
    assert scored is None
    assert state["players_a"][0]["x"] > 500
    assert state["possession"]["side"] == "A"
    assert state["possession"]["player_idx"] == 0
    assert any(frame.get("contact") for frame in trajectory)
    assert state["ball"]["x"] == state["players_a"][0]["x"]
    assert basketball.human_basketball_player_index(state) == 0


def test_commanded_defender_can_reach_and_steal_held_ball():
    state = basketball.new_basketball_state(team_movement=False)
    _carrier_at(state, "A", 700, 437.5)
    state["players_b"][0].update(x=820, y=437.5)
    state["players_b"][4].update(x=1100, y=200)
    trajectory, _, *_ = basketball.apply_basketball_move(state, 0, 180, 65, False)
    assert any(frame.get("contact") for frame in trajectory)
    assert state["possession"]["side"] == "B"
    assert state["possession"]["player_idx"] == 0


def test_pass_is_caught_by_teammate_and_human_control_follows_carrier():
    state = basketball.new_basketball_state(team_movement=False)
    _carrier_at(state, "A", 400, 400)
    state["players_a"][0].update(x=700, y=400)
    power = 300 / (10 * basketball._flight_time(basketball._CATCH_HEIGHT))
    trajectory, scored, *_ = basketball.apply_basketball_move(state, 4, 0, power, True)
    assert scored is None
    assert state["possession"]["player_idx"] == 0
    assert basketball.human_basketball_player_index(state) == 0
    assert any(frame.get("contact") for frame in trajectory)


@pytest.mark.parametrize("side", [True, False])
@pytest.mark.parametrize("action", ["pass", "shot"])
def test_released_arcs_clear_adjacent_discs_before_descending_catches(side, action):
    state = basketball.new_basketball_state(team_movement=False)
    team, opponent = ("A", "B") if side else ("B", "A")
    x = (400 if side else 1000) if action == "pass" else (1070 if side else 330)
    y = 400 if action == "pass" else basketball.FIELD_H / 2
    _carrier_at(state, team, x, y)
    state["players_" + opponent.lower()][4].update(x=x + (38 if side else -38), y=y)
    if action == "pass":
        state["players_" + team.lower()][0].update(x=700, y=y)
        power = 300 / (10 * basketball._flight_time(basketball._CATCH_HEIGHT))
        move = (4, 0 if side else 180, power)
    else:
        move = basketball.basketball_ai_move(state, side)
    trajectory, scored, *_ = basketball.apply_basketball_move(state, *move, side)
    # The adjacent defender used to collide with/catch the first rising
    # frames, making even a legal short pass or layup impossible.
    assert all(frame["possession"] is None for frame in trajectory if 0 < frame["t"] < .5)
    if action == "pass":
        assert scored is None
        assert basketball._read_possession(state) == (team, 0)
        assert next(frame["t"] for frame in trajectory if frame.get("contact")) > .8
    else:
        assert scored == team and state["last_points"] == 2


@pytest.mark.parametrize("side", [True, False])
def test_loose_ball_support_does_not_undo_the_rebounders_previous_approach(side):
    state = basketball.new_basketball_state(mode="aivai", power_cap=60)
    team = "a" if side else "b"
    for roster_side in ("a", "b"):
        for index, player in enumerate(state["players_" + roster_side]):
            x = 180 + index * 45
            player.update(x=x if side else basketball.FIELD_W - x,
                          y=150 if roster_side == team else 700)
    start_x, ball_x = (750, 1000) if side else (650, 400)
    state["players_" + team][4].update(x=start_x, y=400)
    state.update(possession=None, ball={"x": ball_x, "y": 400, "z": 0})
    basketball.apply_basketball_move(state, 4, 0 if side else 180, 60, side)
    after_approach = abs(state["players_" + team][4]["x"] - ball_x)
    assert state["possession"] is None
    basketball.apply_basketball_move(state, 0, 0, 0, not side)
    after_support = abs(state["players_" + team][4]["x"] - ball_x)
    # Both teams may chase the loose ball. The opponent's command must not
    # pull our nearest rebounder back toward a distant man-marking position.
    assert after_support < after_approach - 50


def test_basket_resets_retain_stats_identity_and_record_inbound():
    state = basketball.new_basketball_state(team_movement=False)
    player = state["players_a"][4]
    player.update(name="Maya", color="#abcdef", stats={"size": 80, "power": 65, "weight": 60, "agility": 40})
    _, scored, *_ = _shot(state)
    assert scored == "A"
    assert player["name"] == "Maya" and player["stats"]["power"] == 65
    assert state["move_history"][-1]["sport"] == "basketball"
    assert state["move_history"][-1]["points"] == 3
    assert state["move_history"][-1]["trajectory"]
    assert state["move_history"][-1]["mover"] == "a"
    assert state["move_history"][-1]["reset"]["possession"]["side"] == "B"


def test_first_to_twenty_one_requires_a_two_point_lead():
    state = basketball.new_basketball_state(team_movement=False)
    state.update(score_a=19, score_b=20)
    _carrier_at(state, "A", 1070, 437.5)
    _shot(state)
    assert state["score_a"] == 21 and not state["game_over"]
    _carrier_at(state, "A", 1070, 437.5)
    _shot(state)
    assert state["score_a"] == 23 and state["game_over"] and state["winner"] == "A"
    assert not state["penalty_shootout"]
    with pytest.raises(ValueError, match="finished"):
        basketball.apply_basketball_move(state, 4, 0, 50, True)


def test_stats_change_throwing_range_and_movement_distance():
    distances = []
    for stat in (0, 100):
        state = basketball.new_basketball_state(team_movement=False)
        _carrier_at(state, "A", 400, 100)
        state["players_a"][4]["stats"]["power"] = stat
        trajectory, *_ = basketball.apply_basketball_move(state, 4, 0, 50, True)
        distances.append(next(frame["x"] for frame in trajectory if frame["t"] >= 1.0))
    assert distances[1] > distances[0] + 150
    distances = []
    for agility in (0, 100):
        state = basketball.new_basketball_state(team_movement=False)
        state["players_a"][0].update(x=300, y=100)
        state["players_a"][0]["stats"]["agility"] = agility
        basketball.apply_basketball_move(state, 0, 0, 70, True)
        distances.append(state["players_a"][0]["x"])
    assert distances[0] > distances[1] + 40


def test_ai_can_complete_a_balanced_match_at_both_baskets():
    state = basketball.new_basketball_state()
    for _ in range(35):
        if state["game_over"]:
            break
        side = state["is_player_a"]
        move = basketball.basketball_ai_move(state, side)
        assert 0 <= move[0] < 5 and 0 <= move[2] <= 100
        trajectory, *_ = basketball.apply_basketball_move(state, *move, side)
        assert len(trajectory) <= basketball._MAX_STEPS + 2
        for frame in trajectory:
            for team in ("a", "b"):
                assert all(math.isfinite(player["x"]) and 25 <= player["x"] <= 1375
                           and 25 <= player["y"] <= 850 for player in frame[team])
    assert state["score_a"] >= 6 and state["score_b"] >= 6
    assert state["game_over"]


@pytest.mark.parametrize("cap,seed", [(60, 0), (60, 1), (60, 2), (80, 2), (100, 2), (120, 1)])
def test_ai_completes_matches_with_saved_power_limits_and_point_buy_stats(cap, seed, monkeypatch):
    state = basketball.new_basketball_state(mode="aivai", power_cap=cap)
    if seed:
        rng = random.Random(seed)
        for player in state["players_a"] + state["players_b"]:
            values = [0] * 4
            for _ in range(200):
                values[rng.choice([index for index, value in enumerate(values) if value < 100])] += 1
            player["stats"] = dict(zip(("size", "power", "weight", "agility"), values))
    # Keep this physical, full-match regression independent of CI CPU load;
    # separate tests verify the real deadline and cancelled prediction path.
    monkeypatch.setattr(basketball.time, "perf_counter", lambda: 0)
    for _ in range(400):
        if state["game_over"]:
            break
        side = state["is_player_a"]
        move = basketball.basketball_ai_move(state, side)
        assert 0 <= move[0] < 5 and 0 < move[2] <= min(cap, 100)
        basketball.apply_basketball_move(state, *move, side)
    assert state["game_over"], (cap, seed, state["score_a"], state["score_b"])
    assert max(state["score_a"], state["score_b"]) >= basketball.TARGET_SCORE
    assert abs(state["score_a"] - state["score_b"]) >= basketball.WIN_BY
    assert state["winner"] in ("A", "B")
    assert not state["penalty_shootout"]


def test_long_manual_sessions_bound_the_recorded_trajectory_payload():
    state = basketball.new_basketball_state(team_movement=False)
    state["move_history"] = [{"desc": "old move"} for _ in range(60)]
    basketball.apply_basketball_move(state, 0, 0, 10, True)
    assert len(state["move_history"]) == 60
    assert state["move_history"][-1]["trajectory"]


def test_ai_uses_a_reachable_teammate_when_the_hoop_is_out_of_range():
    state = basketball.new_basketball_state()
    _carrier_at(state, "B", 1215, 437.5)
    for index, player in enumerate(state["players_a"]):
        player.update(x=250 + index * 45, y=100)
    move = basketball.basketball_ai_move(state, False)
    assert move[0] == 4 and move[2] < 100
    _, scored, *_ = basketball.apply_basketball_move(state, *move, False)
    assert scored is None
    assert state["possession"]["side"] == "B"
    assert state["possession"]["player_idx"] != 4


@pytest.mark.parametrize("side", [True, False])
@pytest.mark.parametrize("held", [False, True])
def test_ai_chooses_reachable_stats_over_the_nearest_player(side, held):
    state = basketball.new_basketball_state(team_movement=False, power_cap=80)
    team = "A" if side else "B"
    own, other = state["players_" + team.lower()], state["players_b" if side else "players_a"]
    for index, player in enumerate(own):
        player.update(x=100 + index * 55, y=100)
    for index, player in enumerate(other):
        player.update(x=950 + index * 65, y=750)
    own[0].update(x=640, y=280, stats={"size": 50, "power": 0, "weight": 50, "agility": 100})
    own[1].update(x=490, y=400, stats={"size": 50, "power": 100, "weight": 50, "agility": 0})
    state["ball"] = {"x": 800, "y": 400, "z": 24 if held else 0}
    state["possession"] = basketball._possession("B" if side else "A", 4) if held else None
    if held:
        other[4].update(x=800, y=400)
    move = basketball.basketball_ai_move(state, side)
    assert move[0] == 1
    basketball.apply_basketball_move(state, *move, side)
    assert basketball._read_possession(state) == (team, 1)


@pytest.mark.parametrize("side", [True, False])
def test_ai_rebound_avoids_a_physical_blocker(side):
    state = basketball.new_basketball_state(team_movement=False)
    own, other = state["players_a" if side else "players_b"], state["players_b" if side else "players_a"]
    for index, player in enumerate(own):
        player.update(x=100 + index * 55, y=100)
    for index, player in enumerate(other):
        player.update(x=1000 + index * 55, y=750)
    own[0].update(x=600, y=400)
    own[1].update(x=650, y=560)
    other[0].update(x=680, y=400, stats={"size": 100, "power": 0, "weight": 100, "agility": 0})
    state["ball"], state["possession"] = {"x": 800, "y": 400, "z": 0}, None
    move = basketball.basketball_ai_move(state, side)
    assert move[0] == 1
    basketball.apply_basketball_move(state, *move, side)
    assert basketball._read_possession(state) == ("A" if side else "B", 1)


@pytest.mark.parametrize("side", [True, False])
def test_ai_can_score_when_the_carrier_is_directly_under_the_hoop(side):
    state = basketball.new_basketball_state(team_movement=False)
    team = "A" if side else "B"
    hoop = basketball.HOOPS[team]
    _carrier_at(state, team, hoop["x"], hoop["y"])
    move = basketball.basketball_ai_move(state, side)
    assert move[2] > 0
    _, scored, *_ = basketball.apply_basketball_move(state, *move, side)
    assert scored == team


def test_ai_recovery_checks_at_most_three_worlds_and_preserves_input(monkeypatch):
    state = basketball.new_basketball_state()
    state.update(possession=None, ball={"x": 1330, "y": 100, "z": 0}, power_cap=35)
    before = copy.deepcopy(state)
    calls = []
    real_run = basketball._run_move

    def counted_run(*args, **kwargs):
        calls.append(args[1:])
        return real_run(*args, **kwargs)

    monkeypatch.setattr(basketball, "_run_move", counted_run)
    first = basketball.basketball_ai_move(state, True)
    assert 1 <= len(calls) <= 3
    assert state == before
    assert 0 <= first[0] < 5 and 0 <= first[2] <= 35
    assert basketball.basketball_ai_move(state, True) == first


def test_ai_recovery_cancellation_returns_a_legal_analytic_fallback(monkeypatch):
    state = basketball.new_basketball_state(power_cap=35)
    state.update(possession=None, ball={"x": 1330, "y": 100, "z": 0})
    before = copy.deepcopy(state)
    attempted = []

    def cancel(*args, prediction_deadline=None):
        assert prediction_deadline is not None
        attempted.append(tuple(args[1:4]))
        raise basketball._PredictionCancelled

    monkeypatch.setattr(basketball, "_run_move", cancel)
    move = basketball.basketball_ai_move(state, True)
    assert state == before and len(attempted) == 1
    assert move == attempted[0]
    assert 0 <= move[0] < 5 and 0 <= move[1] < 360 and 0 <= move[2] <= 35


def test_ai_recovery_discards_a_cancelled_world_after_a_completed_move(monkeypatch):
    state = basketball.new_basketball_state(power_cap=35)
    state.update(possession=None, ball={"x": 1330, "y": 100, "z": 0})
    before = copy.deepcopy(state)
    real_run, attempted = basketball._run_move, []

    def finish_then_cancel(*args, **kwargs):
        attempted.append(tuple(args[1:4]))
        if len(attempted) > 1:
            raise basketball._PredictionCancelled
        return real_run(*args)

    monkeypatch.setattr(basketball, "_run_move", finish_then_cancel)
    move = basketball.basketball_ai_move(state, True)
    assert len(attempted) == 2 and move == attempted[0]
    assert state == before


def test_speculative_deadline_cancels_per_step_without_affecting_live_physics(monkeypatch):
    state = basketball.new_basketball_state()
    before = copy.deepcopy(state)
    move = (4, 0, 70)
    authoritative = basketball._run_move(state, *move, True)
    ticks = iter((0.0, 0.5, 2.0))
    monkeypatch.setattr(basketball.time, "perf_counter", lambda: next(ticks))
    with pytest.raises(basketball._PredictionCancelled):
        basketball._run_move(state, *move, True, prediction_deadline=1.0)
    assert state == before

    def forbidden_clock():
        raise AssertionError("Authoritative physics must not use the prediction deadline")

    monkeypatch.setattr(basketball.time, "perf_counter", forbidden_clock)
    assert basketball._run_move(state, *move, True) == authoritative
    assert state == before


def test_ai_crowded_defender_opens_a_fresh_steal_approach_inside_the_wall():
    state = basketball.new_basketball_state(team_movement=False)
    _carrier_at(state, "B", 65, 400)
    for player in state["players_a"]:
        player.update(x=45, y=400)
    before = copy.deepcopy(state)
    move = basketball.basketball_ai_move(state, True)
    assert state == before
    # Moving left would be pinned against the wall; retreat into the court.
    assert move[0] == 0 and move[2] > 0
    assert 0 < move[1] < 90
    basketball.apply_basketball_move(state, *move, True)
    defender = state["players_a"][move[0]]
    assert math.hypot(defender["x"] - 65, defender["y"] - 400) > 60


@pytest.mark.parametrize("cap", [0, 35, 60, 100])
@pytest.mark.parametrize("side", [True, False])
@pytest.mark.parametrize("possession_side", [None, "A", "B"])
def test_ai_commands_remain_legal_for_caps_and_asymmetric_stats(cap, side, possession_side):
    state = basketball.new_basketball_state(power_cap=cap)
    for team in ("a", "b"):
        for index, player in enumerate(state["players_" + team]):
            player["stats"] = {"size": 20 + index * 15, "power": 80 - index * 15,
                               "weight": 20 + index * 15, "agility": 80 - index * 15}
    if possession_side:
        player = state["players_" + possession_side.lower()][4]
        _carrier_at(state, possession_side, player["x"], player["y"])
    else:
        state["possession"] = None
        state["ball"] = {"x": 800, "y": 350, "z": 0}
    before = copy.deepcopy(state)
    move = basketball.basketball_ai_move(state, side)
    assert state == before
    assert 0 <= move[0] < 5
    assert 0 <= move[1] < 360 and math.isfinite(move[1])
    assert 0 <= move[2] <= cap and math.isfinite(move[2])
    trajectory, *_ = basketball.apply_basketball_move(state, *move, side)
    assert len(trajectory) <= basketball._MAX_STEPS + 2
    assert all(math.isfinite(frame[axis]) for frame in trajectory for axis in ("x", "y", "z"))


@pytest.mark.parametrize("angle,power,index", [(float("nan"), 20, 4), (0, float("inf"), 4), (0, 50, "invalid")])
def test_invalid_commands_fail_without_mutation(angle, power, index):
    state = basketball.new_basketball_state()
    before = copy.deepcopy(state)
    with pytest.raises(ValueError):
        basketball.apply_basketball_move(state, index, angle, power, True)
    assert state == before
