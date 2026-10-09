"""Command boundaries and saved physics settings at the HTTP entry points."""
import copy
import os
from types import SimpleNamespace

os.environ.setdefault("DEV_MODE", "1")

import pytest

import app as appmod
from db import customization
from models.soccer_logic import new_soccer_state


@pytest.fixture
def api(monkeypatch):
    state = new_soccer_state(mode="hvh", player_count=7, half_length=9999)
    room = {"status": "active", "player_a": "dev:physics-api", "player_b": "dev:opponent", "game": state}
    saved = []
    calls = []
    monkeypatch.setattr(appmod, "get_game", lambda _uid: state)
    monkeypatch.setattr(appmod, "_get_room", lambda _room: room)
    monkeypatch.setattr(appmod, "save_game", lambda _uid, game: saved.append(game))
    monkeypatch.setattr(appmod, "_save_room", lambda _room, game: saved.append(game))
    monkeypatch.setattr(appmod, "_auto_save_state", lambda *_args: None)
    monkeypatch.setattr(appmod, "_auto_clear_state", lambda *_args: None)
    monkeypatch.setattr(appmod, "_track_scene_usage", lambda *_args: None)
    monkeypatch.setattr(appmod, "_ach_toasts", lambda: [])
    monkeypatch.setattr(appmod, "_mem_short", lambda *_args: None)

    def kick(game, index, angle, power, side_a):
        calls.append((index, angle, power, side_a))
        assert len(game["snapshots"]) == 1
        game["kick_count"] += 1
        return [dict(game["ball"])], None, "Kick", {"x": 0, "y": 0}, None

    monkeypatch.setattr(appmod, "apply_kick", kick)
    with appmod.app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = "dev:physics-api"
        yield SimpleNamespace(client=client, state=state, room=room, saved=saved, calls=calls)


@pytest.mark.parametrize("route", ["/move", "/online/physics-api/move"])
@pytest.mark.parametrize("field,value", [
    ("angle", float("nan")), ("angle", float("inf")), ("angle", "-inf"),
    ("angle", {}), ("power", float("nan")), ("power", "not a number"),
    ("power", []), ("player_idx", float("inf")), ("player_idx", None),
])
def test_malformed_numeric_command_rejected_without_snapshot_or_state_change(api, route, field, value):
    before = copy.deepcopy(api.state)
    response = api.client.post(route, json={"player_idx": 0, "angle": 0, "power": 80, field: value})
    assert response.status_code == 400
    assert response.get_json()["error"]
    assert api.state == before
    assert not api.calls and not api.saved


@pytest.mark.parametrize("route", ["/move", "/online/physics-api/move"])
@pytest.mark.parametrize("body", ["[]", "null", '"invalid"', '{"power":'])
def test_non_object_and_malformed_json_rejected_without_mutation(api, route, body):
    before = copy.deepcopy(api.state)
    response = api.client.post(route, data=body, content_type="application/json")
    assert response.status_code == 400
    assert api.state == before
    assert not api.calls and not api.saved


@pytest.mark.parametrize("route", ["/move", "/online/physics-api/move"])
@pytest.mark.parametrize("count", [1, 7, 11])
def test_last_actual_player_can_move_and_saved_cap_is_authoritative(api, route, count):
    state = new_soccer_state(mode="hvh", player_count=count, power_cap=45)
    api.state.clear()
    api.state.update(state)
    response = api.client.post(route, json={"player_idx": count - 1, "angle": 720, "power": 900})
    assert response.status_code == 200
    assert api.calls == [(count - 1, 0.0, 45.0, True)]
    assert response.get_json()["move_result"]["player_idx"] == count - 1
    assert response.get_json()["move_result"]["power"] == 45
    assert api.state["snapshots"][0]["kick_count"] == 0


def test_online_b_uses_its_actual_team_length(api):
    api.state["players_a"] = api.state["players_a"][:2]
    api.state["is_player_a"] = False
    with api.client.session_transaction() as session:
        session["user_id"] = "dev:opponent"
    response = api.client.post("/online/physics-api/move", json={"player_idx": 6, "angle": 180, "power": 50})
    assert response.status_code == 200
    assert api.calls == [(6, 180.0, 50.0, False)]


