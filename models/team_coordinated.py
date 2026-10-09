"""Coordinate the whole roster with a small physics-verified tactical shortlist."""
from __future__ import annotations

from models.search_budget import current_search
from models.soccer_logic import simulate_kick
from models.tactics import tactical_candidates, evaluate_outcome

MODEL_NAME = "Team Coordinated"
DESCRIPTION = "Reachable shooter selection, open passes, safe clears and support recovery."


def get_ai_move(state, is_player_a):
    search = current_search()
    allowed = search.allowed if search is not None and search.side == is_player_a else None
    candidates = tactical_candidates(state, is_player_a, allowed, max_candidates=8)
    best_move = candidates[0]
    best_value = float("-inf")
    target = "A" if is_player_a else "B"
    for move in candidates:
        trajectory, scored = simulate_kick(state, *move, is_player_a)
        value = evaluate_outcome(state, is_player_a, move, trajectory, scored)
        if value > best_value:
            best_move, best_value = move, value
        if scored == target:
            break
    return best_move
