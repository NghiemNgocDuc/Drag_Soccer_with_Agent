"""Sport selection, local lifecycle and physics dispatch through the real API."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from game.session import new_game_state


@pytest.fixture
def basketball_api(monkeypatch):
    import app as application
    from db import customization

    state = new_game_state()
    state["match_started"] = False
    box = {"state": state}
    saved = []

    def save(_user, value):
        box["state"] = value
        saved.append(deepcopy(value))

    monkeypatch.setattr(application, "get_game", lambda _user: box["state"])
    monkeypatch.setattr(application, "save_game", save)
    monkeypatch.setattr(application, "_ach_toasts", lambda: [])
    for name in ("_auto_save_state", "_auto_clear_state", "_track_scene_usage", "_mem_short", "_mem_summ"):
        monkeypatch.setattr(application, name, lambda *_: None)
    monkeypatch.setattr(application._ph, "track_game_start", lambda *_: None)
    monkeypatch.setattr(customization, "get_customization", lambda _user: deepcopy(customization.DEFAULT_CUSTOMIZATION))
    with application.app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = "dev:basketball-api"
        yield SimpleNamespace(client=client, box=box, saved=saved, app=application)


def start(api, mode="hvai"):
    api.box["state"]["game_mode"] = mode
    response = api.client.post("/start_match", json={"sport": "basketball"})
    assert response.status_code == 200
    return response.get_json()


def test_catalog_and_basketball_start_preserve_player_stats(basketball_api):
    api = basketball_api
    catalog = api.client.get("/match_setup").get_json()
    assert {sport["id"] for sport in catalog["sports"]} == {"soccer", "basketball"}
    from models.soccer_logic import inject_player_stats
    source = api.box["state"]
    inject_player_stats(source, [{"size": 40, "power": 70, "weight": 50, "agility": 40}] * 5)
    source["players_a"][0].update(name="Custom player", color="#112233")
    before = deepcopy(source)
    result = start(api)
    assert result["sport"] == "basketball"
    assert result["half_length"] == 0 and result["win_goal_limit"] == 21
    assert result["match_started"] is True and result["clock_running"] is False
    assert result["penalty_shootout"] is False
    assert len(result["players_a"]) == len(result["players_b"]) == 5
    assert result["possession"]["side"] == "A"
    assert result["players_a"][0]["stats"] == before["players_a"][0]["stats"]
    assert result["players_a"][0]["name"] == "Custom player"
    assert "goalkeeper" not in result["player_roles_a"]


@pytest.mark.parametrize("sport", [None, True, [], {}, "football", "BASKETBALL"])
def test_invalid_sports_do_not_change_match(basketball_api, sport):
    before = deepcopy(basketball_api.box["state"])
    assert basketball_api.client.post("/start_match", json={"sport": sport}).status_code == 400
    assert basketball_api.box["state"] == before
    assert not basketball_api.saved


@pytest.mark.parametrize("mode", ["hvai", "hvh", "aivai"])
def test_all_local_modes_support_basketball(basketball_api, mode):
    assert start(basketball_api, mode)["game_mode"] == mode
    before = deepcopy(basketball_api.box["state"])
    assert basketball_api.client.post("/start_match", json={"sport": "soccer"}).status_code == 409
    assert basketball_api.box["state"] == before


def test_moves_ai_and_undo_use_basketball_state(basketball_api, monkeypatch):
    api = basketball_api
    first = start(api)
    monkeypatch.setattr(api.app, "_load_model", lambda *_: pytest.fail("Basketball must use court AI"))
    monkeypatch.setattr(api.app, "_goal_moment_achievements", lambda *_: pytest.fail("Baskets are not soccer goals"))
    # Every human player is selectable on the court, including an off-ball guard.
    response = api.client.post("/move", json={"player_idx": 0, "angle": 0, "power": 35})
    assert response.status_code == 200
    moved = response.get_json()
    assert moved["sport"] == "basketball" and moved["kick_count"] == 1
    assert moved["move_result"]["trajectory"]
    assert moved["is_player_a"] is False
    assert moved["possession"] is not None
    ai = api.client.post("/ai_move", json={})
    assert ai.status_code == 200
    assert ai.get_json()["kick_count"] == 2
    assert ai.get_json()["is_player_a"] is True
    for _ in range(2):
        assert api.client.post("/undo", json={}).status_code == 200
    restored = api.box["state"]
    assert restored["possession"] == first["possession"]
    assert restored["ball"] == first["ball"]
    assert restored["players_a"] == first["players_a"]
    assert restored["kick_count"] == 0 and restored["last_points"] == 0


def test_reset_preserves_sport_and_soccer_can_be_chosen_again(basketball_api):
    api = basketball_api
    start(api)
    reset = api.client.post("/reset", json={})
    assert reset.status_code == 200
    result = reset.get_json()
    assert result["sport"] == "basketball" and result["match_started"] is False
    assert result["half_length"] == 0 and result["win_goal_limit"] == 21
    assert api.client.post("/reset", json={"penalty_mode": True}).status_code == 400
    response = api.client.post("/start_match", json={"sport": "soccer", "formation_a": "1-2-1", "playstyle_a": 3})
    assert response.status_code == 200
    result = response.get_json()
    assert result["sport"] == "soccer" and result["half_length"] == 45
    assert result["formation_a"] == "1-2-1" and result["possession"] is None


def test_saves_survive_roundtrip_and_idle_clock_never_starts_penalties(basketball_api):
    from game.session import get_game, save_game, delete_game
    api = basketball_api
    start(api)
    state = api.box["state"]
    state["start_time"] = 1
    response = api.client.get("/state")
    result = response.get_json()
    assert result["sport"] == "basketball" and not result["clock_running"]
    assert not result["penalty_shootout"] and not result["game_over"]
    user = "dev:basketball-save-roundtrip"
    try:
        save_game(user, state)
        assert get_game(user) == state
    finally:
        delete_game(user)


def test_basketball_results_stay_out_of_soccer_goal_totals(basketball_api, monkeypatch):
    from db import games
    api = basketball_api
    start(api)
    calls = []
    monkeypatch.setattr(games, "save_game_result", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(api.app._ph, "track_game_end", lambda *_: None)
    monkeypatch.setattr(api.app, "_check_casual_achievements", lambda *_: pytest.fail("Soccer badges must not count points"))
    api.box["state"].update(game_over=True, winner="A", score_a=22, score_b=20)
    api.box["state"]["move_history"] = [
        {"sport": "basketball", "scored": "A", "points": 3},
        {"sport": "basketball", "scored": "B", "points": 2},
    ]
    # Redis returns a decoded copy on every request, so finalization must be saved.
    monkeypatch.setattr(api.app, "get_game", lambda _user: deepcopy(api.box["state"]))
    api.client.get("/state")
    api.client.get("/state")
    assert len(calls) == 1
    assert calls[0]["mode"] == "basketball_hvai"
    assert calls[0]["ai_model"] == "basketball"
    assert calls[0]["replay"][0]["initial_score"] == {"a": 19, "b": 18}
    assert "initial_score" not in api.box["state"]["move_history"][0]


def test_saved_basketball_uses_visual_replay_and_keeps_its_metadata(basketball_api, monkeypatch):
    from db import games
    from models.basketball_logic import basketball_ai_move, apply_basketball_move
    api = basketball_api
    start(api)
    state = api.box["state"]
    apply_basketball_move(state, *basketball_ai_move(state, True), True)
    monkeypatch.setattr(games, "get_replay", lambda *_: state["move_history"])
    response = api.client.get("/history/watch/basketball-test")
    assert response.status_code == 200
    assert b'pitch-container' in response.data
    assert b'"sport": "basketball"' in response.data
    assert b'"reset":' in response.data and b'"points": 3' in response.data
