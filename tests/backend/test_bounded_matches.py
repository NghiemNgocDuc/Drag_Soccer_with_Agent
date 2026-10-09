"""Bounded AI simulations must report the score lead when their kick cap expires."""
import pytest

import services.game_analytics as analytics


class NoOp:
    def get_ai_move(self, state, is_player_a):
        return 0, 0.0, 0.0


@pytest.mark.parametrize("score_a,score_b,expected", [
    (2, 1, "A"), (1, 2, "B"), (1, 1, "Draw"),
])
def test_capped_battle_uses_score_and_reports_consistent_win_rate(monkeypatch, score_a, score_b, expected):
    def new_state():
        return {"game_over": False, "winner": None, "score_a": score_a,
                "score_b": score_b, "kick_count": 0, "is_player_a": True}

    def kick(state, *args):
        state["kick_count"] += 1
        state["is_player_a"] = not state["is_player_a"]
        return [], None, "", None, None

    monkeypatch.setattr(analytics, "new_soccer_state", new_state)
    monkeypatch.setattr(analytics, "apply_kick", kick)
    result = analytics.run_model_battle(NoOp(), NoOp(), n_games=1, max_kicks=4)
    assert result["games"][0]["winner"] == expected
    assert result["games"][0]["kick_count"] == 4
    assert result["wins_a"] == int(expected == "A")
    assert result["wins_b"] == int(expected == "B")
    assert result["draws"] == int(expected == "Draw")
    assert result["win_rate_a"] == 100 * int(expected == "A")


@pytest.mark.parametrize("winner,score_a,score_b", [
    ("A", 0, 0), ("B", 3, 0), ("Draw", 1, 1),
])
def test_finished_battle_preserves_authoritative_result(monkeypatch, winner, score_a, score_b):
    monkeypatch.setattr(analytics, "new_soccer_state", lambda: {
        "game_over": True, "winner": winner, "score_a": score_a,
        "score_b": score_b, "kick_count": 0,
    })
    result = analytics.run_model_battle(NoOp(), NoOp(), n_games=1)
    assert result["games"][0]["winner"] == winner
    assert result["games"][0]["kick_count"] == 0


def test_model_failure_preserves_forfeit_even_with_score_lead(monkeypatch):
    class Raises:
        def get_ai_move(self, state, is_player_a):
            raise RuntimeError("model failed")

    monkeypatch.setattr(analytics, "new_soccer_state", lambda: {
        "game_over": False, "winner": None, "score_a": 3,
        "score_b": 0, "kick_count": 0, "is_player_a": True,
    })
    result = analytics.run_model_battle(Raises(), NoOp(), n_games=1)
    assert result["games"][0]["winner"] == "B"
    assert result["wins_b"] == 1
