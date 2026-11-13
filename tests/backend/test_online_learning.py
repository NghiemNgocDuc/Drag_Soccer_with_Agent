"""Authoritative experience capture, bounded storage and promotion integration."""
import copy
import json
import threading
from types import SimpleNamespace

import pytest

import app as appmod
from db.redis_client import _InMemoryFallback
from models import experience_policy as policy
from models import soccer_logic as physics
from services import learning_fitness as fitness
from services import online_learning as learning


@pytest.fixture(autouse=True)
def store(monkeypatch):
    redis = _InMemoryFallback()
    monkeypatch.setattr(learning, "_redis", lambda: redis)
    monkeypatch.setattr(learning, "_local_store_lock", threading.RLock())
    monkeypatch.setattr(learning, "_local_train_lock", threading.Lock())
    monkeypatch.setattr(learning, "_manual_pending", threading.Lock())
    monkeypatch.setattr(learning, "_last_refresh", float("-inf"))
    monkeypatch.setattr(policy, "_active_model", None)
    monkeypatch.delenv("AI_TRAINING_TOKEN", raising=False)
    return redis


def room():
    return {"game": physics.new_soccer_state(mode="hvh", player_count=5, half_length=0),
            "player_a": "private-player-a", "player_b": "private-player-b",
            "name_a": "Private Alice", "name_b": "Private Bob", "chat": "private chat"}


def trajectory(game, x=900):
    return [{"x": x, "y": 430.0, "z": 0,
             "a": copy.deepcopy(game["players_a"]), "b": copy.deepcopy(game["players_b"])}]


def completed_room():
    value = room()
    before = learning.prepare_transition(value["game"])
    for _ in range(learning.MIN_MOVES):
        learning.record_transition(value, before, trajectory(value["game"]), None)
    value["game"].update(game_over=True, winner="A")
    return value


def match(identifier):
    return {"id": identifier, "sport": "soccer", "rules_version": policy.RULES_VERSION,
            "winner": "A", "samples": []}


def test_capture_preserves_both_perspectives_and_score_convention():
    value = room()
    original = copy.deepcopy(value["game"])
    before = learning.prepare_transition(value["game"])
    assert value["game"] == original
    value["game"]["score_a"] = 1
    end = trajectory(value["game"])
    learning.record_transition(value, before, end, "A")
    rows = value["_learning"]["samples"]
    assert [row["side"] for row in rows] == ["A", "B"]
    for row in rows:
        assert row["before"] == before[row["side"]]
        expected = list(policy.compact_features(value["game"], row["side"], endpoint=end[-1]))
        expected[9] = row["before"][9]
        assert row["after"] == expected
        assert row["after"][9] == 0
        assert row["scored"] == "A"
    assert value["_learning"]["moves"] == 1


@pytest.mark.parametrize("change", [
    {"sport": "basketball"}, {"penalty_shootout": True}, {"game_over": True},
    {"power_cap": 55}, {"ball_type": "beach"}, {"ball_size": "giant"},
    {"ball_bounciness": "high"},
])
def test_unsupported_game_profiles_are_not_captured(change):
    state = room()["game"]
    state.update(change)
    assert learning.prepare_transition(state) is None


def test_non_five_player_rosters_are_not_captured():
    state = physics.new_soccer_state(player_count=3)
    assert learning.prepare_transition(state) is None


@pytest.mark.parametrize("tracks", [[], [{"x": 800, "y": 430}], [{"a": [], "b": None}]])
def test_missing_authoritative_terminal_tracks_do_not_invent_experience(tracks):
    value = room()
    learning.record_transition(value, learning.prepare_transition(value["game"]), tracks, None)
    assert "_learning" not in value


