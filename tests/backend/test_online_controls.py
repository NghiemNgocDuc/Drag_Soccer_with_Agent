"""Online room integrity, teardown, queue, and voice-cursor regressions."""
import copy
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("DEV_MODE", "1")

import pytest
import app as appmod
from db.redis_client import r
from db.voice import send_voice_signal, get_voice_signals, VOICE_MAX
from models.soccer_logic import _setup_penalty_positions


@pytest.fixture
def online(monkeypatch):
    clients = []
    rooms = []
    summaries = []
    token = uuid.uuid4().hex[:8]
    before = set(getattr(r, "_store", {}))
    monkeypatch.setattr(appmod, "_ach_toasts", lambda: [])
    monkeypatch.setattr(appmod, "_mem_short", lambda *_: None)
    monkeypatch.setattr(appmod, "_mem_summ", lambda *_: None)
    monkeypatch.setattr(appmod, "_check_online_achievements", lambda *_: None)
    monkeypatch.setattr(appmod, "_check_ranked_achievements", lambda *_: None)
    monkeypatch.setattr(appmod, "_record_season_match", lambda *_: None)
    monkeypatch.setattr(appmod, "_push_recent_pair", lambda *_: None)
    monkeypatch.setattr(appmod, "_save_match_summary", lambda rid, room: summaries.append((rid, copy.deepcopy(room))))

    def client(side):
        value = appmod.app.test_client()
        with value.session_transaction() as session:
            session["user_id"] = f"dev:online-{token}-{side}"
            session["username"] = f"Online {side}"
        clients.append(value)
        return value

    a, b, outsider = client("a"), client("b"), client("outside")

    def room(payload=None, active=True):
        response = a.post("/online/create", json=payload if payload is not None else {"player_count": 3})
        assert response.status_code == 200, response.get_json()
        rid = response.get_json()["room_id"]
        rooms.append(rid)
        if active:
            joined = b.post(f"/online/{rid}/join", json={"password": (payload or {}).get("password")})
            assert joined.status_code == 200, joined.get_json()
        return rid

    yield {"a": a, "b": b, "outside": outsider, "room": room, "summaries": summaries,
           "uid_a": f"dev:online-{token}-a", "uid_b": f"dev:online-{token}-b"}
    for rid in rooms:
        r.delete(f"room:{rid}")
        r.srem("online:active", rid)
        r.srem("ranked:rooms", rid)
    for key in set(getattr(r, "_store", {})) - before:
        if key.startswith("room:"):
            r.srem("online:active", key[5:])
            r.srem("ranked:rooms", key[5:])
        r.delete(key)
    # Remove only this fixture's users from global queue sets.
    for queue in ("quick:queue", "ranked:queue"):
        for side in ("a", "b", "outside"):
            r.srem(queue, f"dev:online-{token}-{side}")


def fake_kick(game, index, angle, power, is_a):
    game["kick_count"] += 1
    game["is_player_a"] = not is_a
    return [dict(game["ball"])], None, "Kick", None, None


@pytest.mark.parametrize("payload", [[1], "bad", {"player_count": 0}, {"player_count": 12},
                                    {"player_count": True}, {"password": "x" * 33}, {"password": [1]}])
def test_create_rejects_invalid_requests(online, payload):
    response = online["a"].post("/online/create", json=payload)
    assert response.status_code == 400
    assert response.get_json()["error"]


def test_create_settings_password_and_shared_link(online):
    rid = online["room"]({"player_count": 5, "password": "  pitch pass  "})
    game = online["b"].get(f"/online/{rid}/state").get_json()["game"]
    assert game["game_mode"] == "hvh" and len(game["players_a"]) == 5
    assert online["a"].get(f"/join/{rid}").headers["Location"].endswith(f"/play3d?room={rid}")


@pytest.mark.parametrize("cursor", ["bad", "1.5", "-2"])
def test_invalid_poll_cursor_is_client_error(online, cursor):
    rid = online["room"]()
    response = online["a"].get(f"/online/{rid}/state?since_kick={cursor}")
    assert response.status_code == 400 and response.is_json


