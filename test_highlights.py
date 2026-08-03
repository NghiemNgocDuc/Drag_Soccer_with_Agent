"""Tests for auto-detected match highlights (db/highlights.py + routes)."""
import os
os.environ.setdefault("DEV_MODE", "1")

import pytest

from db import tournaments as db
from db.highlights import (detect_highlights, get_highlights,
                           resolve_highlight, highlight_id)

from app import app as flask_app


def mk_traj(points):
    return [{"x": x, "y": y, "z": 0} for x, y in points]


def mk_replay(real_moves):
    """real_moves: [{"mover", "traj", "scored"}] → interleaved replay_data."""
    out = []
    for m in real_moves:
        out.append({"desc": "placeholder", "player": "A", "player_idx": 1,
                    "angle": 5.0, "power": 50.0, "scored": m.get("scored")})
        out.append({"mover": m["mover"], "player_idx": 1, "angle": 5.0,
                    "power": 50.0, "trajectory": m["traj"],
                    "push_result": None, "scored": m.get("scored")})
    return out


GOAL_TRAJ = mk_traj([(1000 + 40 * i, 437.5) for i in range(10)])          # to x=1360, A scoring
NEAR_TRAJ = mk_traj([(1000 + 35 * i, 437.5) for i in range(9)])           # to x=1280, fast, close
SLOW_TRAJ = mk_traj([(1000 + 5 * i, 437.5) for i in range(60)])           # to x=1295, slow
FAST_TRAJ = mk_traj([(300 - 30 * i, 437.5) for i in range(6)])            # B side, fast, not near
WEDGE_TRAJ = mk_traj([(584, 826)] * 20)                                   # ball never moves
LEFT_NEAR_TRAJ = mk_traj([(300 - 30 * i, 437.5) for i in range(7)])       # B fast, reaches x=120


def base_replay():
    return mk_replay([
        {"mover": "a", "traj": WEDGE_TRAJ},                                   # kick 1
        {"mover": "b", "traj": WEDGE_TRAJ},                                   # kick 2
        {"mover": "a", "traj": GOAL_TRAJ, "scored": "A"},                     # kick 3 — goal
        {"mover": "a", "traj": NEAR_TRAJ},                                    # kick 4 — near miss
        {"mover": "a", "traj": SLOW_TRAJ},                                    # kick 5 — slow, excluded
        {"mover": "b", "traj": FAST_TRAJ},                                    # kick 6 — fast play
        {"mover": "a", "traj": LEFT_NEAR_TRAJ},                               # kick 7 — near LEFT goal, wrong direction
    ])


# ── Detection ────────────────────────────────────────────────────────────────

def test_goal_detected_with_clip_window():
    hls = detect_highlights(base_replay())
    goals = [h for h in hls if h["type"] == "goal"]
    assert len(goals) == 1
    g = goals[0]
    # kick 3 → real-move index 2 → entries 3 (kick 2, build-up) and 7 (kick 4, aftermath)
    assert g["start"] == 3
    assert g["end"] == 7
    assert g["label"] == "Goal — kick 3"


def test_goal_clip_clamps_at_edges():
    rd = mk_replay([
        {"mover": "a", "traj": GOAL_TRAJ, "scored": "A"},   # first kick is a goal
        {"mover": "b", "traj": WEDGE_TRAJ},
    ])
    g = [h for h in detect_highlights(rd) if h["type"] == "goal"][0]
    assert g["start"] == 1  # no move before → clamp to first real entry
    assert g["end"] == 3

    rd2 = mk_replay([
        {"mover": "a", "traj": WEDGE_TRAJ},
        {"mover": "b", "traj": GOAL_TRAJ, "scored": "B"},   # last kick is a goal
    ])
    g2 = [h for h in detect_highlights(rd2) if h["type"] == "goal"][0]
    assert g2["start"] == 1
    assert g2["end"] == 3  # no move after → clamp to last real entry


def test_near_miss_detected_and_slow_roll_excluded():
    hls = detect_highlights(base_replay())
    nears = [h for h in hls if h["type"] == "near"]
    assert len(nears) == 1
    assert nears[0]["kick"] == 4
    # slow roll reaches x=1295 but is slow → must NOT appear
    assert all(h["type"] != "near" or h["kick"] != 5 for h in hls)