def test_capture_is_bounded_and_identity_free():
    value = room()
    before = learning.prepare_transition(value["game"])
    for _ in range(learning.MAX_SAMPLES):
        learning.record_transition(value, before, trajectory(value["game"]), None)
    capture = value["_learning"]
    assert len(capture["samples"]) == learning.MAX_SAMPLES
    assert capture["moves"] == learning.MAX_SAMPLES
    serialized = json.dumps(capture)
    for private in ("private-player", "Private Alice", "private chat", "players_a", "trajectory"):
        assert private not in serialized
    assert all(set(row) == {"side", "before", "after", "scored"} for row in capture["samples"])
    assert all(len(row[key]) == policy.FEATURE_COUNT for row in capture["samples"] for key in ("before", "after"))


@pytest.mark.parametrize("change,scope", [
    ({"game_over": False}, "game"), ({"sport": "basketball"}, "game"),
    ({"termination": "forfeit"}, "game"), ({"termination": "cancelled"}, "game"),
    ({"penalty_shootout": True}, "game"), ({"winner": None}, "game"),
    ({"player_b": "private-player-a"}, "room"), ({"player_b": None}, "room"),
    ({"learning_queued": True}, "room"),
])
def test_only_eligible_completed_human_matches_are_submitted(change, scope, monkeypatch):
    value = completed_room()
    (value["game"] if scope == "game" else value).update(change)
    monkeypatch.setattr(learning, "_submit", lambda *_: pytest.fail("Ineligible match queued"))
    assert learning.completed_match("room-id", value) is False


def test_short_or_old_rules_match_is_not_submitted(monkeypatch):
    value = completed_room()
    monkeypatch.setattr(learning, "_submit", lambda *_: pytest.fail("Ineligible match queued"))
    value["_learning"]["moves"] = learning.MIN_MOVES - 1
    assert learning.completed_match("room-id", value) is False
    value["_learning"].update(moves=learning.MIN_MOVES, rules_version="old-rules")
    assert learning.completed_match("room-id", value) is False


def test_completion_detaches_packet_and_deduplicates_retries(monkeypatch):
    value = completed_room()
    queued = []
    monkeypatch.setattr(learning, "_submit", lambda function, packet: queued.append(packet) or True)
    assert learning.completed_match("private-room-invitation", value)
    assert learning.completed_match("private-room-invitation", value) is False
    assert len(queued) == 1
    packet = queued[0]
    assert len(packet["id"]) == 24
    assert "private" not in json.dumps(packet)
    preserved = copy.deepcopy(packet)
    value["_learning"]["samples"][0]["before"][0] = 999
    assert packet == preserved


def test_failed_queue_submission_can_be_retried(monkeypatch):
    value = completed_room()
    monkeypatch.setattr(learning, "_submit", lambda *_: False)
    assert learning.completed_match("room-id", value) is False
    assert "learning_queued" not in value
    monkeypatch.setattr(learning, "_submit", lambda *_: True)
    assert learning.completed_match("room-id", value)


def test_corpus_is_deduplicated_and_bounded_to_200_matches(store):
    assert learning._store_match(match("first"))
    assert learning._store_match(match("first")) is False
    assert learning._index(store)["revision"] == 1
    for index in range(learning.MAX_MATCHES + 3):
        assert learning._store_match(match(f"match-{index}"))
    index = learning._index(store)
    assert len(index["ids"]) == learning.MAX_MATCHES
    assert len(set(index["ids"])) == learning.MAX_MATCHES
    assert index["revision"] == learning.MAX_MATCHES + 4
    assert store.get(learning.PREFIX + "match:first") is None
    assert store.get(learning.PREFIX + "match:match-0") is None
    assert len(learning._load_matches(store, index)) == learning.MAX_MATCHES


