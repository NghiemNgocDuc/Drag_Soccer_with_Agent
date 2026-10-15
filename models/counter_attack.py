"""Exploit forward space with direct passes and decisive shots."""
from functools import partial
from models.tactical_styles import choose_style, evaluate_style

MODEL_NAME = 'Counter Attack'
DESCRIPTION = 'Quick forward passes and direct shots, balancing progress against turnovers.'
evaluate_move = partial(evaluate_style, style='counter_attack')


def get_ai_move(state, is_player_a):
    return choose_style(state, is_player_a, 'counter_attack')
