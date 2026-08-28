"""Tests for ranked seasons (soft reset, archived standings, rewards).

Covers: season initialization, the soft-reset formula, per-season match
accounting, snapshot/standings ordering, the guarded transition (awards +
reset + next season) with exactly-once semantics and no-op re-runs,
career (all-time) stats untouched through a transition, cosmetic unlocks
gated by season badges, the crash-tail resume, and the API surface
(season-scoped leaderboard, manual transition route, ranked payload).
"""
import os
import sys
import uuid

os.environ.setdefault("DEV_MODE", "1")
sys.path.insert(0, os.path.dirname(__file__))

import pytest

import app as appmod  # noqa: F401  (imports the Flask app under test)
from app import app
from db import ranked, achievements, seasons
from db.seasons import (
    initialize, apply_match, build_snapshot, transition,
    run_transition_if_due, season_standings, career_summary,
    soft_reset_rating,
)
from db.ranked import record_result
from db.achievements import award, get_earned
from db.customization import is_unlocked
from db.redis_client import r


@pytest.fixture(autouse=True)
def _fresh_store():
    ranked._MEM.clear()
    ranked._MEM_MATCHES.clear()
    ranked._MEM_HISTORY.clear()
    achievements._MEM.clear()
    seasons.reset_mem()
    yield
    ranked._MEM.clear()
    ranked._MEM_MATCHES.clear()
    ranked._MEM_HISTORY.clear()
    achievements._MEM.clear()
    seasons.reset_mem()
    try:
        r.delete("season:lock")
    except Exception:
        pass


def new_uid(tag):
    return f"{tag}-{uuid.uuid4().hex[:8]}"


def login(c, uid_, name_="Tester"):
    with c.session_transaction() as s:
        s["user_id"] = uid_
        s["username"] = name_


def _fresh_season():
    seasons.reset_mem()
    return initialize()


def _seed_season():
    """Season 1 with: p1 placed #1 (champion), p2 placed #2 (top 10),
    p3 not placed (participant), guest:g1 (never rewarded/reset)."""
    season = _fresh_season()
    players = [
        ("p1", 1560, 14, 11, 3),
        ("p2", 1430, 12, 6, 6),
        ("p3", 1350, 6, 3, 3),
        ("guest:g1", 1500, 20, 10, 10),
    ]
    for uid_, rating, games, wins, losses in players:
        ranked._MEM[uid_] = {"user_id": uid_, "rating": rating,
                             "games_played": games, "wins": wins,
                             "losses": losses, "peak_rating": rating}
        if not uid_.startswith("guest:"):
            seasons._MEM_SEASON_RATINGS[(uid_, season["id"])] = {
                "user_id": uid_, "season_id": season["id"], "rating_start": 1200,
                "rating": rating, "games_played": games, "wins": wins,
                "losses": losses, "peak_rating": rating}
    return season


#  Initialization & soft reset 

def test_season_initialization_creates_season_1():
    s1 = _fresh_season()
    assert s1["number"] == 1
    assert s1["status"] == "active"
    assert not s1.get("leaderboard_snapshot")
    assert initialize()["id"] == s1["id"]  # idempotent


def test_soft_reset_formula():
    assert soft_reset_rating(1200) == 1200
    assert soft_reset_rating(1400) == 1300
    assert soft_reset_rating(1600) == 1400
    assert soft_reset_rating(1800) == 1500
    assert soft_reset_rating(1000) == 1100


#  Per-season accounting 

def test_apply_match_creates_and_accumulates_season_row():
    season = _fresh_season()
    uid_ = new_uid("am")
    apply_match(season, uid_, 1200, 1230, won=True)
    row = seasons._MEM_SEASON_RATINGS[(uid_, season["id"])]
    assert row["rating_start"] == 1200
    assert row["rating"] == 1230
    assert row["games_played"] == 1
    assert row["wins"] == 1 and row["losses"] == 0
    assert row["peak_rating"] == 1230
    apply_match(season, uid_, 1230, 1210, won=False)
    assert row["games_played"] == 2
    assert row["wins"] == 1 and row["losses"] == 1
    assert row["peak_rating"] == 1230


