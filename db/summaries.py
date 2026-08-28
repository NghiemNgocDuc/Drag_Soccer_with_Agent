"""Post-match shareable summary cards (aggregation + persistence).

The card is a presentation layer over data other systems already produce:
the online room record (score, winner, ranked flag + `ranked_result`
deltas from db/ranked), each player's saved point-buy lineup
(db/customization), and the season their match belongs to
(db/seasons). No detection or computation is added here — everything is
a read/aggregate at match end, snapshotted into a compact record so the
card stays stable long after the 6h Redis room and later build edits.

Storage lives in one Supabase table (see migration_summaries.sql):
  - `match_summaries`  one row per online match, keyed by room_id;
                       `data` is the full snapshot (jsonb)
Keyed by room_id because that is the only stable match id a casual
room has (ranked rooms additionally get a ranked_matches id).
Degrades to an in-memory registry when Supabase is unavailable (dev /
tests), mirroring db/ranked.py / db/seasons.py.
"""
from __future__ import annotations
import json

from db.redis_client import r

# Hold-over TTL applied to the redis cache of a summary (see get_summary).
_SUMMARY_TTL = 86400 * 7


def _svc():
    from db.supabase_client import service
    return service


#  In-memory fallback (dev: no Supabase) 
_MEM: dict[str, dict] = {}


def reset_mem() -> None:
    _MEM.clear()


def save_summary(room_id: str, data: dict) -> None:
    """Upsert a match summary snapshot (idempotent by room_id)."""
    svc = _svc()
    payload = {k: v for k, v in (data or {}).items()}
    if not svc:
        _MEM[room_id] = payload
        r.setex(f"summary:{room_id}", _SUMMARY_TTL, json.dumps(payload))
        return
    try:
        svc.table("match_summaries").upsert(
            {"room_id": room_id, "data": payload}
        ).execute()
    except Exception:
        # Cache-only fallback keeps share links alive in degraded moments.
        r.setex(f"summary:{room_id}", _SUMMARY_TTL, json.dumps(payload))


def get_summary(room_id: str) -> dict | None:
    """The summary snapshot for a match, or None if unknown/expired."""
    svc = _svc()
    if not svc:
        row = _MEM.get(room_id)
        if row is not None:
            return dict(row)
        cached = r.get(f"summary:{room_id}")
        if cached:
            return json.loads(cached)
        return None
    try:
        row = (svc.table("match_summaries").select("data")
               .eq("room_id", room_id).maybe_single().execute())
        if row.data and row.data.get("data"):
            return dict(row.data["data"])
        r.delete(f"summary:{room_id}")
        return None
    except Exception:
        cached = r.get(f"summary:{room_id}")
        return json.loads(cached) if cached else None


def list_summaries(limit: int = 5000) -> list[dict]:
    """All stored summary payloads, oldest first (analytics input).

    Dev path: in-memory rows plus any Redis-cached summaries (the Redis
    cache can outlive the _MEM dict when rows were written and the
    process restarted). Production: full table read (bounded).
    """
    svc = _svc()
    if not svc:
        seen: set[str] = set()
        rows = []
        for v in _MEM.values():
            room_id = v.get("room_id")
            if room_id and room_id not in seen:
                seen.add(room_id)
                rows.append(dict(v))
        for key, val in r._store.items() if hasattr(r, "_store") else []:
            if isinstance(key, str) and key.startswith("summary:") and isinstance(val, str):
                room_id = key[len("summary:"):]
                if room_id not in seen:
                    seen.add(room_id)
                    try:
                        rows.append(json.loads(val))
                    except ValueError:
                        pass
        return rows[:limit]
    try:
        res = (svc.table("match_summaries").select("data")
               .order("created_at", desc=False).limit(limit).execute())
        return [dict(row["data"]) for row in (res.data or [])]
    except Exception:
        return []