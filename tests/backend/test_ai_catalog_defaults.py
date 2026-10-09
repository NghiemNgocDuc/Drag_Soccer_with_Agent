"""Regression coverage for routes that implicitly select a built-in opponent."""
import os
import re

os.environ.setdefault("DEV_MODE", "1")

import pytest

import app as appmod
import models.soccer_logic as soccer
import services.game_analytics as analytics


@pytest.fixture
def client():
    with appmod.app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = "dev:catalog-default-tests"
            session["username"] = "Catalog Tester"
        yield client


def test_model_catalog_matches_runnable_registry():
    assert {model["id"] for model in analytics.MODEL_CATALOG} == set(appmod.MODELS)
    assert "expectimax" in appmod.MODELS


def test_benchmark_without_model_fields_uses_supported_opponents(client, monkeypatch):
    # Skip match computation while retaining the real registry/import path.
    monkeypatch.setattr(soccer, "new_soccer_state", lambda: {"game_over": True})
    response = client.post("/benchmark", json={"games": 1})
    assert response.status_code == 200
    assert response.json["model_a"] == "greedy"
    assert response.json["model_b"] == "expectimax"
    assert response.json["draws"] == 1


def test_arena_without_model_fields_loads_supported_opponents(client, monkeypatch):
    loaded = []

    def fake_battle(model_a, model_b, n_games, tracer=None):
        loaded.extend([model_a, model_b])
        return {"n_games": n_games}

    monkeypatch.setattr(analytics, "run_model_battle", fake_battle)
    response = client.post("/api/arena/battle", json={"games": 1})
    assert response.status_code == 200
    assert [model.__name__ for model in loaded] == [
        "models.expectimax", "models.greedy_model",
    ]


def test_legacy_benchmark_counts_a_score_lead_at_the_kick_cap(client, monkeypatch):
    monkeypatch.setattr(soccer, "new_soccer_state", lambda: {
        "game_over": False, "winner": None, "score_a": 2, "score_b": 1,
        "is_player_a": True, "kick_count": 0,
    })
    # This checks match completion at the kick cap; decision physics is covered
    # separately with complete states in test_ai_search_budget.py.
    monkeypatch.setattr(appmod, "get_model_move", lambda *args: (0, 0, 0))

    def kick(state, *args):
        state["is_player_a"] = not state["is_player_a"]
        state["kick_count"] += 1

    monkeypatch.setattr(soccer, "apply_kick", kick)
    response = client.post("/benchmark", json={"games": 1})
    assert response.status_code == 200
    assert response.json["avg_kicks"] == 30
    assert response.json["wins_a"] == 1
    assert response.json["draws"] == 0
    assert response.json["win_rate_a"] == 100


@pytest.mark.parametrize("path", ["/benchmark", "/api/arena/battle"])
def test_explicit_retired_opponent_is_rejected(client, path):
    response = client.post(path, json={"model_a": "minimax", "model_b": "greedy", "games": 1})
    assert response.status_code == 400


@pytest.mark.parametrize("path,selector", [
    ("/arena", "model-a"),
    ("/playground", "opponent-sel"),
])
def test_default_selectors_have_a_supported_selected_option(client, path, selector):
    response = client.get(path)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    select = re.search(r'<select[^>]*id="' + selector + r'"[^>]*>(.*?)</select>', html, re.S)
    assert select
    assert re.search(r'<option\s+value="expectimax"\s+selected\b', select[1])
