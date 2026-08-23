"""Analytics dashboard tests: stats math, archetype bucketing, agent-matrix
analysis (incl. non-transitivity), rating progression, season-over-season,
match dynamics, and the public API/caching routes."""
import json
import os
import sys

os.environ.setdefault("DEV_MODE", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest
from services import analytics as A
from db.redis_client import r


# ── Stats helpers ──────────────────────────────────────────────────────────

def test_wilson_ci_small_sample_bounds():
    assert A.wilson_ci(0, 0) == (0.0, 0.0)
    lo, hi = A.wilson_ci(1, 2)          # 50% of 2: wide but valid interval
    assert lo == pytest.approx(0.095, abs=0.02) and hi == pytest.approx(0.905, abs=0.02)
    assert A.wilson_ci(0, 5)[0] == 0.0  # zero successes clamp to 0
    lo, hi = A.wilson_ci(50, 100)
    assert 0.40 <= lo <= 0.41 and 0.59 <= hi <= 0.60
    lo, hi = A.wilson_ci(1, 100)
    assert lo > 0.0 and hi < 0.06       # sparse successes still a positive CI


def test_poisson_ci():
    lo, hi = A.poisson_ci(10, 10)
    assert lo == pytest.approx(0.38, abs=0.01) and hi == pytest.approx(1.62, abs=0.01)
    assert A.poisson_ci(0, 5) == (0.0, 0.0)


def test_proportion_test_method_selection():
    small = A.proportion_test(1, 3, 3, 3)
    assert small["method"] == "fisher" and small["p"] is not None
    assert 0 < small["p"] <= 1
    big = A.proportion_test(40, 100, 60, 100)
    assert big["method"] == "chi2"
    equal = A.proportion_test(10, 20, 10, 20)
    assert equal["p"] >= 0.05
    none = A.proportion_test(1, 0, 1, 1)
    assert none["p"] is None


def test_group_proportion_test():
    groups = [{"key": "a", "k": 8, "n": 20}, {"key": "b", "k": 2, "n": 20}]
    res = A.group_proportion_test(groups)
    assert res["p"] is not None and res["p"] <= 0.05
    assert res["groups"][0]["ci"][0] <= res["groups"][0]["ci"][1]
    tiny = [{"key": "a", "k": 0, "n": 0}, {"key": "b", "k": 1, "n": 1}]
    assert A.group_proportion_test(tiny)["p"] is None
    one = [{"key": "a", "k": 1, "n": 2}]
    assert A.group_proportion_test(one)["p"] is None


def test_confidence_label():
    assert "directional only" in A.confidence_label(4, 10)
    assert A.confidence_label(25, 10) == "25 observations"


# ── Stat-build meta ────────────────────────────────────────────────────────

def _summaries(n, winner_bias=0.6, power_share=0.5):
    """n summaries; side a uses a Power-heavy build, side b Balanced.
    A wins with probability `winner_bias` (no build effect)."""
    out = []
    for i in range(n):
        out.append({
            "room_id": f"room-{i}",
            "player_a": f"ua{i}", "player_b": f"ub{i}",
            "score_a": 2 if i % 2 else 1, "score_b": 1,
            "winner": "A",
            "ranked": True,
            "ranked_result": {"player_a": {"rating_before": 1200 + i},
                              "player_b": {"rating_before": 1180 + i}},
            "build_a": [{"size": 50, "power": 80, "weight": 50, "agility": 50}] * 3,
            "build_b": [{"size": 50, "power": 50, "weight": 50, "agility": 50}] * 3,
            "season": 1, "started_at": 1700000000 + i,
            "total_kicks": 30, "first_goal_kick": 12,
        })
    return out


def test_archetype_classification():
    arch, sig = A._archetype([{"size": 30, "power": 80, "weight": 40, "agility": 50}] * 3)
    assert arch == "power" and sig["power"] == pytest.approx(80)
    arch, _ = A._archetype([{"size": 50, "power": 50, "weight": 50, "agility": 50}] * 3)
    assert arch == "balanced"
    arch, _ = A._archetype([{"size": 70, "power": 60, "weight": 50, "agility": 45}] * 3)
    assert arch == "size"
    arch, _ = A._archetype([])
    assert arch == "balanced"


def test_stat_build_insufficient():
    res = A.stat_build_analysis([])
    assert res["status"] == "insufficient_data" and res["needed"] == 10
    res = A.stat_build_analysis(_summaries(5))
    assert res["status"] == "insufficient_data"


def test_stat_build_homogeneity_and_groups():
    res = A.stat_build_analysis(_summaries(24))
    assert res["status"] == "ok"
    groups = {g["key"]: g for g in res["per_archetype"]}
    assert set(groups) == {"Power-heavy", "Balanced"}
    assert groups["Power-heavy"]["n"] == 24 and groups["Balanced"]["n"] == 24
    # Everyone wins in this synthetic set -> degenerate table, no test claimed
    assert res["test"]["p"] is None or res["test"]["p"] >= 0.05
    assert res["overall"]["n_matches"] == 24 and res["overall"]["n_sides"] == 48
    # All sides carry a pre-match rating -> median-rating strata should exist
    assert res["strata"] is not None and "below" in res["strata"]["tests"]


def test_stat_build_skill_strata_gated_on_rated_rows():
    sums = _summaries(24)
    for s in sums:
        s["ranked"] = False
        s["ranked_result"] = None
    res = A.stat_build_analysis(sums, ratings={})
    assert res["strata"] is None  # no rated rows -> no skill control claimed


def test_stat_build_arch_effect_detected():
    # Power-heavy sides win 90% here: the test should be able to detect it.
    sums = _summaries(40)
    for i, s in enumerate(sums):
        s["winner"] = "A" if i % 10 < 9 else "B"
    res = A.stat_build_analysis(sums)
    grp = {g["key"]: g for g in res["per_archetype"]}
    assert grp["Power-heavy"]["k"] >= 34
    assert res["test"]["p"] is not None and res["test"]["p"] < 0.05


# ── Agent matrix ───────────────────────────────────────────────────────────

def _matrix_with_cycle():
    return [
        {"model_a": "a", "model_b": "b", "wins_a": 3, "wins_b": 2, "draws": 0,
         "win_rate_a": 60.0, "win_rate_b": 40.0},
        {"model_a": "b", "model_b": "c", "wins_a": 3, "wins_b": 2, "draws": 0,
         "win_rate_a": 60.0, "win_rate_b": 40.0},
        {"model_a": "a", "model_b": "c", "wins_a": 2, "wins_b": 3, "draws": 0,
         "win_rate_a": 40.0, "win_rate_b": 60.0},
    ]


def test_matrix_strength_ranking():
    # Transitive: a beats b and c; b beats c -> clean a > b > c means
    trans = [
        {"model_a": "a", "model_b": "b", "wins_a": 4, "wins_b": 1, "draws": 0,
         "win_rate_a": 80.0, "win_rate_b": 20.0},
        {"model_a": "a", "model_b": "c", "wins_a": 4, "wins_b": 1, "draws": 0,
         "win_rate_a": 80.0, "win_rate_b": 20.0},
        {"model_a": "b", "model_b": "c", "wins_a": 3, "wins_b": 2, "draws": 0,
         "win_rate_a": 60.0, "win_rate_b": 40.0},
    ]
    res = A.agent_matrix_analysis(trans)
    assert res["status"] == "ok"
    assert [s["agent"] for s in res["strength"]] == ["a", "b", "c"]
    assert res["strength"][0]["win_rate"] == 80.0
    assert res["cycles"] == []           # transitive -> no rock-paper-scissors
    for s in res["strength"]:
        assert s["ci"][0] <= s["ci"][1]


def test_matrix_non_transitivity_cycle():
    res = A.agent_matrix_analysis(_matrix_with_cycle())
    assert len(res["cycles"]) == 1
    cyc = res["cycles"][0]
    assert set(cyc["cycle"]) == {"a", "b", "c"}
    # a transitivity-only matrix finds nothing
    trans = [
        {"model_a": "a", "model_b": "b", "wins_a": 4, "wins_b": 1, "draws": 0,
         "win_rate_a": 80.0, "win_rate_b": 20.0},
        {"model_a": "b", "model_b": "c", "wins_a": 4, "wins_b": 1, "draws": 0,
         "win_rate_a": 80.0, "win_rate_b": 20.0},
        {"model_a": "a", "model_b": "c", "wins_a": 3, "wins_b": 2, "draws": 0,
         "win_rate_a": 60.0, "win_rate_b": 40.0},
    ]
    assert A.agent_matrix_analysis(trans)["cycles"] == []


def test_matrix_per_opponent_baseline():
    res = A.agent_matrix_analysis(_matrix_with_cycle())
    po = res["per_opponent"]
    assert set(po["a"]["entries"][0].keys()) >= {"agent", "win_rate", "wins", "n_games"}
    # agents b and c played a: b won 40%, c won 60% -> mean 50
    assert po["a"]["mean"] == pytest.approx(50.0)


def test_custom_models_vs_builtins():
    analysis = A.agent_matrix_analysis(_matrix_with_cycle())
    entries = [{
        "model_id": "m1", "model_name": "Sniper", "username": "dev", "score": 55.0,
        "details": [{"opponent": "a", "win_rate": 60.0, "n_games": 5},
                    {"opponent": "b", "win_rate": 30.0, "n_games": 5}],
    }]
    out = A.custom_models_vs_builtins(entries, analysis)
    assert len(out) == 1 and out[0]["model_name"] == "Sniper"
    rows = {x["opponent"]: x for x in out[0]["rows"]}
    assert rows["a"]["builtins_mean"] == 50.0 and rows["a"]["above_builtin_mean"] is True
    assert rows["b"]["above_builtin_mean"] is False


# ── Rating progression ─────────────────────────────────────────────────────

def _matches_for(players, games=10):
    matches = []
    for p, g in players:
        for i in range(g):
            matches.append({
                "id": f"m-{p}-{i}", "room_id": f"r-{p}-{i}",
                "player_a": p, "player_b": f"opp-{p}-{i}",
                "winner": "A",
                "score_a": 2, "score_b": 1,
                "rating_a_before": 1200 + i * 10,
                "rating_a_after": 1210 + i * 10,
                "delta_a": 10, "k_a": 40,
                "rating_b_before": 1180, "rating_b_after": 1170,
                "delta_b": -10, "k_b": 40,
                "created_at": f"2026-01-{i+1:02d}T00:00:00Z",
            })
    return matches


def test_rating_progression_qualification():
    matches = _matches_for([("pro", 10), ("rookie", 3)])
    res = A.rating_progression(matches, [])
    assert res["status"] == "ok"
    assert [t["player"] for t in res["trajectories"]] == ["pro"]
    assert len(res["trajectories"][0]["points"]) == 10


def test_rating_brackets_use_pre_match_rating():
    matches = _matches_for([("pro", 10)])
    res = A.rating_progression(matches, [], min_bracket=3)
    brackets = {b["bracket"]: b for b in res["brackets"]}
    # pre-match ratings are 1200..1290 -> all ten fall in the 1200 bucket
    assert brackets[1200]["n"] == 10
    assert brackets[1200]["win_rate"] == 100.0
    assert brackets[1200]["ci"][0] >= 0.69


def test_rating_bracket_min_n_floor():
    matches = _matches_for([("pro", 10)])
    res = A.rating_progression(matches, [], min_bracket=5)
    assert all(b["n"] >= 5 for b in res["brackets"])


def test_season_over_season():
    snap1 = json.dumps([
        {"user_id": "u1", "rating": 1400, "games_played": 20, "placed": True},
        {"user_id": "u2", "rating": 1300, "games_played": 15, "placed": True},
    ])
    snap2 = json.dumps([
        {"user_id": "u1", "rating": 1350, "games_played": 12, "placed": True},
        {"user_id": "u3", "rating": 1250, "games_played": 8, "placed": True},
    ])
    seasons = [
        {"number": 1, "status": "completed", "leaderboard_snapshot": snap1},
        {"number": 2, "status": "completed", "leaderboard_snapshot": snap2},
    ]
    res = A.season_over_season(seasons)
    assert len(res["pairs"]) == 1
    p = res["pairs"][0]
    assert p["returning"] == 1 and p["retention"] == 50.0
    assert p["mean_rating_change"] == -50.0 and p["n_deltas"] == 1


# ── Match dynamics ─────────────────────────────────────────────────────────

def _near_miss_traj():
    return [{"x": 1000 + i * 20, "y": 437, "z": 0} for i in range(15)]  # ends at 1280


def _near_miss_traj_b():
    # mover b attacks the left goal: descending to 20 + 100 = 120
    return [{"x": 1000 - i * 40, "y": 437, "z": 0} for i in range(23)]


def _goal_traj():
    return [{"x": 1000 + i * 25, "y": 437, "z": 0} for i in range(16)]  # ends 1375+


def test_first_goal_kick_from_replay():
    replay = [
        {"mover": "a", "trajectory": [{"x": 700, "y": 437, "z": 0}, {"x": 800, "y": 437, "z": 0}], "scored": False},
        {"mover": "a", "trajectory": _goal_traj(), "scored": "A"},
    ]
    assert A._first_goal_kick(replay) == 2
    assert A._first_goal_kick([]) is None


def test_near_miss_detection_reuses_highlight_rules():
    replay = [{"mover": "a", "trajectory": _near_miss_traj(), "scored": False}]
    assert A._near_miss_kicks(replay) == [1]
    slow = [{"mover": "a",
             "trajectory": [{"x": 1200 + i, "y": 437, "z": 0} for i in range(5)],
             "scored": False}]
    assert A._near_miss_kicks(slow) == []  # per-frame speed too low


def test_match_dynamics_conversion():
    summaries = [
        {"score_a": 2, "score_b": 1, "first_goal_kick": 9},
        {"score_a": 0, "score_b": 0, "first_goal_kick": None},
    ]
    # replay: near miss at kick 1 (a), goal at kick 2 (within window of 3),
    # near miss at kick 3 (b) with no follow-up goal -> unconverted
    replay = {
        "tid": "t1", "match_id": "m1",
        "replay_data": [
            {"mover": "a", "trajectory": _near_miss_traj(), "scored": False},
            {"mover": "a", "trajectory": _goal_traj(), "scored": "A"},
            {"mover": "b", "trajectory": _near_miss_traj_b(), "scored": False},
        ],
    }
    res = A.match_dynamics(summaries, [replay])
    assert res["status"] == "ok"
    assert res["goals_per_match"]["n_matches"] == 2
    assert res["goals_per_match"]["mean"] == 1.5
    assert res["goals_per_match"]["ci"][0] < res["goals_per_match"]["ci"][1]
    assert res["first_goal"]["mean_kick"] == pytest.approx((9 + 2) / 2)  # summary + replay
    nm = res["near_miss"]
    assert nm["near_misses"] == 2 and nm["converted"] == 1
    assert nm["conversion_rate"] == 50.0 and nm["ci"][0] < nm["ci"][1]


# ── Data access + API/caching routes ───────────────────────────────────────

def test_db_read_all_paths(monkeypatch):
    from db import ranked, summaries
    ranked._MEM.clear(); ranked._MEM_MATCHES.clear(); ranked._MEM_HISTORY.clear()
    summaries.reset_mem()
    try:
        from db.ranked import record_result
        record_result("analytics-room-1", "ua1", "ub1", "A", 2, 1)
        from db.summaries import save_summary
        save_summary("analytics-room-1", {"room_id": "analytics-room-1", "winner": "A"})
        assert len(ranked.get_all_ranked_matches()) == 1
        assert len(ranked.get_all_rating_history()) == 2
        assert len(summaries.list_summaries()) == 1
    finally:
        ranked._MEM.clear(); ranked._MEM_MATCHES.clear(); ranked._MEM_HISTORY.clear()
        summaries.reset_mem()
        r.delete("summary:analytics-room-1")


def test_api_analytics_public_and_cached(monkeypatch):
    from app import app
    for key in ("an:data", "an:matrix", "an:matrix:status"):
        r.delete(key)
    with app.test_client() as c:
        resp = c.get("/api/analytics")
        assert resp.status_code == 200
        j = resp.get_json()
        assert j["meta"]["summaries"] == 0
        assert j["stat_builds"]["status"] == "insufficient_data"
        assert j["agents"]["status"] == "not_computed"
        assert j["cached"] is False
        # second request is served from cache
        again = c.get("/api/analytics").get_json()
        assert again["cached"] is True
    r.delete("an:data")


def test_api_matrix_recompute_guard(monkeypatch):
    from app import app
    r.delete("an:matrix"); r.delete("an:matrix:status")
    from app import _run_matrix_job as _orig
    monkeypatch.setattr("app._run_matrix_job", lambda *a, **k: None)
    try:
        with app.test_client() as c:
            resp = c.post("/api/analytics/matrix/recompute")
            assert resp.status_code == 200
            assert resp.get_json()["ok"] is True
            again = c.post("/api/analytics/matrix/recompute")
            assert again.status_code == 409
    finally:
        monkeypatch.setattr("app._run_matrix_job", _orig)
        r.delete("an:matrix:status")


def test_analytics_page_public():
    from app import app
    with app.test_client() as c:
        resp = c.get("/analytics")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Stat-build outcomes" in html or "AI agent comparison" in html
        assert "chart.umd.min.js" in html
