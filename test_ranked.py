"""Tests for ranked matchmaking + ELO ratings for human players.

Covers: ELO math, placement K-factor tiers, atomic/idempotent result
recording, the Redis queue (join/cancel/status), the matching algorithm with
its widening window, end-to-end ranked match -> rating update, that casual
online play is untouched, and that no client-reachable route can write ratings.
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
from db import ranked
from db.ranked import (
    START_RATING, PLACEMENT_GAMES, K_PROVISIONAL, K_PLACED,
    expected_score, k_factor, outcome_with_placement,
    record_result, get_rating, list_leaderboard,
)


@pytest.fixture(autouse=True)
def _fresh_store():
    ranked._MEM.clear()
    ranked._MEM_MATCHES.clear()
    ranked._MEM_HISTORY.clear()
    from db import seasons
    seasons.reset_mem()
    yield
    ranked._MEM.clear()
    ranked._MEM_MATCHES.clear()
    ranked._MEM_HISTORY.clear()
    seasons.reset_mem()


def new_user(tag):
    return f"{tag}-{uuid.uuid4().hex[:8]}", f"{tag} name"


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
        if key.startswith("ranked:") or key.startswith("room:"):
            r.delete(key)


#  ELO math 

def test_expected_score_symmetric():
    assert expected_score(START_RATING, START_RATING) == pytest.approx(0.5)
    assert expected_score(1600, 1400) > 0.75
    assert expected_score(1400, 1600) < 0.25
    assert expected_score(1600, 1400) + expected_score(1400, 1600) == pytest.approx(1.0)


def test_k_factor_tiers():
    assert k_factor(0) == K_PROVISIONAL
    assert k_factor(PLACEMENT_GAMES - 1) == K_PROVISIONAL
    assert k_factor(PLACEMENT_GAMES) == K_PLACED
    assert k_factor(50) == K_PLACED


def test_outcome_with_placement_deltas():
    # Equal ratings, both provisional -> +20 / -20
    out = outcome_with_placement(1200, 0, 1200, 0, "A")
    assert out["player_a"]["delta"] == 20
    assert out["player_b"]["delta"] == -20
    assert out["player_b"]["k"] == K_PROVISIONAL

    # Slight favourite (1400) beats big underdog (1200) -> small gain
    out = outcome_with_placement(1400, 5, 1200, 5, "A")
    assert out["player_a"]["delta"] == 10   # round(40*(1-0.7597))
    assert out["player_b"]["delta"] == -10
    assert out["player_a"]["k"] == K_PROVISIONAL

    # Placed player at K=20, provisional at K=40
    out = outcome_with_placement(1200, PLACEMENT_GAMES, 1200, 0, "A")
    assert out["player_a"]["k"] == K_PLACED
    assert out["player_b"]["k"] == K_PROVISIONAL
    assert out["player_a"]["delta"] == 10      # 20 * 0.5
    assert out["player_b"]["delta"] == -20     # 40 * 0.5


def test_record_result_applies_atomically_in_memory():
    res = record_result("room1", "uA", "uB", "A", 3, 1)
    assert res["player_a"]["rating_after"] == res["player_a"]["rating_before"] + res["player_a"]["delta"]
    assert res["player_b"]["rating_after"] == res["player_b"]["rating_before"] + res["player_b"]["delta"]

    ra = get_rating("uA")
    rb = get_rating("uB")
    assert ra["rating"] == res["player_a"]["rating_after"]
    assert ra["games_played"] == 1 and ra["wins"] == 1 and ra["losses"] == 0
    assert rb["games_played"] == 1 and rb["wins"] == 0 and rb["losses"] == 1
    assert ra["peak_rating"] == ra["rating"]
    # History + match rows recorded
    assert len(ranked._MEM_HISTORY) == 2
    assert len(ranked._MEM_MATCHES) == 1


def test_record_result_idempotent_by_room_id():
    res1 = record_result("roomX", "uA", "uB", "A", 2, 0)
    before_a = get_rating("uA")["rating"]
    res2 = record_result("roomX", "uA", "uB", "A", 2, 0)
    assert res2 == res1
    assert get_rating("uA")["rating"] == before_a
    assert get_rating("uA")["games_played"] == 1
    assert len(ranked._MEM_MATCHES) == 1


def test_record_result_invalid_winner():
    with pytest.raises(ValueError):
        record_result("room-x", "uA", "uB", "C", 1, 0)


def test_peak_rating_grows_only_upward():
    record_result("m1", "uA", "uB", "A", 1, 0)   # A +20
    record_result("m2", "uB", "uA", "A", 1, 0)   # same players swapped? uA loses
    ra = get_rating("uA")
    assert ra["games_played"] == 2
    assert ra["peak_rating"] >= ra["rating"]
    assert ra["peak_rating"] == max(1220, ra["rating"])


#  Ranked leaderboard (placement gating) 

def test_leaderboard_excludes_provisional_players():
    ranked._MEM["placedA"] = {"user_id": "placedA", "rating": 1400,
                              "games_played": PLACEMENT_GAMES, "wins": 6, "losses": 4,
                              "peak_rating": 1420}
    ranked._MEM["placedB"] = {"user_id": "placedB", "rating": 1320,
                              "games_played": 20, "wins": 10, "losses": 10,
                              "peak_rating": 1350}
    ranked._MEM["newbie"] = {"user_id": "newbie", "rating": 1500,
                             "games_played": 3, "wins": 3, "losses": 0,
                             "peak_rating": 1500}
    entries, total = list_leaderboard()
    assert total == 2
    assert [e["rating"] for e in entries] == [1400, 1320]
    assert entries[0]["rank"] == 1
    assert entries[0]["win_rate"] == pytest.approx(60.0)
    assert all(e["user_id"] != "newbie" for e in entries)


def test_leaderboard_route_smoke():
    _cleanup()
    try:
        ua, na = new_user("rl")
        ranked._MEM[ua] = {"user_id": ua, "rating": 1350, "games_played": PLACEMENT_GAMES,
                           "wins": 7, "losses": 3, "peak_rating": 1350}
        from db import seasons
        seasons.reset_mem()
        season = seasons.initialize()
        seasons._MEM_SEASON_RATINGS[(ua, season["id"])] = {
            "user_id": ua, "season_id": season["id"], "rating_start": 1200,
            "rating": 1350, "games_played": PLACEMENT_GAMES,
            "wins": 7, "losses": 3, "peak_rating": 1350}
        with app.test_client() as c:
            login(c, ua, na)
            resp = c.get("/api/leaderboard/ranked?limit=5&offset=0")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["total"] == 1
            assert data["entries"][0]["rating"] == 1350
            # Every entry is placed
            assert data["entries"][0]["games_played"] >= PLACEMENT_GAMES
            # Season-scoped by default now; all-time still available
            assert data["viewing"] == season["number"]
            all_resp = c.get("/api/leaderboard/ranked?season=all")
            assert all_resp.get_json()["total"] == 1
    finally:
        _cleanup()


#  Queue: join / cancel / status 

def test_ranked_join_rejects_guests():
    _cleanup()
    try:
        with app.test_client() as c:
            login(c, "guest:abc123", "Guest")
            assert c.post("/ranked/join").status_code == 403
            assert c.get("/ranked/status").status_code == 403
    finally:
        _cleanup()


def test_ranked_join_then_cancel():
    _cleanup()
    try:
        ua, na = new_user("q")
        with app.test_client() as c:
            login(c, ua, na)
            data = c.post("/ranked/join").get_json()
            assert data["status"] == "waiting"
            assert ua in {i for i in r.smembers("ranked:queue")}
            wait = c.get("/ranked/status").get_json()
            assert wait["status"] == "waiting"
            assert wait["rating"] == START_RATING
            c.post("/ranked/cancel")
            idle = c.get("/ranked/status").get_json()
            assert idle["status"] == "idle"
            assert ua not in {i for i in r.smembers("ranked:queue")}
    finally:
        _cleanup()


def test_double_join_keeps_original_wait():
    _cleanup()
    try:
        ua, na = new_user("q")
        with app.test_client() as c:
            login(c, ua, na)
            c.post("/ranked/join")
            ts1 = appmod._ranked_join_ts(ua)
            join2 = c.post("/ranked/join").get_json()
            assert join2["status"] == "waiting"
            assert appmod._ranked_join_ts(ua) == ts1  # waiting clock not reset
    finally:
        _cleanup()


#  Matching algorithm 

def test_matches_close_ratings():
    _cleanup()
    try:
        ua, na = new_user("m")
        ub, nb = new_user("m")
        with app.test_client() as c:
            login(c, ua, na)
            data_a = c.post("/ranked/join").get_json()
            assert data_a["status"] == "waiting"
            login(c, ub, nb)
            data_b = c.post("/ranked/join").get_json()
            assert data_b["status"] == "matched"
            rid = data_b["room_id"]
            assert not r.smembers("ranked:queue")
            room = appmod._get_room(rid)
            assert room["ranked"] is True
            assert set((room["player_a"], room["player_b"])) == {ua, ub}
            # The other player sees the match from their own status poll
            login(c, ua, na)
            matched = c.get("/ranked/status").get_json()
            assert matched["status"] == "matched"
            assert matched["room_id"] == rid
    finally:
        _cleanup()


def test_no_match_when_gap_too_wide_for_wait():
    _cleanup()
    try:
        ua, na = new_user("w")
        ub, nb = new_user("w")
        ranked._MEM[ub] = {"user_id": ub, "rating": START_RATING + 300,
                           "games_played": 1, "wins": 1, "losses": 0, "peak_rating": START_RATING + 300}
        with app.test_client() as c:
            login(c, ua, na)
            c.post("/ranked/join")
            login(c, ub, nb)
            data = c.post("/ranked/join").get_json()
            assert data["status"] == "waiting"  # 300 gap > 50 window
            assert not r.smembers("ranked:queue").isdisjoint({ua, ub})
    finally:
        _cleanup()


def test_widening_threshold_after_long_wait():
    _cleanup()
    try:
        ua, na = new_user("w")
        ub, nb = new_user("w")
        ranked._MEM[ub] = {"user_id": ub, "rating": START_RATING + 300,
                           "games_played": 1, "wins": 1, "losses": 0, "peak_rating": START_RATING + 300}
        with app.test_client() as c:
            login(c, ua, na)
            c.post("/ranked/join")
            login(c, ub, nb)
            c.post("/ranked/join")
            # Simulate both waiting a while — window widens to ±600
            now = time.time() - 75
            r.setex(f"ranked:join:{ua}", 3600, str(now))
            r.setex(f"ranked:join:{ub}", 3600, str(now))
            login(c, ua, na)
            data = c.get("/ranked/status").get_json()
            assert data["status"] == "matched"
            room = appmod._get_room(data["room_id"])
            assert set((room["player_a"], room["player_b"])) == {ua, ub}
    finally:
        _cleanup()


def test_stale_unstarted_match_requeued():
    _cleanup()
    try:
        ua, na = new_user("s")
        ub, nb = new_user("s")
        rid = appmod._create_ranked_room(ua, ub)
        room = appmod._get_room(rid)
        room["started_at"] = time.time() - 300
        appmod._save_room(rid, room)
        with app.test_client() as c:
            login(c, ua, na)
            appmod._reclaim_stale_ranked()
            assert appmod._get_room(rid) is None
            assert {ua, ub} <= {m for m in r.smembers("ranked:queue")}
    finally:
        _cleanup()


#  End-to-end: ranked match plays out, ratings update 

def test_ranked_match_updates_ratings_end_to_end(monkeypatch):
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
        ua, na = new_user("e")
        ub, nb = new_user("e")
        with app.test_client() as c:
            login(c, ua, na)
            c.post("/ranked/join")
            login(c, ub, nb)
            matched = c.post("/ranked/join").get_json()
            rid = matched["room_id"]
            room = appmod._get_room(rid)
            assert room["ranked"]
            assert not room.get("ranked_processed")

            # Both join their pre-filled room
            login(c, room["player_a"], na if room["player_a"] == ua else nb)
            join_a = c.post(f"/online/{rid}/join").get_json()
            assert join_a["ranked"] is True

            # Player A makes the winning move
            login(c, room["player_a"], na if room["player_a"] == ua else nb)
            resp = c.post(f"/online/{rid}/move",
                          json={"player_idx": 0, "angle": 0.0, "power": 80.0})
            assert resp.status_code == 200

            room = appmod._get_room(rid)
            assert room["status"] == "done"
            assert room["ranked_processed"] is True
            assert room["ranked_result"]["winner"] == "A"
            # Winner gains, loser loses
            assert room["ranked_result"]["player_a"]["delta"] > 0
            assert room["ranked_result"]["player_b"]["delta"] < 0

            ra = get_rating(room["player_a"])
            rb = get_rating(room["player_b"])
            assert ra["games_played"] == 1 and ra["wins"] == 1
            assert rb["games_played"] == 1 and rb["losses"] == 1
            assert ra["rating"] == room["ranked_result"]["player_a"]["rating_after"]
            assert rb["rating"] == room["ranked_result"]["player_b"]["rating_after"]

            # Re-polling the room retries nothing and keeps the result
            state = c.get(f"/online/{rid}/state?since_kick=-1").get_json()
            assert state["ranked_result"]["winner"] == "A"
    finally:
        _cleanup()


def test_casual_match_does_not_touch_ratings(monkeypatch):
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
        ua, na = new_user("ca")
        ub, nb = new_user("ca")
        with app.test_client() as c:
            rid = _casual_room(c, ua, na, ub, nb)
            room = appmod._get_room(rid)
            assert not room.get("ranked")
            login(c, room["player_a"], na)
            c.post(f"/online/{rid}/move", json={"player_idx": 0, "angle": 0.0, "power": 80.0})
            assert appmod._get_room(rid)["status"] == "done"
            assert appmod._get_room(rid).get("ranked_result") is None
            assert ranked._MEM_MATCHES == []
            assert get_rating(ua)["games_played"] == 0
            assert get_rating(ub)["games_played"] == 0
    finally:
        _cleanup()


def test_no_client_reachable_route_writes_ratings():
    """Rating is write-only via the server hook; any other path must 404."""
    _cleanup()
    try:
        ua, na = new_user("f")
        with app.test_client() as c:
            login(c, ua, na)
            for path in ("/ranked/report", "/api/ranked/report",
                         "/online/xyz/result", "/ranked/complete"):
                resp = c.post(path, json={"winner": "A", "room_id": "xyz"})
                assert resp.status_code in (404, 405), path
            # And definitely nothing recorded
            assert ranked._MEM_MATCHES == []
    finally:
        _cleanup()


#  helpers 

def _casual_room(c, ua, na, ub, nb):
    login(c, ua, na)
    rid = c.post("/online/create", json={"player_count": 3}).get_json()["room_id"]
    login(c, ub, nb)
    c.post(f"/online/{rid}/join", json={})
    return rid