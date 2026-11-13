"""Deterministic physical regression gate for a learned soccer candidate.

This is a small scenario check, not an online win-rate estimate. Models only
rank existing legal actions, and the unchanged tactical scorer measures their
actual physical outcomes. Context-local models never replace live weights.
"""
from __future__ import annotations

import time

from models import experience_policy as policy

FITNESS_VERSION = "physical-tactics-v1"
MAX_SECONDS = 20.0


def _fixtures():
    from models.soccer_logic import new_soccer_state

    for kind in ("opening", "shot", "clearance", "pass", "weak_striker"):
        state = new_soccer_state(player_count=5, half_length=0, team_movement=True)
        if kind == "shot":
            state["ball"] = {"x": 1190.0, "y": 410.0, "z": 0.0}
            state["players_a"][4].update(x=1110.0, y=410.0)
            state["players_b"][0].update(x=1320.0, y=485.0)
        elif kind == "clearance":
            state["ball"] = {"x": 190.0, "y": 437.5, "z": 0.0}
            state["players_a"][1].update(x=120.0, y=437.5)
            state["players_b"][4].update(x=290.0, y=437.5)
        elif kind == "pass":
            state["ball"] = {"x": 600.0, "y": 437.5, "z": 0.0}
            state["players_a"][3].update(x=520.0, y=437.5)
            state["players_a"][4].update(x=850.0, y=240.0)
            state["players_b"][4].update(x=740.0, y=437.5)
        elif kind == "weak_striker":
            state["players_a"][4]["stats"] = {"size": 20, "power": 5, "weight": 30, "agility": 100}
            state["players_a"][3].update(x=620.0, y=437.5)
        yield kind + ":A", state, True
        # A genuine field/team mirror also exercises team-B launch direction.
        import copy
        mirror = copy.deepcopy(state)
        mirror["ball"]["x"] = 1400.0 - state["ball"]["x"]
        for key, opposite in (("players_a", "players_b"), ("players_b", "players_a")):
            mirror[key] = [{**player, "x": 1400.0 - player["x"]} for player in state[opposite]]
        mirror["is_player_a"] = False
        yield kind + ":B", mirror, False


def _choose(state, side, model, deadline):
    from models.soccer_logic import simulate_kick, normalize_kick
    from models.tactics import tactical_candidates, evaluate_outcome

    best, best_value, best_result = None, float("-inf"), None
    with policy.using_model(model):
        candidates = tactical_candidates(state, side, max_candidates=4)
        for candidate in candidates:
            if time.monotonic() >= deadline:
                raise TimeoutError("Physical validation deadline exceeded")
            move = normalize_kick(state, *candidate, side)
            result = simulate_kick(state, *move, side)
            value = evaluate_outcome(state, side, move, *result)
            if value > best_value:
                best, best_value, best_result = move, value, result
    if best is None:
        raise ValueError("No completed fitness candidate")
    # Measure with the fixed scorer, not the policy being judged.
    with policy.using_model(None):
        measured = evaluate_outcome(state, side, best, *best_result)
    return best, measured, best_result[1]


def evaluate_candidate(candidate, incumbent=None):
    started = time.monotonic()
    deadline = started + MAX_SECONDS
    scenarios = []
    try:
        for name, state, side in _fixtures():
            old_move, old_score, old_goal = _choose(state, side, incumbent, deadline)
            new_move, new_score, new_goal = _choose(state, side, candidate, deadline)
            own = "A" if side else "B"
            safe = (new_goal in (None, own) and (old_goal != own or new_goal == own)
                    and new_score >= old_score - 5.0)
            scenarios.append({"name": name, "baseline_value": round(old_score, 6),
                              "candidate_value": round(new_score, 6), "passed": safe,
                              "changed_action": old_move != new_move})
        delta = sum(row["candidate_value"] - row["baseline_value"] for row in scenarios)
        passed = all(row["passed"] for row in scenarios) and delta >= -1e-6
        return {"version": FITNESS_VERSION, "passed": passed, "scenarios": scenarios,
                "value_delta": round(delta, 6), "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                "evaluation": "physical_scenarios_not_win_rate"}
    except (ValueError, TypeError, TimeoutError, OverflowError) as exc:
        return {"version": FITNESS_VERSION, "passed": False, "reason": type(exc).__name__,
                "scenarios": scenarios, "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                "evaluation": "physical_scenarios_not_win_rate"}
