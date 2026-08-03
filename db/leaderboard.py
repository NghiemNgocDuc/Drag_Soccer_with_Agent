"""Leaderboard storage for custom AI models (Supabase, with dev fallback).

Public ranking of user-submitted models. Rows live in `model_leaderboard`
(kept separate from `user_models` so ranked models' private `code` is never
readable via public row-level policies). Every function degrades to an
in-memory registry when Supabase is unavailable (local dev / tests).
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

from db.redis_client import r


def _svc():
    from db.supabase_client import service
    return service


# ── In-memory fallback (dev: no Supabase) ────────────────────────────────
_MEM: dict[str, dict] = {}
_MEM_SEQ = 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_submission(model_id: str, user_id: str, model_name: str,
                    score: float, games_per_opponent: int, details: list[dict]) -> None:
    row = {
        "model_id": model_id,
        "user_id": user_id,
        "model_name": model_name,
        "score": round(float(score), 1),
        "games_per_opponent": games_per_opponent,
        "details": details,
        "benchmarked_at": _now_iso(),
    }
    svc = _svc()
    if not svc:
        _MEM[model_id] = row
        return
    svc.table("model_leaderboard").upsert(row).execute()


def remove_submission(model_id: str) -> None:
    svc = _svc()
    if not svc:
        _MEM.pop(model_id, None)
        return
    try:
        svc.table("model_leaderboard").delete().eq("model_id", model_id).execute()
    except Exception:
        pass


def get_submission(model_id: str) -> dict | None:
    svc = _svc()
    if not svc:
        return _MEM.get(model_id)
    row = svc.table("model_leaderboard").select("*").eq("model_id", model_id)\
        .maybe_single().execute()
    return row.data if row and row.data else None


def get_entry_detail(model_id: str) -> dict | None:
    """Decorated single entry (same shape as list rows) or None."""
    sub = get_submission(model_id)
    if not sub:
        return None
    rows = _decorate([sub])
    return rows[0] if rows else None


def list_user_submissions(user_id: str) -> dict[str, dict]:
    """model_id → {score, benchmarked_at} for one user's submitted models."""
    svc = _svc()
    if not svc:
        return {mid: {"score": s["score"], "benchmarked_at": s["benchmarked_at"]}
                for mid, s in _MEM.items() if s.get("user_id") == user_id}
    rows = svc.table("model_leaderboard")\
        .select("model_id,score,benchmarked_at").eq("user_id", user_id)\
        .execute().data or []
    return {r["model_id"]: {"score": r.get("score"), "benchmarked_at": r.get("benchmarked_at")}
            for r in rows}


def list_leaderboard(limit: int = 20, offset: int = 0,
                     sort: str = "score") -> tuple[list[dict], int]:
    """Ranked rows (model_id, name, owner username/avatar, score, details, time).

    Returns (entries, total_count). Total is true for the in-memory path and
    best-effort (len of all rows) under Supabase via the score index.
    """
    svc = _svc()
    if not svc:
        rows = sorted(_MEM.values(),
                      key=lambda x: x["benchmarked_at"], reverse=True)
        if sort == "recent":
            pass
        else:
            rows = sorted(_MEM.values(), key=lambda x: x["score"], reverse=True)
        total = len(rows)
        page = rows[offset:offset + limit]
        return _decorate(page), total

    order_col = "benchmarked_at" if sort == "recent" else "score"
    res = svc.table("model_leaderboard")\
        .select("model_id,user_id,model_name,score,games_per_opponent,details,benchmarked_at")\
        .order(order_col, desc=True)\
        .range(offset, offset + limit - 1)\
        .execute()
    rows = list(res.data or [])
    total = len(rows) + offset  # best effort without a COUNT on Supabase JS client
    return _decorate(rows), total


def _decorate(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    svc = _svc()
    users = {r["user_id"] for r in rows}
    avatars: dict[str, str] = {}
    usernames: dict[str, str] = {}
    if svc:
        try:
            prof = svc.table("profiles").select("id,username,avatar_url")\
                .in_("id", list(users)).execute().data or []
            for p in prof:
                usernames[p["id"]] = p.get("username") or "Player"
                if p.get("avatar_url"):
                    avatars[p["id"]] = p["avatar_url"]
        except Exception:
            pass
    out = []
    for r in rows:
        out.append({
            "model_id": r["model_id"],
            "model_name": r.get("model_name", "Untitled"),
            "username": usernames.get(r.get("user_id"), "Player"),
            "avatar_url": avatars.get(r.get("user_id")),
            "score": float(r.get("score", 0)),
            "games_per_opponent": r.get("games_per_opponent", 5),
            "details": r.get("details") or [],
            "benchmarked_at": r.get("benchmarked_at"),
        })
    return out


# ── Benchmark status (Redis-or-in-memory, survives across the long run) ──────

def _status_key(model_id: str) -> str:
    return f"lb_bench:{model_id}"


def set_status(model_id: str, status: str, **fields) -> None:
    payload = {"status": status, "model_id": model_id}
    payload.update(fields)
    r.setex(_status_key(model_id), 7200, json.dumps(payload))  # 2h safety cap


def get_status(model_id: str) -> dict | None:
    raw = r.get(_status_key(model_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def clear_status(model_id: str) -> None:
    r.delete(_status_key(model_id))