def test_record_result_plus_apply_match_together():
    season = _fresh_season()
    ua, ub = new_uid("rra"), new_uid("rrb")
    res = record_result("room-x", ua, ub, "A", 3, 1)
    apply_match(season, ua, res["player_a"]["rating_before"],
                res["player_a"]["rating_after"], won=True)
    apply_match(season, ub, res["player_b"]["rating_before"],
                res["player_b"]["rating_after"], won=False)
    sa = seasons._MEM_SEASON_RATINGS[(ua, season["id"])]
    assert sa["rating_start"] == res["player_a"]["rating_before"]
    assert sa["rating"] == res["player_a"]["rating_after"]
    assert sa["games_played"] == 1 and sa["wins"] == 1
    sb = seasons._MEM_SEASON_RATINGS[(ub, season["id"])]
    assert sb["wins"] == 0 and sb["losses"] == 1


def test_build_snapshot_ordering_and_placed_flag():
    season = _fresh_season()
    rows = [
        (new_uid("c"), 1500, 20, 9),
        (new_uid("a"), 1400, 12, 8),   # same rating as b, more wins -> higher
        (new_uid("b"), 1400, 12, 5),
        (new_uid("d"), 1300, 9, 9),    # below placement (9 < 10)
    ]
    for uid_, rating, games, wins in rows:
        ranked._MEM[uid_] = {"user_id": uid_, "rating": rating,
                             "games_played": games, "wins": wins,
                             "losses": 0, "peak_rating": rating}
        seasons._MEM_SEASON_RATINGS[(uid_, season["id"])] = {
            "user_id": uid_, "season_id": season["id"], "rating_start": 1200,
            "rating": rating, "games_played": games, "wins": wins,
            "losses": 0, "peak_rating": rating}
    snap = build_snapshot(season["id"])
    assert [e["user_id"] for e in snap] == [rows[0][0], rows[1][0], rows[2][0], rows[3][0]]
    assert snap[0]["rank"] == 1 and snap[3]["rank"] == 4
    assert snap[0]["placed"] is True and snap[3]["placed"] is False
    assert snap[1]["placed"] is True and snap[2]["placed"] is True


#  The transition 

def test_transition_archives_rewards_and_soft_resets():
    season = _seed_season()
    sid = season["id"]
    result = transition(sid)
    assert result is not None
    assert result["ended_season"] == 1
    assert result["next_season"] == 2
    assert result["snapshot_players"] == 3          # guest has no season row
    assert result["awards"]["sn_participant"] == 3  # p1, p2, p3
    assert result["awards"]["sn_top_10"] == 2       # p1, p2
    assert result["awards"]["sn_champion"] == 1     # p1
    assert result["reset_players"] == 3

    # Badges
    assert {"sn_champion", "sn_top_10", "sn_participant"} <= set(get_earned("p1"))
    assert set(get_earned("p2")) == {"sn_top_10", "sn_participant"}
    assert set(get_earned("p3")) == {"sn_participant"}
    assert not get_earned("guest:g1")

    # Cosmetic gates derive from the new badges
    assert is_unlocked("p1", "ball_design", "titanium") is True
    assert is_unlocked("p2", "ball_design", "titanium") is False
    assert is_unlocked("p2", "goal_effect", "aurora") is True
    assert is_unlocked("p3", "goal_effect", "aurora") is False

    # Soft reset math per account; career rows otherwise untouched
    assert ranked._MEM["p1"]["rating"] == 1380
    assert ranked._MEM["p1"]["games_played"] == 14
    assert ranked._MEM["p1"]["peak_rating"] == 1560
    assert ranked._MEM["p2"]["rating"] == 1315
    assert ranked._MEM["p3"]["rating"] == 1275

    # Boundary entries land in the rating log (match_id null)
    resets = [h for h in ranked._MEM_HISTORY if h.get("match_id") is None]
    assert len(resets) == 3

    # Season 1 archived + snapshot; Season 2 active
    s1 = seasons.get_season(sid)
    assert s1["status"] == "completed"
    assert s1["leaderboard_snapshot"]
    s2 = seasons.get_season_by_number(2)
    assert s2["status"] == "active"


def test_transition_second_run_is_safe_noop():
    season = _seed_season()
    sid = season["id"]
    assert transition(sid) is not None
    before = len(get_earned("p1"))
    assert transition(sid) is None               # completed -> no-op
    assert run_transition_if_due() is None       # new season not due
    assert len(get_earned("p1")) == before       # no double-award
    assert ranked._MEM["p1"]["rating"] == 1380   # no re-compress


