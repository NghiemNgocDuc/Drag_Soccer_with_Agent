"""Tests for the chat feature: match chat, tournament lobby chat, DMs, moderation."""
import os
import sys
import time
import uuid

os.environ.setdefault("DEV_MODE", "1")
sys.path.insert(0, os.path.dirname(__file__))

import pytest

from app import app
from db.redis_client import r
from db.chat import (
    contains_profanity, is_emoji_only, check_rate_limit, conv_id, conv_parties,
    send_ephemeral, get_ephemeral, mark_read, get_blocked, block_user, unblock_user,
)
from db.supabase_client import service as _supa

HAS_SUPABASE = _supa is not None

A, B, C = "u-a-test", "u-b-test", "u-c-test"
NAME_A, NAME_B, NAME_C = "Tester A", "Tester B", "Tester C"


def new_user(tag):
    return f"{tag}-{uuid.uuid4().hex[:8]}", f"{tag} name"


@pytest.fixture()
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def login(client_, uid_, name_):
    with client_.session_transaction() as s:
        s["user_id"] = uid_
        s["username"] = name_


def _cleanup(keys):
    for k in keys:
        try:
            r.delete(k)
        except Exception:
            pass


# ── Unit: helpers ────────────────────────────────────────────────────────

def test_conv_id_stable_and_roundtrip():
    c1 = conv_id(A, B)
    c2 = conv_id(B, A)
    assert c1 == c2
    me, other = conv_parties(c1)
    assert {me, other} == {A, B}


def test_is_emoji_only():
    assert is_emoji_only("⚽🔥")
    assert is_emoji_only("  😂  😤 ")
    assert not is_emoji_only("⚽ goal")
    assert not is_emoji_only("   ")
    assert not is_emoji_only("nice!")


def test_profanity_filter():
    bad = ["fuck this", "you are an asshole", "sh1t", "f-ck", "f u c k this",
           "b1tch", "d1ck", "go to hell assh0le", "n1gger", "ret4rd"]
    for b in bad:
        assert contains_profanity(b), f"expected blocked: {b!r}"
    good = ["nice shot!", "GOAL!", "great assist", "classic goal", "pass it",
            "shoot", "assist", "beautiful", "what a save", "GG"]
    for g in good:
        assert not contains_profanity(g), f"expected allowed: {g!r}"


def test_rate_limit_window():
    key = f"u-rate-test-{uuid.uuid4().hex[:8]}"
    ok = 0
    for _ in range(10):
        if check_rate_limit(key)[0]:
            ok += 1
    assert ok == 10
    assert check_rate_limit(key)[0] is False


def test_send_get_ephemeral():
    rid = f"room-{uuid.uuid4().hex[:8]}"
    _cleanup([f"chat:match:{rid}"])
    try:
        for i in range(3):
            send_ephemeral("match", rid, A, NAME_A, f"msg {i}")
        msgs, nxt = get_ephemeral("match", rid, None, 60, set())
        assert [m["body"] for m in msgs] == ["msg 0", "msg 1", "msg 2"]
        assert nxt == "2"
        more, nxt2 = get_ephemeral("match", rid, 1, 60, set())
        assert [m["body"] for m in more] == ["msg 2"]
        assert nxt2 == "2"
        send_ephemeral("match", rid, B, NAME_B, "from B")
        hidden, _ = get_ephemeral("match", rid, None, 60, {B})
        assert all(m["sender_id"] != B for m in hidden)
    finally:
        _cleanup([f"chat:match:{rid}"])


# ── Match chat (HTTP) ────────────────────────────────────────────────────

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


def test_match_chat_flow():
    ua, na = new_user("ua")
    ub, nb = new_user("ub")
    with app.test_client() as c:
        rid = _create_room(c, ua, na)
        _join_room(c, ub, nb, rid)
        login(c, ua, na)
        r1 = c.post("/chat/send", json={"scope": "match", "scope_id": rid, "body": "hello there"})
        assert r1.status_code == 200, r1.get_data(as_text=True)
        m1 = r1.get_json()["message"]
        assert m1["sender_id"] == ua
        assert m1["mid"] == "0"
        r2 = c.post("/chat/send", json={"scope": "match", "scope_id": rid, "body": "⚽⚽⚽"})
        assert r2.status_code == 200
        assert r2.get_json()["message"]["emoji_only"] is True

        login(c, ub, nb)
        g = c.get(f"/chat/messages?scope=match&scope_id={rid}")
        assert g.status_code == 200
        data = g.get_json()
        assert data["me"] == ub
        assert [m["body"] for m in data["messages"]] == ["hello there", "⚽⚽⚽"]
        assert data["next_after"] == "1"

        # nothing new yet
        empty = c.get(f"/chat/messages?scope=match&scope_id={rid}&after=1")
        assert empty.get_json()["messages"] == []

        login(c, ua, na)
        c.post("/chat/send", json={"scope": "match", "scope_id": rid, "body": "see you"})
        login(c, ub, nb)
        g2 = c.get(f"/chat/messages?scope=match&scope_id={rid}&after=1")
        assert [m["body"] for m in g2.get_json()["messages"]] == ["see you"]
        assert g2.get_json()["next_after"] == "2"