@pytest.mark.parametrize("statistical_pass,physical_pass", [(False, True), (True, False), (True, True)])
def test_promotion_requires_both_independent_gates(store, monkeypatch, statistical_pass, physical_pass):
    old = {"id": "incumbent"}
    store.setex(learning.ACTIVE_KEY, learning.TTL, json.dumps(old))
    learning._store_match(match("training-match"))
    candidate = {"id": "candidate"}
    mocked_fit = {"candidate": candidate if statistical_pass else None,
                  "promoted": statistical_pass, "reason": "test-gate", "metrics": {}}
    monkeypatch.setattr(policy, "fit_candidate", lambda matches, incumbent: mocked_fit)
    evaluations, activations = [], []
    monkeypatch.setattr(fitness, "evaluate_candidate", lambda new, incumbent:
                        evaluations.append((new, incumbent)) or
                        {"passed": physical_pass, "version": fitness.FITNESS_VERSION})
    monkeypatch.setattr(policy, "set_active", lambda value: activations.append(value))
    result = learning.train_now()
    should_promote = statistical_pass and physical_pass
    assert result["promoted"] is should_promote
    assert bool(evaluations) is statistical_pass
    assert bool(activations) is should_promote
    deployed = json.loads(store.get(learning.ACTIVE_KEY))
    assert deployed["id"] == ("candidate" if should_promote else "incumbent")
    if should_promote:
        assert deployed["fitness"]["passed"] is True
    assert json.loads(store.get(learning.REPORT_KEY))["promoted"] is should_promote


@pytest.mark.parametrize("stage", ["fit", "physics"])
def test_failed_training_retains_incumbent_and_releases_lock(store, monkeypatch, stage):
    old = json.dumps({"id": "healthy"})
    store.setex(learning.ACTIVE_KEY, learning.TTL, old)
    activations = []
    monkeypatch.setattr(policy, "set_active", lambda value: activations.append(value))

    def broken(*args, **kwargs):
        raise RuntimeError("Training failed")

    monkeypatch.setattr(policy, "fit_candidate", broken if stage == "fit" else
                        lambda *a, **k: {"candidate": {"id": "new"}, "promoted": True, "metrics": {}})
    monkeypatch.setattr(fitness, "evaluate_candidate", broken)
    with pytest.raises(RuntimeError, match="Training failed"):
        learning.train_now()
    assert store.get(learning.ACTIVE_KEY) == old
    assert activations == []
    assert not learning._local_train_lock.locked()


def test_expired_cross_worker_lease_cannot_commit_or_reset(store):
    calls = []
    store.eval = lambda *args: calls.append(args) or 0
    store.setex(learning.ACTIVE_KEY, learning.TTL, "healthy")
    lease = SimpleNamespace(local=SimpleNamespace(token=b"expired-owner"))
    for reset in (False, True):
        with pytest.raises(RuntimeError, match="lease expired"):
            learning._commit(store, lease, {"promoted": True}, {"id": "new"}, reset=reset)
    assert len(calls) == 2
    assert store.get(learning.ACTIVE_KEY) == "healthy"
    assert store.get(learning.REPORT_KEY) is None


def test_public_status_excludes_corpus_identifiers(store):
    learning._store_match(match("secret-match-id"))
    store.setex(learning.REPORT_KEY, learning.TTL, json.dumps({
        "metrics": {"train_ids": ["secret-match-id"], "holdout_ids": ["other-id"], "candidate_mse": .2}}))
    before = store.get(learning.REPORT_KEY)
    status = learning.status()
    assert status["stored_matches"] == 1
    assert status["status"] == "collecting"
    assert status["last_training"]["metrics"] == {"candidate_mse": .2}
    assert "secret-match-id" not in json.dumps(status)
    assert store.get(learning.REPORT_KEY) == before


def test_refresh_and_reset_share_promotion_lock_and_do_not_reactivate_old_model(store, monkeypatch):
    model = {"id": "old", "fitness": {"passed": True, "version": fitness.FITNESS_VERSION}}
    store.setex(learning.ACTIVE_KEY, learning.TTL, json.dumps(model))
    activations = []

    def activate(value):
        assert learning._local_train_lock.locked()
        activations.append(value)

    monkeypatch.setattr(policy, "set_active", activate)
    learning._refresh()
    learning.reset_policy()
    learning._refresh()
    assert activations == [model, None, None]
    assert store.get(learning.ACTIVE_KEY) is None


@pytest.mark.parametrize("stored", ["broken-json", "[]", '"wrong-type"', '{"fitness":{"passed":false}}'])
def test_corrupt_or_unvalidated_refresh_keeps_cached_policy(store, monkeypatch, stored):
    store.setex(learning.ACTIVE_KEY, learning.TTL, stored)
    monkeypatch.setattr(policy, "set_active", lambda *_: pytest.fail("Corrupt storage replaced cached model"))
    learning._refresh()


