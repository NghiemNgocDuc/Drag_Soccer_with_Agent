"""One authored table per player. The picture and the bot share resolve()."""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from uuid import uuid4

from db.redis_client import r
from games.pack_bot import best_action
from games.pack_engine import check_table, present, resolve
from games.pack_spec import RENDER_BUDGET_S, native_games, normalize, opening_state, presets

_TTL = 60 * 60 * 6
_LOCKS = tuple(threading.Lock() for _ in range(64))


@contextmanager
def _write_lock(user_id):
    """Serialize starts and shots across production workers and local threads."""
    if hasattr(r, "lock"):
        lock = r.lock(f"packwrite:{user_id}", timeout=120, blocking_timeout=1)
        acquired = lock.acquire(blocking=True)
    else:
        lock = _LOCKS[hash(user_id) % len(_LOCKS)]
        acquired = lock.acquire(timeout=1)
    if not acquired:
        raise LookupError("A table update is in progress. Refresh the match and try again.")
    try:
        yield
    finally:
        lock.release()


def presets_payload() -> dict:
    return {
        "presets": [{"id": key, "spec": spec} for key, spec in presets().items()],
        "native": native_games(),
        "render_budget_s": RENDER_BUDGET_S,
    }


def preview_pack(raw: dict) -> dict:
    started = time.perf_counter()
    spec = normalize(raw)
    checks = check_table(spec)
    elapsed_ms = (time.perf_counter() - started) * 1000
    if elapsed_ms > RENDER_BUDGET_S * 1000:
        raise ValueError("This table took longer than 20 seconds to prepare.")
    state = opening_state(spec)
    return {
        "ok": True,
        "spec": spec,
        "view": present(spec, state),
        "checks": checks,
        "elapsed_ms": round(elapsed_ms, 1),
        "render_budget_s": RENDER_BUDGET_S,
    }


def start_pack(user_id: str, raw: dict) -> dict:
    started = time.perf_counter()
    spec = normalize(raw)
    check_table(spec)
    state = opening_state(spec)
    match = {"spec": spec, "state": state, "match_id": uuid4().hex}
    elapsed_ms = (time.perf_counter() - started) * 1000
    if elapsed_ms > RENDER_BUDGET_S * 1000:
        raise ValueError("This table took longer than 20 seconds to prepare.")
    with _write_lock(user_id):
        _save(user_id, match)
    payload = present(spec, state)
    payload["elapsed_ms"] = round(elapsed_ms, 1)
    payload["spec"] = spec
    payload["match_id"] = match["match_id"]
    return payload


def pack_state(user_id: str) -> dict | None:
    match = _load(user_id)
    if not match:
        return None
    view = present(match["spec"], match["state"])
    view["spec"] = match["spec"]
    view["match_id"] = match.get("match_id")
    return view


def play_turn(user_id: str, angle, power, match_id=None, expected_shot=None) -> dict:
    if not isinstance(match_id, str) or not match_id or type(expected_shot) is not int or expected_shot < 0:
        raise ValueError("Refresh the match before taking a shot.")
    with _write_lock(user_id):
        match = _load(user_id)
        if not match:
            raise LookupError("Start a table before you take a shot.")
        spec, state = match["spec"], match["state"]
        if match.get("match_id") != match_id or state["shot"] != expected_shot:
            raise LookupError("The match changed in another tab. Refresh it before shooting.")
        if state.get("game_over"):
            raise ValueError("This table is already finished.")
        if state.get("turn") != "A":
            raise ValueError("The bot is still at the table.")
        started = time.perf_counter()
        clips = []
        state, frames, info = resolve(spec, state, "A", angle, power)
        clips.append(_clip("You", frames, info))
        bot = None
        extras = 0
        # A bot own-goal gives it the next kickoff. Each retained turn awards
        # the human a point, so the score target bounds this loop without ever
        # leaving a live table stuck on the bot's turn.
        while state.get("turn") == "B" and not state.get("game_over") and extras <= spec["win_score"]:
            decision = best_action(spec, state, "B")
            state, frames, info = resolve(spec, state, "B", decision["angle"], decision["power"])
            clips.append(_clip("Bot", frames, info))
            bot = decision
            extras += 1
        match["state"] = state
        _save(user_id, match)
        view = present(spec, state)
        view["clips"] = clips
        view["bot"] = bot
        view["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        view["match_id"] = match["match_id"]
        return view


def _clip(label: str, frames: list[dict], info: dict) -> dict:
    return {
        "label": label,
        "frames": frames,
        "scored_by": info.get("scored_by"),
        "scratch": bool(info.get("scratch")),
        "potted": bool(info.get("potted")),
        "steps": info.get("steps", 0),
    }


def _key(user_id: str) -> str:
    return f"packmatch:{user_id}"


def _load(user_id: str) -> dict | None:
    raw = r.get(_key(user_id))
    if not raw:
        return None
    data = json.loads(raw)
    if not isinstance(data, dict) or "spec" not in data or "state" not in data:
        return None
    return data


def _save(user_id: str, match: dict) -> None:
    r.setex(_key(user_id), _TTL, json.dumps(match))
