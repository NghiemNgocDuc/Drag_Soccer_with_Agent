"""Decision traces — durable per-turn AI decisions for loss analysis.

One Supabase table (see migration_decision_traces.sql):
  - `decision_traces`  one row per traced turn. The unique (match_id, turn)
                       constraint makes re-saves idempotent (tournament sims
                       can be re-run / re-cached without duplication).

Rows are written by the server (service role) from the sim call sites in
app.py whenever a *logged-in user's own model* participates (arena battle,
leaderboard benchmark, tournament sim). Retention (confirmed with user):
traces older than 30 days are pruned, and each owner keeps at most ~200
recent traced matches.

All functions degrade to an in-memory registry when Supabase is unavailable
(local dev / tests).
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta

MAX_AGE_DAYS = 30
MAX_MATCHES_PER_OWNER = 200
_MAX_SUPABASE_ROWS = 5000  # hard cap on any single owner-read


def _svc():
    from db.supabase_client import service
    return service


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


#  In-memory fallback (dev: no Supabase) 
_MEM: list[dict] = []          # rows, newest appended last
_MEM_HORIZON: float = 0.0      # last age-prune time (monotonic)

_LAST_PRUNE: float = 0.0       # global lazy-prune throttle
import time as _time


def _fmt(row: dict, *, with_snapshot: bool = True) -> dict:
    out = dict(row)
    out["created_at"] = row.get("created_at", _now_iso())
    if not with_snapshot:
        out.pop("state_snapshot", None)
    return out


#  Write 

def save_trace(row: dict) -> None:
    """Insert/overwrite one trace row; lazy-prunes once in a while.

    `row` must contain owner_id, match_id, model_id, model_label, opponent,
    result, score_for, score_against, turn, mover, decision, state_snapshot,
    outcome_tag. Failures are swallowed by the caller (services/loss_analysis)
    — tracing must never break a live match.
    """
    row = dict(row)
    # Always stamp "now": deterministic match ids get re-saved on re-runs,
    # and a fresh timestamp keeps the owner's match list correctly ordered.
    row["created_at"] = _now_iso()
    svc = _svc()
    try:
        if svc:
            svc.table("decision_traces").upsert(
                row, on_conflict="match_id,turn").execute()
        else:
            # Replace any existing row with the same (match_id, turn).
            for i, r in enumerate(_MEM):
                if r["match_id"] == row["match_id"] and r["turn"] == row["turn"]:
                    _MEM[i] = row
                    break
            else:
                _MEM.append(row)
    except Exception:
        pass  # caller swallows anyway
    _lazy_prune()


def _lazy_prune() -> None:
    global _LAST_PRUNE
    now = _time.time()
    if now - _LAST_PRUNE < 300:  # at most once per 5 minutes
        return
    _LAST_PRUNE = now
    try:
        prune_expired()
    except Exception:
        pass


#  Pruning (30-day window + per-owner cap) 

def prune_expired(max_age_days: int = MAX_AGE_DAYS,
                  max_matches: int = MAX_MATCHES_PER_OWNER) -> int:
    """Delete traces older than `max_age_days` and trim each owner to the
    `max_matches` most recent distinct matches. Returns rows deleted."""
    deleted = 0
    svc = _svc()
    try:
        if not svc:
            global _MEM
            horizon = datetime.now(timezone.utc) - timedelta(days=max_age_days)
            kept: list[dict] = []
            for r in _MEM:
                try:
                    ts = datetime.fromisoformat(r["created_at"])
                except (ValueError, TypeError):
                    ts = datetime.now(timezone.utc)
                if ts < horizon:
                    deleted += 1
                    continue
                kept.append(r)
            # Trim per-owner match surplus (keep newest matches).
            _MEM, dropped = _trim_owner_surplus(kept, max_matches)
            return deleted + dropped

        horizon = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
        res = (svc.table("decision_traces")
               .lt("created_at", horizon).delete().execute())
        deleted += len(res.data or []) if res and res.data else 0
        # Per-owner caps.
        owners = set()
        rows = (svc.table("decision_traces")
                .select("owner_id").limit(_MAX_SUPABASE_ROWS).execute())
        for r in (rows.data or []):
            owners.add(r["owner_id"])
        for owner in owners:
            matches = list_matches(owner, limit=10_000)
            surplus = len(matches) - max_matches
            if surplus > 0:
                drop = [m["match_id"] for m in matches[-surplus:]]
                for mid in drop:
                    res = (svc.table("decision_traces")
                           .eq("owner_id", owner).eq("match_id", mid)
                           .delete().execute())
                    deleted += len(res.data or []) if res and res.data else 0
        return deleted
    except Exception:
        return deleted


def _trim_owner_surplus(rows: list[dict], max_matches: int) -> tuple[list[dict], int]:
    """In-memory per-owner match cap: keep newest `max_matches` matches.
    Returns (kept rows, number of dropped rows)."""
    from collections import OrderedDict
    by_owner: dict[str, OrderedDict] = {}
    for r in rows:
        b = by_owner.setdefault(r["owner_id"], OrderedDict())
        b.setdefault(r["match_id"], []).append(r)
    out: list[dict] = []
    dropped = 0
    for owner, matches in by_owner.items():
        ordered = list(matches.values())
        ordered.sort(key=lambda rows_: rows_[0].get("created_at", ""))  # oldest first
        if len(ordered) > max_matches:
            dropped += sum(len(x) for x in ordered[: len(ordered) - max_matches])
            ordered = ordered[len(ordered) - max_matches:]
        for rows_ in ordered:
            out.extend(rows_)
    return out, dropped


#  Read 

def list_matches(owner_id: str, model_id: str | None = None, limit: int = 200) -> list[dict]:
    """Distinct traced matches for an owner, newest first.

    Each entry: match_id, model_id, model_label, opponent, result,
    score_for, score_against, turn_count, created_at (no snapshots).
    """
    rows = list_traces(owner_id, limit=0, model_id=model_id)
    groups: dict[str, dict] = {}
    for r in rows:
        mid = r["match_id"]
        g = groups.setdefault(mid, {
            "match_id": mid,
            "model_id": r.get("model_id"),
            "model_label": r.get("model_label", ""),
            "opponent": r.get("opponent", ""),
            "result": r.get("result", ""),
            "score_for": r.get("score_for", 0),
            "score_against": r.get("score_against", 0),
            "turn_count": 0,
            "created_at": r.get("created_at", ""),
        })
        g["turn_count"] += 1
        if r.get("created_at", "") > g["created_at"]:
            g["created_at"] = r["created_at"]
    matches = sorted(groups.values(), key=lambda m: m["created_at"], reverse=True)
    return matches[:limit]


def list_traces(owner_id: str, limit: int = 5000, model_id: str | None = None) -> list[dict]:
    """A model owner's trace rows, newest created_at first.

    Snapshots are included (they are needed to rebuild state). `model_id`
    optionally restricts to one model (the "user_model:<uuid>" form).
    """
    svc = _svc()
    try:
        if not svc:
            rows = [r for r in _MEM if r.get("owner_id") == owner_id]
            if model_id:
                rows = [r for r in rows if r.get("model_id") == model_id]
            rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
            return rows[:limit] if limit else rows
        q = (svc.table("decision_traces")
             .select("*")
             .eq("owner_id", owner_id))
        if model_id:
            q = q.eq("model_id", model_id)
        res = (q.order("created_at", desc=True)
               .limit(_MAX_SUPABASE_ROWS if not limit else limit)
               .execute())
        return res.data or []
    except Exception:
        return []


def get_match(owner_id: str, match_id: str) -> list[dict]:
    """One match's trace rows, ordered by `turn` ascending (snapshots kept)."""
    svc = _svc()
    try:
        if not svc:
            rows = [r for r in _MEM
                    if r.get("owner_id") == owner_id and r.get("match_id") == match_id]
            rows.sort(key=lambda r: r.get("turn", 0))
            return rows
        res = (svc.table("decision_traces")
               .select("*")
               .eq("owner_id", owner_id).eq("match_id", match_id)
               .order("turn", desc=False)
               .execute())
        return res.data or []
    except Exception:
        return []


def get_match_meta(owner_id: str, match_id: str) -> dict | None:
    rows = get_match(owner_id, match_id)
    if not rows:
        return None
    r = rows[0]
    return {
        "match_id": match_id,
        "model_id": r.get("model_id"),
        "model_label": r.get("model_label", ""),
        "opponent": r.get("opponent", ""),
        "result": r.get("result", ""),
        "score_for": r.get("score_for", 0),
        "score_against": r.get("score_against", 0),
        "turn_count": len(rows),
    }