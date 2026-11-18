"""Authored tables render immediately, and the bot plays the same physics."""
import os
import time
from types import SimpleNamespace

os.environ.setdefault("DEV_MODE", "1")

import pytest

from games.pack_bot import best_action
from games.pack_engine import resolve
from games.pack_spec import normalize, opening_state, presets
from games.pack_store import preview_pack


def test_every_preset_is_ready_within_the_render_budget():
    started = time.perf_counter()
    for key in presets():
        payload = preview_pack({"preset": key})
        assert payload["elapsed_ms"] < 2000
        assert payload["checks"]["deterministic"] is True
        assert payload["checks"]["bounded"] is True
        assert payload["view"]["bodies"]["ball"]
    assert time.perf_counter() - started < 20


def test_air_hockey_bot_converts_an_open_shot():
    spec = normalize({"preset": "air_hockey"})
    state = opening_state(spec)
    state["turn"] = "B"
    decision = best_action(spec, state, "B", budget_s=0.9)
    _after, _frames, info = resolve(spec, state, "B", decision["angle"], decision["power"])
    assert decision["ms"] < 1500
    assert decision["sims"] > 4
    assert info["scored_by"] == "B"


def test_pocket_bot_pots_a_straight_ball():
    spec = normalize({"preset": "pocket_table"})
    state = opening_state(spec)
    state["turn"] = "B"
    state["cue"] = {"x": 480.0, "y": 340.0, "vx": 0.0, "vy": 0.0}
    state["ball"] = {"x": 480.0, "y": 170.0, "vx": 0.0, "vy": 0.0}
    decision = best_action(spec, state, "B", budget_s=1.0)
    _after, _frames, info = resolve(spec, state, "B", decision["angle"], decision["power"])
    assert decision["ms"] < 1500
    assert info["scored_by"] == "B"
    assert info["scratch"] is False


def test_gravity_and_bumpers_stay_finite():
    for raw in ({"preset": "gravity_duel"}, {"preset": "bumper_table"},
                {"family": "strike", "name": "Heavy", "gravity": 1100, "ball_bounce": 0.4, "bumpers": 2}):
        spec = normalize(raw)
        state = opening_state(spec)
        after, frames, info = resolve(spec, state, "A", 12, 80)
        assert frames
        assert info["steps"] > 0
        for point in frames[-1]["bodies"].values():
            assert all(abs(value) < 5000 for value in point)
        assert after["turn"] in ("A", "B")


@pytest.mark.parametrize("raw", [
    {"family": "chess"},
    {"field_w": "wide"},
    {"gravity": float("nan")},
    [],
])
def test_rejected_descriptions(raw):
    with pytest.raises(ValueError):
        normalize(raw)


@pytest.mark.parametrize("angle,power", [
    (None, 20),
    (0, None),
    (0, float("nan")),
    (float("inf"), 20),
    pytest.param(10 ** 10000, 20, id="oversized-integer"),
])
def test_rejected_shot_inputs(angle, power):
    spec = normalize({"preset": "air_hockey"})
    with pytest.raises(ValueError):
        resolve(spec, opening_state(spec), "A", angle, power)


def test_same_shot_repeats():
    spec = normalize({"preset": "air_hockey"})
    state = opening_state(spec)
    first = resolve(spec, state, "A", 8, 77)
    second = resolve(spec, state, "A", 8, 77)
    assert first[2]["scored_by"] == second[2]["scored_by"]
    assert first[1][-1]["bodies"] == second[1][-1]["bodies"]


@pytest.fixture
def pack_api():
    import app as application
    with application.app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = "dev:pack-player"
        yield SimpleNamespace(client=client)


def test_api_renders_starts_and_plays(pack_api):
    client = pack_api.client
    presets_response = client.get("/api/packs/presets")
    assert presets_response.status_code == 200
    names = {item["spec"]["name"] for item in presets_response.get_json()["presets"]}
    assert "Air hockey" in names
    preview = client.post("/api/packs/preview", json={"preset": "air_hockey"})
    assert preview.status_code == 200
    body = preview.get_json()
    assert body["elapsed_ms"] < 20000
    assert float(preview.headers["X-Pack-Render-Ms"]) < 20000
    page = client.get("/packs")
    assert page.status_code == 302
    assert page.headers["Location"].endswith("/hub/play")
    started = client.post("/api/packs/start", json={"preset": "bumper_table"})
    assert started.status_code == 200
    assert started.get_json()["turn"] == "A"
    token = started.get_json()
    move = client.post("/api/packs/move", json={"angle": 0, "power": 76,
                       "match_id": token["match_id"], "expected_shot": token["shot"]})
    assert move.status_code == 200
    played = move.get_json()
    assert played["clips"][0]["label"] == "You"
    assert played["elapsed_ms"] < 20000
    assert client.post("/api/packs/preview", json={"family": "nope"}).status_code == 400
    assert client.post("/api/packs/move", json={"angle": "sideways", "power": 10}).status_code == 400


