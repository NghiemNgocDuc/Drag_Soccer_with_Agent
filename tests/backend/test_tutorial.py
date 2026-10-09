"""Tutorial checks use playable opponents and preserve existing lesson progress."""
import os

os.environ.setdefault("DEV_MODE", "1")

import pytest

import app as appmod
from services import tutorial
from services.game_analytics import MODEL_CATALOG


def lesson_targets():
    return sorted({target for lesson in tutorial.LESSONS
                   for target in (lesson.get("target_choice") or [lesson.get("opponent")])
                   if target and target not in {"random", "do_nothing"}})


def test_lesson_opponents_are_in_the_current_game_catalog():
    supported = {model["id"] for model in MODEL_CATALOG}
    assert set(lesson_targets()) <= supported
    assert set(tutorial.BUILTIN_OPPONENTS) == supported == set(appmod.MODELS)


@pytest.mark.parametrize("target", lesson_targets())
def test_real_opponents_play_a_tutorial_turn(monkeypatch, target):
    # Keep this a short real physics check, rather than five benchmark matches.
    # Both the user's starter and the built-in must run and kick successfully.
    monkeypatch.setattr(tutorial, "MAX_KICKS", 2)
    lesson = dict(tutorial.get_lesson(1), opponent=target)
    error, result = tutorial._run_one_match(
        lesson["starter"], tutorial.resolve_opponent(target), lesson)
    assert error is None
    assert result["kicks"] == 2
    assert result["game_over"] is False
    assert result["winner"] in {"A", "B", "Draw"}


@pytest.mark.parametrize("target", ["monte_carlo", "bayesian", "q_learning",
                                   "value_iteration", "policy_iteration", "minimax",
                                   "missing_model"])
def test_retired_and_unknown_opponents_fail_before_running(target):
    with pytest.raises(ValueError, match="Unknown tutorial opponent"):
        tutorial.resolve_opponent(target)


def test_unavailable_opponent_has_actionable_error(monkeypatch):
    from services import game_analytics
    monkeypatch.setattr(game_analytics, "_load_model", lambda name: None)
    result = tutorial.run_milestone_check(tutorial.get_lesson(3)["starter"], 3)
    assert result["passed"] is False
    assert result["games"] == 0
    assert result["error"] == "Tutorial opponent unavailable: greedy"


@pytest.mark.parametrize("target", tutorial.get_lesson(4)["target_choice"])
def test_choice_milestone_resolves_the_selected_model(monkeypatch, target):
    resolved = []
    real_resolve = tutorial.resolve_opponent

    def resolve(name):
        resolved.append(name)
        return real_resolve(name)

    outcomes = iter(["A", "B", "Draw", "A", "A"])

    def run_match(code, opponent, lesson):
        assert callable(opponent.get_ai_move)
        return None, {"winner": next(outcomes), "score_a": 1, "score_b": 0,
                      "kicks": 2, "game_over": True}

    monkeypatch.setattr(tutorial, "resolve_opponent", resolve)
    monkeypatch.setattr(tutorial, "_run_one_match", run_match)
    result = tutorial.run_milestone_check(tutorial.get_lesson(4)["starter"], 4, target)
    assert resolved == [target]
    assert result["target"] == target
    assert result["passed"] is True
    assert (result["wins"], result["losses"], result["draws"], result["games"]) == (3, 1, 1, 5)


@pytest.mark.parametrize("target", [None, "monte_carlo", "bayesian", "expectimax"])
def test_choice_milestone_rejects_targets_outside_its_lesson(monkeypatch, target):
    def unexpected_run(*args):
        pytest.fail("An invalid target must not start a match")

    monkeypatch.setattr(tutorial, "_run_one_match", unexpected_run)
    result = tutorial.run_milestone_check(tutorial.get_lesson(4)["starter"], 4, target)
    assert result["passed"] is False
    assert result["games"] == 0
    assert "potential_field, voronoi" in result["error"]


