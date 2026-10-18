"""Player jobs influence real-outcome scoring without blocking emergencies."""
from copy import deepcopy

import pytest

from models.soccer_logic import FIELD_W, new_soccer_state, simulate_kick
from models.tactics import evaluate_outcome, quick_move, tactical_candidates


def role_state():
    state = new_soccer_state(player_count=5, team_movement=True)
    state["ball"].update(x=600.0, y=437.5)
    for player, xy in zip(state["players_a"],
                          [(90, 437.5), (400, 210), (430, 660), (650, 500), (1000, 437.5)]):
        player.update(x=xy[0], y=xy[1])
    for player, xy in zip(state["players_b"],
                          [(1310, 437.5), (1150, 180), (1150, 700), (1000, 100), (800, 750)]):
        player.update(x=xy[0], y=xy[1])
    return state


def end_frame(state, x=1000.0, y=437.5):
    return {"x": x, "y": y, "a": deepcopy(state["players_a"]),
            "b": deepcopy(state["players_b"])}


def role_bonus(state, move, end):
    manual = {**state, "team_movement": False}
    return (evaluate_outcome(state, True, move, [end], None)
            - evaluate_outcome(manual, True, move, [end], None))


def test_defender_can_pass_forward_without_abandoning_defensive_half():
    state = role_state()
    covered = end_frame(state)
    abandoned = deepcopy(covered)
    covered["a"][1].update(x=450, y=210)
    abandoned["a"][1].update(x=950, y=210)
    # Ball and all other players finish in identical places; only the selected
    # defender's real final position differs.
    assert evaluate_outcome(state, True, (1, 0, 80), [covered], None) > evaluate_outcome(
        state, True, (1, 0, 80), [abandoned], None)
    assert role_bonus(state, (1, 0, 80), covered) > role_bonus(state, (1, 0, 80), abandoned)


def test_support_values_receiving_teammate_more_than_an_uncontrolled_pass():
    state = role_state()
    received = end_frame(state)
    loose = end_frame(state, y=580)
    assert role_bonus(state, (3, 0, 70), received) > role_bonus(state, (3, 0, 70), loose)


def test_attacker_role_rewards_forward_play():
    state = role_state()
    forward = end_frame(state, x=800)
    backward = end_frame(state, x=400)
    assert role_bonus(state, (4, 0, 70), forward) > role_bonus(state, (4, 180, 70), backward)


@pytest.mark.parametrize("index", range(5))
def test_scoring_and_own_goal_avoidance_dominate_every_job(index):
    state = role_state()
    end = end_frame(state)
    assert evaluate_outcome(state, True, (index, 0, 100), [end], "A") == 10000
    assert evaluate_outcome(state, True, (index, 0, 100), [end], "B") == -10000


def test_emergency_clearance_bypasses_positional_discipline():
    state = role_state()
    state["ball"]["x"] = 140
    end = end_frame(state, x=500)
    end["a"][4].update(x=180, y=437.5)
    assert role_bonus(state, (4, 180, 100), end) == pytest.approx(0)


def test_only_reachable_attacker_can_clear_even_in_defensive_zone():
    state = role_state()
    state["ball"].update(x=180, y=437.5)
    for player in state["players_a"][:4]:
        player.update(x=800, y=200)
    state["players_a"][4].update(x=125, y=437.5)
    move = quick_move(state, True)
    assert move[0] == 4
    trajectory, scored = simulate_kick(state, *move, True)
    assert scored != "B"
    assert trajectory[-1]["x"] > 180


def test_explicit_allowed_player_remains_legal_regardless_of_role():
    state = role_state()
    state["ball"]["x"] = 1000
    assert all(move[0] == 1 for move in tactical_candidates(state, True, [1]))


def test_penalty_does_not_apply_ordinary_roles():
    state = role_state()
    state["penalty_shootout"] = True
    end = end_frame(state)
    assert role_bonus(state, (1, 0, 80), end) == pytest.approx(0)


def test_role_scoring_mirrors_and_leaves_match_unchanged():
    state = role_state()
    before = deepcopy(state)
    end = end_frame(state)
    reflected = deepcopy(state)
    reflected["ball"]["x"] = FIELD_W - state["ball"]["x"]
    other_end = {"x": FIELD_W - end["x"], "y": end["y"]}
    for dst, src in (("players_a", "players_b"), ("players_b", "players_a")):
        reflected[dst] = [{**p, "x": FIELD_W - p["x"]} for p in state[src]]
    other_end["a"] = [{**p, "x": FIELD_W - p["x"]} for p in end["b"]]
    other_end["b"] = [{**p, "x": FIELD_W - p["x"]} for p in end["a"]]
    assert evaluate_outcome(state, True, (1, 0, 80), [end], None) == pytest.approx(
        evaluate_outcome(reflected, False, (1, 180, 80), [other_end], None))
    assert state == before
