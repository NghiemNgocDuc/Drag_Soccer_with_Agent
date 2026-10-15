"""Keep the ball through short passes and nearby supporting receivers."""
from functools import partial
from models.tactical_styles import choose_style, evaluate_style

MODEL_NAME = 'Possession'
DESCRIPTION = 'Patient short passes, safe receivers and support runs to retain the ball.'
evaluate_move = partial(evaluate_style, style='possession')


def get_ai_move(state, is_player_a):
    return choose_style(state, is_player_a, 'possession')
