"""Tests for the post-match shareable summary card.

Covers: the summaries store (in-memory roundtrip + redis fallback), the
season-attribution helper, the aggregation built at match end (ranked
snapshots deltas; casual stores none; builds are snapshotted), the
public (no-login) summary route rendering every required element plus
the honest no-highlight state, casual pages omitting rating data, the
missing-match redirect, and the confirmed goal>near-miss>fast-play best-
highlight priority.
"""
import os
import sys
import time
import uuid

os.environ.setdefault("DEV_MODE", "1")
sys.path.insert(0, os.path.dirname(__file__))

import pytest

import app as appmod
from app import app
from db.redis_client import r
from db import ranked, seasons, customization, summaries
from db.summaries import save_summary, get_summary
from db.highlights import best_highlight


@pytest.fixture(autouse=True)
def _fresh_store():
    ranked._MEM.clear()
    ranked._MEM_MATCHES.clear()
    ranked._MEM_HISTORY.clear()
    seasons.reset_mem()
    customization._MEM.clear()
    summaries.reset_mem()
    yield
    ranked._MEM.clear()
    ranked._MEM_MATCHES.clear()
    ranked._MEM_HISTORY.clear()
    seasons.reset_mem()
    customization._MEM.clear()
    summaries.reset_mem()


def new_user(tag):
    return f"{tag}-{uuid.uuid4().hex[:8]}", f"{tag} {uuid.uuid4().hex[:4]}"


def login(client_, uid_, name_):
    with client_.session_transaction() as s:
        s["user_id"] = uid_
        s["username"] = name_


def _cleanup():
    for k in ("ranked:queue", "ranked:rooms", "online:active", "season:lock"):
        try:
            r.delete(k)
        except Exception:
            pass
    for key in list(r._store.keys()):
        if key.startswith("ranked:") or key.startswith("room:") or key.startswith("summary:"):
            r.delete(key)


def _set_build(uid_, side, stats):
    customization._MEM[uid_] = {"player_stats": {side: stats}}


def _make_room(rid, ua, na, ub, nb, ranked_flag=True, winner="A"):
    return {
        "player_a": ua, "player_b": ub,
        "name_a": na, "name_b": nb,
        "started_at": time.time(),
        "ranked": ranked_flag,
        "ranked_result": None,
        "game": {"score_a": 2 if winner == "A" else 0,
                 "score_b": 0 if winner == "A" else 2,
                 "winner": winner, "kick_count": 4},
    }


#  Summaries store (in-memory + redis fallback) 

def test_summary_save_get_roundtrip():
    save_summary("roomAbc", {"room_id": "roomAbc", "score_a": 2, "score_b": 1})
    got = get_summary("roomAbc")
    assert got["score_a"] == 2 and got["score_b"] == 1
    assert get_summary("missing") is None


def test_summary_save_get_mem_path_matches_redis_fallback():
    save_summary("roomRedis", {"winner": "A"})
    assert summaries._MEM["roomRedis"]["winner"] == "A"
    raw = r.get("summary:roomRedis")
    assert raw and "A" in raw


def test_summary_upsert_idempotent():
    save_summary("rid", {"score_a": 1})
    save_summary("rid", {"score_a": 3, "winner": "A"})
    got = get_summary("rid")
    assert got["score_a"] == 3 and got["winner"] == "A"


def test_summary_redis_fallback_read_when_mem_cleared():
    save_summary("rid2", {"score_a": 7})
    summaries._MEM.pop("rid2", None)
    assert get_summary("rid2")["score_a"] == 7


#  season_for_time 

def test_season_for_time_inside_window():
    from db.seasons import initialize, season_for_time
    seasons.reset_mem()
    initialize()
    assert season_for_time(time.time())["number"] == 1
    assert season_for_time(time.time() - 10 ** 9) is None


#  Aggregation at match end 

def test_ranked_summary_snapshots_deltas_builds_season():
    from db.seasons import initialize
    from db.ranked import record_result
    seasons.reset_mem()
    initialize()
    ua, na = new_user("sum")
    ub, nb = new_user("sum")
    rid = "roomRanked1"
    room = _make_room(rid, ua, na, ub, nb, ranked_flag=True)
    room["ranked_result"] = record_result(rid, ua, ub, "A", 2, 0)
    _set_build(ua, "a", [{"size": 70, "power": 55, "weight": 45, "agility": 30},
                         {"size": 50, "power": 50, "weight": 50, "agility": 50},
                         {"size": 50, "power": 50, "weight": 50, "agility": 50}])
    _set_build(ub, "b", [{"size": 25, "power": 60, "weight": 60, "agility": 55},
                         {"size": 50, "power": 50, "weight": 50, "agility": 50},
                         {"size": 50, "power": 50, "weight": 50, "agility": 50}])
    appmod._save_match_summary(rid, room)

    s = get_summary(rid)
    assert s["ranked"] is True
    assert s["winner"] == "A"
    assert s["ranked_result"]["player_a"]["delta"] > 0
    assert s["ranked_result"]["player_b"]["delta"] < 0
    assert s["season"] == 1
    assert s["build_a"][0]["size"] == 70
    assert s["build_b"][0]["size"] == 25
    from db.customization import _DEFAULT_PLAYER_STATS
    ux, _ = new_user("sum")
    assert appmod._saved_lineup(ux, "b") == [dict(x) for x in _DEFAULT_PLAYER_STATS]