def test_hvai_rejects_manual_keeper_move(api):
    api.state["game_mode"] = "hvai"
    before = copy.deepcopy(api.state)
    response = api.client.post("/move", json={"player_idx": 0, "angle": 0, "power": 80})
    assert response.status_code == 400
    assert "striker" in response.get_json()["error"]
    assert api.state == before
    assert not api.calls and not api.saved


def test_hvai_accepts_striker_and_defaults_to_striker(api):
    api.state['game_mode'] = 'hvai'
    response = api.client.post('/move', json={'angle': 0, 'power': 80})
    assert response.status_code == 200
    assert api.calls == [(6, 0.0, 80.0, True)]
    assert response.get_json()['human_player_idx'] == 6


@pytest.mark.parametrize('requested', [1, 3, 7, 11])
def test_new_online_matches_are_five_a_side(requested):
    state = appmod._new_online_game({'player_count': requested})
    assert state['player_count'] == 5
    assert len(state['players_a']) == len(state['players_b']) == 5


def test_reset_replaces_legacy_roster_with_five_players(api):
    response = api.client.post('/reset', json={'player_count': 11})
    assert response.status_code == 200
    state = response.get_json()
    assert state['player_count'] == 5
    assert len(state['players_a']) == len(state['players_b']) == 5
    assert state['human_player_idx'] == 4


def test_hotseat_b_controls_its_actual_team(api):
    api.state["is_player_a"] = False
    api.state["players_a"] = api.state["players_a"][:2]
    response = api.client.post("/move", json={"player_idx": 6, "angle": 180, "power": 50})
    assert response.status_code == 200
    assert api.calls == [(6, 180.0, 50.0, False)]
    assert response.get_json()["move_result"]["player_idx"] == 6


def test_hotseat_b_penalty_uses_team_b(api, monkeypatch):
    api.state.update(is_player_a=False, penalty_shootout=True)
    calls = []

    def penalty(game, index, angle, power, side_a):
        calls.append((index, angle, power, side_a))
        return [dict(game["ball"])], False, "Penalty kick"

    monkeypatch.setattr(appmod, "apply_penalty_kick", penalty)
    response = api.client.post("/move", json={"player_idx": 0, "angle": 180, "power": 50})
    assert response.status_code == 200
    assert calls == [(0, 180.0, 50.0, False)]


def test_ai_versus_ai_rejects_manual_moves_without_mutation(api):
    api.state["game_mode"] = "aivai"
    before = copy.deepcopy(api.state)
    response = api.client.post("/move", json={"player_idx": 0, "angle": 0, "power": 50})
    assert response.status_code == 403
    assert api.state == before
    assert not api.calls and not api.saved


def test_apply_move_validates_before_pushing_an_undo_snapshot(api):
    before = copy.deepcopy(api.state)
    with pytest.raises(ValueError):
        appmod._apply_move(api.state, 0, float("nan"), 80, True)
    assert api.state == before
    assert not api.calls


@pytest.mark.parametrize("penalty", [False, True])
def test_reset_applies_saved_ball_physics_and_player_stats(api, monkeypatch, penalty):
    stats_a = {"size": 80, "power": 60, "weight": 20, "agility": 40}
    stats_b = {"size": 20, "power": 40, "weight": 80, "agility": 60}
    settings = copy.deepcopy(customization.DEFAULT_CUSTOMIZATION)
    settings.update(ball_type="beach", ball_bounciness="super_high", ball_size="large", power_cap=45)
    settings["player_stats"] = {"a": [stats_a] * 5, "b": [stats_b] * 5}
    monkeypatch.setattr(customization, "get_customization", lambda _uid: copy.deepcopy(settings))
    response = api.client.post("/reset", json={"player_count": 5, "penalty_mode": penalty})
    assert response.status_code == 200
    payload = response.get_json()
    game = api.saved[-1]
    for field in ("ball_type", "ball_bounciness", "ball_size", "power_cap"):
        assert game[field] == payload[field] == settings[field]
    assert game["penalty_shootout"] is penalty
    assert game["players_a"][0]["stats"] == stats_a
    assert game["players_b"][0]["stats"] == stats_b


@pytest.mark.parametrize("route", ["/move", "/online/physics-api/move"])
def test_command_requires_authentication(route):
    with appmod.app.test_client() as client:
        assert client.post(route, json={"power": 50}).status_code == 401