def test_participant_threshold_not_met():
    season = _fresh_season()
    uid_ = new_uid("low")
    ranked._MEM[uid_] = {"user_id": uid_, "rating": 1280, "games_played": 4,
                         "wins": 1, "losses": 3, "peak_rating": 1280}
    seasons._MEM_SEASON_RATINGS[(uid_, season["id"])] = {
        "user_id": uid_, "season_id": season["id"], "rating_start": 1200,
        "rating": 1280, "games_played": 4, "wins": 1, "losses": 3,
        "peak_rating": 1280}
    transition(season["id"])
    assert not get_earned(uid_)
    assert ranked._MEM[uid_]["rating"] == 1240   # still soft-reset


def test_achievements_and_all_time_untouched_by_transition():
    season = _fresh_season()
    uid_ = new_uid("keep")
    award(uid_, "exp_first_goal")                # pre-existing badge survives
    ranked._MEM[uid_] = {"user_id": uid_, "rating": 1500, "games_played": 20,
                         "wins": 12, "losses": 8, "peak_rating": 1600}
    seasons._MEM_SEASON_RATINGS[(uid_, season["id"])] = {
        "user_id": uid_, "season_id": season["id"], "rating_start": 1450,
        "rating": 1500, "games_played": 11, "wins": 6, "losses": 5,
        "peak_rating": 1550}
    transition(season["id"])
    assert "exp_first_goal" in get_earned(uid_)
    row = ranked._MEM[uid_]
    assert row["games_played"] == 20
    assert row["wins"] == 12 and row["losses"] == 8
    assert row["peak_rating"] == 1600            # all-time peak untouched


#  Standings, history, career 

def test_standings_read_archived_snapshot_after_transition():
    season = _seed_season()
    sid = season["id"]
    entries, total = season_standings(sid, limit=2, offset=0)
    assert len(entries) == 2 and total == 2          # placed players only
    top = entries[0]
    assert top["user_id"] == "p1" and top["placed"] is True
    transition(sid)
    entries2, total2 = season_standings(sid, limit=2, offset=0)
    assert total2 == 2
    assert entries2[0]["user_id"] == top["user_id"]
    assert entries2[0]["rating"] == top["rating"] == 1560


def test_career_summary_tracks_seasons_and_best_rank():
    season1 = _seed_season()
    transition(season1["id"])
    season2 = initialize()                       # the new active season
    apply_match(season2, "p1", 1380, 1390, won=True)
    summ = career_summary("p1")
    assert summ["seasons_played"] == 2
    assert summ["best_rank"] == 1
    assert summ["best_rating"] == 1560


def test_crash_tail_resume_creates_next_season():
    season = _seed_season()
    sid = season["id"]
    transition(sid)
    s2 = seasons.get_season_by_number(2)
    del seasons._MEM_SEASONS[s2["id"]]           # wipe the successor = crash tail
    seasons.initialize()                         # resume should re-create it
    assert seasons.get_season_by_number(2) is not None
    assert len(get_earned("p1")) == 3            # awards still idempotent


#  API surface 

def test_api_leaderboard_season_scoping_and_past_browse():
    season = _seed_season()
    with app.test_client() as c:
        login(c, "p1", "P One")
        data = c.get("/api/leaderboard/ranked").get_json()
        assert data["viewing"] == season["number"]
        assert data["entries"][0]["user_id"] == "p1"
        assert data["season"]["number"] == season["number"]
        assert any(s["number"] == season["number"] for s in data["seasons"])

        c.post("/api/seasons/_transition?force=1")
        past = c.get("/api/leaderboard/ranked?season=1").get_json()
        assert past["viewing"] == 1
        assert past["total"] == 2               # placed players only
        assert c.get("/api/leaderboard/ranked?season=999").status_code == 404

        all_t = c.get("/api/leaderboard/ranked?season=all").get_json()
        assert all_t["viewing"] == "all"
        assert all_t["total"] == 3              # career rows persist past reset


def test_manual_transition_route_runs_and_noops():
    _seed_season()
    with app.test_client() as c:
        login(c, "p1", "P One")
        resp = c.post("/api/seasons/_transition?force=1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True and not data.get("noop")
        assert data["transition"]["next_season"] == 2
        # The job-style call (no force) against the in-progress season no-ops
        again = c.post("/api/seasons/_transition").get_json()
        assert again.get("noop") is True


def test_ranked_payload_carries_season_info():
    season = _fresh_season()
    with app.test_client() as c:
        login(c, new_uid("sp"), "SeasonP")
        data = c.get("/ranked/status").get_json()
        assert data["season"]["number"] == season["number"]
        assert data["season"]["ends_in_s"] >= 0
