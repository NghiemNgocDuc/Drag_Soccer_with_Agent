"""Cooperative, request-local search limits for the built-in soccer agents.

Keep completed tactical candidates when refinement runs out of time. The
physics hook checks the same deadline inside a prediction, so a timeout does
not leave a full angle sweep consuming a worker in the background.
"""
from __future__ import annotations

from contextvars import ContextVar
import math
import os
import threading
import time
from types import ModuleType


BUILTIN_MODULES = frozenset({
    "models.greedy_model", "models.genetic_fuzzy", "models.potential_field",
    "models.voronoi", "models.a2c_lite", "models.expectimax",
    "models.adaptive_learner", "models.goalnet_gat", "models.edms",
    "models.team_coordinated", "models.hybrid_ensemble", "models.langchain_model",
})


def _setting(name, default, low, high):
    try:
        return max(low, min(high, int(os.environ.get(name, default))))
    except (ValueError, TypeError):
        return default


DEFAULT_BUDGET_S = _setting("AI_DECISION_BUDGET_MS", 120, 20, 1000) / 1000
DEFAULT_MAX_SIMULATIONS = _setting("AI_MAX_SIMULATIONS", 12, 1, 64)
_current: ContextVar[SearchBudget | None] = ContextVar("soccer_ai_search", default=None)


class SearchFinished(BaseException):
    """Unwind even an ensemble's broad exception handler to its owning runner."""


def current_search():
    return _current.get()


def is_builtin(model):
    return isinstance(model, ModuleType) and model.__name__ in BUILTIN_MODULES


class SearchBudget:
    def __init__(self, state, side, allowed_players, budget_s, max_simulations,
                 cancelled=None):
        from models.tactics import quick_move

        self.state = state
        self.side = side
        players = state["players_a" if side else "players_b"]
        eligible = range(len(players)) if allowed_players is None else allowed_players
        self.allowed = tuple(i for i in eligible if 0 <= i < len(players))
        if not self.allowed:
            raise ValueError("No eligible AI player")
        self.budget_s = max(0.0, float(budget_s))
        self.deadline = time.perf_counter() + self.budget_s
        self.max_simulations = max(1, int(max_simulations))
        self.cancelled = cancelled or threading.Event()
        self.simulations = 0
        self.cache_hits = 0
        self.cache = {}
        self.best_move = quick_move(state, side, self.allowed)
        self.best_value = float("-inf")
        self.reason = None

    def checkpoint(self):
        if self.cancelled.is_set() or time.perf_counter() >= self.deadline:
            self.reason = "deadline"
            raise SearchFinished()

    def _key(self, state, move, side):
        # Cache only predictions of the original board, never opponent reply
        # states. Include current board/settings so a mutated input cannot reuse
        # a prediction made before that change.
        if state is not self.state or side != self.side:
            return None
        ball = state["ball"]
        roster = tuple(tuple(
            (p["x"], p["y"], tuple((p.get("stats") or {}).get(k, 50)
                                 for k in ("size", "power", "weight", "agility")))
            for p in state[key]
        ) for key in ("players_a", "players_b"))
        settings = tuple(state.get(k) for k in (
            "power_cap", "ball_type", "ball_size", "ball_bounciness", "keeper_style_a",
            "keeper_style_b", "penalty_shootout", "penalty_goalkeeper_move",
        ))
        return (ball["x"], ball["y"], ball.get("z", 0), roster, settings, move)

    def cached(self, state, move, side):
        self.checkpoint()
        if state is self.state and side == self.side and move[0] not in self.allowed:
            self.reason = "player_constraint"
            raise SearchFinished()
        key = self._key(state, move, side)
        if key is not None and key in self.cache:
            self.cache_hits += 1
            return self.cache[key]
        return None

    def begin(self):
        self.checkpoint()
        if self.simulations >= self.max_simulations:
            self.reason = "simulation_limit"
            raise SearchFinished()
        self.simulations += 1

    def record(self, state, move, side, result):
        from models.tactics import evaluate_outcome

        key = self._key(state, move, side)
        if key is None:
            return
        self.cache[key] = result
        value = evaluate_outcome(state, side, move, *result)
        if value > self.best_value:
            self.best_value = value
            self.best_move = move


def get_model_move(model, state, is_player_a, *, allowed_players=None,
                   budget_s=None, max_simulations=None, diagnostics=None,
                   cancelled=None):
    """Run a built-in with a shared budget; preserve custom-model execution.

Each search owns its cache, deadline and best candidate. Nested ensemble
experts share the parent budget instead of starting another complete search.
"""
    if not is_builtin(model):
        return model.get_ai_move(state, is_player_a)
    from models.soccer_logic import normalize_kick, simulate_kick
    from models.tactics import tactical_candidates

    search = SearchBudget(
        state, is_player_a, allowed_players,
        DEFAULT_BUDGET_S if budget_s is None else budget_s,
        DEFAULT_MAX_SIMULATIONS if max_simulations is None else max_simulations,
        cancelled,
    )
    token = _current.set(search)
    started = time.perf_counter()
    try:
        # The Q-table already makes a sub-millisecond decision. Preserve that
        # fast path and its learning metadata; constraints still apply.
        if model.__name__ == "models.adaptive_learner":
            move = normalize_kick(state, *model.get_ai_move(state, is_player_a), is_player_a)
            if move[0] in search.allowed:
                search.best_move = move
            else:
                # The fallback did not execute the sampled Q action.
                state.pop("_learner_last", None)
            return search.best_move

        for move in tactical_candidates(state, is_player_a, search.allowed, max_candidates=4):
            result = simulate_kick(state, *move, is_player_a)
            if result[1] == ("A" if is_player_a else "B"):
                return search.best_move

        search.checkpoint()
        if model.__name__ == "models.langchain_model":
            # Live fast mode never waits for remote inference. An explicitly
            # larger budget can still use the original LLM-guided strategy.
            choose = model._fallback_move if search.budget_s < 1.0 else model.get_ai_move
        else:
            choose = model.get_ai_move
        move = normalize_kick(state, *choose(state, is_player_a), is_player_a)
        if move[0] in search.allowed and all(math.isfinite(v) for v in move[1:]):
            simulate_kick(state, *move, is_player_a)
    except SearchFinished:
        pass
    finally:
        _current.reset(token)
        if diagnostics is not None:
            diagnostics.update({
                "simulations": search.simulations,
                "cache_hits": search.cache_hits,
                "search_ms": round((time.perf_counter() - started) * 1000, 1),
                "search_stop": search.reason,
            })
    return search.best_move
