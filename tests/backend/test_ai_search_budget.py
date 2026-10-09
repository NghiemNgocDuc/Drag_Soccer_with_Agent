"""Bounded decisions preserve useful moves, physical outcomes and worker capacity."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import importlib
import math
import threading
from types import SimpleNamespace

import pytest

import app as appmod
from models import search_budget as budget, soccer_logic as physics, tactics


@contextmanager
def active_search(state, side=True, **kwargs):
    search = budget.SearchBudget(state, side, None, 10, 20, **kwargs)
    token = budget._current.set(search)
    try:
        yield search
    finally:
        budget._current.reset(token)


def test_limit_retains_best_completed_prediction_and_unwinds_expert(monkeypatch):
    model = importlib.import_module("models.greedy_model")
    state = physics.new_soccer_state(player_count=3)
    moves = [(2, 0.0, 100.0), (2, 90.0, 50.0)]
    expected = max(moves, key=lambda move: tactics.evaluate_outcome(
        state, True, move, *physics.simulate_kick(state, *move, True)))
    monkeypatch.setattr(tactics, "tactical_candidates", lambda *_a, **_k: moves)
    continued = []

    def expert(state, side):
        # Even broad legacy handlers must not restart work after exhaustion.
        try:
            physics.simulate_kick(state, 2, 180, 100, side)
        except Exception:
            continued.append(True)
        return moves[1]

    monkeypatch.setattr(model, "get_ai_move", expert)
    info = {}
    assert budget.get_model_move(model, state, True, budget_s=10,
                                 max_simulations=2, diagnostics=info) == expected
    assert info["simulations"] == 2
    assert info["search_stop"] == "simulation_limit"
    assert continued == []
    assert budget.current_search() is None


@pytest.mark.parametrize("change", ["ball", "stats", "ball_type", "ball_size",
                                    "ball_bounciness", "partition"])
def test_cache_reuses_identical_predictions_and_invalidates_physics_changes(change):
    state = physics.new_soccer_state(player_count=3)
    with active_search(state) as search:
        first = physics.simulate_kick(state, 0, 0, 60, True)
        assert physics.simulate_kick(state, 0, 360, 60, True) is first
        assert search.simulations == 1 and search.cache_hits == 1
        if change == "ball":
            state["ball"]["x"] += 10
        elif change == "stats":
            state["players_a"][0]["stats"] = dict(size=80, power=20, weight=50, agility=50)
        elif change == "partition":
            # Same flattened roster, different team membership and keepers.
            state["players_b"].insert(0, state["players_a"].pop())
        else:
            state[change] = {"ball_type": "beach", "ball_size": "large",
                             "ball_bounciness": "super_high"}[change]
        assert physics.simulate_kick(state, 0, 0, 60, True) is not first
        assert search.simulations == 2


@pytest.mark.parametrize("side", [True, False])
@pytest.mark.parametrize("count", [1, 3, 11])
@pytest.mark.parametrize("penalty", [False, True])
def test_fast_prediction_keeps_exact_final_ball_players_and_goal(side, count, penalty):
    state = physics.new_soccer_state(player_count=count)
    if penalty:
        state["penalty_shootout"] = True
        appmod._setup_penalty_positions(state, side)
        state["penalty_goalkeeper_move"] = "left"
    move = tactics.quick_move(state, side)
    ordinary, goal = physics.simulate_kick(state, *move, side)
    before = deepcopy(state)
    with active_search(state, side):
        fast, fast_goal = physics.simulate_kick(state, *move, side)
    assert fast_goal == goal
    for key in ("x", "y", "z", "a", "b", "t"):
        assert fast[-1].get(key) == ordinary[-1].get(key)
    assert state == before


def test_cancellation_stops_inside_physics_and_does_not_cache_partial_result(monkeypatch):
    cancelled = threading.Event()
    original = physics.pymunk.Space.step
    steps = []

    def step(space, dt):
        steps.append(dt)
        original(space, dt)
        if len(steps) == 2:
            cancelled.set()

    monkeypatch.setattr(physics.pymunk.Space, "step", step)
    state = physics.new_soccer_state(player_count=11)
    with active_search(state, cancelled=cancelled) as search:
        with pytest.raises(budget.SearchFinished):
            physics.simulate_kick(state, 10, 0, 100, True)
        assert search.cache == {}
        assert search.reason == "deadline"
    assert 2 <= len(steps) <= 24


def test_timed_out_builtin_releases_worker_after_next_physics_checkpoint(monkeypatch):
    pool = ThreadPoolExecutor(max_workers=1)
    slots = threading.BoundedSemaphore(1)
    entered, release = threading.Event(), threading.Event()
    original = physics.pymunk.Space.step

    def paused_step(space, dt):
        if not entered.is_set():
            entered.set()
            assert release.wait(2)
        original(space, dt)

    monkeypatch.setattr(appmod, "_ai_pool", pool)
    monkeypatch.setattr(appmod, "_ai_slots", slots)
    monkeypatch.setattr(physics.pymunk.Space, "step", paused_step)
    monkeypatch.setattr(budget, "DEFAULT_BUDGET_S", 5)
    model = importlib.import_module("models.team_coordinated")
    state = physics.new_soccer_state(player_count=11)
    before = deepcopy(state)
    info = {}
    try:
        move = appmod._get_ai_move_with_timeout(model, state, True, timeout=0.1,
                                               fallback_info=info)
        assert entered.is_set()
        assert info == {"reason": "timeout"}
        assert move == appmod._quick_ai_move(state, True)
    finally:
        release.set()
        pool.shutdown(wait=True, cancel_futures=True)
    assert state == before
    assert slots.acquire(blocking=False)
    slots.release()


def test_concurrent_searches_have_independent_caches_and_limits():
    barrier = threading.Barrier(2)

    def run(count):
        state = physics.new_soccer_state(player_count=count)
        with active_search(state) as search:
            barrier.wait(timeout=2)
            for _ in range(2):
                physics.simulate_kick(state, 0, 0, 60, True)
            assert budget.current_search() is search
            return search.simulations, search.cache_hits, len(search.cache)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run, [1, 11])) == [(1, 1, 1), (1, 1, 1)]
    assert budget.current_search() is None


@pytest.mark.parametrize("name", sorted(appmod.MODELS))
@pytest.mark.parametrize("count", [1, 3, 7, 11])
@pytest.mark.parametrize("side", [True, False])
def test_all_builtin_decisions_are_legal_for_supported_rosters(name, count, side, monkeypatch):
    model = importlib.import_module(appmod.MODELS[name])
    if name == "adaptive_learner":
        monkeypatch.setattr(model, "_Q", {})
    state = physics.new_soccer_state(player_count=count, power_cap=55)
    info = {}
    index, angle, power = budget.get_model_move(model, state, side, budget_s=10,
                                               max_simulations=2, diagnostics=info)
    assert 0 <= index < count
    assert math.isfinite(angle) and 0 <= angle < 360
    assert 0 <= power <= 55
    assert info["simulations"] <= 2


@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize("explore", [True, False])
def test_learner_only_selects_existing_pawns_and_preserves_action_metadata(count, explore, monkeypatch):
    learner = importlib.import_module("models.adaptive_learner")
    state = physics.new_soccer_state(player_count=count)
    monkeypatch.setattr(learner, "_Q", {learner._key_for(state, True): list(range(36))})
    monkeypatch.setattr(learner.random, "random", lambda: 0 if explore else 1)
    monkeypatch.setattr(learner.random, "randrange", lambda stop: stop - 1)
    monkeypatch.setattr(physics, "_build_space", lambda *_: pytest.fail("Q lookup ran physics"))
    info = {}
    move = budget.get_model_move(learner, state, True, diagnostics=info)
    assert move[0] == count - 1
    assert state["_learner_last"]["a"] == count * 12 - 1
    assert info["simulations"] == 0


def test_fast_llm_mode_uses_local_strategy_without_remote_inference(monkeypatch):
    model = importlib.import_module("models.langchain_model")
    monkeypatch.setattr(model, "get_ai_move", lambda *_: pytest.fail("Fast mode invoked remote inference"))
    calls = []
    monkeypatch.setattr(model, "_fallback_move", lambda *_: calls.append(True) or (0, 0, 50))
    state = physics.new_soccer_state(player_count=3)
    assert budget.get_model_move(model, state, True, budget_s=0.9, max_simulations=12)
    assert calls == [True]


def test_excluded_player_is_replanned_instead_of_reusing_wrong_angle(monkeypatch):
    state = physics.new_soccer_state(mode="hvai", player_count=3)
    wrong_move = (0, 270.0, 5.0)
    monkeypatch.setattr(appmod, "_load_model", lambda _: SimpleNamespace(get_ai_move=lambda *_: wrong_move))
    applied = []
    monkeypatch.setattr(appmod, "_apply_move", lambda _s, i, a, p, _side: applied.append((i, a, p)) or {})
    result = appmod._do_ai_move(state, "custom", True, excluded_player_idx=0)
    assert applied == [tactics.quick_move(state, True, [1, 2])]
    assert applied[0][1:] != wrong_move[1:]
    assert result["fallback_reason"] == "player_constraint"


def test_budgeted_builtin_respects_captain_and_teammate_constraints():
    model = importlib.import_module("models.team_coordinated")
    state = physics.new_soccer_state(mode="hvai", player_count=7)
    for side, allowed in [(False, [0]), (True, list(range(1, 7)))]:
        move = budget.get_model_move(model, state, side, allowed_players=allowed,
                                     budget_s=10, max_simulations=4)
        assert move[0] in allowed


def test_learner_constraint_fallback_does_not_claim_an_unexecuted_action(monkeypatch):
    learner = importlib.import_module("models.adaptive_learner")
    state = physics.new_soccer_state(player_count=7)
    monkeypatch.setattr(learner, "_Q", {})
    move = budget.get_model_move(learner, state, True, allowed_players=[6])
    assert move[0] == 6
    assert "_learner_last" not in state


def test_expired_search_returns_legal_seed_without_starting_physics(monkeypatch):
    model = importlib.import_module("models.greedy_model")
    state = physics.new_soccer_state(player_count=7)
    monkeypatch.setattr(physics, "_build_space", lambda *_: pytest.fail("Expired search started physics"))
    info = {}
    move = budget.get_model_move(model, state, True, allowed_players=[6],
                                 budget_s=0, diagnostics=info)
    assert move[0] == 6
    assert info["simulations"] == 0 and info["search_stop"] == "deadline"
    assert budget.current_search() is None
