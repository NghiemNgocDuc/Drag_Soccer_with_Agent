"""Physical checks reject regressions without changing live inference models."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from models import experience_policy as policy
from services import learning_fitness as fitness


def learned_model():
    matches = []
    for game in range(48):
        winner = "A" if game % 2 else "B"
        samples = []
        for action in range(10):
            side = "A" if action % 2 else "B"
            sign = 1 if side == winner else -1
            before, after = [0.0] * 10, [0.0] * 10
            before[2], after[2] = sign * .15, sign * (.75 + game % 3 * .05)
            samples.append({"side": side, "before": before, "after": after, "scored": None})
        matches.append({"id": f"opaque-game-{game}", "sport": "soccer",
                        "rules_version": policy.RULES_VERSION, "winner": winner, "samples": samples})
    report = policy.fit_candidate(matches)
    assert report["promoted"], report
    return report["candidate"]


@pytest.fixture(autouse=True)
def clear_active():
    policy.set_active(None)
    yield
    policy.set_active(None)


def test_real_baseline_passes_all_ten_scenarios_without_state_or_model_changes(monkeypatch):
    model = learned_model()
    assert policy.set_active(model)
    identifier = policy.active_model_id()
    fixtures = list(fitness._fixtures())
    original = deepcopy(fixtures)
    monkeypatch.setattr(fitness, "_fixtures", lambda: iter(fixtures))
    report = fitness.evaluate_candidate(None)
    assert report["passed"], report
    assert report["evaluation"] == "physical_scenarios_not_win_rate"
    assert report["version"] == fitness.FITNESS_VERSION
    assert len(report["scenarios"]) == 10
    assert {row["name"] for row in report["scenarios"]} == {
        f"{kind}:{side}" for kind in ("opening", "shot", "clearance", "pass", "weak_striker")
        for side in ("A", "B")}
    assert all(row["passed"] and not row["changed_action"] for row in report["scenarios"])
    assert report["value_delta"] == 0
    assert fixtures == original
    assert policy.active_model_id() == identifier


def test_real_candidate_ranking_is_isolated_and_measured_with_fixed_scorer(monkeypatch):
    from models import tactics

    model = learned_model()
    _, state, side = next(fitness._fixtures())
    original = deepcopy(state)
    seen = []
    evaluate = tactics.evaluate_outcome

    def inspect(*args, **kwargs):
        seen.append(policy.active_model_id())
        return evaluate(*args, **kwargs)

    monkeypatch.setattr(tactics, "evaluate_outcome", inspect)
    move, value, goal = fitness._choose(state, side, model, fitness.time.monotonic() + fitness.MAX_SECONDS)
    assert seen and seen[-1] is None
    assert all(identifier == model["id"] for identifier in seen[:-1])
    assert policy.active_model_id() is None
    assert 0 <= move[0] < 5 and 0 <= move[1] < 360 and 0 <= move[2] <= 100
    assert -10000 <= value <= 10000 and goal in (None, "A", "B")
    assert state == original


def test_deadline_expiry_rejects_candidate_and_preserves_active_model(monkeypatch):
    model = learned_model()
    assert policy.set_active(model)
    identifier = policy.active_model_id()
    clock_calls = 0

    def expired_clock():
        nonlocal clock_calls
        clock_calls += 1
        return 0 if clock_calls == 1 else fitness.MAX_SECONDS + 1

    monkeypatch.setattr(fitness, "time", SimpleNamespace(monotonic=expired_clock))
    report = fitness.evaluate_candidate(None)
    assert report["passed"] is False
    assert report["reason"] == "TimeoutError"
    assert report["scenarios"] == []
    assert policy.active_model_id() == identifier


@pytest.mark.parametrize("side,baseline,candidate", [
    (True, (0.0, None), (10000.0, "B")),
    (False, (0.0, None), (10000.0, "A")),
    (True, (10000.0, "A"), (9999.0, None)),
    (False, (10000.0, "B"), (9999.0, None)),
    (True, (100.0, None), (94.9, None)),
])
def test_own_goals_missed_guaranteed_scores_and_material_regressions_fail(monkeypatch, side, baseline, candidate):
    fixture = next(fitness._fixtures())
    monkeypatch.setattr(fitness, "_fixtures", lambda: iter([("regression", fixture[1], side)]))
    candidate_marker = object()

    def choose(state, selected_side, model, deadline):
        score, goal = candidate if model is candidate_marker else baseline
        return (4, 0, 80), score, goal

    monkeypatch.setattr(fitness, "_choose", choose)
    report = fitness.evaluate_candidate(candidate_marker)
    assert not report["passed"]
    assert report["scenarios"][0]["passed"] is False


def test_small_per_scenario_losses_cannot_hide_negative_total_delta(monkeypatch):
    fixtures = list(fitness._fixtures())[:2]
    monkeypatch.setattr(fitness, "_fixtures", lambda: iter(fixtures))
    candidate_marker = object()

    def choose(state, side, model, deadline):
        return (4, 0, 80), 99.0 if model is candidate_marker else 100.0, None

    monkeypatch.setattr(fitness, "_choose", choose)
    report = fitness.evaluate_candidate(candidate_marker)
    assert all(row["passed"] for row in report["scenarios"])
    assert report["value_delta"] == -2.0
    assert report["passed"] is False


def test_balanced_bounded_tradeoffs_can_pass(monkeypatch):
    fixtures = list(fitness._fixtures())[:2]
    monkeypatch.setattr(fitness, "_fixtures", lambda: iter(fixtures))
    candidate_marker = object()

    def choose(state, side, model, deadline):
        value = (96.0 if side else 104.0) if model is candidate_marker else 100.0
        return ((4, 10, 80) if model is candidate_marker else (4, 0, 80)), value, None

    monkeypatch.setattr(fitness, "_choose", choose)
    report = fitness.evaluate_candidate(candidate_marker)
    assert report["passed"]
    assert report["value_delta"] == 0
    assert all(row["passed"] and row["changed_action"] for row in report["scenarios"])


def test_invalid_candidate_fails_without_replacing_active_model():
    active = learned_model()
    assert policy.set_active(active)
    report = fitness.evaluate_candidate({"weights": [1.0] * 10, "validated": True})
    assert report["passed"] is False
    assert report["reason"] == "ValueError"
    assert policy.active_model_id() == active["id"]
