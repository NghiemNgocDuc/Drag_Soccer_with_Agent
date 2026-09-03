"""Tests for the achievements / badges system.

Covers: the catalog (31 badges, valid fields), the double-award guard,
earned/list/count reads, the Redis toast queue roundtrip, the ranked
win-streak helper, app helper functions (hat-trick / extreme build), guest
gating, and the /achievements page smoke test.
"""
import os
import sys
import uuid

os.environ.setdefault("DEV_MODE", "1")
sys.path.insert(0, os.path.dirname(__file__))

import pytest

import app as appmod
from app import app
from db import achievements
from db.achievements import (
    ACHIEVEMENTS, award, definitions, list_for_user, get_earned,
    count_earned, drain_toasts,
)
from db import ranked
from db.ranked import record_result


@pytest.fixture(autouse=True)
def _fresh_store():
    achievements._MEM.clear()
    ranked._MEM.clear()
    ranked._MEM_MATCHES.clear()
    ranked._MEM_HISTORY.clear()
    from db.redis_client import r as redis
    if hasattr(redis, "_store"):
        for key in list(redis._store.keys()):
            if key.startswith("ach:"):
                redis.delete(key)
    yield
    achievements._MEM.clear()
    ranked._MEM.clear()
    ranked._MEM_MATCHES.clear()
    ranked._MEM_HISTORY.clear()


def new_uid(tag):
    return f"{tag}-{uuid.uuid4().hex[:8]}"


# ── Catalog ────────────────────────────────────────────────────────────────

def test_catalog_has_31_badges():
    # catalog grows as we add play-more progress badges; emoji kept only for chat per cleanup (so allow empty)
    assert len(ACHIEVEMENTS) >= 33
    for key, d in ACHIEVEMENTS.items():
        assert d["category"] in achievements.CATEGORY_LABELS
        assert d["name"] and d["description"] and "emoji" in d
        assert "order" in d


def test_definitions_ordered():
    defs = definitions()
    assert len(defs) == len(ACHIEVEMENTS)
    assert defs[0]["key"] == "ai_first_model"  # alphabetical first category
    keys = [d["key"] for d in defs]
    assert all(k in ACHIEVEMENTS for k in keys)
    cats = [d["category"] for d in defs]
    assert cats == sorted(cats)  # grouped by category


# ── Awarding / double-award guard ──────────────────────────────────────────

def test_award_grants_once_then_returns_none():
    uid = new_uid("aw")
    first = award(uid, "exp_first_goal")
    assert first is not None
    assert first["key"] == "exp_first_goal"
    assert first["name"] == "Opening Scorer"
    assert award(uid, "exp_first_goal") is None  # double-award guarded
    assert award(uid, "exp_first_goal") is None  # still guarded


def test_award_unknown_key_and_empty_user():
    assert award("", "exp_first_goal") is None
    assert award(new_uid("x"), "does_not_exist") is None


def test_earned_list_and_count():
    uid = new_uid("rd")
    assert count_earned(uid) == 0
    award(uid, "rk_first_win")
    award(uid, "sk_rocket")
    assert count_earned(uid) == 2
    earned = get_earned(uid)
    assert set(earned) == {"rk_first_win", "sk_rocket"}
    lst = list_for_user(uid)
    by_key = {a["key"]: a for a in lst}
    assert len(lst) == len(ACHIEVEMENTS)
    assert by_key["rk_first_win"]["earned"] is True
    assert by_key["rk_first_win"]["awarded_at"] is not None
    assert by_key["exp_photo"]["earned"] is False
    assert by_key["exp_photo"]["awarded_at"] is None
    # Other users never see this user's badges
    assert count_earned(new_uid("other")) == 0


# ── Toast queue ────────────────────────────────────────────────────────────

def test_toast_pushed_and_drained():
    uid = new_uid("tq")
    badge = award(uid, "tour_champion")
    assert badge is not None
    drained = drain_toasts(uid)
    assert [d["key"] for d in drained] == ["tour_champion"]
    assert drained[0]["name"] == "Champion"
    assert drained[0]["awarded_at"]
    assert drain_toasts(uid) == []  # drained exactly once
    # No toast when the badge was already earned
    assert award(uid, "tour_champion") is None
    assert drain_toasts(uid) == []


def test_toasts_come_out_oldest_last():
    uid = new_uid("tq2")
    award(uid, "exp_photo")
    award(uid, "exp_sense4")
    drained = drain_toasts(uid)
    assert [d["key"] for d in drained] == ["exp_photo", "exp_sense4"]