@pytest.mark.parametrize("body", [None, [], "not a table"])
def test_pack_endpoints_reject_non_object_bodies(pack_api, body):
    client = pack_api.client
    for endpoint in ("/api/packs/preview", "/api/packs/start", "/api/packs/move"):
        response = client.post(endpoint, json=body)
        assert response.status_code == 400
        assert response.get_json()["error"] == "Request body must be a JSON object."


def test_invalid_shot_does_not_consume_the_turn(pack_api):
    client = pack_api.client
    assert client.post("/api/packs/start", json={"preset": "air_hockey"}).status_code == 200
    before = client.get("/api/packs/state").get_json()

    response = client.post("/api/packs/move", json={"angle": 0, "power": float("nan"),
                           "match_id": before["match_id"], "expected_shot": before["shot"]})

    assert response.status_code == 400
    assert "finite" in response.get_json()["error"]
    after = client.get("/api/packs/state").get_json()
    assert after["shot"] == before["shot"]
    assert after["turn"] == before["turn"]


@pytest.mark.parametrize("preset", list(presets()))
def test_canonical_spec_round_trip_preserves_physics(preset):
    spec = normalize({"preset": preset})
    assert normalize(spec) == spec
    edited = normalize({**spec, "ball_r": 19})
    assert edited["ball"]["r"] == 19
    assert edited["ball"]["mass"] == spec["ball"]["mass"]
    assert edited["felt"] == spec["felt"]


def test_unknown_preset_is_not_silently_replaced():
    with pytest.raises(ValueError, match="known table"):
        normalize({"preset": "missing"})


def test_stale_duplicate_and_previous_match_shots_are_rejected(pack_api):
    client = pack_api.client
    initial = client.post("/api/packs/start", json={"preset": "air_hockey"}).get_json()
    shot = {"angle": 0, "power": 60, "match_id": initial["match_id"], "expected_shot": 0}
    assert client.post("/api/packs/move", json=shot).status_code == 200
    before = client.get("/api/packs/state").get_json()
    assert client.post("/api/packs/move", json=shot).status_code == 409
    assert client.get("/api/packs/state").get_json() == before
    replacement = client.post("/api/packs/start", json={"preset": "pocket_table"}).get_json()
    assert replacement["match_id"] != initial["match_id"]
    assert client.post("/api/packs/move", json=shot).status_code == 409
    assert client.get("/api/packs/state").get_json()["shot"] == 0


def test_concurrent_duplicate_moves_only_commit_once():
    from concurrent.futures import ThreadPoolExecutor
    from games.pack_store import start_pack, play_turn, pack_state
    initial = start_pack("dev:concurrent-pack", {"preset": "air_hockey"})
    def shot():
        try:
            return play_turn("dev:concurrent-pack", 0, 60, initial["match_id"], 0)
        except LookupError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: shot(), range(2)))
    accepted = [result for result in results if result is not None]
    assert len(accepted) == 1
    assert pack_state("dev:concurrent-pack")["shot"] == accepted[0]["shot"]


def test_preview_never_replaces_saved_match(pack_api):
    client = pack_api.client
    client.post("/api/packs/start", json={"preset": "air_hockey"})
    before = client.get("/api/packs/state").get_json()
    assert client.post("/api/packs/preview", json={"preset": "pocket_table"}).status_code == 200
    assert client.get("/api/packs/state").get_json() == before


def test_repeated_bot_own_goals_cannot_strand_the_turn(monkeypatch):
    from games import pack_store
    initial = pack_store.start_pack("dev:own-goals", {"preset": "air_hockey", "win_score": 4})
    def own_goal(spec, state, side, angle, power):
        state = dict(state, shot=state["shot"] + 1, turn="B")
        if side == "B":
            state["score_a"] += 1
            if state["score_a"] == spec["win_score"]:
                state.update(game_over=True, winner="A")
        return state, [], {"scored_by": "A" if side == "B" else None}
    monkeypatch.setattr(pack_store, "resolve", own_goal)
    monkeypatch.setattr(pack_store, "best_action", lambda *args: {"angle": 0, "power": 30})
    result = pack_store.play_turn("dev:own-goals", 0, 30, initial["match_id"], 0)
    assert result["game_over"] and result["winner"] == "A"
    assert result["score_a"] == 4


def test_six_bumpers_supported_by_the_editor():
    assert len(normalize({"bumpers": 6})["bumpers"]) == 6
