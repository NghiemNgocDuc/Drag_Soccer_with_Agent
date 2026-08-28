"""Tests for achievement-gated cosmetics.

Covers: the unlock map sanity (keys exist, values are real, defaults never
gated, gameplay fields never gated), locked/unlocked derivation, server-side
enforcement at /customize/save (reject locked, allow unlocked, allow
non-gated + player_stats always), the /customize page lock rendering, and the
toast reward enrichment ("Unlocked: ...").
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
from db.achievements import ACHIEVEMENTS, award
from db import customization
from db.customization import (
    UNLOCK_REQUIREMENTS, COSMETIC_REWARDS, DEFAULT_CUSTOMIZATION,
    locked_values, is_unlocked,
)


@pytest.fixture(autouse=True)
def _fresh_store():
    achievements._MEM.clear()
    from db.redis_client import r as redis
    if hasattr(redis, "_store"):
        for key in list(redis._store.keys()):
            if key.startswith("ach:"):
                redis.delete(key)
    yield
    achievements._MEM.clear()


def new_uid(tag):
    return f"{tag}-{uuid.uuid4().hex[:8]}"


#  Map sanity 

def test_unlock_map_keys_exist_in_catalog():
    for (field, value), key in UNLOCK_REQUIREMENTS.items():
        assert key in ACHIEVEMENTS, f"unknown achievement {key}"
        assert field in DEFAULT_CUSTOMIZATION, f"unknown field {field}"


def test_gated_values_are_real_select_values():
    """Values must match what customize.html offers for the field."""
    valid = {
        "ball_design":      {"classic", "euro", "gold", "titanium", "retro", "neon", "pixel"},
        "crowd_palette":    {"classic", "team_a", "team_b", "rainbow", "mono"},
        "bg_scene":         {"night", "sunset", "cloudy", "day", "cyber"},
        "grass_shade":      {"dark", "bright", "emerald", "winter", "neon"},
        "goal_effect":      {"confetti", "fireworks", "aurora", "flash", "shake", "none"},
        "floodlight_color": {"warm", "cool", "red", "purple"},
    }
    for (field, value), key in UNLOCK_REQUIREMENTS.items():
        assert value in valid[field], f"unknown value {field}={value}"
    # every gated field is a select-style field (never color/range/gameplay)
    assert set(f for (f, v) in UNLOCK_REQUIREMENTS) == set(valid)


def test_defaults_never_gated():
    for (field, value), key in UNLOCK_REQUIREMENTS.items():
        assert DEFAULT_CUSTOMIZATION[field] != value, f"default {field} is gated!"


def test_gameplay_fields_never_gated():
    gameplay = {"player_stats", "half_length", "win_goal_limit", "power_cap",
                "player_count", "stadium_vignette"}
    gated_fields = {f for (f, v) in UNLOCK_REQUIREMENTS}
    assert gated_fields.isdisjoint(gameplay)


def test_cosmetic_rewards_reverse_map_complete():
    assert len(COSMETIC_REWARDS) == len(set(UNLOCK_REQUIREMENTS.values()))
    for (f, v), k in UNLOCK_REQUIREMENTS.items():
        assert COSMETIC_REWARDS[k], f"no rewards for {k}"
        assert any("ball" in r or "crowd" in r or "scene" in r or "pitch" in r
                   or "Fireworks" in r or "floodlights" in r or "Aurora" in r
                   for r in COSMETIC_REWARDS[k])


#  Locked derivation 

def test_everything_locked_for_unknown_user():
    locks = locked_values(None)
    assert locks == set(UNLOCK_REQUIREMENTS)
    assert locked_values("nobody:here") == set(UNLOCK_REQUIREMENTS)


def test_unlocked_after_earning_achievement():
    uid = new_uid("unl")
    assert ("ball_design", "gold") in locked_values(uid)
    award(uid, "rk_first_win")
    assert ("ball_design", "gold") not in locked_values(uid)
    # unrelated achievement changes nothing
    assert ("bg_scene", "cyber") in locked_values(uid)
    award(uid, "ai_first_model")
    assert ("bg_scene", "cyber") not in locked_values(uid)
    assert ("crowd_palette", "rainbow") in locked_values(uid)


def test_is_unlocked_helpers():
    uid = new_uid("hlp")
    assert is_unlocked(uid, "ball_design", "classic") is True     # not gated
    assert is_unlocked(uid, "ball_design", "gold") is False
    assert is_unlocked(uid, "player_stats", "whatever") is True   # not gated
    award(uid, "sk_big_win")
    assert is_unlocked(uid, "crowd_palette", "rainbow") is True


#  Server-side enforcement at /customize/save 

def _save(client, payload):
    return client.post("/customize/save", json=payload)


def test_save_rejects_locked_value():
    uid = new_uid("lock")
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["user_id"] = uid
        resp = _save(c, {"ball_design": "gold"})
        assert resp.status_code == 403
        data = resp.get_json()
        assert data["ok"] is False
        assert "locked" in data["error"] and "Ranked Debut" in data["error"]
        # nothing persisted
        assert customization.get_customization(uid)["ball_design"] == "classic"


def test_save_allows_unlocked_value():
    uid = new_uid("unlock")
    award(uid, "rk_first_win")
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["user_id"] = uid
        resp = _save(c, {"ball_design": "gold", "team_a_color": "#111111"})
        assert resp.status_code == 200 and resp.get_json()["ok"] is True
        assert customization.get_customization(uid)["ball_design"] == "gold"


def test_save_allows_non_gated_values_always():
    uid = new_uid("free")
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["user_id"] = uid
        for field, value in (("ball_design", "retro"), ("bg_scene", "day"),
                             ("crowd_palette", "mono"), ("goal_effect", "shake"),
                             ("floodlight_color", "warm"), ("grass_shade", "winter")):
            resp = _save(c, {field: value})
            assert resp.status_code == 200, field
            assert customization.get_customization(uid)[field] == value


def test_player_stats_never_gated():
    uid = new_uid("stats")
    ps = {"a": [{"size": 80, "power": 20, "weight": 20, "agility": 20}],
          "b": [{"size": 20, "power": 80, "weight": 20, "agility": 20}]}
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["user_id"] = uid
        resp = _save(c, {"player_stats": ps})
        assert resp.status_code == 200 and resp.get_json()["ok"] is True
        assert customization.get_customization(uid)["player_stats"] == ps


def test_save_ignores_unknown_keys_still():
    uid = new_uid("unk")
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["user_id"] = uid
        resp = _save(c, {"not_a_real_setting": 1})
        assert resp.status_code == 200


def test_save_requires_login():
    with app.test_client() as c:
        assert _save(c, {"ball_design": "gold"}).status_code in (302, 401)


#  Page lock rendering 

def test_customize_page_marks_locked_options():
    uid = new_uid("page")
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["user_id"] = uid
            s["username"] = "LockPage"
        body = c.get("/customize").get_data(as_text=True)
        # lock map reaches the page for every gate
        assert "rk_first_win" in body and "ai_first_model" in body
        assert "tour_champion" in body
        # this user's locked set marks all 8 as locked
        assert '"ball_design:gold"' in body
        assert '"floodlight_color:purple"' in body
        assert '"grass_shade:neon"' in body


def test_customize_page_unlocks_after_achievement():
    uid = new_uid("page2")
    award(uid, "friend_match")
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["user_id"] = uid
        body = c.get("/customize").get_data(as_text=True)
        assert '"grass_shade:neon"' not in body
        assert '"ball_design:gold"' in body  # still locked


#  Toast reward enrichment 

def test_toast_carries_unlock_rewards(monkeypatch):
    uid = new_uid("toast")
    monkeypatch.setattr(appmod, "uid", lambda: uid)
    award(uid, "rk_first_win")
    toasts = appmod._ach_toasts()
    assert len(toasts) == 1
    assert toasts[0]["key"] == "rk_first_win"
    assert "Gold ball" in toasts[0].get("unlock", [])
    # drained
    assert appmod._ach_toasts() == []


def test_toast_without_rewards_has_no_unlock_key(monkeypatch):
    uid = new_uid("toast2")
    monkeypatch.setattr(appmod, "uid", lambda: uid)
    award(uid, "exp_first_goal")
    toasts = appmod._ach_toasts()
    assert toasts and "unlock" not in toasts[0]
