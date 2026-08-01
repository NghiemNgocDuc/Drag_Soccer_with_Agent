"""Supabase Storage operations for profile photos.

Uploads are scoped to `avatars/{user_id}/...`. The server derives the user_id
exclusively from the session (never from client input), and the bucket's RLS
policies (see sql/account_features.sql) enforce the same owner-only scoping for
direct client access.
"""
from __future__ import annotations
import time

AVATAR_BUCKET = "avatars"
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
MAX_AVATAR_BYTES = 5 * 1024 * 1024


def _svc():
    from db.supabase_client import service
    return service


def _bucket():
    svc = _svc()
    if not svc:
        return None
    try:
        svc.storage.create_bucket(AVATAR_BUCKET, public=True)
    except Exception:
        pass  # already exists (or storage not provisioned — upload will surface it)
    return svc


def _path(user_id: str, ext: str) -> str:
    # Storage paths are relative to the bucket: avatars/{user_id}/photo_*.ext
    return f"{user_id}/photo_{int(time.time() * 1000)}.{ext}"


def _user_objects(svc, user_id: str) -> list[str]:
    try:
        data = svc.storage.from_(AVATAR_BUCKET).list(user_id) or []
        return [f"{user_id}/{o['name']}" for o in data if isinstance(o, dict) and o.get("name")]
    except Exception:
        return []


def upload_avatar(user_id: str, data: bytes, ext: str) -> str | None:
    """Upload a photo for the user and return its public URL (or None on failure)."""
    svc = _bucket()
    if not svc:
        return None
    try:
        path = _path(user_id, ext)
        svc.storage.from_(AVATAR_BUCKET).upload(
            path,
            data,
            {"content-type": f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext}"},
        )
        for old in _user_objects(svc, user_id):
            if old == path or not old.startswith(f"{user_id}/photo_"):
                continue
            svc.storage.from_(AVATAR_BUCKET).remove([old])
        return svc.storage.from_(AVATAR_BUCKET).get_public_url(path)
    except Exception:
        return None


def remove_avatar(user_id: str) -> bool:
    """Delete all objects under the user's avatar folder."""
    svc = _svc()
    if not svc:
        return False
    try:
        objects = _user_objects(svc, user_id)
        if objects:
            svc.storage.from_(AVATAR_BUCKET).remove(objects)
        return True
    except Exception:
        return False