# ── Ranked win streak helper ───────────────────────────────────────────────

def test_get_win_streak_counts_consecutive_wins():
    a = new_uid("sA")
    b = new_uid("sB")
    # Newest last: A win, then B win, then B win
    record_result(f"room-streak-0-{a}", a, b, "A", 3, 0)
    record_result(f"room-streak-1-{a}", a, b, "B", 0, 3)
    record_result(f"room-streak-2-{a}", a, b, "B", 0, 3)
    assert ranked.get_win_streak(a) == 0  # lost the most recent match
    assert ranked.get_win_streak(b) == 2  # won the last two in a row
    record_result(f"room-streak-3-{a}", a, b, "A", 3, 0)
    assert ranked.get_win_streak(a) == 1  # just the single latest win


def test_get_win_streak_all_wins():
    a = new_uid("wA")
    b = new_uid("wB")
    for i in range(5):
        record_result(f"room-win-{i}-{a}", a, b, "A", 3, 0)
    assert ranked.get_win_streak(a) == 5
    assert ranked.get_win_streak(b) == 0


# ── app.py detection helpers ───────────────────────────────────────────────

def test_hat_trick_side_detects_3_same_player_goals():
    moves = [
        {"player": "A", "player_idx": 1, "scored": "A"},
        {"player": "A", "player_idx": 2, "scored": "A"},
        {"player": "A", "player_idx": 1, "scored": "A"},
        {"player": "A", "player_idx": 1, "scored": "A"},  # third by player 1
    ]
    assert appmod._hat_trick_side(moves) == "A"


def test_hat_trick_side_ignores_own_goals_and_misses():
    moves = [
        {"player": "A", "player_idx": 1, "scored": "B"},  # own goal — no
        {"player": "A", "player_idx": 1, "scored": None},
        {"player": "A", "player_idx": 1, "scored": "A"},
        {"player": "A", "player_idx": 1, "scored": "A"},
    ]
    assert appmod._hat_trick_side(moves) is None


def test_extreme_build_requires_every_player():
    assert appmod._extreme_build([
        {"size": 70, "power": 30, "weight": 30, "agility": 30},
        {"size": 30, "power": 30, "weight": 65, "agility": 30},
    ]) is True
    assert appmod._extreme_build([
        {"size": 70, "power": 30, "weight": 30, "agility": 30},
        {"size": 50, "power": 50, "weight": 50, "agility": 50},  # balanced — fails
    ]) is False
    assert appmod._extreme_build([]) is False
    assert appmod._extreme_build(None) is False
    assert appmod._extreme_build([
        {"size": 70, "power": 45, "weight": 30, "agility": 30},  # 45 in between — fails
    ]) is False


def test_ach_grant_drops_guests():
    from db.achievements import _MEM
    appmod._ach_grant("guest:abc", "exp_photo")
    assert _MEM == {}
    appmod._ach_grant("dev:test", "exp_photo")
    assert "exp_photo" in _MEM["dev:test"]
    assert count_earned("dev:test") == 1


def test_ach_toasts_respects_session():
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["user_id"] = "dev:toast-session"
            s["username"] = "Toast"
        award("dev:toast-session", "sk_loft")
        data = c.get("/state").get_json()
        assert [d["key"] for d in data["achievements"]] == ["sk_loft"]
        # Already drained — the next read is empty
        data2 = c.get("/state").get_json()
        assert data2["achievements"] == []


# ── Page smoke ─────────────────────────────────────────────────────────────

def test_toasts_pump_route():
    uid = new_uid("pump")
    award(uid, "rk_first_win")
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["user_id"] = uid
        resp = c.get("/api/achievements/toasts")
        data = resp.get_json()
        assert resp.status_code == 200
        assert [d["key"] for d in data["achievements"]] == ["rk_first_win"]
        resp2 = c.get("/api/achievements/toasts")
        assert resp2.get_json()["achievements"] == []  # drained


def test_achievements_page_requires_login():
    with app.test_client() as c:
        assert c.get("/achievements").status_code == 302


def test_achievements_page_renders():
    uid = new_uid("pg")
    award(uid, "rk_first_win")
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["user_id"] = uid
            s["username"] = "AchTester"
        resp = c.get("/achievements")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Ranked Debut" in body          # earned badge
        assert "Opening Scorer" in body        # unearned badge (shown greyed)
        assert f"1/{len(ACHIEVEMENTS)}" in body  # progress header
        assert "Locked" in body                # unearned marker


