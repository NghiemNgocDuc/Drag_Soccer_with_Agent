"""Close the ball with outfield players while retaining shared team spacing."""
from functools import partial
from models.tactical_styles import choose_style, evaluate_style

MODEL_NAME = 'High Press'
DESCRIPTION = 'Closes down the ball and contests possession with disciplined outfield pressure.'
evaluate_move = partial(evaluate_style, style='high_press')


def get_ai_move(state, is_player_a):
    return choose_style(state, is_player_a, 'high_press')