@pytest.mark.parametrize("value", [True, [], -1, 1.2, "not-a-number"])
def test_invalid_expected_revision_does_not_mutate(online, monkeypatch, value):
    rid = online["room"]()
    before = appmod._get_room(rid)
    monkeypatch.setattr(appmod, "apply_kick", lambda *_: pytest.fail("Invalid command reached physics"))
    response = online["a"].post(f"/online/{rid}/move", json={"expected_move_count": value})
    assert response.status_code == 400
    assert appmod._get_room(rid) == before


def test_moves_are_monotonic_and_stale_commands_conflict(online, monkeypatch):
    rid = online["room"]()
    monkeypatch.setattr(appmod, "apply_kick", fake_kick)
    first = online["a"].post(f"/online/{rid}/move", json={"expected_move_count": 0, "power": 0})
    assert first.status_code == 200 and first.get_json()["move_count"] == 1
    before = appmod._get_room(rid)
    duplicate = online["a"].post(f"/online/{rid}/move", json={"expected_move_count": 0, "power": 0})
    assert duplicate.status_code == 409
    assert appmod._get_room(rid) == before
    second = online["b"].post(f"/online/{rid}/move", json={"expected_move_count": 1, "power": 0})
    assert second.status_code == 200
    polled = online["a"].get(f"/online/{rid}/state?since_kick=0").get_json()
    assert polled["move_count"] == polled["game"]["online_move_count"] == 2
    assert [move["kick_count"] for move in polled["moves"]] == [1, 2]
    assert polled["last_move"]["kick_count"] == 2
    assert online["a"].get(f"/online/{rid}/state?since_kick=2").get_json()["moves"] == []


def test_real_penalties_advance_online_cursor_without_changing_match_clock(online):
    rid = online["room"]()
    room = appmod._get_room(rid)
    room["game"].update(penalty_shootout=True, period="penalties", penalty_kick_num=0,
                        penalty_a_score=0, penalty_b_score=0, penalty_kicks=[], penalty_goalkeeper_move=None)
    _setup_penalty_positions(room["game"], True)
    appmod._save_room(rid, room)
    for revision, side in ((0, "a"), (1, "b")):
        response = online[side].post(f"/online/{rid}/move", json={"expected_move_count": revision, "player_idx": 0, "power": 0, "angle": 0})
        assert response.status_code == 200, response.get_json()
        result = response.get_json()
        assert result["move_result"]["is_penalty"] is True
        assert result["game"]["kick_count"] == 0
        assert result["game"]["penalty_kick_num"] == result["move_count"] == revision + 1
    polled = online["a"].get(f"/online/{rid}/state?since_kick=1").get_json()
    assert [move["kick_count"] for move in polled["moves"]] == [2]


def test_last_regular_move_is_not_labeled_as_a_penalty(online, monkeypatch):
    rid = online["room"]()
    def enter_penalties(game, *args):
        result = fake_kick(game, *args)
        game["penalty_shootout"] = True
        return result
    monkeypatch.setattr(appmod, "apply_kick", enter_penalties)
    response = online["a"].post(f"/online/{rid}/move", json={"power": 0})
    assert response.get_json()["game"]["penalty_shootout"] is True
    assert response.get_json()["move_result"]["is_penalty"] is False