def test_near_miss_requires_attacking_direction():
    # kick 7 is fast and reaches within 100px of the LEFT goal line, but mover a
    # attacks right → not a near miss for a
    hls = detect_highlights(base_replay())
    assert all(h["kick"] != 7 or h["type"] == "fast" for h in hls)


def test_fast_play_detected_goal_excluded():
    hls = detect_highlights(base_replay())
    fasts = [h for h in hls if h["type"] == "fast"]
    assert [h["kick"] for h in fasts] == [6, 7]
    # the scoring kick (kick 3, speed 2400) must not double-list as fast play
    assert all(h["kick"] != 3 or h["type"] == "goal" for h in hls)


def test_wedged_moves_never_highlighted():
    rd = mk_replay([{"mover": "a", "traj": WEDGE_TRAJ}] * 4)
    assert detect_highlights(rd) == []


def test_empty_and_single_frame_trajectories_safe():
    rd = mk_replay([{"mover": "a", "traj": [{"x": 700, "y": 437.5, "z": 0}]}])
    assert detect_highlights(rd) == []


# ── Caching + share registry ─────────────────────────────────────────────────

def _seed_match(replay_data):
    with flask_app.test_client() as c:
        c.post("/auth/register", data={"username": "hltest", "email": "hltest@t.com",
                                       "password": "pass123", "confirm": "pass123"})
        r = c.post("/tournaments/create", json={"name": "hlt"})
        tid = r.get_json()["tournament"]["id"]
        c.post(f"/tournaments/{tid}/add", json={"participant_id": "greedy", "name": "g"})
        c.post(f"/tournaments/{tid}/add", json={"participant_id": "greedy", "name": "g"})
        c.post(f"/tournaments/{tid}/generate")
        mid = db.get_tournament(tid)["matches"][0]["id"]
        db.save_match_result(tid, mid, "winner", replay_data)
        return tid, mid


def test_get_highlights_caches_and_registers_share_ids():
    tid, mid = _seed_match(base_replay())
    hls = get_highlights(tid, mid)
    assert hls and all(h.get("id") for h in hls)
    # deterministic ids — second call returns identical cached list
    assert get_highlights(tid, mid) == hls
    assert hls[0]["id"] == highlight_id(tid, mid, hls[0])
    # registry roundtrip
    r1 = resolve_highlight(hls[0]["id"])
    assert r1 and r1["tid"] == tid and r1["match_id"] == mid
    assert r1["start"] == hls[0]["start"] and r1["end"] == hls[0]["end"]
    assert resolve_highlight("nonexistent") is None


def test_get_highlights_missing_match_returns_none():
    assert get_highlights("nope", "nope") is None


# ── HTTP routes ──────────────────────────────────────────────────────────────

def test_highlights_api_requires_login():
    c = flask_app.test_client()
    assert c.get("/matches/x/y/highlights").status_code == 302


def test_highlights_api_returns_json():
    tid, mid = _seed_match(base_replay())
    with flask_app.test_client() as c:
        c.post("/auth/login", json={"email": "hltest@t.com", "password": "pass123"})
        r = c.get(f"/matches/{tid}/{mid}/highlights")
        assert r.status_code == 200
        data = r.get_json()
        assert data["highlights"]
        assert {h["type"] for h in data["highlights"]} >= {"goal", "near", "fast"}


def test_highlight_page_requires_login_and_unknown_404_flow():
    c = flask_app.test_client()
    assert c.get("/highlight/deadbeef").status_code == 302  # login redirect
    c.post("/auth/register", data={"username": "hl2", "email": "hl2@t.com",
                                   "password": "pass123", "confirm": "pass123"})
    r = c.get("/highlight/deadbeef")
    assert r.status_code == 302  # flash redirect, not crash


def test_highlight_page_renders_clip():
    tid, mid = _seed_match(base_replay())
    hls = get_highlights(tid, mid)
    hid = hls[0]["id"]
    with flask_app.test_client() as c:
        c.post("/auth/register", data={"username": "hl3", "email": "hl3@t.com",
                                       "password": "pass123", "confirm": "pass123"})
        r = c.get(f"/highlight/{hid}")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "Match Replay" in body
        assert f"/highlight/{hid}" in body or "Highlights" in body