def test_match_chat_requires_membership():
    ua, na = new_user("ua")
    ub, nb = new_user("ub")
    with app.test_client() as c:
        rid = _create_room(c, ua, na)
        login(c, ub, nb)
        bad_send = c.post("/chat/send", json={"scope": "match", "scope_id": rid, "body": "sneak"})
        assert bad_send.status_code == 403
        bad_get = c.get(f"/chat/messages?scope=match&scope_id={rid}")
        assert bad_get.status_code == 403


def test_match_chat_guest_allowed():
    ua, na = new_user("ua")
    with app.test_client() as c:
        rid = _create_room(c, ua, na)
        # fresh browser (no session) joins via invite link -> guest identity
        with c.session_transaction() as s:
            s.clear()
        j = c.post(f"/online/{rid}/join", json={})
        assert j.status_code == 200, j.get_data(as_text=True)
        g = c.post("/chat/send", json={"scope": "match", "scope_id": rid, "body": "hi from guest"})
        assert g.status_code == 200, g.get_data(as_text=True)
        assert g.get_json()["message"]["sender_id"].startswith("guest:")
        gget = c.get(f"/chat/messages?scope=match&scope_id={rid}")
        assert gget.status_code == 200
        assert [m["body"] for m in gget.get_json()["messages"]] == ["hi from guest"]


def test_match_chat_profanity_and_validation():
    ua, na = new_user("ua")
    with app.test_client() as c:
        rid = _create_room(c, ua, na)
        p = c.post("/chat/send", json={"scope": "match", "scope_id": rid, "body": "f-ck you"})
        assert p.status_code == 400
        e = c.post("/chat/send", json={"scope": "match", "scope_id": rid, "body": "   "})
        assert e.status_code == 400
        long_body = "x" * 281
        lr = c.post("/chat/send", json={"scope": "match", "scope_id": rid, "body": long_body})
        assert lr.status_code == 400
        bad_scope = c.post("/chat/send", json={"scope": "nope", "scope_id": rid, "body": "hi"})
        assert bad_scope.status_code == 400
        no_room = c.post("/chat/send", json={"scope": "match", "scope_id": "zzz-nope", "body": "hi"})
        assert no_room.status_code == 404


def test_match_chat_rate_limit_endpoint():
    ua, na = new_user("ua")
    with app.test_client() as c:
        rid = _create_room(c, ua, na)
        for _ in range(10):
            rsp = c.post("/chat/send", json={"scope": "match", "scope_id": rid, "body": "yo"})
            assert rsp.status_code == 200, rsp.get_data(as_text=True)
        rsp = c.post("/chat/send", json={"scope": "match", "scope_id": rid, "body": "yo"})
        assert rsp.status_code == 429


# ── Tournament lobby chat (HTTP) ─────────────────────────────────────────

def test_tournament_lobby_chat():
    ua, na = new_user("ua")
    ub, nb = new_user("ub")
    with app.test_client() as c:
        login(c, ua, na)
        t = c.post("/tournaments/create", json={"name": "Chat Cup"}).get_json()["tournament"]
        tid = t["id"]

        # creator can chat
        ok = c.post("/chat/send", json={"scope": "tournament", "scope_id": tid, "body": "welcome!"})
        assert ok.status_code == 200

        # random user cannot
        login(c, ub, nb)
        denied = c.post("/chat/send", json={"scope": "tournament", "scope_id": tid, "body": "hi"})
        assert denied.status_code == 403
        denied_get = c.get(f"/chat/messages?scope=tournament&scope_id={tid}")
        assert denied_get.status_code == 403

        # friend participant can
        login(c, ua, na)
        add = c.post(f"/tournaments/{tid}/add", json={"participant_id": f"friend:{ub}", "name": nb})
        assert add.status_code == 200, add.get_data(as_text=True)
        login(c, ub, nb)
        ok2 = c.post("/chat/send", json={"scope": "tournament", "scope_id": tid, "body": "joined as friend"})
        assert ok2.status_code == 200
        g = c.get(f"/chat/messages?scope=tournament&scope_id={tid}")
        assert g.status_code == 200
        assert [m["body"] for m in g.get_json()["messages"]] == ["welcome!", "joined as friend"]


# ── DMs (HTTP, needs Supabase) ───────────────────────────────────────────

