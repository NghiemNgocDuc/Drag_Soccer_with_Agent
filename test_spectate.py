"""Tests for Live Spectator Mode: open active-match listing, open state
delivery, read-only enforcement (moves/voice/chat), and match-end cleanup."""
import os
import sys
import uuid

os.environ.setdefault("DEV_MODE", "1")
sys.path.insert(0, os.path.dirname(__file__))

import pytest

import app as appmod
from app import app
from db.redis_client import r


def new_user(tag):
    return f"{tag}-{uuid.uuid4().hex[:8]}", f"{tag} name"


def login(client_, uid_, name_):
    with client_.session_transaction() as s:
        s["user_id"] = uid_
        s["username"] = name_


def _create_room(client_, uid_, name_):
    login(client_, uid_, name_)
    resp = client_.post("/online/create", json={"player_count": 3})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["room_id"]


def _join_room(client_, uid_, name_, rid):
    login(client_, uid_, name_)
    resp = client_.post(f"/online/{rid}/join", json={})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def _cleanup(rid=None):
    keys = ["online:active"]
    if rid:
        keys += [f"room:{rid}", f"chat:match:{rid}"]
    for k in keys:
        try:
            r.delete(k)
        except Exception:
            pass


def _active_ids():
    try:
        return {i for i in r.smembers("online:active")}
    except Exception:
        return set()


#  Active-match listing 

def test_waiting_room_not_listed():
    _cleanup()
    try:
        ua, na = new_user("sa")
        with app.test_client() as c:
            rid = _create_room(c, ua, na)
            resp = c.get("/api/spectate/active")
            assert resp.status_code == 200
            assert resp.get_json()["matches"] == []
            assert _active_ids() == set()
            _cleanup(rid)
    finally:
        _cleanup()


def test_active_match_listed_with_details():
    _cleanup()
    try:
        ua, na = new_user("sa")
        ub, nb = new_user("sb")
        with app.test_client() as c:
            rid = _create_room(c, ua, na)
            _join_room(c, ub, nb, rid)
            data = c.get("/api/spectate/active").get_json()
            matches = data["matches"]
            assert len(matches) == 1
            m = matches[0]
            assert m["room_id"] == rid
            assert m["name_a"] == na
            assert m["name_b"] == nb
            assert m["score_a"] == 0 and m["score_b"] == 0
            assert m["kick_count"] == 0
            assert m["started_at"] > 0
            assert rid in _active_ids()
            _cleanup(rid)
    finally:
        _cleanup()


def test_stale_and_done_rooms_pruned_from_list():
    _cleanup()
    try:
        ua, na = new_user("sa")
        ub, nb = new_user("sb")
        with app.test_client() as c:
            rid = _create_room(c, ua, na)
            _join_room(c, ub, nb, rid)

            # A second room whose id is in the set but whose room blob is gone
            ghost = "g" * 10
            r.sadd("online:active", ghost)

            # Mark the real room "done" directly (as the move handler would)
            room = appmod._get_room(rid)
            room["status"] = "done"
            appmod._save_room(rid, room)

            data = c.get("/api/spectate/active").get_json()
            assert data["matches"] == []
            # Both stale ids pruned from the set
            assert _active_ids() == set()
            _cleanup(rid)
    finally:
        _cleanup()


def test_game_over_removes_room_from_active(monkeypatch):
    _cleanup()
    try:
        def fake_kick(game, pidx, angle, power, is_a):
            game["game_over"] = True
            game["winner"] = "A"
            game["score_a"] = 1
            traj = [{"x": 700, "y": 437.5, "z": 0, "a": [], "b": [],
                     "ref": {"x": 700, "y": 300}}]
            return traj, "A", "goal", (700, 437.5), None

        monkeypatch.setattr(appmod, "apply_kick", fake_kick)
        ua, na = new_user("sa")
        ub, nb = new_user("sb")
        with app.test_client() as c:
            rid = _create_room(c, ua, na)
            _join_room(c, ub, nb, rid)
            assert len(c.get("/api/spectate/active").get_json()["matches"]) == 1
            login(c, ua, na)  # A is first to move
            resp = c.post(f"/online/{rid}/move",
                          json={"player_idx": 0, "angle": 0.0, "power": 80.0})
            assert resp.status_code == 200, resp.get_data(as_text=True)
            assert appmod._get_room(rid)["status"] == "done"
            assert c.get("/api/spectate/active").get_json()["matches"] == []
            assert rid not in _active_ids()
            _cleanup(rid)
    finally:
        _cleanup()


#  Open state delivery 

