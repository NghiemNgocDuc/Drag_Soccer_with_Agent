"""Contact geometry, team decisions and cheap AI fallbacks on real match states."""
from copy import deepcopy
import math

import pytest

from models.common import aim_through, progress_score
from models.soccer_logic import FIELD_W, FIELD_H, new_soccer_state, simulate_kick
from models.tactics import evaluate_outcome, quick_move, tactical_candidates


def _place(players, positions):
    for player, (x, y) in zip(players, positions):
        player.update(x=x, y=y)


def _pass_state(blocked=False):
    state = new_soccer_state(player_count=3)
    state["ball"].update(x=600.0, y=437.5)
    _place(state["players_a"], [(90, 437.5), (545, 437.5), (800, 560)])
    _place(state["players_b"], [(1310, 437.5), (850, 437.5),
                                (700, 498.75) if blocked else (1050, 200)])
    return state


def _mirror(state):
    result = deepcopy(state)
    result["ball"]["x"] = FIELD_W - state["ball"]["x"]
    for key, original in (("players_a", "players_b"), ("players_b", "players_a")):
        result[key] = [{**player, "x": FIELD_W - player["x"]}
                       for player in state[original]]
    return result


def _angle_difference(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


def test_progress_rewards_attack_direction_on_both_sides():
    assert progress_score(900, True, False) > progress_score(600, True, False)
    assert progress_score(500, False, False) > progress_score(800, False, False)
    for defensive in (False, True):
        assert progress_score(910, True, defensive) == progress_score(FIELD_W - 910, False, defensive)


def test_aim_is_an_absolute_contact_safe_angle_and_mirrors():
    angle = aim_through(800, 250, 700, 400, 0, 437.5)
    direct = math.degrees(math.atan2(150, -100)) % 360
    assert _angle_difference(angle, direct) < math.degrees(math.asin(32 / math.hypot(100, 150)))
    reflected = aim_through(FIELD_W - 800, 250, FIELD_W - 700, 400,
                            FIELD_W, 437.5)
    assert _angle_difference(reflected, 180 - angle) < 1e-9


@pytest.mark.parametrize("pawn", [(645, 437.5), (755, 437.5), (700, 382.5), (700, 492.5)])
@pytest.mark.parametrize("target", [(1400, 437.5), (0, 600)])
def test_aimed_launch_contacts_ball_in_real_physics(pawn, target):
    state = new_soccer_state(player_count=1)
    state["ball"].update(x=700, y=437.5)
    _place(state["players_a"], [pawn])
    _place(state["players_b"], [(1250, 200)])
    angle = aim_through(*pawn, 700, 437.5, *target)
    trajectory, _ = simulate_kick(state, 0, angle, 100, True)
    displacement = math.hypot(trajectory[-1]["x"] - 700, trajectory[-1]["y"] - 437.5)
    assert displacement > 10.0


@pytest.mark.parametrize("count", [1, 3, 7, 11])
def test_candidates_are_legal_mirrored_and_do_not_mutate_match(count):
    state = new_soccer_state(player_count=count, power_cap=55)
    original = deepcopy(state)
    candidates = tactical_candidates(state, True)
    opposite = tactical_candidates(_mirror(state), False)
    assert candidates
    assert len(candidates) == len(opposite)
    for move, reflected in zip(candidates, opposite):
        index, angle, power = move
        assert 0 <= index < count
        assert 0 <= angle < 360
        assert 0 <= power <= 55
        assert reflected[0] == index
        assert reflected[2] == pytest.approx(power)
        assert _angle_difference(reflected[1], 180 - angle) < 1e-7
    assert state == original


def test_reach_uses_actual_power_agility_and_match_cap():
    state = new_soccer_state(player_count=3, power_cap=60)
    state["ball"].update(x=700, y=437.5)
    _place(state["players_a"], [(70, 437.5), (580, 350), (600, 437.5)])
    state["players_a"][1]["stats"] = {"size": 50, "power": 20, "weight": 50, "agility": 80}
    state["players_a"][2]["stats"] = {"size": 50, "power": 80, "weight": 50, "agility": 20}
    candidates = tactical_candidates(state, True)
    assert all(move[0] == 2 for move in candidates)
    assert all(move[2] <= 60 for move in candidates)


def test_forced_player_is_respected_even_when_teammate_is_closer():
    state = _pass_state()
    assert all(move[0] == 0 for move in tactical_candidates(state, True, allowed_players=[0]))
    assert quick_move(state, True, allowed_players=[2])[0] == 2
    with pytest.raises(ValueError, match="eligible"):
        tactical_candidates(state, True, allowed_players=[])


def test_keeper_stays_home_when_an_outfielder_can_reach():
    state = _pass_state()
    state["players_a"][0].update(x=550, y=437.5)
    assert all(move[0] != 0 for move in tactical_candidates(state, True))


def test_keeper_clears_when_it_is_the_only_reachable_defender():
    state = new_soccer_state(player_count=3)
    state["ball"].update(x=140, y=437.5)
    _place(state["players_a"], [(85, 437.5), (600, 200), (650, 650)])
    _place(state["players_b"], [(1310, 437.5), (750, 200), (850, 650)])
    move = quick_move(state, True)
    assert move[0] == 0
    trajectory, scored = simulate_kick(state, *move, True)
    assert scored != "B"
    assert trajectory[-1]["x"] > 140


def test_open_pass_is_planned_and_blocked_receiver_is_skipped():
    state = _pass_state()
    move = quick_move(state, True)
    assert move[0] == 1
    assert move[2] < 90  # control the reception instead of always shooting at maximum power
    trajectory, _ = simulate_kick(state, *move, True)
    receiver = state["players_a"][2]
    assert math.hypot(trajectory[-1]["x"] - receiver["x"],
                      trajectory[-1]["y"] - receiver["y"]) < 65
    blocked_moves = tactical_candidates(_pass_state(blocked=True), True)
    assert not any(index == move[0] and _angle_difference(angle, move[1]) < 1
                   and abs(power - move[2]) < 1 for index, angle, power in blocked_moves)


def test_no_reachable_player_moves_support_closer_and_preserves_keeper():
    state = new_soccer_state(player_count=3, power_cap=35)
    state["ball"].update(x=900, y=437.5)
    _place(state["players_a"], [(800, 437.5), (450, 350), (300, 650)])
    _place(state["players_b"], [(1310, 437.5), (1000, 200), (1100, 650)])
    move = quick_move(state, True)
    assert move[0] == 1
    trajectory, _ = simulate_kick(state, *move, True)
    before = state["players_a"][move[0]]
    after = trajectory[-1]["a"][move[0]]
    assert math.hypot(after["x"] - 900, after["y"] - 437.5) < math.hypot(before["x"] - 900, before["y"] - 437.5)


def test_outcome_prefers_clearance_possession_and_goal_over_backward_play():
    state = _pass_state()
    move = (1, 0.0, 80.0)
    backward = [{"x": 380, "y": 437.5}]
    clear = [{"x": 800, "y": 560}]
    assert evaluate_outcome(state, True, move, clear, None) > evaluate_outcome(state, True, move, backward, None)
    controlled = [{"x": 800, "y": 560}]
    unsafe = [{"x": 850, "y": 437.5}]
    assert evaluate_outcome(state, True, move, controlled, None) > evaluate_outcome(state, True, move, unsafe, None)
    assert evaluate_outcome(state, True, move, clear, "A") > 9000
    assert evaluate_outcome(state, True, move, clear, "B") < -9000


def test_quick_move_does_not_create_a_physics_space(monkeypatch):
    import pymunk

    def forbidden(*args, **kwargs):
        raise AssertionError("quick decisions must not run speculative physics")

    monkeypatch.setattr(pymunk, "Space", forbidden)
    assert quick_move(_pass_state(), True)[0] == 1


def test_clean_near_goal_candidate_scores_with_real_physics():
    state = new_soccer_state(player_count=3)
    state["ball"].update(x=1050, y=437.5)
    _place(state["players_a"], [(90, 437.5), (995, 437.5), (1160, 650)])
    _place(state["players_b"], [(1300, 650), (950, 250), (850, 650)])
    move = quick_move(state, True)
    _, scored = simulate_kick(state, *move, True)
    assert scored == "A"
