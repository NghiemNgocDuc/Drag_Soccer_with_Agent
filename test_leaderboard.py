"""Tests for the custom-AI-model leaderboard (benchmark orchestration, storage, routes)."""
import os
os.environ.setdefault("DEV_MODE", "1")

import pytest

import db.leaderboard as lb
import db.user_models as um
import app as appmod
from app import app as flask_app
from services.game_analytics import benchmark_model_vs_builtins


# ── Fast deterministic stand-ins (no real built-ins: ~56s/game each) ─────────

class _NoOpUser:
    MODEL_NAME = "NoOp User"

    def get_ai_move(self, state, is_player_a):
        return 0, 0.0, 0.0


class _RaisingOpp:
    MODEL_NAME = "Raises"

    def get_ai_move(self, state, is_player_a):
        raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def _clean_mem():
    lb._MEM.clear()
    yield
    lb._MEM.clear()


# ── Benchmark orchestration ──────────────────────────────────────────────────

def test_benchmark_score_is_mean_of_per_opponent_win_rates():
    res = benchmark_model_vs_builtins(_NoOpUser(), n_games=1,
                                      opponents=[_RaisingOpp(), _NoOpUser()])
    # vs raising opponent the user wins every game; vs a no-op twin it draws
    assert res["score"] == 50.0
    by_name = {d["opponent"]: d for d in res["details"]}
    assert by_name["Raises"]["win_rate"] == 100.0
    assert by_name["Raises"]["wins"] == 1
    assert by_name["NoOp User"]["win_rate"] == 0.0
    assert by_name["NoOp User"]["draws"] == 1
    assert res["n_games"] == 1


def test_benchmark_progress_callback_reports_total():
    seen = []
    benchmark_model_vs_builtins(_NoOpUser(), n_games=1,
                                opponents=[_RaisingOpp(), _NoOpUser()],
                                progress_callback=lambda d, n: seen.append((d, n)))
    assert seen and seen[-1][1] == 2  # total = 2 opponents × 1 game


# ── Storage (in-memory fallback) ─────────────────────────────────────────────

def _seed(user="u1"):
    lb.save_submission("m1", user, "Alpha", 80.0, 5,
                       [{"opponent": "Greedy", "win_rate": 80.0, "wins": 4, "draws": 0, "losses": 1, "n_games": 5}])
    lb.save_submission("m2", user, "Beta", 55.0, 5, [])
    lb.save_submission("m3", user, "Gamma", 92.5, 5, [])


def test_list_sorted_by_score_desc_with_pagination():
    _seed()
    entries, total = lb.list_leaderboard(limit=2, offset=0)
    assert total == 3
    assert [e["model_name"] for e in entries] == ["Gamma", "Alpha"]
    entries2, _ = lb.list_leaderboard(limit=2, offset=2)
    assert [e["model_name"] for e in entries2] == ["Beta"]


def test_list_sort_recent_uses_benchmarked_at():
    _seed()
    # Windows datetime resolution can make rapid saves identical; set distinct times
    lb._MEM["m1"]["benchmarked_at"] = "2026-01-01T00:00:00+00:00"
    lb._MEM["m2"]["benchmarked_at"] = "2026-02-01T00:00:00+00:00"
    lb._MEM["m3"]["benchmarked_at"] = "2026-03-01T00:00:00+00:00"
    entries, _ = lb.list_leaderboard(limit=10, sort="recent")
    assert [e["model_name"] for e in entries] == ["Gamma", "Beta", "Alpha"]


def test_get_remove_roundtrip():
    _seed()
    assert lb.get_submission("m1")["score"] == 80.0
    lb.remove_submission("m1")
    assert lb.get_submission("m1") is None
    assert lb.get_entry_detail("m2")["score"] == 55.0
    assert lb.get_entry_detail("nope") is None


def test_list_user_submissions_filters_by_user():
    _seed()
    lb.save_submission("m9", "someone-else", "Intruder", 10.0, 5, [])
    subs = lb.list_user_submissions("u1")
    assert set(subs) == {"m1", "m2", "m3"}
    assert subs["m1"]["score"] == 80.0


# ── HTTP routes ──────────────────────────────────────────────────────────────

