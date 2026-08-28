"""Ranked matchmaking: ELO ratings for human players.

Storage lives in three Supabase tables (see migration_ranked.sql):
  - `ratings`           one row per user: rating, games_played, wins, losses, peak
  - `ranked_matches`    one row per rated match (room_id unique -> idempotent)
  - `rating_history`    full per-change log (feeds future seasons/charts)

Every result is applied by a single security-definer RPC
(`submit_ranked_result`) so the match row, both history rows, and both
rating updates commit atomically. All functions degrade to an in-memory
registry when Supabase is unavailable (local dev / tests).

ELO (confirmed design):
    E_a = 1 / (1 + 10^((Rb - Ra) / 400))        expected score
    R'  = R + K * (S - E), S = 1 win / 0 loss
    K   = 40 during placement (< PLACEMENT_GAMES), 20 after
    start rating 1200, no floor/clamp. Draws are impossible in this game
    (sudden-death shootout guarantees a winner), so win/loss only.
"""
from __future__ import annotations
from datetime import datetime, timezone

START_RATING     = 1200
PLACEMENT_GAMES  = 10
K_PROVISIONAL    = 40
K_PLACED         = 20
ELO_SCALE        = 400


def _svc():
    from db.supabase_client import service
    return service


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


#  In-memory fallback (dev: no Supabase) 
_MEM: dict[str, dict] = {}      # user_id -> ratings row
_MEM_MATCHES: list[dict] = []   # ranked_matches rows (newest last)
_MEM_HISTORY: list[dict] = []   # rating_history rows


#  ELO math 

def expected_score(ra: float, rb: float) -> float:
    """Expected score of player A vs player B (0..1)."""
    return 1.0 / (1.0 + 10.0 ** ((rb - ra) / ELO_SCALE))


def k_factor(games_played: int) -> int:
    return K_PROVISIONAL if games_played < PLACEMENT_GAMES else K_PLACED


def compute_outcome(ra: int, rb: int, winner: str) -> dict:
    """New ratings/deltas for both players given winner 'A' | 'B'.

    `games_played` (pre-match) is folded in by the caller via k_factor.
    Returns {player_a: {..}, player_b: {..}} with rating_before/after, delta, k.
    """
    ea = expected_score(ra, rb)
    eb = 1.0 - ea
    sa = 1.0 if winner == "A" else 0.0
    sb = 1.0 - sa
    da = round(k_factor(0) * (sa - ea))
    db = round(k_factor(0) * (sb - eb))
    return {
        "player_a": {"rating_before": ra, "rating_after": ra + da, "delta": da, "k": k_factor(0)},
        "player_b": {"rating_before": rb, "rating_after": rb + db, "delta": db, "k": k_factor(0)},
    }


def outcome_with_placement(ra: int, ga: int, rb: int, gb: int, winner: str) -> dict:
    """compute_outcome with per-player K from their pre-match games played."""
    ea = expected_score(ra, rb)
    eb = 1.0 - ea
    sa = 1.0 if winner == "A" else 0.0
    sb = 1.0 - sa
    ka, kb = k_factor(ga), k_factor(gb)
    da = round(ka * (sa - ea))
    db = round(kb * (sb - eb))
    return {
        "player_a": {"rating_before": ra, "rating_after": ra + da, "delta": da, "k": ka},
        "player_b": {"rating_before": rb, "rating_after": rb + db, "delta": db, "k": kb},
    }


#  Reads 

def _default_row(user_id: str) -> dict:
    return {"user_id": user_id, "rating": START_RATING, "games_played": 0,
            "wins": 0, "losses": 0, "peak_rating": START_RATING}


def get_rating(user_id: str) -> dict:
    """Current rating row (defaults if the user has never played ranked)."""
    svc = _svc()
    if not svc:
        return dict(_MEM.get(user_id) or _default_row(user_id))
    row = svc.table("ratings").select("*").eq("user_id", user_id)\
        .maybe_single().execute()
    return row.data if row and row.data else _default_row(user_id)


def get_ratings(user_ids: list[str]) -> dict[str, dict]:
    """Batch rating rows keyed by user id (missing users -> defaults)."""
    uids = list(dict.fromkeys(user_ids))
    out: dict[str, dict] = {u: _default_row(u) for u in uids}
    svc = _svc()
    if not svc:
        for u in uids:
            if u in _MEM:
                out[u] = dict(_MEM[u])
        return out
    rows = svc.table("ratings").select("*").in_("user_id", uids).execute().data or []
    for row in rows:
        out[row["user_id"]] = row
    return out


