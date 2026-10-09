"""Regression coverage for request overhead and stalled AI work."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import copy
import os
import threading
import time

os.environ.setdefault("DEV_MODE", "1")

import pytest
from flask import session

import app as appmod
import db.friends as friends
from db.redis_client import _InMemoryFallback
from models.soccer_logic import new_soccer_state


@pytest.fixture
def presence_clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(appmod, "_presence_heartbeats", OrderedDict())
    monkeypatch.setattr(appmod, "time", SimpleNamespace(
        monotonic=lambda: now[0], time=time.time,
    ))
    return now


def heartbeat_request(user_id, path="/health"):
    with appmod.app.test_request_context(path):
        if user_id is not None:
            session["user_id"] = user_id
        appmod._presence_heartbeat()


def test_presence_skips_static_and_anonymous_requests(monkeypatch, presence_clock):
    touched = []
    monkeypatch.setattr(friends, "heartbeat", touched.append)
    heartbeat_request(None)
    heartbeat_request("player", "/static/css/design-system.css")
    heartbeat_request("player", "/static/vendor/three/three.module.js")
    assert touched == []
    assert not appmod._presence_heartbeats


def test_presence_repeated_polls_share_a_heartbeat(monkeypatch, presence_clock):
    touched = []
    monkeypatch.setattr(friends, "heartbeat", touched.append)
    heartbeat_request("player")
    for _ in range(20):
        presence_clock[0] += 0.5
        heartbeat_request("player")
    heartbeat_request("other-player")
    assert touched == ["player", "other-player"]
    presence_clock[0] = appmod._PRESENCE_HEARTBEAT_INTERVAL
    heartbeat_request("player")
    assert touched == ["player", "other-player", "player"]


def test_presence_registry_has_a_fixed_user_bound(monkeypatch, presence_clock):
    monkeypatch.setattr(appmod, "_PRESENCE_HEARTBEAT_MAX_USERS", 2)
    monkeypatch.setattr(friends, "heartbeat", lambda _uid: None)
    for user_id in ("first", "second", "third"):
        heartbeat_request(user_id)
    assert list(appmod._presence_heartbeats) == ["second", "third"]


def test_presence_status_changes_bypass_request_throttle(monkeypatch, presence_clock):
    redis = _InMemoryFallback()
    monkeypatch.setattr(friends, "_r", lambda: redis)
    heartbeat_request("player")
    assert friends.get_presence(["player"])["player"]["status"] == "online"
    friends.set_presence("player", "in_match", "room-1")
    heartbeat_request("player")
    assert friends.get_presence(["player"])["player"]["room_id"] == "room-1"
    presence_clock[0] += appmod._PRESENCE_HEARTBEAT_INTERVAL
    heartbeat_request("player")
    assert friends.get_presence(["player"])["player"]["status"] == "in_match"
    friends.set_presence("player", "online")
    heartbeat_request("player")
    assert friends.get_presence(["player"])["player"]["status"] == "online"


def test_presence_concurrent_requests_do_not_duplicate_writes(monkeypatch, presence_clock):
    started, release = threading.Event(), threading.Event()
    touched = []

    def blocked_heartbeat(user_id):
        touched.append(user_id)
        started.set()
        release.wait(2)

    monkeypatch.setattr(friends, "heartbeat", blocked_heartbeat)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(heartbeat_request, "player")
        try:
            assert started.wait(1)
            second = pool.submit(heartbeat_request, "player")
            second.result(timeout=1)
            assert touched == ["player"]
        finally:
            release.set()
        first.result(timeout=1)


def test_presence_failed_heartbeat_can_retry(monkeypatch, presence_clock):
    calls = []

    def failed_heartbeat(user_id):
        calls.append(user_id)
        raise RuntimeError("Redis unavailable")

    monkeypatch.setattr(friends, "heartbeat", failed_heartbeat)
    heartbeat_request("player")
    heartbeat_request("player")
    assert calls == ["player", "player"]


@pytest.fixture
def ai_pool(monkeypatch):
    pool = ThreadPoolExecutor(max_workers=2)
    monkeypatch.setattr(appmod, "_ai_pool", pool)
    monkeypatch.setattr(appmod, "_ai_slots", threading.BoundedSemaphore(2))
    yield pool
    pool.shutdown(wait=True, cancel_futures=True)


def test_ai_result_keeps_live_state_isolated(monkeypatch, ai_pool):
    state = new_soccer_state(player_count=7)
    before = copy.deepcopy(state)
    seen = []

    def model_move(snapshot, is_player_a):
        seen.append(snapshot)
        snapshot["ball"]["x"] = -500
        snapshot["players_a"][0]["x"] = -500
        return 6, 7.0, 88.0

    model = SimpleNamespace(get_ai_move=model_move, __name__="models.greedy_model")
    assert appmod._get_ai_move_with_timeout(model, state, True) == (6, 7.0, 88.0)
    assert state == before
    assert seen[0]["field"] == state["field"]


def test_ai_snapshot_does_not_copy_accumulated_replays(ai_pool):
    class ReplayMustNotBeCopied:
        def __deepcopy__(self, _memo):
            raise AssertionError("Replay history was copied into AI search")

    state = new_soccer_state()
    state["move_history"] = [ReplayMustNotBeCopied()]
    state["snapshots"] = [ReplayMustNotBeCopied()]
    seen = []

    def model_move(snapshot, is_player_a):
        seen.append(snapshot)
        return 2, 0.0, 80.0

    model = SimpleNamespace(get_ai_move=model_move, __name__="models.greedy_model")
    assert appmod._get_ai_move_with_timeout(model, state, True) == (2, 0.0, 80.0)
    assert seen[0]["move_history"] == []
    assert seen[0]["snapshots"] == []


def test_uploaded_ai_preserves_isolated_history_contract(ai_pool):
    state = new_soccer_state()
    state["move_history"] = [{"angle": 10.0, "trajectory": [{"x": 700.0, "y": 437.5}]}]
    state["snapshots"] = [{"ball": {"x": 700.0, "y": 437.5}}]
    before = copy.deepcopy(state)

    def user_move(snapshot, is_player_a):
        assert snapshot["move_history"] == before["move_history"]
        assert snapshot["snapshots"] == before["snapshots"]
        snapshot["move_history"][0]["trajectory"][0]["x"] = -500
        snapshot["snapshots"][0]["ball"]["x"] = -500
        return 2, snapshot["move_history"][0]["angle"], 80.0

    model = SimpleNamespace(get_ai_move=user_move)
    assert appmod._get_ai_move_with_timeout(model, state, True) == (2, 10.0, 80.0)
    assert state == before


def test_timed_out_ai_retains_capacity_and_cannot_mutate_live_state(ai_pool):
    release = threading.Event()
    completed = threading.Event()
    state = new_soccer_state(mode="aivai")
    before = copy.deepcopy(state)
    fallback_info = {}

    def blocked_model(snapshot, is_player_a):
        try:
            release.wait(2)
            snapshot["ball"]["x"] = -500
            snapshot["players_a"][0]["x"] = -500
            return 0, 0.0, 80.0
        finally:
            completed.set()

    try:
        result = appmod._get_ai_move_with_timeout(
            SimpleNamespace(get_ai_move=blocked_model), state, True,
            timeout=0.01, fallback_info=fallback_info,
        )
        assert fallback_info == {"reason": "timeout"}
        assert result == appmod._quick_ai_move(state, True)
        # One timed-out task still owns a slot; only one more is available.
        assert appmod._ai_slots.acquire(blocking=False)
        assert not appmod._ai_slots.acquire(blocking=False)
        appmod._ai_slots.release()
        assert state == before
    finally:
        release.set()
    assert completed.wait(1)
    assert state == before


def test_saturated_ai_pool_does_not_enqueue_or_run_greedy(monkeypatch, ai_pool):
    state = new_soccer_state(mode="aivai")
    assert appmod._ai_slots.acquire(blocking=False)
    assert appmod._ai_slots.acquire(blocking=False)
    model_calls = []
    monkeypatch.setattr(appmod, "_load_model", lambda _name: pytest.fail("Expensive fallback loaded"))
    fallback_info = {}
    try:
        result = appmod._get_ai_move_with_timeout(
            SimpleNamespace(get_ai_move=lambda *_args: model_calls.append(1)),
            state, True, fallback_info=fallback_info,
        )
        assert result == appmod._quick_ai_move(state, True)
        assert fallback_info == {"reason": "busy"}
        assert model_calls == []
    finally:
        appmod._ai_slots.release()
        appmod._ai_slots.release()


def test_ai_submission_failure_returns_slot(monkeypatch, ai_pool):
    class BrokenPool:
        def submit(self, *_args):
            raise RuntimeError("Executor unavailable")

    monkeypatch.setattr(appmod, "_ai_pool", BrokenPool())
    state = new_soccer_state()
    info = {}
    result = appmod._get_ai_move_with_timeout(None, state, True, fallback_info=info)
    assert result == appmod._quick_ai_move(state, True)
    assert info == {"reason": "error"}
    assert appmod._ai_slots.acquire(blocking=False)
    assert appmod._ai_slots.acquire(blocking=False)
    appmod._ai_slots.release()
    appmod._ai_slots.release()


def test_ai_response_reports_capacity_fallback(monkeypatch, ai_pool):
    state = new_soccer_state(mode="aivai")
    monkeypatch.setattr(appmod, "_load_model", lambda _name: SimpleNamespace())
    monkeypatch.setattr(appmod, "_apply_move", lambda *_args: {"trajectory": []})
    assert appmod._ai_slots.acquire(blocking=False)
    assert appmod._ai_slots.acquire(blocking=False)
    try:
        result = appmod._do_ai_move(state, "greedy", True)
        assert result["timeout_fallback"] is True
        assert result["fallback_reason"] == "busy"
        assert result["think_ms"] >= 0
    finally:
        appmod._ai_slots.release()
        appmod._ai_slots.release()


def test_penalty_ai_runs_only_penalty_physics(monkeypatch, ai_pool):
    state = new_soccer_state()
    state.update(penalty_shootout=True, period="penalties", score_a=3, score_b=2, kick_count=7)
    appmod._setup_penalty_positions(state, True)
    state["penalty_goalkeeper_move"] = "left"
    original_ball = copy.deepcopy(state["ball"])
    model = SimpleNamespace(get_ai_move=lambda *_args: (0, 0.0, 100.0))
    monkeypatch.setattr(appmod, "_load_model", lambda _name: model)
    monkeypatch.setattr(appmod, "_apply_move", lambda *_args: pytest.fail("Regular physics ran before penalty"))
    result = appmod._do_penalty_ai(state, "greedy", True)
    assert result["trajectory"]
    assert state["penalty_kick_num"] == 1
    assert len(state["penalty_kicks"]) == 1
    assert (state["score_a"], state["score_b"], state["kick_count"]) == (3, 2, 7)
    assert state["snapshots"][-1]["ball"] == original_ball
