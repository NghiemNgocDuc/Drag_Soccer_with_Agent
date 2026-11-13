"""Offline evidence must improve held-out prediction before affecting tactics."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import math
import time

import pytest

from models import experience_policy as policy
from models.soccer_logic import new_soccer_state


def experience(count=48, samples=10):
    """Independent games with a repeatable possession/outcome relationship."""
    matches = []
    for game in range(count):
        winner = "A" if game % 2 else "B"
        rows = []
        for action in range(samples):
            side = "A" if action % 2 else "B"
            result = 1 if side == winner else -1
            before = [0.0] * policy.FEATURE_COUNT
            after = before.copy()
            before[2] = result * .15
            after[2] = result * (.75 + (game % 3) * .05)
            rows.append({"side": side, "before": before, "after": after, "scored": None})
        matches.append({"id": f"opaque-game-{game}", "sport": "soccer",
                        "rules_version": policy.RULES_VERSION, "winner": winner, "samples": rows})
    return matches


@pytest.fixture(autouse=True)
def clear_active():
    policy.set_active(None)
    yield
    policy.set_active(None)


@pytest.fixture
def promoted():
    report = policy.fit_candidate(experience())
    assert report["promoted"], report
    return report["candidate"]


def board():
    state = new_soccer_state(player_count=5)
    state["ball"].update(x=500, y=350)
    state["players_a"][4].update(x=420, y=350)
    state["players_b"][4].update(x=820, y=350)
    return state


def frame(state, x=650, y=350):
    return {"x": x, "y": y, "z": 0,
            "a": deepcopy(state["players_a"]), "b": deepcopy(state["players_b"])}


def test_features_are_bounded_detached_and_use_endpoint_tracks():
    state = board()
    endpoint = frame(state)
    endpoint["a"][4].update(x=640, y=350)
    original = deepcopy(state), deepcopy(endpoint)
    features = policy.compact_features(state, True, endpoint)
    final = deepcopy(state)
    final["ball"].update(x=endpoint["x"], y=endpoint["y"])
    final["players_a"], final["players_b"] = endpoint["a"], endpoint["b"]
    assert features == policy.compact_features(final, "A")
    assert len(features) == policy.FEATURE_COUNT
    assert all(math.isfinite(value) and -1 <= value <= 1 for value in features)
    assert (state, endpoint) == original


def test_features_mirror_for_both_teams():
    state = board()
    state.update(score_a=3, score_b=1)
    endpoint = frame(state)
    mirrored = deepcopy(state)
    mirrored["ball"]["x"] = 1400 - state["ball"]["x"]
    mirrored["score_a"], mirrored["score_b"] = state["score_b"], state["score_a"]
    mirrored["players_a"] = [{**player, "x": 1400 - player["x"]} for player in state["players_b"]]
    mirrored["players_b"] = [{**player, "x": 1400 - player["x"]} for player in state["players_a"]]
    mirrored_end = {**endpoint, "x": 1400 - endpoint["x"],
                    "a": [{**player, "x": 1400 - player["x"]} for player in endpoint["b"]],
                    "b": [{**player, "x": 1400 - player["x"]} for player in endpoint["a"]]}
    assert policy.compact_features(state, True, endpoint) == pytest.approx(policy.compact_features(mirrored, False, mirrored_end))


def test_power_and_braking_reach_follow_actual_stat_direction():
    state = board()
    # Keep every other player far away so the striker supplies nearest reach.
    for player in state["players_a"][:-1]:
        player.update(x=100, y=800)
    state["players_a"][4]["stats"] = {"power": 50, "agility": 50}
    normal = policy.compact_features(state, True)
    state["players_a"][4]["stats"]["power"] = 100
    powerful = policy.compact_features(state, True)
    state["players_a"][4]["stats"].update(power=50, agility=100)
    agile = policy.compact_features(state, True)
    assert powerful[3] > normal[3] > agile[3]


def test_learns_actual_outcome_signal_and_reports_prediction_evidence(promoted):
    metrics = promoted["metrics"]
    assert metrics["evaluation"] == "whole_match_prediction_mse"
    assert metrics["candidate_mse"] < metrics["baseline_mse"] - .005
    assert metrics["holdout_matches"] >= 4
    assert metrics["improved_match_fraction"] >= .6
    assert metrics["max_game_regret"] <= .05
    assert set(metrics["train_ids"]).isdisjoint(metrics["holdout_ids"])
    assert metrics["train_matches"] + metrics["holdout_matches"] == metrics["matches"]
    good, bad = [0.0] * 10, [0.0] * 10
    good[2], bad[2] = .8, -.8
    assert policy.predict(promoted, good) > policy.predict(promoted, bad)


def test_fit_is_deterministic_and_read_only():
    matches = experience()
    original = deepcopy(matches)
    first = policy.fit_candidate(matches)
    second = policy.fit_candidate(list(reversed(matches)))
    assert matches == original
    assert first == second


def test_entire_match_holdout_never_enters_fit_matrix(monkeypatch):
    import numpy as np

    matches = experience()
    matrices = []
    solve = np.linalg.solve

    def inspect(matrix, targets):
        matrices.append((matrix.copy(), targets.copy()))
        return solve(matrix, targets)

    monkeypatch.setattr(np.linalg, "solve", inspect)
    first = policy.fit_candidate(matches)
    for match in matches:
        if match["id"] in first["metrics"]["holdout_ids"]:
            match["winner"] = "Draw"
            for row in match["samples"]:
                row["after"][2] *= -1
    policy.fit_candidate(matches)
    assert len(matrices) == 2
    assert np.array_equal(matrices[0][0], matrices[1][0])
    assert np.array_equal(matrices[0][1], matrices[1][1])


@pytest.mark.parametrize("matches", [[], experience(19), experience(40, 2)])
def test_rejects_insufficient_completed_evidence(matches):
    report = policy.fit_candidate(matches)
    assert not report["promoted"]
    assert report["candidate"] is None
    assert report["reason"] == "insufficient_completed_match_evidence"


def test_rejects_duplicate_ids_unsupported_rules_and_nonfinite_samples():
    matches = experience(19)
    matches.extend(deepcopy(matches) * 2)
    unsupported = experience(60)
    for index, match in enumerate(unsupported):
        match["id"] += "-invalid"
        if index % 3 == 0:
            match["sport"] = "basketball"
        elif index % 3 == 1:
            match["rules_version"] = "soccer-other"
        else:
            for row in match["samples"]:
                row["after"][0] = float("nan")
    report = policy.fit_candidate(matches + unsupported)
    assert not report["promoted"]
    assert report["metrics"]["matches"] == 19


def test_rejects_conflicting_results_under_one_match_id():
    matches = experience(20)
    conflict = deepcopy(matches[0])
    conflict["winner"] = "Draw"
    report = policy.fit_candidate(matches + [conflict])
    assert report["metrics"]["matches"] == 19
    assert not report["promoted"]


def test_candidate_must_improve_incumbent_not_only_baseline(promoted):
    report = policy.fit_candidate(experience(), incumbent=promoted)
    assert not report["promoted"]
    assert report["reason"] == "no_holdout_improvement"
    assert report["metrics"]["candidate_mse"] == report["metrics"]["incumbent_mse"]


def test_unpredictable_or_inverted_signal_does_not_promote():
    matches = experience()
    for match in matches:
        for row in match["samples"]:
            row["after"][2] *= -1
    report = policy.fit_candidate(matches)
    assert not report["promoted"]
    assert report["reason"] == "structural_safety_failed"


def test_saturated_intercept_cannot_hide_backwards_progress_coefficient():
    model = {"weights": [-.1, 0, 0, 0, 0, -2, 0, 0, 0, 0], "bias": 2}
    assert not policy._structural_safety(model)


def test_each_observed_side_and_match_get_equal_weight():
    record = policy._detached_matches(experience(1))[0]
    duplicated = (record[0], record[1], record[2] + tuple(row for row in record[2] if row[0] == "A") * 20)
    observations = policy._observations(duplicated)
    assert sum(row[2] for row in observations) == pytest.approx(1)
    rows_a = [row for row, sample in zip(observations, duplicated[2]) if sample[0] == "A"]
    assert sum(row[2] for row in rows_a) == pytest.approx(.5)


def test_active_model_requires_valid_evidence_and_copies_coefficients(promoted):
    state, move = board(), (4, 0, 60)
    trajectory = [frame(state)]
    assert policy.outcome_adjustment(state, True, move, trajectory, None) == 0
    assert not policy.set_active({"weights": [0.0] * 10, "bias": 0.0, "validated": True})
    assert policy.set_active(promoted)
    identifier = policy.active_model_id()
    before = policy.outcome_adjustment(state, True, move, trajectory, None)
    promoted["weights"][2] = -4
    promoted["metrics"]["holdout_matches"] = 0
    assert policy.active_model_id() == identifier
    assert policy.outcome_adjustment(state, True, move, trajectory, None) == before
    assert not policy.set_active(promoted)
    assert policy.active_model_id() == identifier
    assert policy.set_active(None)
    assert policy.active_model_id() is None


@pytest.mark.parametrize("corrupt", ["schema", "version", "weights", "metrics", "id", "nan"])
def test_corrupt_or_stale_model_never_activates(promoted, corrupt):
    if corrupt == "schema":
        promoted["schema_version"] += 1
    elif corrupt == "version":
        promoted["validation_version"] = "older"
    elif corrupt == "weights":
        promoted["weights"] = [1.0]
    elif corrupt == "metrics":
        promoted["metrics"]["candidate_mse"] = 100
    elif corrupt == "id":
        promoted["id"] = "forged"
    else:
        promoted["weights"][0] = float("nan")
    assert not policy.set_active(promoted)
    assert policy.active_model_id() is None


@pytest.mark.parametrize("change", [
    {"sport": "basketball"}, {"penalty_shootout": True}, {"period": "penalties"},
    {"ball_type": "beach"}, {"ball_size": "large"}, {"ball_bounciness": "high"},
    {"power_cap": 80}, {"player_count": 7}, {"rules_version": "new-rules"},
])
def test_out_of_profile_inference_is_zero(promoted, change):
    assert policy.set_active(promoted)
    state = board()
    state.update(change)
    assert policy.outcome_adjustment(state, True, (4, 0, 60), [frame(state)], None) == 0


def test_goals_empty_trajectory_and_invalid_board_bypass_learning(promoted):
    assert policy.set_active(promoted)
    state = board()
    end = frame(state)
    for scored in ("A", "B"):
        assert policy.outcome_adjustment(state, True, (4, 0, 60), [end], scored) == 0
    for trajectory in ([], [None], [{"x": float("nan"), "y": 100}], [{"x": 100, "y": 100, "z": 5}]):
        assert policy.outcome_adjustment(state, True, (4, 0, 60), trajectory, None) == 0
    state["ball"]["x"] = float("inf")
    assert policy.outcome_adjustment(state, True, (4, 0, 60), [end], None) == 0


def test_correction_is_bounded_read_only_and_cheap(promoted):
    assert policy.set_active(promoted)
    state = board()
    trajectory = [frame(state, x=1300)]
    original = deepcopy(state), deepcopy(trajectory)
    start = time.perf_counter()
    for _ in range(500):
        adjustment = policy.outcome_adjustment(state, True, (4, 0, 60), trajectory, None)
        assert math.isfinite(adjustment) and abs(adjustment) <= 40
    assert time.perf_counter() - start < 3.0
    assert (state, trajectory) == original


def test_context_candidate_is_thread_isolated_and_snapshot_survives_update(promoted):
    assert policy.set_active(promoted)
    identifier = policy.active_model_id()
    with policy.using_model():
        policy.set_active(None)
        assert policy.active_model_id() == identifier
        with policy.using_model(None):
            assert policy.active_model_id() is None
        assert policy.active_model_id() == identifier
        with ThreadPoolExecutor(max_workers=1) as worker:
            assert worker.submit(policy.active_model_id).result() is None
    assert policy.active_model_id() is None
    with policy.using_model(promoted):
        assert policy.active_model_id() == identifier
    with pytest.raises(ValueError):
        with policy.using_model({"weights": [1] * 10}):
            pass