@pytest.mark.skipif(not HAS_SUPABASE, reason="Supabase not configured")
def test_dm_friends_only():
    ua, na = new_user("ua")
    ub, nb = new_user("ub")
    uc, nc = new_user("uc")
    _cleanup([f"friends:{ua}", f"friends:{ub}", f"friends:{uc}"])
    _cleanup([f"chat_read:{ua}", f"chat_read:{ub}"])
    try:
        from db.redis_client import r as _r
        import json as _json
        _r.setex(f"friends:{ua}", 3600, _json.dumps([{"uid": ub, "username": nb}]))
        _r.setex(f"friends:{ub}", 3600, _json.dumps([{"uid": ua, "username": na}]))

        with app.test_client() as c:
            login(c, ub, nb)
            not_friend = c.post("/chat/send", json={"scope": "dm", "to_uid": uc, "body": "hi"})
            assert not_friend.status_code == 403
            not_friend_get = c.get("/chat/messages?scope=dm&with=" + uc)
            assert not_friend_get.status_code == 403

            login(c, ua, na)
            ok = c.post("/chat/send", json={"scope": "dm", "to_uid": ub, "body": "friend hello"})
            assert ok.status_code == 200, ok.get_data(as_text=True)
            assert ok.get_json()["message"]["sender_id"] == ua

            login(c, ub, nb)
            g = c.get("/chat/messages?scope=dm&with=" + ua + "&mark_read=1")
            assert g.status_code == 200
            data = g.get_json()
            assert data["scope_id"] == conv_id(ua, ub)
            assert [m["body"] for m in data["messages"]] == ["friend hello"]

            login(c, ua, na)
            g2 = c.get("/chat/messages?scope=dm&with=" + ub)
            assert g2.status_code == 200
            assert [m["body"] for m in g2.get_json()["messages"]] == ["friend hello"]

            convs = c.get("/chat/conversations")
            assert convs.status_code == 200
            assert any(cv["conv_id"] == conv_id(ua, ub) for cv in convs.get_json()["conversations"])

            # A blocks C, then C can't send to A
            blk = c.post("/chat/block", json={"target": uc})
            assert blk.status_code == 200
            login(c, uc, nc)
            blocked_send = c.post("/chat/send", json={"scope": "dm", "to_uid": ua, "body": "hey"})
            assert blocked_send.status_code == 403
            login(c, ua, na)
            un = c.post("/chat/unblock", json={"target": uc})
            assert un.status_code == 200
            bl = c.get("/chat/blocked")
            assert bl.status_code == 200
            assert uc not in bl.get_json()["blocked"]
    finally:
        _cleanup([f"friends:{ua}", f"friends:{ub}", f"friends:{uc}",
                  f"chat_read:{ua}", f"chat_read:{ub}"])


@pytest.mark.skipif(not HAS_SUPABASE, reason="Supabase not configured")
def test_block_hides_in_match():
    ua, na = new_user("ua")
    ub, nb = new_user("ub")
    with app.test_client() as c:
        rid = _create_room(c, ua, na)
        _join_room(c, ub, nb, rid)
        login(c, ub, nb)
        c.post("/chat/send", json={"scope": "match", "scope_id": rid, "body": "from B"})
        login(c, ua, na)
        # block B
        blk = c.post("/chat/block", json={"target": ub})
        assert blk.status_code == 200
        g = c.get(f"/chat/messages?scope=match&scope_id={rid}")
        assert g.status_code == 200
        assert g.get_json()["messages"] == []
        un = c.post("/chat/unblock", json={"target": ub})
        assert un.status_code == 200
        g2 = c.get(f"/chat/messages?scope=match&scope_id={rid}")
        assert [m["body"] for m in g2.get_json()["messages"]] == ["from B"]


@pytest.mark.skipif(not HAS_SUPABASE, reason="Supabase not configured")
def test_report_message():
    ua, na = new_user("ua")
    ub, nb = new_user("ub")
    with app.test_client() as c:
        rid = _create_room(c, ua, na)
        _join_room(c, ub, nb, rid)
        login(c, ub, nb)
        c.post("/chat/send", json={"scope": "match", "scope_id": rid, "body": "suspicious"})
        login(c, ua, na)
        rp = c.post("/chat/report", json={"scope": "match", "scope_id": rid, "mid": "0", "reason": "spam"})
        assert rp.status_code == 200, rp.get_data(as_text=True)
        bad = c.post("/chat/report", json={"scope": "match", "scope_id": rid, "mid": "99", "reason": "spam"})
        assert bad.status_code == 404
        no_room = c.post("/chat/report", json={"scope": "match", "scope_id": "nope", "mid": "0"})
        assert no_room.status_code == 404


# ── Auth gating ──────────────────────────────────────────────────────────

def test_chat_endpoints_require_login():
    with app.test_client() as c:
        assert c.get("/messages").status_code in (302, 401)
        assert c.post("/chat/send", json={"scope": "dm", "to_uid": B, "body": "x"}).status_code == 401
        assert c.post("/chat/send", json={"scope": "tournament", "scope_id": "x", "body": "x"}).status_code == 401
        assert c.get("/chat/conversations").status_code in (302, 401)
        assert c.post("/chat/block", json={"target": B}).status_code == 401
        assert c.post("/chat/unblock", json={"target": B}).status_code == 401
        assert c.get("/chat/blocked").status_code in (302, 401)