def list_leaderboard(limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
    """Placed players only (games_played >= PLACEMENT_GAMES), by rating desc.

    Returns (entries, total_placed). Entries carry rank/username/avatar so
    the page can render directly.
    """
    svc = _svc()
    if not svc:
        rows = [r for r in _MEM.values()
                if r.get("games_played", 0) >= PLACEMENT_GAMES]
        rows = sorted(rows, key=lambda x: x["rating"], reverse=True)
        total = len(rows)
        page = rows[offset:offset + limit]
        return _decorate(page, offset), total

    res = svc.table("ratings")\
        .select("user_id,rating,games_played,wins,losses,peak_rating")\
        .gte("games_played", PLACEMENT_GAMES)\
        .order("rating", desc=True)\
        .range(offset, offset + limit - 1)\
        .execute()
    rows = list(res.data or [])
    total = len(rows) + offset  # best effort without COUNT
    return _decorate(rows, offset), total


def _decorate(rows: list[dict], offset: int) -> list[dict]:
    if not rows:
        return []
    svc = _svc()
    users = {r["user_id"] for r in rows}
    usernames: dict[str, str] = {}
    avatars: dict[str, str] = {}
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
    for i, r in enumerate(rows):
        uid_ = r["user_id"]
        games = int(r.get("games_played", 0))
        wins = int(r.get("wins", 0))
        losses = int(r.get("losses", 0))
        out.append({
            "rank": offset + i + 1,
            "user_id": uid_,
            "username": usernames.get(uid_, "Player"),
            "avatar_url": avatars.get(uid_),
            "rating": int(r.get("rating", START_RATING)),
            "games_played": games,
            "wins": wins,
            "losses": losses,
            "win_rate": round(100.0 * wins / games, 1) if games else 0.0,
            "peak_rating": int(r.get("peak_rating", START_RATING)),
        })
    return out


def get_win_streak(user_id: str, limit: int = 50) -> int:
    """Consecutive ranked wins ending at this user's most recent match.

    Scans the user's ranked_matches newest-first and counts wins until a
    loss (or the window runs out). Used by the ranked streak achievements.
    """
    svc = _svc()
    if not svc:
        matches = [m for m in reversed(_MEM_MATCHES)
                   if m["player_a"] == user_id or m["player_b"] == user_id]
    else:
        try:
            res = (svc.table("ranked_matches")
                   .select("player_a,player_b,winner")
                   .or_(f"player_a.eq.{user_id},player_b.eq.{user_id}")
                   .order("created_at", desc=True).limit(limit).execute())
        except Exception:
            return 0
        matches = list(res.data or [])
    streak = 0
    for m in matches:
        side = "a" if m.get("player_a") == user_id else "b"
        if m.get("winner") == side.upper():
            streak += 1
        else:
            break
    return streak


def get_rating_history(user_id: str, limit: int = 20) -> list[dict]:
    """Recent rating changes for one user, newest first."""
    svc = _svc()
    if not svc:
        rows = [h for h in _MEM_HISTORY if h["user_id"] == user_id]
        rows = rows[-limit:][::-1]
        return rows
    res = svc.table("rating_history")\
        .select("*").eq("user_id", user_id)\
        .order("created_at", desc=True).limit(limit).execute()
    return list(res.data or [])


def get_all_rating_history(limit: int = 10000) -> list[dict]:
    """All rating-history rows, oldest first (analytics input)."""
    svc = _svc()
    if not svc:
        return [dict(h) for h in _MEM_HISTORY]
    try:
        res = (svc.table("rating_history").select("*")
               .order("created_at", desc=False).limit(limit).execute())
        return list(res.data or [])
    except Exception:
        return []


def get_all_ranked_matches(limit: int = 5000) -> list[dict]:
    """All rated-match rows, oldest first (analytics input)."""
    svc = _svc()
    if not svc:
        return [dict(m) for m in _MEM_MATCHES]
    try:
        res = (svc.table("ranked_matches").select("*")
               .order("created_at", desc=False).limit(limit).execute())
        return list(res.data or [])
    except Exception:
        return []


#  Result application (authoritative, atomic, idempotent) 

def record_result(room_id: str, player_a: str, player_b: str,
                  winner: str, score_a: int, score_b: int) -> dict:
    """Apply a completed ranked match to both players' ratings.

    Must only be called from the server-side match-completion hook (never
    from a client-trusted request). Atomic via the `submit_ranked_result`
    RPC (single transaction). Idempotent by `room_id`: a repeated call
    returns the already-stored result instead of double-applying.

    Returns {match_id, player_a: {rating_before, rating_after, delta, k},
             player_b: {..}}.
    """
    if winner not in ("A", "B"):
        raise ValueError(f"Ranked match winner must be 'A' or 'B', got {winner!r}")
    svc = _svc()
    if not svc:
        return _record_in_memory(room_id, player_a, player_b, winner, score_a, score_b)

    ra = get_rating(player_a)
    rb = get_rating(player_b)
    outcome = outcome_with_placement(
        int(ra.get("rating", START_RATING)), int(ra.get("games_played", 0)),
        int(rb.get("rating", START_RATING)), int(rb.get("games_played", 0)),
        winner,
    )
    try:
        res = svc.rpc("submit_ranked_result", {
            "p_room_id": room_id,
            "p_player_a": player_a,
            "p_player_b": player_b,
            "p_winner": 1 if winner == "A" else 0,
            "p_score_a": int(score_a),
            "p_score_b": int(score_b),
            "p_rating_a_before": outcome["player_a"]["rating_before"],
            "p_rating_a_after":  outcome["player_a"]["rating_after"],
            "p_delta_a": outcome["player_a"]["delta"],
            "p_k_a": outcome["player_a"]["k"],
            "p_rating_b_before": outcome["player_b"]["rating_before"],
            "p_rating_b_after":  outcome["player_b"]["rating_after"],
            "p_delta_b": outcome["player_b"]["delta"],
            "p_k_b": outcome["player_b"]["k"],
        }).execute()
        data = (res.data or [{}])[0] if isinstance(res.data, list) else (res.data or {})
        match_id = (data or {}).get("match_id")
    except Exception:
        # Idempotency: if this room was already applied, fetch and return it.
        existing = svc.table("ranked_matches").select("*")\
            .eq("room_id", room_id).maybe_single().execute()
        if existing and existing.data:
            return _from_match_row(existing.data)
        raise
    return {
        "match_id": match_id,
        "room_id": room_id,
        "winner": winner,
        "score_a": int(score_a),
        "score_b": int(score_b),
        "player_a": outcome["player_a"],
        "player_b": outcome["player_b"],
    }


def _record_in_memory(room_id: str, player_a: str, player_b: str,
                      winner: str, score_a: int, score_b: int) -> dict:
    for m in _MEM_MATCHES:
        if m["room_id"] == room_id:
            return _from_match_row(m)
    ra = _MEM.get(player_a) or _default_row(player_a)
    rb = _MEM.get(player_b) or _default_row(player_b)
    outcome = outcome_with_placement(ra["rating"], ra["games_played"],
                                     rb["rating"], rb["games_played"], winner)
    match = {
        "id": f"mem-{len(_MEM_MATCHES) + 1}",
        "room_id": room_id, "winner": winner,
        "score_a": int(score_a), "score_b": int(score_b),
        "player_a": player_a, "player_b": player_b,
        "rating_a_before": outcome["player_a"]["rating_before"],
        "rating_a_after":  outcome["player_a"]["rating_after"],
        "delta_a": outcome["player_a"]["delta"],
        "k_a": outcome["player_a"]["k"],
        "rating_b_before": outcome["player_b"]["rating_before"],
        "rating_b_after":  outcome["player_b"]["rating_after"],
        "delta_b": outcome["player_b"]["delta"],
        "k_b": outcome["player_b"]["k"],
    }
    for uid_, side in ((player_a, "a"), (player_b, "b")):
        row = _MEM.setdefault(uid_, _default_row(uid_))
        row["rating"] = outcome[f"player_{side}"]["rating_after"]
        row["games_played"] += 1
        if winner == side.upper():
            row["wins"] += 1
        else:
            row["losses"] += 1
        row["peak_rating"] = max(row["peak_rating"], row["rating"])
        row["updated_at"] = _now_iso()
        _MEM_HISTORY.append({
            "user_id": uid_, "match_id": match["id"],
            "rating_before": outcome[f"player_{side}"]["rating_before"],
            "rating_after":  outcome[f"player_{side}"]["rating_after"],
            "delta": outcome[f"player_{side}"]["delta"],
            "created_at": _now_iso(),
        })
    _MEM_MATCHES.append(match)
    return _from_match_row(match)


def _from_match_row(row: dict) -> dict:
    return {
        "match_id": row.get("id"),
        "room_id": row.get("room_id"),
        "winner": row.get("winner"),
        "score_a": int(row.get("score_a", 0)),
        "score_b": int(row.get("score_b", 0)),
        "player_a": {"rating_before": row["rating_a_before"],
                     "rating_after": row["rating_a_after"],
                     "delta": row["delta_a"], "k": row["k_a"]},
        "player_b": {"rating_before": row["rating_b_before"],
                     "rating_after": row["rating_b_after"],
                     "delta": row["delta_b"], "k": row["k_b"]},
    }