def _register(c, name="lbuser"):
    c.post("/auth/register", data={"username": name, "email": f"{name}@t.com",
                                   "password": "pass123", "confirm": "pass123"})


def test_leaderboard_list_requires_login():
    c = flask_app.test_client()
    assert c.get("/api/leaderboard/models").status_code == 302


def test_leaderboard_list_empty_and_detail_404():
    c = flask_app.test_client()
    _register(c)
    r = c.get("/api/leaderboard/models")
    assert r.status_code == 200
    assert r.get_json()["entries"] == []
    assert c.get("/api/leaderboard/models/nope").status_code == 404


def test_submit_starts_benchmark_owner_only(monkeypatch):
    fake = {"id": "m1", "user_id": "dev:lbuser@t.com", "name": "Alpha", "code": "def get_ai_move(s,a): return 0,0,0"}
    monkeypatch.setattr(um, "get_model_by_id", lambda mid, requesting_user_id=None: fake)
    calls = {}
    monkeypatch.setattr(appmod, "_run_leaderboard_bench",
                        lambda *a, **k: calls.update({"args": a}))
    c = flask_app.test_client()
    # not logged in → JSON POST gets 401 (decorator), HTML GETs get 302
    assert c.post("/api/models/user/m1/submit-leaderboard", json={}).status_code == 401
    _register(c)
    # wrong owner → 404
    monkeypatch.setattr(um, "get_model_by_id",
                        lambda mid, requesting_user_id=None: {**fake, "user_id": "other"})
    assert c.post("/api/models/user/m1/submit-leaderboard", json={}).status_code == 404
    # owner → benchmark started
    monkeypatch.setattr(um, "get_model_by_id", lambda mid, requesting_user_id=None: fake)
    r = c.post("/api/models/user/m1/submit-leaderboard", json={"games": 5})
    assert r.status_code == 200
    assert r.get_json()["total_games"] == 35
    assert calls["args"] == ("m1", "dev:lbuser@t.com", "Alpha", fake["code"], 5)


def test_submit_rejects_concurrent_run(monkeypatch):
    fake = {"id": "m1", "user_id": "dev:lbuser@t.com", "name": "Alpha", "code": "x"}
    monkeypatch.setattr(um, "get_model_by_id", lambda mid, requesting_user_id=None: fake)
    monkeypatch.setattr(appmod, "_run_leaderboard_bench", lambda *a, **k: None)
    lb.set_status("m1", "running", done=1, total=35)
    c = flask_app.test_client()
    _register(c)
    assert c.post("/api/models/user/m1/submit-leaderboard", json={}).status_code == 409
    lb.clear_status("m1")


def test_status_endpoint_flows(monkeypatch):
    fake = {"id": "m1", "user_id": "dev:lbuser@t.com", "name": "Alpha", "code": "x"}
    monkeypatch.setattr(um, "get_model_by_id", lambda mid, requesting_user_id=None: fake)
    c = flask_app.test_client()
    _register(c)
    assert c.get("/api/models/user/m1/leaderboard-status").get_json()["status"] == "idle"
    lb.set_status("m1", "done", score=61.5, details=[{"opponent": "Greedy", "win_rate": 61.5}])
    st = c.get("/api/models/user/m1/leaderboard-status").get_json()
    assert st["status"] == "done" and st["score"] == 61.5
    lb.clear_status("m1")


def test_detail_route_shows_stored_breakdown():
    c = flask_app.test_client()
    _register(c)
    lb.save_submission("m1", "dev:lbuser@t.com", "Alpha", 61.5, 5,
                       [{"opponent": "Greedy", "win_rate": 61.5, "wins": 3, "draws": 1, "losses": 1, "n_games": 5}])
    d = c.get("/api/leaderboard/models/m1").get_json()
    assert d["model_name"] == "Alpha"
    assert d["score"] == 61.5
    assert d["details"][0]["opponent"] == "Greedy"
    # a private (never submitted) model is not visible anywhere
    assert c.get("/api/leaderboard/models/private-model").status_code == 404
    entries = c.get("/api/leaderboard/models").get_json()["entries"]
    assert all(e["model_id"] == "m1" for e in entries)
