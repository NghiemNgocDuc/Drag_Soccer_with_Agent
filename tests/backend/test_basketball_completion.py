"""Finish real local matches through HTTP and reopen the persisted replay."""
from copy import deepcopy
import uuid

import pytest

from models.basketball_logic import basketball_ai_move


@pytest.mark.parametrize("mode", ["hvai", "hvh", "aivai"])
def test_natural_match_completion_and_saved_replay(monkeypatch, mode):
    import app as application
    from db import customization, games, supabase_client
    from game.session import delete_game, get_game

    user = "dev:basketball-completion-" + uuid.uuid4().hex
    monkeypatch.setattr(supabase_client, "service", None)
    monkeypatch.setattr(customization, "get_customization", lambda _: deepcopy(customization.DEFAULT_CUSTOMIZATION))
    for name in ("_auto_save_state", "_auto_clear_state", "_track_scene_usage", "_mem_short", "_mem_summ"):
        monkeypatch.setattr(application, name, lambda *_: None)
    from db.redis_client import r

    try:
        with application.app.test_client() as client:
            with client.session_transaction() as session:
                session.update(user_id=user, username="Basketball completion")
            assert client.post("/reset", json={"sport": "basketball"}).status_code == 200
            assert client.post("/set_mode", json={"mode": mode}).status_code == 200
            response = client.post("/start_match", json={"sport": "basketball"})
            assert response.status_code == 200
            state = response.get_json()
            for _ in range(80):
                if state["game_over"]:
                    break
                side_a = state["is_player_a"]
                # A test driver chooses legal human inputs; authoritative HTTP
                # handlers still own physics, turn changes, scores and saves.
                if mode == "hvh" or (mode == "hvai" and side_a):
                    index, angle, power = basketball_ai_move(state, side_a)
                    response = client.post("/move", json={"player_idx": index, "angle": angle, "power": power})
                else:
                    response = client.post("/ai_move", json={})
                assert response.status_code == 200
                state = response.get_json()
                assert not state.get("error")
                assert state["sport"] == "basketball" and not state["clock_running"]
                assert not state["penalty_shootout"]

            assert state["game_over"], (mode, state["kick_count"], state["score_a"], state["score_b"])
            assert max(state["score_a"], state["score_b"]) >= 21
            assert abs(state["score_a"] - state["score_b"]) >= 2
            assert state["winner"] == ("A" if state["score_a"] > state["score_b"] else "B")
            final = deepcopy(state)
            # Repeated reads and an extra action may not duplicate the result
            # or keep playing after the winning basket.
            state = client.post("/ai_move", json={}).get_json()
            assert state["kick_count"] == final["kick_count"]
            assert (state["score_a"], state["score_b"]) == (final["score_a"], final["score_b"])
            assert client.get("/state").get_json()["game_over"]
            assert get_game(user)["game_over"]

            if mode == "hvai":
                recent = games.get_user_stats(user)["recent"]
                assert len(recent) == 1 and recent[0]["mode"] == "basketball_hvai"
                replay_id = recent[0]["replay_id"]
                replay = games.get_replay(user, replay_id)
                assert replay and len(replay) == final["kick_count"]
                for side in ("A", "B"):
                    assert sum(move.get("points", 0) for move in replay if move.get("scored") == side) == final["score_" + side.lower()]
                assert replay[-1]["scored"] == final["winner"]
                assert replay[-1]["reset"]["possession"]
                rendered = client.get("/history/watch/" + replay_id)
                assert rendered.status_code == 200
                assert b'pitch-container' in rendered.data and b'"sport": "basketball"' in rendered.data
    finally:
        recent = games.get_user_stats(user)["recent"]
        for record in recent:
            if record.get("replay_id"):
                r.delete(games._replay_key(user, record["replay_id"]))
        r.delete("history:" + user)
        delete_game(user)