def test_casual_summary_stores_no_rating():
    ua, na = new_user("sum")
    ub, nb = new_user("sum")
    rid = "roomCasual1"
    appmod._save_match_summary(rid, _make_room(rid, ua, na, ub, nb, ranked_flag=False))
    s = get_summary(rid)
    assert s["ranked"] is False
    assert s["ranked_result"] is None
    assert s["winner"] == "A"


#  best-highlight priority (goal > near-miss > fast-play, confirmed) 

def test_best_highlight_priority():
    def mk(t, k):
        return {"type": t, "kick": k, "start": 0, "end": 1, "label": t}
    assert best_highlight([]) is None
    assert best_highlight([mk("fast", 1), mk("goal", 9), mk("near", 4)])["type"] == "goal"
    assert best_highlight([mk("fast", 1), mk("near", 4)])["type"] == "near"
    assert best_highlight([mk("fast", 1), mk("fast", 6)])["kick"] == 1
    assert best_highlight([mk("fast", 1)])["type"] == "fast"


#  Public route + page rendering 

def test_summary_page_public_and_renders_everything():
    _cleanup()
    try:
        from db.seasons import initialize
        from db.ranked import record_result
        seasons.reset_mem()
        initialize()
        ua, na = new_user("pg")
        ub, nb = new_user("pg")
        rid = "roomPage1"
        room = _make_room(rid, ua, na, ub, nb, winner="A")
        room["ranked_result"] = record_result(rid, ua, ub, "A", 2, 0)
        _set_build(ua, "a", [{"size": 70, "power": 55, "weight": 45, "agility": 30},
                             {"size": 50, "power": 50, "weight": 50, "agility": 50},
                             {"size": 50, "power": 50, "weight": 50, "agility": 50}])
        _set_build(ub, "b", [{"size": 25, "power": 60, "weight": 60, "agility": 55},
                             {"size": 50, "power": 50, "weight": 50, "agility": 50},
                             {"size": 50, "power": 50, "weight": 50, "agility": 50}])
        appmod._save_match_summary(rid, room)

        with app.test_client() as c:
            resp = c.get(f"/match/{rid}/summary")
            assert resp.status_code == 200
            html = resp.get_data(as_text=True)
            assert na in html and nb in html
            assert "WIN" in html and "2 : 0" in html
            assert "+2" in html and "-2" in html
            assert "200/200" in html
            assert "Weight" in html and "Agility" in html
            assert "Season 1" in html
            assert "No highlights were detected for this match" in html
    finally:
        _cleanup()


def test_summary_route_public_without_login():
    _cleanup()
    try:
        save_summary("roomOpen", {
            "room_id": "roomOpen", "name_a": "Ally", "name_b": "Bob",
            "player_a": None, "player_b": None,
            "score_a": 1, "score_b": 1, "winner": None,
            "ranked": False, "ranked_result": None,
            "build_a": [{"size": 50, "power": 50, "weight": 50, "agility": 50}],
            "build_b": [{"size": 50, "power": 50, "weight": 50, "agility": 50}],
            "season": None})
        with app.test_client() as c:
            resp = c.get("/match/roomOpen/summary")
            assert resp.status_code == 200
            assert "Ally" in resp.get_data(as_text=True)
    finally:
        _cleanup()


def test_casual_summary_page_omits_rating_section():
    _cleanup()
    try:
        ua, na = new_user("cs")
        ub, nb = new_user("cs")
        rid = "roomCasualPage"
        appmod._save_match_summary(rid, _make_room(rid, ua, na, ub, nb, ranked_flag=False))
        with app.test_client() as c:
            html = c.get(f"/match/{rid}/summary").get_data(as_text=True)
            assert "Rating change" not in html
            assert "+20" not in html
            assert "No highlights were detected for this match" in html
    finally:
        _cleanup()


def test_missing_summary_redirects_home():
    _cleanup()
    try:
        with app.test_client() as c:
            resp = c.get("/match/doesnotexist/summary")
            assert resp.status_code == 302
    finally:
        _cleanup()