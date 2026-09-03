"""Supabase database operations for game persistence and stats."""
from __future__ import annotations
from datetime import datetime, timezone


def _svc():
    from db.supabase_client import service
    return service


def _replay_key(user_id: str, rid: str) -> str:
    return f"replay:{user_id}:{rid}"

def save_game_result(
    user_id: str,
    mode: str,
    ai_model: str,
    winner: str,
    score_a: int,
    score_b: int,
    total_moves: int,
    replay: list | None = None,
) -> str | None:
    rid = None
    try:
        import uuid as _uuid
        rid = _uuid.uuid4().hex[:10]
        if replay is not None:
            from db.redis_client import r as _r
            import json as _j
            _r.setex(_replay_key(user_id, rid), 30*86400, _j.dumps(replay))
    except Exception:
        pass
    svc = _svc()
    if not svc:
        try:
            from db.redis_client import r as _r
            import json as _j
            key = f"history:{user_id}"
            raw = _r.get(key)
            lst = _j.loads(raw) if raw else []
            lst.insert(0, {"id": rid, "mode": mode, "ai_model": ai_model, "winner": winner, "score_a": score_a, "score_b": score_b, "total_moves": total_moves, "ended_at": datetime.now(timezone.utc).isoformat(), "replay_id": rid})
            lst = lst[:5]
            _r.setex(key, 30*86400, _j.dumps(lst))
            # prune old replays
            if len(lst) < 5:  # keep only 5 old replay keys? clean extras lazily
                pass
        except Exception:
            pass
        return rid
    try:
        row = svc.table("games").insert({
            "user_id":     user_id,
            "mode":        mode,
            "ai_model":    ai_model,
            "winner":      winner,
            "score_a":     score_a,
            "score_b":     score_b,
            "total_moves": total_moves,
            "ended_at":    datetime.now(timezone.utc).isoformat(),
            "replay_id":   rid,
        }).execute()
    except Exception:
        # Supabase without replay_id column (pre-migration) — insert without it
        row = svc.table("games").insert({
            "user_id":     user_id,
            "mode":        mode,
            "ai_model":    ai_model,
            "winner":      winner,
            "score_a":     score_a,
            "score_b":     score_b,
            "total_moves": total_moves,
            "ended_at":    datetime.now(timezone.utc).isoformat(),
        }).execute()
    try:
        rows = svc.table("games").select("id,ended_at,replay_id").eq("user_id", user_id).order("ended_at", desc=True).execute().data or []
        if len(rows) > 5:
            old = rows[5:]
            old_ids = [r["id"] for r in old]
            svc.table("games").delete().in_("id", old_ids).execute()
            # delete old replays
            try:
                from db.redis_client import r as _r
                for r in old:
                    if r.get("replay_id"):
                        _r.delete(_replay_key(user_id, r["replay_id"]))
            except Exception:
                pass
        from db.redis_client import r as _r
        import json as _j
        recent = svc.table("games").select("*").eq("user_id", user_id).order("ended_at", desc=True).limit(5).execute().data or []
        _r.setex(f"history:{user_id}", 30*86400, _j.dumps(recent))
    except Exception:
        pass
    return rid

def get_replay(user_id: str, replay_id: str) -> list | None:
    try:
        from db.redis_client import r as _r
        import json as _j
        raw = _r.get(_replay_key(user_id, replay_id))
        if raw:
            return _j.loads(raw)
    except Exception:
        pass
    return None


def get_user_stats(user_id: str) -> dict:
    svc = _svc()
    empty = {"games_played":0,"wins":0,"losses":0,"draws":0,"goals_for":0,"goals_against":0,"recent":[],"online_played":0,"online_wins":0}
    if not svc:
        try:
            from db.redis_client import r as _r
            import json as _j
            raw = _r.get(f"history:{user_id}")
            if raw:
                lst = _j.loads(raw)[:5]
                empty["games_played"] = len(lst)
                empty["wins"] = sum(1 for r in lst if r.get("winner") == "A")
                empty["losses"] = sum(1 for r in lst if r.get("winner") == "B")
                empty["draws"] = sum(1 for r in lst if r.get("winner") == "Draw")
                empty["recent"] = [{"id": r.get("id"), "replay_id": r.get("replay_id"), "mode": r.get("mode"), "score_a": r.get("score_a"), "score_b": r.get("score_b"), "winner": r.get("winner",""), "ended_at": r.get("ended_at",""), "ai_model": r.get("ai_model","")} for r in lst]
                return empty
        except Exception:
            pass
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
            {"id": r.get("id"), "replay_id": r.get("replay_id"), "mode":r["mode"],"score_a":r["score_a"],"score_b":r["score_b"],
             "winner":r.get("winner",""),"ended_at":r.get("ended_at",""), "ai_model": r.get("ai_model","")}
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
