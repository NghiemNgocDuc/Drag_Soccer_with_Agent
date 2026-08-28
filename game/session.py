"""Per-user game state stored in Upstash Redis."""
from __future__ import annotations
import copy
import json

from db.redis_client import r
from models.soccer_logic import new_soccer_state, FIELD_W, FIELD_H

GAME_TTL = 86_400 * 7  # 7 days


def _backfill_field(state: dict) -> dict:
    """Add the `field` key to states saved before it existed (additive).

    Legacy Redis/DB states lack ``state["field"]``; user models may read it.
    """
    if "field" not in state:
        state["field"] = {"width": FIELD_W, "height": FIELD_H}
    return state


def new_game_state(
    mode: str = "hvai",
    model_b: str = "greedy",
    model_a: str = "greedy",
    player_count: int = 7,
    half_length: int = 45,
    win_goal_limit: int = 5,
    power_cap: int = 100,
    formation_a: str | None = None,
    formation_b: str | None = None,
    referee_name: str | None = None,
) -> dict:
    return new_soccer_state(
        mode=mode, model_b=model_b, model_a=model_a,
        player_count=player_count,
        half_length=half_length,
        win_goal_limit=win_goal_limit,
        power_cap=power_cap,
        formation_a=formation_a,
        formation_b=formation_b,
        referee_name=referee_name,
    )


def get_game(user_id: str) -> dict:
    raw = r.get(f"game:{user_id}")
    if raw:
        data = json.loads(raw)
        if "ball" in data:   # valid soccer state
            return _backfill_field(data)
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
        "referee":     copy.deepcopy(state.get("referee")),
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


#  Playground state 

PG_TTL = 3_600


def new_pg_state(pg_mode: str = "human_vs_code", opponent: str = "greedy") -> dict:
    state = new_game_state()
    state["pg_mode"]     = pg_mode
    state["pg_opponent"] = opponent
    state["snapshots"]   = []
    return state


def get_pg(user_id: str) -> dict | None:
    raw = r.get(f"pg:{user_id}")
    if raw:
        return _backfill_field(json.loads(raw))
    return None


def save_pg(user_id: str, state: dict) -> None:
    r.setex(f"pg:{user_id}", PG_TTL, json.dumps(state))