def test_concurrent_duplicate_kick_is_applied_once(online, monkeypatch):
    rid = online["room"]()
    entered, release = threading.Event(), threading.Event()
    calls = []
    def blocked(game, *args):
        calls.append(1); entered.set()
        assert release.wait(2)
        return fake_kick(game, *args)
    monkeypatch.setattr(appmod, "apply_kick", blocked)
    a2 = appmod.app.test_client()
    with a2.session_transaction() as session:
        session["user_id"] = online["uid_a"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(online["a"].post, f"/online/{rid}/move", json={"expected_move_count": 0})
        assert entered.wait(2)
        second = pool.submit(a2.post, f"/online/{rid}/move", json={"expected_move_count": 0})
        time.sleep(.03); release.set()
        responses = [first.result(), second.result()]
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert calls == [1] and appmod._get_room(rid)["move_count"] == 1


def test_join_and_waiting_cancel_are_atomic(online):
    rid = online["room"](active=False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        join = pool.submit(online["b"].post, f"/online/{rid}/join", json={})
        cancel = pool.submit(online["a"].post, f"/online/{rid}/leave", json={"waiting_only": True})
        joined, cancelled = join.result(), cancel.result()
    room = appmod._get_room(rid)
    if joined.status_code == 200:
        assert cancelled.status_code == 409 and room["status"] == "active"
        assert not room["game"]["game_over"]
    else:
        assert joined.status_code == 409 and cancelled.status_code == 200
        assert room["status"] == "cancelled" and room["player_b"] is None
    assert room["game"].get("winner") is None


def test_invite_cannot_replace_an_opponent_or_restart_a_completed_room(online):
    response = online["a"].post("/online/invite/send", json={"to_uid": online["uid_b"]})
    invite = response.get_json(); rid = invite["room_id"]
    assert online["outside"].post(f"/online/{rid}/join", json={}).status_code == 200
    before = appmod._get_room(rid)
    accepted = online["b"].post(f"/online/invite/{invite['invite_id']}/accept", json={})
    assert accepted.status_code == 409 and appmod._get_room(rid) == before
    before["status"] = "done"; before["game"]["game_over"] = True
    appmod._save_room(rid, before)
    assert online["b"].post(f"/online/invite/{invite['invite_id']}/accept", json={}).status_code == 409
    assert online["b"].get("/online/invites").get_json() == []


def test_waiting_cancel_and_finished_forfeit_are_idempotent(online):
    waiting = online["room"](active=False)
    cancelled = online["a"].post(f"/online/{waiting}/leave", json={"waiting_only": True})
    assert cancelled.get_json()["status"] == "cancelled"
    assert online["b"].post(f"/online/{waiting}/join", json={}).status_code == 409
    rid = online["room"]()
    assert online["outside"].post(f"/online/{rid}/leave", json={}).status_code == 403
    result = online["a"].post(f"/online/{rid}/leave", json={}).get_json()
    assert result["status"] == "done" and result["game"]["winner"] == "B"
    assert result["game"]["game_over"] and result["game"]["termination"] == "forfeit"
    assert rid not in r.smembers("online:active")
    repeated = online["a"].post(f"/online/{rid}/leave", json={})
    assert repeated.get_json()["game"]["winner"] == "B"
    assert len(online["summaries"]) == 1


def test_ranked_forfeit_uses_server_result_hook_once(online, monkeypatch):
    from db import ranked, money
    rid = online["room"]()
    room = appmod._get_room(rid); room["ranked"] = True; appmod._save_room(rid, room)
    r.sadd("ranked:rooms", rid)
    r.setex(f"ranked:match:{online['uid_a']}", 60, rid)
    calls = []
    def record(**data):
        calls.append(data)
        return {"winner": data["winner"], "player_a": {"delta": -20}, "player_b": {"delta": 20}}
    monkeypatch.setattr(ranked, "record_result", record)
    monkeypatch.setattr(money, "add_coins", lambda *_: None)
    response = online["a"].post(f"/online/{rid}/leave", json={})
    assert response.status_code == 200
    assert response.get_json()["ranked_result"]["winner"] == "B"
    assert online["a"].post(f"/online/{rid}/leave", json={}).status_code == 200
    assert len(calls) == 1 and calls[0]["player_b"] == online["uid_b"]
    assert r.get(f"ranked:match:{online['uid_a']}") is None
    assert rid not in r.smembers("ranked:rooms")


def test_first_ranked_move_cannot_be_deleted_by_stale_reclamation(online, monkeypatch):
    rid = online["room"]()
    room = appmod._get_room(rid); room.update(ranked=True, started_at=time.time()-120)
    appmod._save_room(rid, room); r.sadd("ranked:rooms", rid)
    entered, release = threading.Event(), threading.Event()
    def blocked(game, *args):
        entered.set(); assert release.wait(2); return fake_kick(game, *args)
    monkeypatch.setattr(appmod, "apply_kick", blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        move = pool.submit(online["a"].post, f"/online/{rid}/move", json={"power": 0})
        assert entered.wait(2)
        reclaim = pool.submit(appmod._reclaim_stale_ranked)
        time.sleep(.03); release.set()
        assert move.result().status_code == 200; reclaim.result()
    assert appmod._get_room(rid)["move_count"] == 1
    assert online["uid_a"] not in r.smembers("ranked:queue")


def test_team_selection_cannot_teleport_players_after_kick(online, monkeypatch):
    rid = online["room"]()
    chosen = online["a"].post(f"/online/{rid}/team", json={"team_id": "brazil"})
    assert chosen.status_code == 200
    assert online["b"].post(f"/online/{rid}/team", json={"team_id": "brazil"}).status_code == 409
    monkeypatch.setattr(appmod, "apply_kick", fake_kick)
    assert online["a"].post(f"/online/{rid}/move", json={"power": 0}).status_code == 200
    before = appmod._get_room(rid)
    assert online["a"].post(f"/online/{rid}/team", json={"team_id": "argentina"}).status_code == 409
    assert appmod._get_room(rid) == before


def test_quick_pvp_matches_two_sessions_and_polls_without_crashing(online):
    first = online["a"].post("/api/quick/join", json={"mode": "pvp"})
    assert first.get_json()["status"] == "waiting"
    second = online["b"].post("/api/quick/join", json={"mode": "pvp"})
    assert second.status_code == 200 and second.get_json()["status"] == "matched"
    status = online["a"].get("/api/quick/status")
    assert status.status_code == 200 and status.get_json()["room_id"] == second.get_json()["room_id"]


def test_expired_queue_waiters_and_finished_match_pointers_are_pruned(online):
    stale = f"stale-{uuid.uuid4().hex}"
    for queue in ("quick", "ranked"):
        r.sadd(f"{queue}:queue", stale)
        r.setex(f"{queue}:join:{stale}", 1, time.time()-7200)
    assert stale not in appmod._quick_members()
    assert stale not in appmod._ranked_members()
    rid = online["room"](); room = appmod._get_room(rid); room["status"] = "done"; appmod._save_room(rid, room)
    r.setex(f"quick:match:{online['uid_a']}", 30, rid)
    assert online["a"].get("/api/quick/status").get_json()["status"] == "idle"


def test_voice_sequence_keeps_advancing_after_queue_cap(online):
    rid = online["room"]()
    for index in range(VOICE_MAX+20):
        sent = send_voice_signal(rid, "peer", "ice", {"candidate": index})
        assert sent["seq"] == index
    retained = json.loads(r.get(f"voice_signal:{rid}"))
    assert len(retained) == VOICE_MAX and retained[0]["seq"] == 20
    recent, cursor = get_voice_signals(rid, 99)
    assert [signal["seq"] for signal in recent] == list(range(100,120)) and cursor == "119"
    send_voice_signal(rid, "peer", "mute", {"muted": True})
    fresh, cursor = get_voice_signals(rid, 119)
    assert [signal["seq"] for signal in fresh] == [120] and cursor == "120"


def test_concurrent_voice_candidates_are_not_lost(online):
    rid = online["room"]()
    with ThreadPoolExecutor(max_workers=8) as pool:
        signals = list(pool.map(lambda index: send_voice_signal(rid, "peer", "ice", {"candidate": index}), range(24)))
    assert sorted(signal["seq"] for signal in signals) == list(range(24))
    received, cursor = get_voice_signals(rid, None)
    assert len(received) == 24 and cursor == "23"


@pytest.mark.parametrize("queue", ["quick", "ranked"])
def test_queue_cancel_preserves_a_match_that_already_formed(online, queue):
    rid = online["room"]()
    room = appmod._get_room(rid)
    room["ranked"] = queue == "ranked"
    appmod._save_room(rid, room)
    r.setex(f"{queue}:match:{online['uid_a']}", 60, rid)
    r.sadd(f"{queue}:queue", online["uid_a"])
    r.setex(f"{queue}:join:{online['uid_a']}", 60, time.time())
    route = "/api/quick/cancel" if queue == "quick" else "/ranked/cancel"
    cancelled = online["a"].post(route, json={})
    assert cancelled.status_code == 200
    assert cancelled.get_json()["status"] == "matched" and cancelled.get_json()["room_id"] == rid
    assert r.get(f"{queue}:match:{online['uid_a']}") == rid
    assert online["uid_a"] not in r.smembers(f"{queue}:queue")
    assert appmod._get_room(rid)["status"] == "active"
    assert appmod._get_room(rid)["game"].get("winner") is None
