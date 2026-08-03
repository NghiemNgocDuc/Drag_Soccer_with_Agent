"""Tests for the 1v1 voice-chat signaling relay (WebRTC offer/answer/ICE)."""
import os
import sys
import uuid

os.environ.setdefault("DEV_MODE", "1")
sys.path.insert(0, os.path.dirname(__file__))

import pytest

from app import app
from db.redis_client import r
from db.voice import send_voice_signal, get_voice_signals
from db.supabase_client import service as _supa

HAS_SUPABASE = _supa is not None


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


def _cleanup(keys):
    for k in keys:
        try:
            r.delete(k)
        except Exception:
            pass


def test_unit_signal_queue_and_cursor():
    rid = f"voice-{uuid.uuid4().hex[:8]}"
    _cleanup([f"voice_signal:{rid}"])
    try:
        send_voice_signal(rid, "peer-a", "offer", {"sdp": "v=0 offer"})
        send_voice_signal(rid, "peer-b", "answer", {"sdp": "v=0 answer"})
        send_voice_signal(rid, "peer-a", "ice", {"candidate": {"c": 1}})
        sigs, nxt = get_voice_signals(rid, None, set())
        assert [s["type"] for s in sigs] == ["offer", "answer", "ice"]
        assert nxt == "2"
        assert sigs[0]["from"] == "peer-a"
        assert sigs[1]["data"]["sdp"] == "v=0 answer"
        inc, nxt2 = get_voice_signals(rid, 0, set())
        assert [s["type"] for s in inc] == ["answer", "ice"]
        assert nxt2 == "2"
        empty, _ = get_voice_signals(rid, 2, set())
        assert empty == []
        # blocked sender is filtered out
        filtered, _ = get_voice_signals(rid, None, {"peer-a"})
        assert [s["type"] for s in filtered] == ["answer"]
    finally:
        _cleanup([f"voice_signal:{rid}"])


def test_unit_invalid_type():
    with pytest.raises(ValueError):
        send_voice_signal("r", "p", "shout", {})


def test_voice_signal_relay_http():
    ua, na = new_user("va")
    ub, nb = new_user("vb")
    uc, nc = new_user("vc")
    with app.test_client() as c:
        rid = _create_room(c, ua, na)
        _join_room(c, ub, nb, rid)

        # host sends offer
        login(c, ua, na)
        off = c.post(f"/online/{rid}/voice", json={"type": "offer", "data": {"sdp": "SDP-OFFER"}})
        assert off.status_code == 200, off.get_data(as_text=True)
        assert off.get_json()["signal"]["seq"] == 0

        # host polls: own offer is filtered out
        st = c.get(f"/online/{rid}/state?since_kick=-1&voice_after=-1")
        assert st.status_code == 200
        assert st.get_json()["voice_signals"] == []
        assert st.get_json()["voice_after"] == "0"

        # peer receives the offer
        login(c, ub, nb)
        st2 = c.get(f"/online/{rid}/state?since_kick=-1&voice_after=-1")
        data = st2.get_json()
        assert len(data["voice_signals"]) == 1
        assert data["voice_signals"][0]["type"] == "offer"
        assert data["voice_signals"][0]["from"] == ua
        assert data["voice_signals"][0]["data"]["sdp"] == "SDP-OFFER"
        assert data["voice_after"] == "0"

        # peer answers
        ans = c.post(f"/online/{rid}/voice", json={"type": "answer", "data": {"sdp": "SDP-ANSWER"}})
        assert ans.status_code == 200

        # peer sends ICE
        ice = c.post(f"/online/{rid}/voice",
                     json={"type": "ice", "data": {"candidate": {"candidate": "cand-1"}}})
        assert ice.status_code == 200

        # host sees answer + ice (own offer still filtered)
        login(c, ua, na)
        st3 = c.get(f"/online/{rid}/state?since_kick=-1&voice_after=0")
        d3 = st3.get_json()
        assert [s["type"] for s in d3["voice_signals"]] == ["answer", "ice"]
        assert d3["voice_after"] == "2"
        # no new signals after cursor
        st4 = c.get(f"/online/{rid}/state?since_kick=-1&voice_after=2")
        assert st4.get_json()["voice_signals"] == []

        # no voice_after param -> no signals field at all
        st5 = c.get(f"/online/{rid}/state?since_kick=-1")
        assert "voice_signals" not in st5.get_json()

        # outsider cannot send or read signals
        login(c, uc, nc)
        den = c.post(f"/online/{rid}/voice", json={"type": "offer", "data": {}})
        assert den.status_code == 403
        den2 = c.get(f"/online/{rid}/state?since_kick=-1&voice_after=-1")
        assert "voice_signals" not in den2.get_json()  # my_side is None -> no signals


def test_voice_gate_and_validation():
    ua, na = new_user("va")
    with app.test_client() as c:
        rid = _create_room(c, ua, na)
        # no session -> 401
        with c.session_transaction() as s:
            s.clear()
        r1 = c.post(f"/online/{rid}/voice", json={"type": "offer", "data": {}})
        assert r1.status_code == 401
        # invalid type
        login(c, ua, na)
        r2 = c.post(f"/online/{rid}/voice", json={"type": "telepathy", "data": {}})
        assert r2.status_code == 400
        # unknown room
        r3 = c.post("/online/nope-xyz/voice", json={"type": "offer", "data": {}})
        assert r3.status_code == 404
        # mute signal shape
        m = c.post(f"/online/{rid}/voice", json={"type": "mute", "data": {"muted": True}})
        assert m.status_code == 200


@pytest.mark.skipif(not HAS_SUPABASE, reason="Supabase not configured")
def test_voice_blocked_pair_cannot_signal():
    ua, na = new_user("va")
    ub, nb = new_user("vb")
    with app.test_client() as c:
        rid = _create_room(c, ua, na)
        _join_room(c, ub, nb, rid)
        # A blocks B
        login(c, ua, na)
        blk = c.post("/chat/block", json={"target": ub})
        assert blk.status_code == 200
        # B's signals are rejected at the source (A blocked B)
        login(c, ub, nb)
        r1 = c.post(f"/online/{rid}/voice", json={"type": "offer", "data": {}})
        assert r1.status_code == 403
        # A can still signal B (blocking stops *receiving* from them), but B
        # will never see A's signals: blocked users are filtered on read too
        login(c, ua, na)
        r2 = c.post(f"/online/{rid}/voice", json={"type": "offer", "data": {"sdp": "x"}})
        assert r2.status_code == 200
        login(c, ub, nb)
        st = c.get(f"/online/{rid}/state?since_kick=-1&voice_after=-1")
        assert st.get_json()["voice_signals"] == []
        # cleanup block
        login(c, ua, na)
        c.post("/chat/unblock", json={"target": ub})
