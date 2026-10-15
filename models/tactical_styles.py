"""Bounded tactical personalities, using the same real physics and team support.

These are deterministic heuristics, not downloaded or trained neural models.
The scorer also ranks seeds and completed predictions at a budget cutoff, so
the request runner cannot silently replace a personality with generic scoring.
"""
from functools import partial
import importlib
import math

from models.search_budget import current_search, get_model_move
from models.soccer_logic import FIELD_W, simulate_kick
from models.tactics import evaluate_outcome, tactical_candidates


def distance(a, b):
    return math.hypot(a['x']-b['x'], a['y']-b['y'])


def evaluate_style(state, side, move, trajectory, scored, *, style, support_plan=None):
    base = evaluate_outcome(state, side, move, trajectory, scored, support_plan=support_plan)
    if scored or not trajectory:
        return base  # Goals and own-goal avoidance always dominate personality.
    end = trajectory[-1]
    own = state['players_a' if side else 'players_b']
    opponents = state['players_b' if side else 'players_a']
    own_end = end.get('a' if side else 'b', own)
    opposing_end = end.get('b' if side else 'a', opponents)
    ours = min((distance(p, end) for p in own_end), default=FIELD_W)
    theirs = min((distance(p, end) for p in opposing_end), default=FIELD_W)
    advantage = max(-180.0, min(180.0, theirs-ours))
    progress = (end['x']-state['ball']['x']) * (1 if side else -1)
    if style == 'possession':
        receivers = [p for i,p in enumerate(own_end) if i not in (0,move[0])]
        receiver_gap = min((distance(p,end) for p in receivers), default=FIELD_W)
        return (base - progress*.65 + advantage*1.3
                + max(0.0, 90-receiver_gap)*1.1 - max(0.0, ours-110)*.6)
    if style == 'counter_attack':
        # Attack space quickly, but still penalize giving an opponent easy possession.
        return base + progress*.9 + max(0.0, advantage)*.3 - max(0.0, -advantage)*.65
    if style == 'high_press':
        before = min((distance(p,state['ball']) for p in own[1:] or own), default=FIELD_W)
        after = min((distance(p,end) for p in own_end[1:] or own_end), default=FIELD_W)
        closing = max(-200.0, min(200.0, before-after))
        # One pawn closes down each turn; support/spacing and keeper safety remain shared.
        return base + closing*1.2 + max(0.0, 130-after)*.8 + advantage*.4
    raise ValueError('Unknown tactical style')


def choose_style(state, side, style):
    search = current_search()
    if search is None:
        # Direct calls from workshop tools keep the same limits as live requests.
        return get_model_move(importlib.import_module('models.'+style), state, side)
    allowed = search.allowed if search.side == side else None
    scorer = partial(evaluate_style, style=style)
    candidates = tactical_candidates(state, side, allowed, max_candidates=8, evaluator=scorer)
    best, best_value = candidates[0], float('-inf')
    for move in candidates:
        trajectory, scored = simulate_kick(state, *move, side)
        value = scorer(state, side, move, trajectory, scored)
        if value > best_value:
            best, best_value = move, value
        if scored == ('A' if side else 'B'):
            break
    return best
