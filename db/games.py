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
        return svc.rpc("get_leaderboard", {"limit_count": limit}).execute().data or []
    except Exception:
        return []