def test_profile_page_includes_achievement_count():
    uid = new_uid("pr")
    award(uid, "exp_photo")
    award(uid, "exp_sense4")
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["user_id"] = uid
            s["username"] = "AchProf"
        resp = c.get("/profile")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Achievements" in body
        assert "/achievements" in body


# ── Tournament champion helper ──────────────────────────────────────────────

def _make_completed_tournament(winner_participant_id_a, winner_participant_id_b,
                               winner_side="a"):
    """Small 2-player tournament; returns (tid, participant dicts)."""
    from db.tournaments import (create_tournament, add_participant,
                                generate_bracket, save_match_result,
                                get_tournament)
    tid = create_tournament("dev:host", "V Tournament")["id"]
    pa = add_participant(tid, winner_participant_id_a, "Alpha")
    pb = add_participant(tid, winner_participant_id_b, "Beta")
    assert generate_bracket(tid)
    match = get_tournament(tid)["matches"][0]
    win_part = pa if winner_side == "A" else pb
    save_match_result(tid, match["id"], win_part["id"], [])
    assert get_tournament(tid)["status"] == "completed"
    return tid, pa, pb


def test_tournament_champion_awarded_to_human_final_winner():
    uid = new_uid("champ")
    tid, _pa, _pb = _make_completed_tournament(f"friend:{uid}", "greedy", "A")
    appmod._tournament_champion_award(tid)
    assert count_earned(uid) == 1
    assert "tour_champion" in get_earned(uid)


def test_tournament_champion_requires_human_winner():
    uid = new_uid("champ2")
    tid, _pa, _pb = _make_completed_tournament("devbot", f"friend:{uid}", "A")
    appmod._tournament_champion_award(tid)
    assert "tour_champion" not in get_earned(uid)


def test_tournament_champion_unfinished_no_award():
    uid = new_uid("champ3")
    from db.tournaments import create_tournament, add_participant, get_tournament
    tid = create_tournament("dev:host", "Pending")["id"]
    add_participant(tid, f"friend:{uid}", "Alpha")
    appmod._tournament_champion_award(tid)
    assert get_tournament(tid)["status"] == "pending"
    assert "tour_champion" not in get_earned(uid)


# ── Online (friends / clean sheet / rout) helper ───────────────────────────

def test_online_achievements_friend_match_and_rout():
    pa, pb = new_uid("frA"), new_uid("frB")
    appmod._save_friends(pa, [{"uid": pb, "username": "B", "since": 0}])
    appmod._save_friends(pb, [{"uid": pa, "username": "A", "since": 0}])
    room = {"player_a": pa, "player_b": pb}
    game = {"winner": "A", "score_a": 6, "score_b": 0,
            "move_history": [{"player": "A", "player_idx": 0, "scored": "A"}] * 3,
            "player_stats": {}}
    appmod._check_online_achievements(room, game)
    assert "friend_match" in get_earned(pa)
    assert "friend_match" in get_earned(pb)
    assert "sk_clean_sheet" in get_earned(pa)
    assert "sk_big_win" in get_earned(pa)
    assert "sk_pan_trick" in get_earned(pa)


def test_online_achievements_skip_guests():
    pa = new_uid("oga")
    pb = "guest:xyz"
    room = {"player_a": pa, "player_b": pb}
    game = {"winner": "A", "score_a": 1, "score_b": 0, "move_history": []}
    appmod._check_online_achievements(room, game)
    assert count_earned(pa) == 0


# ── Scene tracking (Night Owl) ─────────────────────────────────────────────

def test_track_scene_usage_awards_at_4_distinct_scenes(monkeypatch):
    uid = new_uid("scene")
    scenes = {"night", "day", "cloudy", "sunset"}
    def fake_custom(user):
        return {"bg_scene": scenes.pop()}
    monkeypatch.setattr("db.customization.get_customization", fake_custom)
    for _ in range(4):
        appmod._track_scene_usage(uid)
    assert "exp_sense4" in get_earned(uid)


def test_track_scene_usage_repeat_scene_no_award(monkeypatch):
    uid = new_uid("scene2")
    monkeypatch.setattr("db.customization.get_customization",
                        lambda u: {"bg_scene": "night"})
    for _ in range(6):
        appmod._track_scene_usage(uid)
    assert "exp_sense4" not in get_earned(uid)