def test_stats_lesson_applies_the_same_build_to_both_teams(monkeypatch):
    builds = []
    real_inject = tutorial.inject_player_stats

    def inject(state, team_a, team_b):
        builds.append((team_a, team_b))
        real_inject(state, team_a, team_b)

    monkeypatch.setattr(tutorial, "MAX_KICKS", 2)
    monkeypatch.setattr(tutorial, "inject_player_stats", inject)
    lesson = tutorial.get_lesson(6)
    error, result = tutorial._run_one_match(
        lesson["starter"], tutorial.resolve_opponent(lesson["opponent"]), lesson)
    assert error is None
    assert result["kicks"] == 2
    assert len(builds) == 1
    assert builds[0][0] == builds[0][1] == lesson["stats_inject"]
    assert builds[0][0][2]["power"] == 85


def test_existing_progress_ids_and_optional_unlocks_are_preserved():
    assert tutorial.get_lesson(5)["slug"] == "beat-minimax"
    completed = {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.0}
    state = tutorial.unlock_state(completed)
    assert all(state[lesson] == "completed" for lesson in completed)
    assert state[6] == state[7] == "unlocked"
    assert tutorial.is_unlocked(7, completed)
    assert not tutorial.is_unlocked(5, {1, 2, 3})


@pytest.mark.parametrize("score_a, score_b, explicit_winner, expected", [
    (2, 0, None, "A"), (0, 2, None, "B"), (1, 1, None, "Draw"),
    (2, 0, "B", "B"), (0, 2, "A", "A"), (2, 0, "Draw", "Draw"),
])
def test_capped_tutorial_result_uses_score_and_preserves_finished_winners(
        monkeypatch, score_a, score_b, explicit_winner, expected):
    state = {"score_a": score_a, "score_b": score_b, "winner": explicit_winner,
             "game_over": explicit_winner is not None, "is_player_a": True}

    def kick(state, *args):
        state["kick_count"] += 1
        state["is_player_a"] = not state["is_player_a"]

    monkeypatch.setattr(tutorial, "new_soccer_state", lambda **kwargs: state)
    monkeypatch.setattr(tutorial, "execute_user_model", lambda *args: (0, 0.0, 0.0))
    monkeypatch.setattr(tutorial, "apply_kick", kick)
    monkeypatch.setattr(tutorial, "MAX_KICKS", 2)
    lesson = tutorial.get_lesson(1)
    error, result = tutorial._run_one_match(lesson["starter"], tutorial.DoNothingBot(), lesson)
    assert error is None
    assert result["winner"] == expected
    assert result["game_over"] is (explicit_winner is not None)
    assert state["winner"] == explicit_winner  # evaluation never rewrites live engine state


def test_learn_page_renders_supported_target_controls(monkeypatch):
    from db import tutorial as progress
    monkeypatch.setattr(progress, "get_progress", lambda user_id: {1: 1.0, 2: 1.0, 3: 1.0})
    with appmod.app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = "dev:tutorial-page-test"
            session["username"] = "Tutorial tester"
        response = client.get("/learn")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    for target in tutorial.get_lesson(4)["target_choice"]:
        assert f'value="{target}"' in page
    assert 'value="monte_carlo"' not in page
    assert 'value="bayesian"' not in page
    assert "Beat Expectimax" in page


@pytest.mark.parametrize("target, expected", [("potential_field", 200), ("voronoi", 200),
                                             ("monte_carlo", 400), ("bayesian", 400)])
def test_check_api_only_accepts_the_supported_lesson_choices(monkeypatch, target, expected):
    from db import tutorial as progress
    import threading

    jobs = []

    class CapturedThread:
        def __init__(self, *, target, args, daemon):
            jobs.append(args)

        def start(self):
            pass

    monkeypatch.setattr(threading, "Thread", CapturedThread)
    monkeypatch.setattr(progress, "get_progress", lambda user_id: {1: 1.0, 2: 1.0, 3: 1.0})
    monkeypatch.setattr(progress, "get_status", lambda *args: None)
    with appmod.app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = "dev:tutorial-api-test"
        response = client.post("/api/tutorial/check", json={
            "lesson_id": 4, "target": target, "code": tutorial.get_lesson(4)["starter"],
        })
    assert response.status_code == expected
    if expected == 200:
        assert response.json["status"] == "running"
        assert jobs[0][1:] == (4, tutorial.get_lesson(4)["starter"], target)
    else:
        assert not jobs
