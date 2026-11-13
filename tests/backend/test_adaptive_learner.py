"""Live policy fitness and isolated legacy self-play regressions."""
import copy
import math

import pytest

from models import adaptive_learner as learner
from models import soccer_logic as physics
from models import tactics


@pytest.fixture(autouse=True)
def isolated_learner(monkeypatch):
    monkeypatch.setattr(learner, "_Q", {})
    monkeypatch.setattr(learner, "_META", {"games": 0, "wins": 0})
    monkeypatch.setattr(learner, "_save", lambda: None)


@pytest.mark.parametrize("count", [1, 3, 5, 11])
@pytest.mark.parametrize("side", [True, False])
def test_untrained_live_policy_is_deterministic_tactical_and_read_only(count, side, monkeypatch):
    state = physics.new_soccer_state(player_count=count)
    expected = tactics.quick_move(state, side)
    monkeypatch.setattr(learner.random, "random", lambda: pytest.fail("Live AI explored"))
    state["_learner_last"] = {"a": 0}
    assert learner.get_ai_move(state, side) == expected
    assert learner.get_ai_move(state, side) == expected
    assert learner._Q == {}
    assert "_learner_last" not in state


@pytest.mark.parametrize("side", [True, False])
def test_five_player_policy_can_choose_striker_despite_legacy_keeper_bias(side):
    state = physics.new_soccer_state(player_count=5)
    roster = state["players_a" if side else "players_b"]
    opponents = state["players_b" if side else "players_a"]
    for player in roster:
        player.update(x=80 if side else physics.FIELD_W - 80, y=100)
    for player in opponents:
        player.update(x=physics.FIELD_W - 80 if side else 80, y=100)
    roster[4].update(x=750 if side else physics.FIELD_W - 750, y=physics.FIELD_H / 2)
    state["ball"].update(x=780 if side else physics.FIELD_W - 780, y=physics.FIELD_H / 2)
    learner._Q[learner._key_for(state, side)] = [1000.0] + [0.0] * 35
    assert learner.get_ai_move(state, side)[0] == 4


def test_existing_legacy_table_selects_best_valid_action_without_random(monkeypatch):
    state = physics.new_soccer_state(player_count=2)
    learner._Q[learner._key_for(state, True)] = list(range(36))
    monkeypatch.setattr(learner.random, "random", lambda: pytest.fail("Live AI explored"))
    move = learner.get_ai_move(state, True)
    assert move == learner._action_to_move(23, state, True)
    assert state["_learner_last"]["a"] == 23


@pytest.mark.parametrize("row", [[0.0] * 36, [8.0] * 36,
                                 [float("nan")] * 36, [float("inf")] * 36,
                                 [-10.0] * 35 + [-1.0], [10.0]])
def test_uninformative_or_corrupt_q_rows_use_tactics(row):
    state = physics.new_soccer_state(player_count=3)
    learner._Q[learner._key_for(state, True)] = row
    assert learner.get_ai_move(state, True) == tactics.quick_move(state, True)
    assert "_learner_last" not in state


@pytest.mark.parametrize("x,y", [(80, 100), (450, 350), (1000, 600), (1390, 430)])
def test_position_value_is_mirrored_between_sides(x, y):
    opponents = [{"x": x + 20, "y": y + 15}]
    mirrored = [{"x": physics.FIELD_W - p["x"], "y": p["y"]} for p in opponents]
    assert learner._epv(x, y, True, opponents) == pytest.approx(
        learner._epv(physics.FIELD_W - x, y, False, mirrored))