def test_refresh_storage_failure_keeps_cached_policy(monkeypatch):
    monkeypatch.setattr(learning, "_redis", lambda: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(policy, "set_active", lambda *_: pytest.fail("Unavailable storage replaced cached model"))
    learning._refresh()


def test_manual_training_admission_is_bounded_and_recoverable(monkeypatch):
    work = []
    monkeypatch.setattr(learning, "_submit", lambda function: work.append(function) or True)
    monkeypatch.setattr(learning, "train_now", lambda: {})
    assert learning.request_training()
    assert learning.request_training() is False
    assert len(work) == 1
    work.pop()()
    assert learning.request_training()
    work.pop()()
    assert not learning._manual_pending.locked()


def test_failed_submission_releases_manual_admission(monkeypatch):
    monkeypatch.setattr(learning, "_submit", lambda *_: False)
    assert learning.request_training() is False
    assert not learning._manual_pending.locked()


def test_worker_queue_has_four_slots_and_returns_them_on_completion(monkeypatch):
    callbacks = []
    executor = SimpleNamespace(submit=lambda *args: SimpleNamespace(
        add_done_callback=lambda callback: callbacks.append(callback)))
    monkeypatch.setattr(learning, "_slots", threading.BoundedSemaphore(4))
    monkeypatch.setattr(learning, "_executor", executor)
    for _ in range(4):
        assert learning._submit(lambda: None)
    assert learning._submit(lambda: None) is False
    callbacks.pop()(None)
    assert learning._submit(lambda: None)
    for callback in callbacks:
        callback(None)


def test_refresh_is_scheduled_at_most_once_per_30_seconds(monkeypatch):
    now, work = [10.0], []
    monkeypatch.setattr(learning.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(learning, "_submit", lambda function: work.append(function) or True)
    learning.schedule_refresh()
    now[0] = 25
    learning.schedule_refresh()
    assert len(work) == 1
    now[0] = 40
    learning.schedule_refresh()
    assert len(work) == 2


def test_training_cadence_uses_new_completed_matches_and_ignores_duplicates(store, monkeypatch):
    trained = []

    def train():
        index = learning._index(store)
        trained.append(index["revision"])
        store.setex(learning.REPORT_KEY, learning.TTL, json.dumps({"revision": index["revision"]}))

    monkeypatch.setattr(learning, "train_now", train)
    for index in range(30):
        learning._store_and_maybe_train(match(f"cadence-{index}"))
    assert trained == [20, 30]
    learning._store_and_maybe_train(match("cadence-29"))
    assert trained == [20, 30]


@pytest.mark.parametrize("raw", ["[]", "bad-json", '{"ids":["a","a",null,3,""],"revision":"bad"}'])
def test_corrupt_index_and_report_are_normalized(store, raw):
    store.setex(learning.INDEX_KEY, learning.TTL, raw)
    store.setex(learning.REPORT_KEY, learning.TTL, "[]")
    status = learning.status()
    assert status["stored_matches"] <= 1
    assert status["last_training"] == {}
    assert learning._index(store)["revision"] == 0


def test_refresh_skips_while_promotion_owns_lease(monkeypatch):
    monkeypatch.setattr(policy, "set_active", lambda *_: pytest.fail("Busy lease changed cached policy"))
    learning._local_train_lock.acquire()
    try:
        learning._refresh()
    finally:
        learning._local_train_lock.release()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_dev_mode", True)
    monkeypatch.setattr(learning, "request_training", lambda: True)
    return appmod.app.test_client()


def sign_in(client, user="dev:learning-operator"):
    with client.session_transaction() as session:
        session["user_id"] = user
        session["username"] = "Learning operator"


@pytest.mark.parametrize("endpoint", ["train", "reset"])
def test_anonymous_global_mutation_is_denied(client, endpoint):
    response = client.post(f"/api/learning/{endpoint}", json={})
    assert response.status_code == 403


@pytest.mark.parametrize("payload", [None, [], "bad", 3])
def test_operator_training_rejects_non_object_options(client, payload):
    sign_in(client)
    response = client.post("/api/learning/train", json=payload)
    assert response.status_code == 400


def test_operator_training_rejects_basketball_data_request(client):
    sign_in(client)
    response = client.post("/api/learning/train", json={"sport": "basketball"})
    assert response.status_code == 400


def test_dev_signed_in_training_queues_and_busy_returns_409(client, monkeypatch):
    sign_in(client)
    response = client.post("/api/learning/train", json={})
    assert response.status_code == 202
    assert response.get_json()["source"] == "completed_online_matches"
    monkeypatch.setattr(learning, "request_training", lambda: False)
    assert client.post("/api/learning/train", json={}).status_code == 409


def test_production_requires_exact_operator_secret_even_for_signed_in_user(client, monkeypatch):
    sign_in(client)
    monkeypatch.setattr(appmod, "_dev_mode", False)
    monkeypatch.setenv("AI_TRAINING_TOKEN", "private-operator-token")
    assert client.post("/api/learning/train", json={}).status_code == 403
    assert client.post("/api/learning/train", json={}, headers={"X-AI-Training-Token": "wrong"}).status_code == 403
    allowed = client.post("/api/learning/train", json={}, headers={"X-AI-Training-Token": "private-operator-token"})
    assert allowed.status_code == 202


def test_production_secret_can_authorize_headless_operator(client, monkeypatch):
    monkeypatch.setattr(appmod, "_dev_mode", False)
    monkeypatch.setenv("AI_TRAINING_TOKEN", "headless-token")
    assert client.post("/api/learning/train", json={}, headers={"X-AI-Training-Token": "headless-token"}).status_code == 202


def test_public_status_remains_read_only_and_operator_reset_works(client, store):
    response = client.get("/api/learning/status")
    assert response.status_code == 200
    assert response.get_json()["experience"]["source"] == "completed_online_matches"
    sign_in(client)
    store.setex(learning.ACTIVE_KEY, learning.TTL, json.dumps({"id": "old"}))
    assert client.post("/api/learning/reset", json={}).status_code == 200
    assert store.get(learning.ACTIVE_KEY) is None


def test_operator_reset_reports_busy_promotion(client, monkeypatch):
    sign_in(client)

    def busy():
        raise RuntimeError("Learning update already running")

    monkeypatch.setattr(learning, "reset_policy", busy)
    response = client.post("/api/learning/reset", json={})
    assert response.status_code == 409


def test_online_endpoint_captures_once_and_denies_spectator_and_stale_retry(client, monkeypatch):
    value = room()
    value.update(status="active", player_a="dev:learning-operator", player_b="dev:other", move_count=0)
    value["game"]["match_started"] = True
    sign_in(client)
    monkeypatch.setattr(appmod, "_get_room", lambda _: value)
    monkeypatch.setattr(appmod, "_save_room", lambda *_: None)
    monkeypatch.setattr(appmod, "_advance_online_clock", lambda *_: False)
    monkeypatch.setattr(appmod, "_mem_short", lambda *_: None)
    monkeypatch.setattr(appmod, "_ach_toasts", lambda: [])
    monkeypatch.setattr(appmod, "_goal_moment_achievements", lambda *_: None)
    # Exercise the real physics/trajectory integration once.
    response = client.post("/online/test-learning-room/move", json={"player_idx": 4, "angle": 0, "power": 40, "expected_move_count": 0})
    assert response.status_code == 200, response.get_json()
    assert value["_learning"]["moves"] == 1
    assert len(value["_learning"]["samples"]) == 2
    stale = client.post("/online/test-learning-room/move", json={"expected_move_count": 0})
    assert stale.status_code == 409
    assert value["_learning"]["moves"] == 1
    sign_in(client, "dev:spectator")
    denied = client.post("/online/test-learning-room/move", json={"expected_move_count": 1})
    assert denied.status_code == 403
    assert value["_learning"]["moves"] == 1
