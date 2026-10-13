"""Authoritative 90-minute matches, extra time, and idle period boundaries."""
import copy

import pytest

import app as appmod
from db.customization import DEFAULT_CUSTOMIZATION
from game.session import new_game_state
from models import soccer_logic as physics


def match(**kwargs):
    state = new_game_state(**kwargs)
    state["start_time"] = 1000.0
    return state


def test_playable_matches_and_customization_default_to_90_minutes():
    assert new_game_state()["half_length"] == 45
    assert DEFAULT_CUSTOMIZATION["half_length"] == 45


@pytest.mark.parametrize("elapsed,period,minutes", [
    (0, "regular_first", 0), (134.95, "regular_first", 44.983333333),
    (135, "regular_second", 45), (270, "et_first", 90),
    (315, "et_second", 105), (360, "penalties", 120),
])
def test_periods_advance_in_order_even_after_idle(elapsed, period, minutes):
    state = match()
    physics.advance_match_clock(state, 1000 + elapsed)
    assert state["period"] == period
    assert physics.match_clock(state, 1000 + elapsed)["match_clock_seconds"] / 60 == pytest.approx(minutes)
    assert state["kick_count"] == 0
    assert state["penalty_shootout"] == (period == "penalties")


def test_half_time_reset_preserves_roster_stats_and_is_idempotent():
    state = match()
    state["players_a"][4].update(x=1000, name="Striker", stats=dict(physics.DEFAULT_STATS))
    assert physics.advance_match_clock(state, 1135)
    assert not state["is_player_a"]
    assert state["players_a"][4]["name"] == "Striker"
    assert state["players_a"][4]["stats"] == physics.DEFAULT_STATS
    before = copy.deepcopy(state)
    assert not physics.advance_match_clock(state, 1135)
    assert state == before


@pytest.mark.parametrize("elapsed", [270, 315, 360, 10000])
def test_regulation_leader_finishes_at_90_even_after_long_idle(elapsed):
    state = match()
    state["score_b"] = 1
    physics.advance_match_clock(state, 1000 + elapsed)
    assert state["game_over"] and state["winner"] == "B"
    assert not state["penalty_shootout"]
    clock = physics.match_clock(state, 50000)
    assert clock["match_clock_seconds"] == 90 * 60
    assert not clock["clock_running"] and clock["next_period_at"] is None


def test_extra_time_leader_continues_at_105_and_finishes_at_120():
    state = match()
    physics.advance_match_clock(state, 1270)
    state["score_a"] = 1
    physics.advance_match_clock(state, 1315)
    assert state["period"] == "et_second" and not state["game_over"]
    physics.advance_match_clock(state, 1360)
    assert state["game_over"] and state["winner"] == "A"
    assert physics.match_clock(state, 50000)["match_clock_seconds"] == 120 * 60


def test_penalties_freeze_clock_and_only_initialize_once():
    state = match()
    physics.advance_match_clock(state, 1360)
    state["penalty_a_score"] = 2
    assert not physics.advance_match_clock(state, 50000)
    assert state["penalty_a_score"] == 2
    assert physics.match_clock(state, 50000)["match_clock_seconds"] == 120 * 60
    assert not physics.match_clock(state, 50000)["clock_running"]


def test_clock_clamps_to_authoritative_boundary_until_state_refresh():
    state = match()
    clock = physics.match_clock(state, 1200)
    assert clock["match_clock_seconds"] == 45 * 60
    assert clock["next_period_at"] == 135
    assert clock["clock_running"]


def test_timed_match_does_not_end_at_five_goals(monkeypatch):
    state = match()
    state["score_a"] = 5
    monkeypatch.setattr(physics.time, "time", lambda: 1010)
    physics.apply_kick(state, 4, 90, 0, True)
    assert not state["game_over"]
    assert state["score_a"] == 5


@pytest.fixture
def clock_api(monkeypatch):
    state = match(mode="hvh")
    room = {"game": state, "status": "active", "player_a": "dev:clock", "player_b": "dev:other",
            "name_a": "A", "name_b": "B", "move_count": 7,
            "last_move": {"kick_count": 7, "trajectory": []}, "move_log": []}
    saved = []
    monkeypatch.setattr(physics.time, "time", lambda: 1135)
    monkeypatch.setattr(appmod, "get_game", lambda _uid: state)
    monkeypatch.setattr(appmod, "save_game", lambda _uid, st: saved.append(copy.deepcopy(st)))
    monkeypatch.setattr(appmod, "_get_room", lambda _room: room)
    monkeypatch.setattr(appmod, "_save_room", lambda _room, value: saved.append(copy.deepcopy(value)))
    monkeypatch.setattr(appmod, "_ach_toasts", lambda: [])
    with appmod.app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = "dev:clock"
        yield client, state, room, saved


def test_state_refresh_advances_idle_period_and_saves(clock_api):
    client, state, _room, saved = clock_api
    response = client.get("/state").get_json()
    assert response["period"] == "regular_second"
    assert response["match_clock_seconds"] == 45 * 60
    assert response["next_period_at"] == 270
    assert state["kick_count"] == 0 and saved


@pytest.mark.parametrize("route", ["/move", "/ai_move", "/random_move"])
def test_boundary_move_returns_reset_without_executing_stale_command(clock_api, route):
    client, state, _room, saved = clock_api
    response = client.post(route, json={"player_idx": 4, "angle": 0, "power": 50}).get_json()
    assert response["period_changed"]
    assert response["period"] == "regular_second"
    assert state["kick_count"] == 0 and saved


def test_online_poll_advances_one_revision_without_replaying_last_kick(clock_api):
    client, state, room, saved = clock_api
    response = client.get("/online/clock/state?since_kick=7").get_json()
    assert response["game"]["period"] == "regular_second"
    assert response["move_count"] == 8
    assert "last_move" not in response
    assert state["kick_count"] == 0 and saved
    second = client.get("/online/clock/state?since_kick=8").get_json()
    assert second["move_count"] == 8


def test_online_stale_boundary_move_is_rejected(clock_api):
    client, state, _room, _saved = clock_api
    response = client.post("/online/clock/move", json={"expected_move_count": 7, "player_idx": 4, "angle": 0, "power": 50})
    assert response.status_code == 409
    assert response.get_json()["period_changed"]
    assert state["kick_count"] == 0


def test_waiting_room_does_not_consume_match_time(clock_api):
    client, state, room, _saved = clock_api
    room["status"] = "waiting"
    room["player_b"] = None
    response = client.get("/online/clock/state").get_json()
    assert response["game"]["match_clock_seconds"] == 0
    assert not response["game"]["clock_running"]
    assert state["period"] == "regular_first"
    with client.session_transaction() as session:
        session["user_id"] = "dev:joining"
    assert client.post("/online/clock/join", json={}).status_code == 200
    assert state["start_time"] == 1135