def test_reward_pays_for_improvement_and_is_mirrored():
    stationary = {"x": 900, "y": 430}
    assert learner._reward_for(stationary, None, "A", True, False, 900,
                               start_y=430) == pytest.approx(0)
    end = {"x": 1050, "y": 410}
    forward = learner._reward_for(end, None, "A", True, False, 900, start_y=430)
    reverse = learner._reward_for({"x": 900, "y": 430}, None, "A", True, False,
                                  1050, start_y=410)
    mirrored = learner._reward_for({"x": physics.FIELD_W - 1050, "y": 410},
                                   None, "B", False, False,
                                   physics.FIELD_W - 900, start_y=430)
    assert forward > 0
    assert reverse == pytest.approx(-forward)
    assert mirrored == pytest.approx(forward)
    assert learner._reward_for(end, "B", "A", True, False, 900) == -120
    assert learner._reward_for(end, "A", "A", True, False, 900) == 120


@pytest.mark.parametrize("next_side,terminal,expected", [
    (True, False, learner.ALPHA * (2 + learner.GAMMA * 10)),
    (False, False, learner.ALPHA * 2),
    (True, True, learner.ALPHA * 2),
])
def test_bootstrap_waits_for_own_next_decision(next_side, terminal, expected):
    state = physics.new_soccer_state(player_count=1)
    next_state = copy.deepcopy(state)
    next_state["ball"]["x"] = 1000
    next_state["is_player_a"] = next_side
    next_state["game_over"] = terminal
    # Large values for nonexistent pawns must not inflate the bootstrap.
    learner._Q[learner._key_for(next_state, True)] = [10.0] * 12 + [999.0] * 24
    learner.note_transition(state, 0, 2, next_state, True, save=False)
    assert learner._Q[learner._key_for(state, True)][0] == pytest.approx(expected)


def test_status_does_not_reload_or_advertise_live_exploration(monkeypatch):
    monkeypatch.setattr(learner, "_load", lambda: pytest.fail("Status reloaded training state"))
    learner._Q["test"] = [1.0] * 36
    stats = learner.get_stats()
    assert stats["states"] == 1
    assert stats["epsilon"] == 0
    assert stats["training_epsilon"] > 0


def test_offline_training_waits_for_opponent_and_publishes_complete_batch(monkeypatch):
    from models import search_budget
    from services import game_analytics

    monkeypatch.setattr(game_analytics, "_load_model", lambda _: object())
    monkeypatch.setattr(search_budget, "get_model_move", lambda *_: (0, 0, 50))
    monkeypatch.setattr(learner.random, "random", lambda: 1)
    transitions = []
    original_transition = learner.note_transition

    def record_transition(prev, action, reward, nxt, side, save=True):
        transitions.append(None if nxt is None else nxt["kick_count"])
        assert learner._Q == {}, "Partial candidate escaped to live policy"
        return original_transition(prev, action, reward, nxt, side, save)

    def kick(state, index, angle, power, side):
        assert learner._Q == {}, "Partial candidate escaped to live policy"
        state["kick_count"] += 1
        state["ball"]["x"] += 50
        state["is_player_a"] = not side
        if state["kick_count"] == 4:
            state.update(game_over=True, winner="B")
        return [dict(state["ball"])], None, "", None, None

    monkeypatch.setattr(learner, "note_transition", record_transition)
    monkeypatch.setattr(physics, "apply_kick", kick)
    result = learner.train_self_play(1)
    assert transitions == [2, None]
    assert result["completed_games"] == 1
    assert result["wins"] == 0
    assert result["epsilon"] == 0
    assert learner._META["games"] == 1
    assert learner._Q
    assert not learner._TRAIN_LOCK.locked()


def test_failed_offline_batch_keeps_live_table_and_releases_training(monkeypatch):
    from services import game_analytics

    monkeypatch.setattr(game_analytics, "_load_model", lambda _: object())
    learner._Q["original"] = [2.0] * 36
    original = copy.deepcopy(learner._Q)

    def broken_kick(*args):
        raise RuntimeError("Failed simulation")

    monkeypatch.setattr(physics, "apply_kick", broken_kick)
    with pytest.raises(RuntimeError, match="Failed simulation"):
        learner.train_self_play(1)
    assert learner._Q == original
    assert not learner._TRAIN_LOCK.locked()
    assert learner._training.get() is None
