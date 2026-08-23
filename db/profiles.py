"""Supabase operations on the public `profiles` table."""
from __future__ import annotations


def _svc():
    from db.supabase_client import service
    return service


_MEM_USERS: dict[str, str] = {}  # user_id -> username (dev fallback when Supabase is absent)


def register_user(user_id: str, username: str) -> None:
    """Dev-path registry: auth routes call this so rooms/leaderboards can
    resolve usernames without a Supabase profiles table."""
    _MEM_USERS[user_id] = username


def get_profile(user_id: str) -> dict | None:
    svc = _svc()
    if not svc:
        username = _MEM_USERS.get(user_id)
        if username is None:
            return None
        return {"id": user_id, "username": username, "avatar_url": None}
    try:
        row = svc.table("profiles").select("id,username,avatar_url").eq("id", user_id).maybe_single().execute()
        return row.data
    except Exception:
        return None


def get_avatar_url(user_id: str) -> str | None:
    prof = get_profile(user_id)
    if prof:
        return prof.get("avatar_url") or None
    return None


def set_avatar_url(user_id: str, url: str | None) -> bool:
    svc = _svc()
    if not svc:
        return False
    try:
        svc.table("profiles").update({"avatar_url": url}).eq("id", user_id).execute()
        return True
    except Exception:
        return False
