"""Tests for the loss-analysis feature ("why did my model lose this match?").

Covers: outcome tagging (goal / good chance / neutral / poor / own-goal
risk), snapshot/rebuild roundtrips, deterministic playback against the
stored pre-kick state, on-demand built-in comparisons, the decision-traces
store (idempotent upserts, per-model filtering, 30-day + per-owner pruning),
pattern aggregation (with honest caveats), and the owner-only routes +
page.
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
from db import decision_traces as traces
from db import user_models
from models.soccer_logic import new_soccer_state, GOAL_Y1, GOAL_Y2
from services import loss_analysis as la
from services.loss_analysis import (
    build_snapshot, reconstruct_state, tag_outcome, playback_turn,
    builtin_decision, aggregate_patterns, save_traced_turn,
    TAG_GOAL, TAG_CHANCE, TAG_NEUTRAL, TAG_POOR, TAG_OWN_GOAL,
)


@pytest.fixture(autouse=True)
def _fresh_store():
    traces._MEM.clear()
    user_models._MEM.clear()
    user_models._MEM_SEQ[0] = 0
    yield
    traces._MEM.clear()
    user_models._MEM.clear()
    user_models._MEM_SEQ[0] = 0


def new_uid(tag):
    return f"{tag}-{uuid.uuid4().hex[:8]}"


def login(client_, uid_, name_="Tester"):
    with client_.session_transaction() as s:
        s["user_id"] = uid_
        s["username"] = name_


def make_model(owner, name="Tracer Bot"):
    from db.user_models import create_model
    return create_model(owner, name, "test model",
                        "def get_ai_move(state, is_player_a):\n    return 0, 0.0, 50.0")


def _traj(points):
    return [{"x": float(x), "y": float(y), "z": 0.0} for x, y in points]


def _snap_a():
    return {"is_player_a": True}


#  Outcome tagging 

def test_tag_goal():
    assert tag_outcome(_snap_a(), {"angle": 0}, "A", _traj([(600, 400), (1380, 430)])) == TAG_GOAL


def test_tag_own_goal():
    # Team A kicked into the goal B attacks -> B scored.
    assert tag_outcome(_snap_a(), {"angle": 180}, "B", _traj([(600, 400), (20, 430)])) == TAG_OWN_GOAL


def test_tag_good_chance():
    # Ends at the attacking goal line, on-target -> keeper just saved it.
    assert tag_outcome(_snap_a(), {"angle": 0}, None, _traj([(600, 400), (1360, 430)])) == TAG_CHANCE
    # Team B mirror: ends at x <= 50 inside the mouth band.
    assert tag_outcome({"is_player_a": False}, {}, None, _traj([(800, 400), (30, 437)])) == TAG_CHANCE


def test_tag_poor_backward():
    assert tag_outcome(_snap_a(), {"angle": 200}, None, _traj([(600, 400), (500, 400)])) == TAG_POOR


def test_tag_poor_tap():
    # Barely moved and slow -> tapped the ball, no threat.
    assert tag_outcome(_snap_a(), {"angle": 0}, None, _traj([(600, 400), (610, 402), (620, 404)])) == TAG_POOR


def test_tag_neutral():
    assert tag_outcome(_snap_a(), {"angle": 30}, None, _traj([(600, 400), (700, 300), (900, 200)])) == TAG_NEUTRAL


def test_tag_no_trajectory():
    assert tag_outcome(_snap_a(), {"angle": 0}, None, None) == TAG_NEUTRAL


def test_goal_mouth_geometry():
    # Sanity: the mouth band constants match the frozen goal aperture.
    assert GOAL_Y1 == 356 and GOAL_Y2 == 519
    assert la.GOAL_CENTER_Y == 437.5


#  Snapshot / rebuild 

def test_snapshot_roundtrip():
    st = new_soccer_state()
    st["ball"] = {"x": 700.0, "y": 437.5, "z": 0.0}
    st["score_a"], st["score_b"] = 1, 2
    st["kick_count"] = 7
    st["move_history"] = [{"desc": "x"}]
    snap = build_snapshot(st)
    assert "move_history" not in snap
    rebuilt = reconstruct_state(snap)
    assert rebuilt["ball"] == st["ball"]
    assert rebuilt["score_a"] == 1 and rebuilt["score_b"] == 2
    assert rebuilt["kick_count"] == 7
    assert rebuilt["move_history"] == []
    # Snapshot is a deep copy: mutating the source doesn't corrupt it.
    st["ball"]["x"] = 0.0
    assert snap["ball"]["x"] == 700.0


#  Playback (real physics, deterministic) 

def test_playback_deterministic_and_advances_ball():
    st = new_soccer_state()
    snap = build_snapshot(st)
    dec = {"player_idx": 2, "angle": 0.0, "power": 90.0}
    r1 = playback_turn(snap, dec)
    r2 = playback_turn(snap, dec)
    assert r1["trajectory"] == r2["trajectory"]
    assert len(r1["trajectory"]) >= 2
    start_x = r1["trajectory"][0]["x"]
    end_x = r1["trajectory"][-1]["x"]
    assert end_x > start_x + 50  # team A attacks +x


def test_playback_miss_reflects_engine_truth():
    # Diagonal/backward kicks at power miss in this engine (known: angles
    # >= 10 deg rarely connect), so playback reports the miss honestly —
    # own-goal classification therefore relies on the stored scored flag.
    st = new_soccer_state()
    snap = build_snapshot(st)
    dec = {"player_idx": 2, "angle": 180.0, "power": 90.0}
    r1 = playback_turn(snap, dec)
    r2 = playback_turn(snap, dec)
    assert r1["trajectory"] == r2["trajectory"]
    assert "missed" in r1["desc"].lower()
    end_x = r1["trajectory"][-1]["x"]
    assert end_x <= 700.0 + 5  # no progress toward the opponent goal


#  Comparison (on-demand, no pre-storage) 

def test_builtin_decision_minimax():
    st = new_soccer_state()
    d = builtin_decision(build_snapshot(st), "minimax")
    assert d is not None
    assert d["model"] == "minimax"
    assert d["name"]
    assert isinstance(d["player_idx"], int)
    assert isinstance(d["angle"], (int, float))
    assert isinstance(d["power"], (int, float))


def test_builtin_decision_unknown_key():
    assert builtin_decision(build_snapshot(new_soccer_state()), "not_a_model") is None


def test_default_comparison_is_minimax():
    assert la.default_comparison_model() == "minimax"


#  Store (in-memory fallback) 

def _seed_turns(owner, model_id, n=6, opp="Greedy Striker", match_id="arena:u1:A:0",
                result="loss", sf=0, sa=2):
    st = new_soccer_state()
    for i in range(n):
        save_traced_turn(
            owner_id=owner, model_id=model_id, model_label="Tracer Bot",
            match_id=match_id, opponent=opp, result=result,
            score_for=sf, score_against=sa,
            turn=i, mover="a" if i % 2 == 0 else "b",
            pre_state=st, decision={"player_idx": 0, "angle": 10.0, "power": 50.0},
            scored=None, trajectory=_traj([(600, 400), (700, 410)]),
        )


def test_save_and_list_roundtrip():
    owner = new_uid("db")
    mid = "arena:u1:A:0"
    _seed_turns(owner, "user_model:dev-1", n=6, match_id=mid)
    matches = traces.list_matches(owner)
    assert len(matches) == 1
    assert matches[0]["match_id"] == mid
    assert matches[0]["turn_count"] == 6
    assert matches[0]["opponent"] == "Greedy Striker"
    assert matches[0]["result"] == "loss"
    assert matches[0]["score_for"] == 0 and matches[0]["score_against"] == 2
    rows = traces.get_match(owner, mid)
    assert [r["turn"] for r in rows] == [0, 1, 2, 3, 4, 5]  # ordered by turn
    assert rows[0]["outcome_tag"] == TAG_NEUTRAL
    meta = traces.get_match_meta(owner, mid)
    assert meta["turn_count"] == 6


def test_upsert_idempotent():
    owner = new_uid("up")
    mid = "arena:u1:A:0"
    _seed_turns(owner, "user_model:dev-1", n=3, match_id=mid)
    n_before = len(traces._MEM)
    # Re-save the same (match_id, turn) pair with different data.
    save_traced_turn(
        owner_id=owner, model_id="user_model:dev-1", model_label="Tracer Bot",
        match_id=mid, opponent="Greedy Striker", result="win", score_for=1, score_against=0,
        turn=1, mover="b", pre_state=new_soccer_state(),
        decision={"player_idx": 2, "angle": 44.0, "power": 90.0},
        scored="A", trajectory=_traj([(600, 400), (1380, 430)]),
    )
    assert len(traces._MEM) == n_before  # overwritten, not duplicated
    rows = traces.get_match(owner, mid)
    assert rows[1]["decision"]["player_idx"] == 2
    assert rows[1]["result"] == "win"
    assert rows[1]["outcome_tag"] == TAG_GOAL


def test_model_filter():
    owner = new_uid("mf")
    _seed_turns(owner, "user_model:dev-1", n=4, match_id="arena:u1:A:0")
    _seed_turns(owner, "user_model:dev-2", n=3, match_id="arena:u1:A:1")
    assert len(traces.list_matches(owner)) == 2
    only1 = traces.list_matches(owner, model_id="user_model:dev-1")
    assert [m["match_id"] for m in only1] == ["arena:u1:A:0"]
    other = new_uid("mf2")
    assert traces.list_matches(other) == []


def test_prune_age_and_cap():
    owner = new_uid("pr")
    old = (time.time() - 31 * 86400)
    st = new_soccer_state()
    for i in range(3):
        save_traced_turn(
            owner_id=owner, model_id="user_model:dev-1", model_label="M",
            match_id=f"oldmatch:{i}", opponent="X", result="loss",
            score_for=0, score_against=1, turn=0, mover="a",
            pre_state=st, decision={"player_idx": 0, "angle": 0.0, "power": 50.0},
            scored=None, trajectory=_traj([(600, 400), (700, 410)]),
        )
    for r in traces._MEM:
        r["created_at"] = _old_iso(old)
    assert traces.prune_expired(max_age_days=30, max_matches=200) >= 3
    assert traces._MEM == []

    # Per-owner match cap.
    for i in range(5):
        save_traced_turn(
            owner_id=owner, model_id="user_model:dev-1", model_label="M",
            match_id=f"capmatch:{i}", opponent="X", result="loss",
            score_for=0, score_against=1, turn=0, mover="a",
            pre_state=st, decision={"player_idx": 0, "angle": 0.0, "power": 50.0},
            scored=None, trajectory=_traj([(600, 400), (700, 410)]),
        )
    deleted = traces.prune_expired(max_age_days=30, max_matches=2)
    assert deleted >= 3
    remaining = traces.list_matches(owner)
    assert len(remaining) == 2


def _old_iso(t):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


#  Aggregate patterns 

def test_aggregate_patterns():
    owner = new_uid("ag")
    _seed_turns(owner, "user_model:dev-1", n=3, match_id="m1", result="loss", sf=0, sa=2)
    # One goal match vs a different opponent.
    save_traced_turn(
        owner_id=owner, model_id="user_model:dev-1", model_label="Tracer Bot",
        match_id="m2", opponent="Minimax", result="win", score_for=1, score_against=0,
        turn=0, mover="a", pre_state=new_soccer_state(),
        decision={"player_idx": 0, "angle": 0.0, "power": 90.0},
        scored="A", trajectory=_traj([(600, 400), (1380, 437)]),
    )
    rows = traces.list_traces(owner, limit=100)
    p = aggregate_patterns(rows)
    assert p["n_matches"] == 2
    assert p["n_turns"] == 4
    assert p["record"] == {"wins": 1, "losses": 1, "draws": 0}
    assert p["outcomes"][TAG_GOAL] == 1
    assert p["outcomes"][TAG_NEUTRAL] == 3
    opps = {o["opponent"]: o for o in p["per_opponent"]}
    assert set(opps) == {"Greedy Striker", "Minimax"}
    assert opps["Minimax"]["outcomes"][TAG_GOAL] == 1
    assert sum(p["by_third"]["early"].values()) + sum(p["by_third"]["middle"].values()) \
        + sum(p["by_third"]["late"].values()) == 4
    assert p["caveats"]  # honest caveats always present
    assert p["own_goal_match_ids"] == []


def test_aggregate_patterns_own_goal_detection():
    owner = new_uid("og")
    save_traced_turn(
        owner_id=owner, model_id="user_model:dev-1", model_label="Tracer Bot",
        match_id="m1", opponent="Greedy Striker", result="loss", score_for=0, score_against=1,
        turn=0, mover="a", pre_state=new_soccer_state(),
        decision={"player_idx": 0, "angle": 180.0, "power": 90.0},
        scored="B", trajectory=_traj([(600, 400), (20, 437)]),
    )
    p = aggregate_patterns(traces.list_traces(owner, limit=100))
    assert p["outcomes"][TAG_OWN_GOAL] == 1
    assert p["own_goal_match_ids"] == ["m1"]


#  Routes (owner-only) 

def test_page_requires_login():
    c = app.test_client()
    r = c.get("/loss-analysis?model=dev-1")
    assert r.status_code == 302


def test_page_unknown_model_redirects():
    c = app.test_client()
    login(c, new_uid("pg"))
    r = c.get("/loss-analysis?model=dev-999")
    assert r.status_code == 302


def test_page_renders_analysis_mode():
    owner = new_uid("pg")
    make_model(owner, "Page Bot")
    c = app.test_client()
    login(c, owner)
    r = c.get("/loss-analysis?model=dev-1")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Loss Analysis" in html
    assert 'id="loss-section"' in html
    assert 'const LOSS_MODEL = "user_model:dev-1";' in html
    assert 'const LOSS_MODEL_NAME = "Page Bot";' in html
    # Replay-only UI must not leak into loss mode.
    assert 'id="btn-next"' not in html
    assert 'id="loss-strip"' in html  # decision strip container


def test_matches_api_owner_only():
    owner = new_uid("ma")
    other = new_uid("ma")
    make_model(owner, "API Bot")
    _seed_turns(owner, "user_model:dev-1", n=2, match_id="arena:u1:A:0")
    c = app.test_client()
    assert c.get("/api/loss/models/user_model:dev-1/matches").status_code == 302  # no login
    login(c, other)
    assert c.get("/api/loss/models/user_model:dev-1/matches").status_code == 404  # not owner
    login(c, owner)
    r = c.get("/api/loss/models/user_model:dev-1/matches")
    assert r.status_code == 200
    data = r.get_json()
    assert data["model_name"] == "API Bot"
    assert len(data["matches"]) == 1
    assert data["matches"][0]["turn_count"] == 2


def test_traces_api_shape():
    owner = new_uid("tr")
    make_model(owner)
    _seed_turns(owner, "user_model:dev-1", n=3, match_id="arena:u1:A:0")
    c = app.test_client()
    login(c, owner)
    r = c.get("/api/loss/models/user_model:dev-1/matches/arena:u1:A:0")
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["traces"]) == 3
    t = data["traces"][0]
    assert t["turn"] == 0
    assert t["mover"] == "a"
    assert t["decision"]["angle"] == 10.0
    assert "state" in t and t["state"]["is_player_a"] is True
    assert "score_a" in t["state"] and "players_a" in t["state"]
    # Full snapshot never leaves the server.
    assert "state_snapshot" not in t
    assert c.get("/api/loss/models/user_model:dev-1/matches/nope").status_code == 404


def test_compare_api():
    owner = new_uid("co")
    make_model(owner)
    _seed_turns(owner, "user_model:dev-1", n=2, match_id="arena:u1:A:0")
    c = app.test_client()
    login(c, owner)
    r = c.get("/api/loss/models/user_model:dev-1/matches/arena:u1:A:0/turns/0/compare?model=minimax")
    assert r.status_code == 200
    data = r.get_json()
    assert data["yours"]["angle"] == 10.0
    assert data["theirs"]["model"] == "minimax"
    assert "player_idx" in data["theirs"]
    assert "diff" in data
    bad = c.get("/api/loss/models/user_model:dev-1/matches/arena:u1:A:0/turns/0/compare?model=zzz")
    assert bad.status_code == 400
    miss = c.get("/api/loss/models/user_model:dev-1/matches/arena:u1:A:0/turns/99/compare?model=minimax")
    assert miss.status_code == 404


def test_playback_api():
    owner = new_uid("pb")
    make_model(owner)
    _seed_turns(owner, "user_model:dev-1", n=1, match_id="arena:u1:A:0")
    c = app.test_client()
    login(c, owner)
    r = c.post("/api/loss/models/user_model:dev-1/matches/arena:u1:A:0/turns/0/playback")
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["trajectory"]) >= 2
    assert "desc" in data and "outcome_tag" in data


def test_patterns_api():
    owner = new_uid("pa")
    make_model(owner)
    _seed_turns(owner, "user_model:dev-1", n=3, match_id="arena:u1:A:0")
    c = app.test_client()
    login(c, owner)
    r = c.get("/api/loss/models/user_model:dev-1/patterns")
    assert r.status_code == 200
    p = r.get_json()
    assert p["n_matches"] == 1 and p["n_turns"] == 3
    assert p["caveats"]
