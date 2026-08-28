"""Tutorial curriculum progress (Supabase, with dev fallback).

The Learn page is a guided, sequentially-unlocked curriculum for new
AI builders (7 lessons, see services/tutorial.py). A lesson's progress
lives here: one row per (user_id, lesson_id), written by the background
milestone-check thread once a machine-checked challenge passes.

Storage lives in one Supabase table (see migration_tutorial.sql):
  - `tutorial_progress`  (user_id, lesson_id) primary key — the
                         double-complete guard; re-running an already
                         completed lesson is a safe no-op.

Degrades to an in-memory registry when Supabase is unavailable
(dev / tests), mirroring db/ranked.py / db/leaderboard.py / db/seasons.py.

Milestone-check status (running/done/failed) is a short-lived Redis
key (`tut_bench:{user_id}:{lesson_id}`, 2 h cap) — the same pattern as
the leaderboard bench progress, so the check can run in a background
thread while the Learn page polls.
"""
from __future__ import annotations
import json
import time

from db.redis_client import r as _redis

_STATUS_TTL = 7200  # 2 h safety cap, same as leaderboard bench status


def _svc():
    from db.supabase_client import service
    return service


#  In-memory fallback (dev: no Supabase) 
_MEM: dict[str, dict[int, float]] = {}  # user_id -> {lesson_id: completed_at_epoch}


def reset_mem() -> None:
    _MEM.clear()


def get_progress(user_id: str) -> dict[int, float]:
    """{lesson_id: completed_at_epoch} for a user (empty dict if none)."""
    if not user_id:
        return {}
    svc = _svc()
    if not svc:
        return dict(_MEM.get(user_id, {}))
    try:
        rows = (svc.table("tutorial_progress")
                .select("lesson_id,completed_at").eq("user_id", user_id).execute())
        return {int(r["lesson_id"]): float(r["completed_at"])
                for r in (rows.data or [])}
    except Exception:
        return dict(_MEM.get(user_id, {}))


def is_complete(user_id: str, lesson_id: int) -> bool:
    return lesson_id in get_progress(user_id)


def mark_complete(user_id: str, lesson_id: int, completed_at: float | None = None) -> bool:
    """Record a completed lesson; returns True only on the first write.

    Idempotent re-runs (already-complete) return False.
    """
    if not user_id:
        return False
    ts = completed_at if completed_at is not None else time.time()
    svc = _svc()
    newly = not is_complete(user_id, lesson_id)
    if not svc:
        _MEM.setdefault(user_id, {})[lesson_id] = ts
        return newly
    try:
        svc.table("tutorial_progress").insert({
            "user_id": user_id, "lesson_id": int(lesson_id),
            "completed_at": float(ts),
        }).execute()
    except Exception:
        pass  # already-present (unique pk) or degraded — _MEM still records
        _MEM.setdefault(user_id, {})[lesson_id] = ts
    return newly


#  Milestone-check status (Redis-or-in-memory) 

def _status_key(user_id: str, lesson_id: int) -> str:
    return f"tut_bench:{user_id}:{lesson_id}"


def set_status(user_id: str, lesson_id: int, status: str, **fields) -> None:
    payload = {"status": status, "lesson_id": lesson_id, "user_id": user_id}
    payload.update(fields)
    _redis.setex(_status_key(user_id, lesson_id), _STATUS_TTL, json.dumps(payload))


def get_status(user_id: str, lesson_id: int) -> dict | None:
    raw = _redis.get(_status_key(user_id, lesson_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def clear_status(user_id: str, lesson_id: int) -> None:
    _redis.delete(_status_key(user_id, lesson_id))