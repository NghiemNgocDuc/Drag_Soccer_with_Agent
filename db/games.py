"""Supabase database operations for game persistence and stats."""
from __future__ import annotations
from datetime import datetime, timezone


def _svc():
    from db.supabase_client import service
    return service


def save_game_result(
    user_id: str,
    mode: str,
    ai_model: str,
    winner: str,
    score_a: int,
    score_b: int,
    total_moves: int,
) -> None:
    svc = _svc()
    if not svc:
        # also keep 5 recent in Redis for DEV_MODE
        try:
            from db.redis_client import r as _r
            import json as _j
            key = f"history:{user_id}"
            raw = _r.get(key)
            lst = _j.loads(raw) if raw else []
            lst.insert(0, {"mode": mode, "ai_model": ai_model, "winner": winner, "score_a": score_a, "score_b": score_b, "total_moves": total_moves, "ended_at": datetime.now(timezone.utc).isoformat()})
            lst = lst[:5]
            _r.setex(key, 30*86400, _j.dumps(lst))
        except Exception:
            pass
        return
    svc.table("games").insert({
        "user_id":     user_id,
        "mode":        mode,
        "ai_model":    ai_model,
        "winner":      winner,
        "score_a":     score_a,
        "score_b":     score_b,
        "total_moves": total_moves,
        "ended_at":    datetime.now(timezone.utc).isoformat(),
    }).execute()
    # keep only most recent 5 to save space
    try:
        rows = svc.table("games").select("id,ended_at").eq("user_id", user_id).order("ended_at", desc=True).execute().data or []
        if len(rows) > 5:
            old_ids = [r["id"] for r in rows[5:]]
            svc.table("games").delete().in_("id", old_ids).execute()
        # also mirror to Redis for fast history
        from db.redis_client import r as _r
        import json as _j
        recent = svc.table("games").select("*").eq("user_id", user_id).order("ended_at", desc=True).limit(5).execute().data or []
        _r.setex(f"history:{user_id}", 30*86400, _j.dumps(recent))
    except Exception:
        pass


def get_user_stats(user_id: str) -> dict:
    svc = _svc()
    empty = {"games_played":0,"wins":0,"losses":0,"draws":0,"goals_for":0,"goals_against":0,"recent":[],"online_played":0,"online_wins":0}
    if not svc:
        return empty
    rows = svc.table("games").select("*").eq("user_id", user_id).order("ended_at", desc=True).execute().data or []
    hvai = [r for r in rows if r.get("mode") == "hvai"]
    online = [r for r in rows if r.get("mode") == "online"]
    total_gf = sum(r.get("score_a",0) for r in hvai)
    total_ga = sum(r.get("score_b",0) for r in hvai)
    return {
        "games_played": len(rows),
        "wins":   sum(1 for r in hvai if r.get("winner") == "A"),
        "losses": sum(1 for r in hvai if r.get("winner") == "B"),
        "draws":  sum(1 for r in hvai if r.get("winner") == "Draw"),
        "goals_for": total_gf,
        "goals_against": total_ga,
        "online_played": len(online),
        "online_wins": sum(1 for r in online if r.get("winner") == "A"),
        "recent": [
            {"mode":r["mode"],"score_a":r["score_a"],"score_b":r["score_b"],
             "winner":r.get("winner",""),"ended_at":r.get("ended_at","")}
            for r in rows[:5] if r.get("score_a") is not None
        ],
    }


def get_leaderboard(limit: int = 20) -> list[dict]:
    svc = _svc()
    if not svc:
        return []
    try:
        rows = svc.rpc("get_leaderboard", {"limit_count": limit}).execute().data or []
        ids = [r["id"] for r in rows if r.get("id")]
        if ids:
            avatars = (svc.table("profiles").select("id,avatar_url")
                       .in_("id", ids).execute().data or [])
            avatar_map = {a["id"]: a.get("avatar_url") for a in avatars}
            for r in rows:
                r["avatar_url"] = avatar_map.get(r.get("id"))
        return rows
    except Exception:
        return []