def test_spectator_state_poll_open_and_read_only():
    _cleanup()
    try:
        ua, na = new_user("sa")
        ub, nb = new_user("sb")
        with app.test_client() as c:
            rid = _create_room(c, ua, na)
            _join_room(c, ub, nb, rid)
            # No session at all — logged-out visitor
            with app.test_client() as c2:
                st = c2.get(f"/online/{rid}/state?since_kick=-1")
                assert st.status_code == 200
                data = st.get_json()
                assert data["my_side"] is None
                assert data["room_id"] == rid
                assert data["name_a"] == na
                assert data["name_b"] == nb
                assert data["status"] == "active"
                assert "is_player_a" in data["game"]
                assert "players_a" in data["game"] and "players_b" in data["game"]
                assert "last_move" not in data or data["last_move"] is None
            _cleanup(rid)
    finally:
        _cleanup()


#  Write-path enforcement (server-side, spectator = not a participant) 

def test_spectator_move_rejected():
    _cleanup()
    try:
        ua, na = new_user("sa")
        ub, nb = new_user("sb")
        with app.test_client() as c:
            rid = _create_room(c, ua, na)
            _join_room(c, ub, nb, rid)
            us, ns = new_user("sc")
            login(c, us, ns)
            resp = c.post(f"/online/{rid}/move",
                          json={"player_idx": 0, "angle": 0.0, "power": 80.0})
            assert resp.status_code == 403
            _cleanup(rid)
    finally:
        _cleanup()


def test_spectator_voice_rejected():
    _cleanup()
    try:
        ua, na = new_user("sa")
        ub, nb = new_user("sb")
        with app.test_client() as c:
            rid = _create_room(c, ua, na)
            _join_room(c, ub, nb, rid)
            us, ns = new_user("sc")
            login(c, us, ns)
            resp = c.post(f"/online/{rid}/voice",
                          json={"type": "offer", "data": {"sdp": "x"}})
            assert resp.status_code == 403
            _cleanup(rid)
    finally:
        _cleanup()


def test_spectator_cannot_take_full_room_slot():
    _cleanup()
    try:
        ua, na = new_user("sa")
        ub, nb = new_user("sb")
        us, ns = new_user("sc")
        with app.test_client() as c:
            rid = _create_room(c, ua, na)
            _join_room(c, ub, nb, rid)
            login(c, us, ns)
            resp = c.post(f"/online/{rid}/join", json={})
            assert resp.status_code == 400
            _cleanup(rid)
    finally:
        _cleanup()


#  Read-only match chat 

def test_spectator_reads_chat_but_cannot_post():
    _cleanup()
    try:
        ua, na = new_user("sa")
        ub, nb = new_user("sb")
        with app.test_client() as c:
            rid = _create_room(c, ua, na)
            _join_room(c, ub, nb, rid)
            # A player sends a message
            login(c, ua, na)
            sent = c.post("/chat/send",
                          json={"scope": "match", "scope_id": rid, "body": "gl hf"})
            assert sent.status_code == 200, sent.get_data(as_text=True)
            # Spectator reads it
            us, ns = new_user("sc")
            login(c, us, ns)
            msgs = c.get(f"/chat/messages?scope=match&scope_id={rid}")
            assert msgs.status_code == 200, msgs.get_data(as_text=True)
            bodies = [m["body"] for m in msgs.get_json()["messages"]]
            assert "gl hf" in bodies
            # Spectator cannot post
            post = c.post("/chat/send",
                          json={"scope": "match", "scope_id": rid, "body": "hello"})
            assert post.status_code == 403
            _cleanup(rid)
    finally:
        _cleanup()


def test_spectator_chat_read_closes_when_match_ends():
    _cleanup()
    try:
        ua, na = new_user("sa")
        ub, nb = new_user("sb")
        us, ns = new_user("sc")
        with app.test_client() as c:
            rid = _create_room(c, ua, na)
            _join_room(c, ub, nb, rid)
            room = appmod._get_room(rid)
            room["status"] = "done"
            appmod._save_room(rid, room)
            login(c, us, ns)
            msgs = c.get(f"/chat/messages?scope=match&scope_id={rid}")
            assert msgs.status_code == 403
            # Participants still read fine after the match
            login(c, ua, na)
            msgs2 = c.get(f"/chat/messages?scope=match&scope_id={rid}")
            assert msgs2.status_code == 200
            _cleanup(rid)
    finally:
        _cleanup()


#  Pages 

def test_spectate_pages():
    _cleanup()
    try:
        ua, na = new_user("sa")
        ub, nb = new_user("sb")
        with app.test_client() as c:
            # Lobby is open (no login required)
            lobby = c.get("/spectate")
            assert lobby.status_code == 200
            assert b"Live Matches" in lobby.data or b"live matches" in lobby.data.lower()

            rid = _create_room(c, ua, na)
            _join_room(c, ub, nb, rid)

            # Live view renders the 3D viewer in live mode (no login)
            view = c.get(f"/spectate/{rid}")
            assert view.status_code == 200
            html = view.get_data(as_text=True)
            assert "initLive" in html
            assert rid in html

            # Missing room redirects to the lobby
            missing = c.get("/spectate/does-not-exist")
            assert missing.status_code == 302
            assert "/spectate" in missing.headers["Location"]
            _cleanup(rid)
    finally:
        _cleanup()
