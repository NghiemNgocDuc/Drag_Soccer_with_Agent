"""Supabase operations on the public `profiles` table."""
from __future__ import annotations


def _svc():
    from db.supabase_client import service
    return service


def get_profile(user_id: str) -> dict | None:
    svc = _svc()
    if not svc:
        return None
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
