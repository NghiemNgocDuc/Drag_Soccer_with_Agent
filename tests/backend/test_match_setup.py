"""Local pre-match choices must be confirmed before physics or clocks run."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from models.soccer_logic import FIELD_H, FIELD_W, _home_positions, new_soccer_state


@pytest.fixture
def setup_api(monkeypatch):
    import app as application
    from db import customization

    state = new_soccer_state(mode="hvai", player_count=5, half_length=45)
    state.update(match_started=False, formation_a="2-1-1", formation_b="2-1-1")
    box = {"state": state}
    saved = []

    def save(_user, value):
        box["state"] = value
        saved.append(deepcopy(value))

    monkeypatch.setattr(application, "get_game", lambda _user: box["state"])
    monkeypatch.setattr(application, "save_game", save)
    monkeypatch.setattr(application, "_ach_toasts", lambda: [])
    monkeypatch.setattr(application, "_auto_save_state", lambda *_: None)
    monkeypatch.setattr(application, "_auto_clear_state", lambda *_: None)
    monkeypatch.setattr(application, "_track_scene_usage", lambda *_: None)
    monkeypatch.setattr(application, "_mem_short", lambda *_: None)
    monkeypatch.setattr(application._ph, "track_game_start", lambda *_: None)
    settings = deepcopy(customization.DEFAULT_CUSTOMIZATION)
    monkeypatch.setattr(customization, "get_customization", lambda _user: deepcopy(settings))
    with application.app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = "dev:match-setup"
        yield SimpleNamespace(client=client, box=box, saved=saved, app=application, settings=settings)


def start_payload(**changes):
    return {"formation_a": "1-2-1", "playstyle_a": 4, **changes}


def test_new_local_session_waits_for_setup_but_engine_states_do_not():
    from game.session import delete_game, get_game, new_game_state
    user_id = "dev:fresh-match-setup"
    delete_game(user_id)
    try:
        state = get_game(user_id)
        assert state["match_started"] is False
        assert state["formation_a"] == state["formation_b"] == "2-1-1"
        assert get_game(user_id) == state
        assert "match_started" not in new_game_state()
    finally:
        delete_game(user_id)


def test_catalog_describes_every_playable_five_player_formation(setup_api):
    from models.formations import FORMATIONS_5
    response = setup_api.client.get("/match_setup")
    assert response.status_code == 200
    catalog = response.get_json()
    assert catalog["default_formation"] == "2-1-1"
    assert {item["id"] for item in catalog["formations"]} == set(FORMATIONS_5)
    for item in catalog["formations"]:
        assert item["name"] and item["description"]
        assert len(item["positions"]) == 5
        assert sum(item["rows"]) == 4


def test_waiting_clock_does_not_advance_when_state_is_polled(setup_api, monkeypatch):
    from models import soccer_logic
    state = setup_api.box["state"]
    state["start_time"] = state["turn_start_time"] = 10
    monkeypatch.setattr(soccer_logic, "time", SimpleNamespace(time=lambda: 10000))
    before = deepcopy(state)
    response = setup_api.client.get("/state")
    assert response.status_code == 200
    result = response.get_json()
    assert result["match_started"] is False
    assert result["match_clock_seconds"] == 0
    assert result["clock_running"] is False
    assert setup_api.box["state"] == before


@pytest.mark.parametrize("route", ["/move", "/ai_move", "/random_move"])
def test_move_routes_do_no_physics_or_ai_work_before_start(setup_api, monkeypatch, route):
    def unexpected(*_args, **_kwargs):
        pytest.fail("No AI search or physics is allowed while choosing setup")
    monkeypatch.setattr(setup_api.app, "_load_model", unexpected)
    monkeypatch.setattr(setup_api.app, "_do_ai_move", unexpected)
    monkeypatch.setattr(setup_api.app, "_apply_move", unexpected)
    before = deepcopy(setup_api.box["state"])
    response = setup_api.client.post(route, json={"player_idx": 4, "angle": 0, "power": 30})
    assert response.status_code == 409
    assert setup_api.box["state"] == before
    assert not setup_api.saved


@pytest.mark.parametrize("formation", ["2-1-1", "1-2-1", "2-0-2", "1-1-2"])
def test_start_applies_formation_preserves_player_identity_and_starts_clock(setup_api, monkeypatch, formation):
    state = setup_api.box["state"]
    for index, player in enumerate(state["players_a"]):
        player.update(name=f"Player {index}", color="#112233", size=20 + index, custom_tag="keep")
    state["playstyle_b"] = 2
    before = deepcopy(state)
    from models import soccer_logic
    clock = SimpleNamespace(time=lambda: 12345.5, monotonic=setup_api.app.time.monotonic)
    monkeypatch.setattr(setup_api.app, "time", clock)
    monkeypatch.setattr(soccer_logic, "time", clock)
    response = setup_api.client.post("/start_match", json=start_payload(formation_a=formation))
    assert response.status_code == 200
    result = response.get_json()
    started = setup_api.box["state"]
    assert result["match_started"] is True
    assert result["formation_a"] == formation
    assert result["playstyle_a"] == 4 and result["playstyle_b"] == 2
    assert result["start_time"] == result["turn_start_time"] == 12345.5
    assert result["clock_running"] is True and result["match_clock_seconds"] == 0
    assert result["kick_count"] == 0
    assert result["ball"] == before["ball"]
    assert started["players_b"] == before["players_b"]
    for old, player, position in zip(before["players_a"], started["players_a"], _home_positions(5, "a", formation)):
        assert (player["x"], player["y"]) == position
        assert {k: v for k, v in player.items() if k not in ("x", "y")} == {
            k: v for k, v in old.items() if k not in ("x", "y")}


@pytest.mark.parametrize("mode", ["aivai", "hvh"])
def test_both_team_setup_is_supported_in_shared_local_modes(setup_api, mode):
    assert setup_api.client.post("/set_mode", json={"mode": mode}).status_code == 200
    payload = start_payload(formation_b="2-0-2", playstyle_b=1)
    response = setup_api.client.post("/start_match", json=payload)
    assert response.status_code == 200
    result = response.get_json()
    assert result["formation_a"] == "1-2-1" and result["formation_b"] == "2-0-2"
    assert result["playstyle_a"] == 4 and result["playstyle_b"] == 1
    assert [(p["x"], p["y"]) for p in result["players_b"]] == _home_positions(5, "b", "2-0-2")


def test_manual_only_setup_does_not_require_meaningless_ai_levels(setup_api):
    setup_api.box["state"]["game_mode"] = "hvh"
    response = setup_api.client.post("/start_match", json={"formation_a": "1-2-1", "formation_b": "2-0-2"})
    assert response.status_code == 200
    assert response.get_json()["playstyle_a"] == response.get_json()["playstyle_b"] == 3


@pytest.mark.parametrize("payload", [
    {}, {"formation_a": "1-2-1"}, {"playstyle_a": 3},
    start_payload(formation_a="2-3-1"), start_payload(formation_a=None),
    start_payload(formation_a=[]), start_payload(formation_a=True),
    start_payload(playstyle_a=None), start_payload(playstyle_a=True),
    start_payload(playstyle_a=0), start_payload(playstyle_a=6),
    start_payload(playstyle_a="3"), start_payload(playstyle_a=3.0),
])
def test_invalid_choices_are_atomic(setup_api, payload):
    before = deepcopy(setup_api.box["state"])
    response = setup_api.client.post("/start_match", json=payload)
    assert response.status_code == 400
    assert setup_api.box["state"] == before
    assert not setup_api.saved


@pytest.mark.parametrize("body", ["[]", "null", '"setup"', '{"formation_a":'])
def test_invalid_start_json_shape_is_rejected(setup_api, body):
    before = deepcopy(setup_api.box["state"])
    response = setup_api.client.post("/start_match", data=body, content_type="application/json")
    assert response.status_code == 400
    assert setup_api.box["state"] == before


@pytest.mark.parametrize("field,value", [("formation_b", "1-1-2"), ("playstyle_b", 5)])
def test_human_cannot_choose_opponent_setup(setup_api, field, value):
    before = deepcopy(setup_api.box["state"])
    response = setup_api.client.post("/start_match", json=start_payload(**{field: value}))
    assert response.status_code == 403
    assert setup_api.box["state"] == before


def test_second_team_validation_happens_before_either_team_is_changed(setup_api):
    setup_api.box["state"]["game_mode"] = "aivai"
    before = deepcopy(setup_api.box["state"])
    response = setup_api.client.post("/start_match", json=start_payload(formation_b="2-0-2", playstyle_b=9))
    assert response.status_code == 400
    assert setup_api.box["state"] == before
    assert not setup_api.saved


@pytest.mark.parametrize("change", [
    {"match_started": True}, {"penalty_shootout": True}, {"game_over": True},
    {"kick_count": 1}, {"players_a": []}, {"players_b": []},
])
def test_start_rejects_states_which_cannot_be_repositioned(setup_api, change):
    setup_api.box["state"].update(change)
    before = deepcopy(setup_api.box["state"])
    assert setup_api.client.post("/start_match", json=start_payload()).status_code == 409
    assert setup_api.box["state"] == before


def test_started_match_cannot_change_tactics_or_mode_until_reset(setup_api):
    assert setup_api.client.post("/start_match", json=start_payload()).status_code == 200
    before = deepcopy(setup_api.box["state"])
    assert setup_api.client.post("/start_match", json=start_payload()).status_code == 409
    assert setup_api.client.post("/set_playstyle", json={"level": 1}).status_code == 409
    assert setup_api.client.post("/set_mode", json={"mode": "aivai"}).status_code == 409
    assert setup_api.box["state"] == before
    assert setup_api.client.post("/set_mode", json={"mode": "hvai"}).status_code == 200


def test_reset_requires_new_confirmation_and_remembers_both_teams_choices(setup_api):
    setup_api.box["state"]["game_mode"] = "aivai"
    assert setup_api.client.post("/start_match", json=start_payload(formation_b="1-1-2", playstyle_b=1)).status_code == 200
    response = setup_api.client.post("/reset", json={})
    assert response.status_code == 200
    result = response.get_json()
    assert result["match_started"] is False and result["clock_running"] is False
    assert (result["formation_a"], result["formation_b"]) == ("1-2-1", "1-1-2")
    assert (result["playstyle_a"], result["playstyle_b"]) == (4, 1)
    assert [(p["x"], p["y"]) for p in result["players_a"]] == _home_positions(5, "a", "1-2-1")


def test_legacy_seven_player_formation_metadata_is_not_carried_into_five_player_reset(setup_api):
    setup_api.box["state"].update(formation_a="2-3-1", formation_b={"invalid": True})
    setup_api.settings.update(formation_a="2-2-2", formation_b="3-2-1")
    result = setup_api.client.post("/reset", json={}).get_json()
    assert result["formation_a"] == result["formation_b"] == "2-1-1"
    assert result["player_count"] == 5


@pytest.mark.parametrize("payload", [[], "reset", {"penalty_mode": "false"}, {"formation_a": []}, {"formation_b": "2-3-1"}])
def test_reset_validation_does_not_clear_existing_match(setup_api, payload):
    before = deepcopy(setup_api.box["state"])
    response = setup_api.client.post("/reset", json=payload)
    assert response.status_code == 400
    assert setup_api.box["state"] == before
    assert not setup_api.saved


def test_penalty_practice_does_not_require_regular_match_setup(setup_api):
    response = setup_api.client.post("/reset", json={"penalty_mode": True})
    assert response.status_code == 200
    assert response.get_json()["match_started"] is True
    assert response.get_json()["penalty_shootout"] is True


@pytest.mark.parametrize("route,method", [("/match_setup", "get"), ("/start_match", "post")])
def test_setup_routes_require_authentication(route, method):
    import app as application
    with application.app.test_client() as client:
        assert getattr(client, method)(route, content_type="application/json").status_code == 401
