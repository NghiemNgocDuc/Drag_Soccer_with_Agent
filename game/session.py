"""Per-user game state stored in Upstash Redis."""
from __future__ import annotations
import copy
import json

from db.redis_client import r
from models.soccer_logic import new_soccer_state

GAME_TTL = 86_400 * 7  # 7 days


def new_game_state(
    mode: str = "hvai",
    model_b: str = "greedy",
    model_a: str = "greedy",
    player_count: int = 3,
) -> dict:
    return new_soccer_state(mode=mode, model_b=model_b, model_a=model_a, player_count=player_count)


def get_game(user_id: str) -> dict:
    raw = r.get(f"game:{user_id}")
    if raw:
        data = json.loads(raw)
        if "ball" in data:   # valid soccer state
            return data
    state = new_game_state()
    save_game(user_id, state)
    return state


def save_game(user_id: str, state: dict) -> None:
    r.setex(f"game:{user_id}", GAME_TTL, json.dumps(state))


def delete_game(user_id: str) -> None:
    r.delete(f"game:{user_id}")


def push_snapshot(state: dict) -> None:
    snap = {
        "ball":        copy.deepcopy(state["ball"]),
        "players_a":   copy.deepcopy(state["players_a"]),
        "players_b":   copy.deepcopy(state["players_b"]),
        "score_a":     state["score_a"],
        "score_b":     state["score_b"],
        "is_player_a": state["is_player_a"],
        "kick_count":  state.get("kick_count", 0),
    }
    state["snapshots"].append(snap)
    if len(state["snapshots"]) > 30:
        state["snapshots"].pop(0)


def pop_snapshot(state: dict) -> dict | None:
    if not state["snapshots"]:
        return None
    return state["snapshots"].pop()


# ── Playground state ───────────────────────────────────────────────────────────

PG_TTL = 3_600


def new_pg_state(pg_mode: str = "human_vs_code", opponent: str = "greedy") -> dict:
    state = new_game_state()
    state["pg_mode"]     = pg_mode
    state["pg_opponent"] = opponent
    state["snapshots"]   = []
    return state


def get_pg(user_id: str) -> dict | None:
    raw = r.get(f"pg:{user_id}")
    return json.loads(raw) if raw else None


def save_pg(user_id: str, state: dict) -> None:
    r.setex(f"pg:{user_id}", PG_TTL, json.dumps(state))
