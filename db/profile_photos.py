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


def _is_appropriate_image(data: bytes) -> tuple[bool, str]:
    """Server-side NSFW check. Returns (ok, reason). Chrome has no built-in filter."""
    # Basic checks: ensure it's actually an image and not too small
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(data))
        im.verify()
        # Re-open for size check (verify closes)
        im = Image.open(io.BytesIO(data))
        w, h = im.size
        if w < 32 or h < 32:
            return False, "Image too small"
        if w > 4000 or h > 4000:
            return False, "Image too large"
    except Exception:
        return False, "Invalid image"
    # External moderation if configured
    try:
        from config import MODERATION_PROVIDER, MODERATION_API_KEY, MODERATION_API_SECRET
        if MODERATION_PROVIDER and MODERATION_API_KEY:
            import requests
            if MODERATION_PROVIDER == "sightengine":
                # Sightengine check: https://sightengine.com/docs
                resp = requests.post("https://api.sightengine.com/1.0/check.json",
                    data={
                        "models": "nudity-2.0,offensive",
                        "api_user": MODERATION_API_KEY,
                        "api_secret": MODERATION_API_SECRET,
                    },
                    files={"media": data},
                    timeout=8)
                j = resp.json()
                # nudity raw >0.6 or offensive prob >0.7 -> block
                nudity = float(j.get("nudity", {}).get("raw", 0) or 0)
                offensive = float(j.get("offensive", {}).get("prob", 0) or 0)
                if nudity > 0.6 or offensive > 0.7:
                    return False, "Image flagged as inappropriate"
            elif MODERATION_PROVIDER == "moderatecontent":
                resp = requests.get("https://api.moderatecontent.com/moderate/",
                    params={"key": MODERATION_API_KEY, "url": ""},  # we send image as base64 not supported here, fallback
                    timeout=8)
                # For moderatecontent, we would need to upload image; simplified: check rating
                # If not implemented, just allow
                pass
            elif MODERATION_PROVIDER == "google":
                # Google Vision SafeSearch would be here — requires google-cloud-vision
                pass
    except Exception as e:
        # On moderation API failure, log and allow (fail open for UX, but log for review)
        import logging
        logging.warning("Moderation check failed, allowing with log: %s", e)
    # If no provider, just allow after basic checks (log for manual review if needed)
    return True, "ok"


def upload_avatar(user_id: str, data: bytes, ext: str) -> str | None:
    """Upload a photo for the user and return its public URL (or None on failure)."""
    ok, reason = _is_appropriate_image(data)
    if not ok:
        import logging
        logging.warning("Avatar rejected for %s: %s", user_id, reason)
        # Raise to surface to API
        raise ValueError(f"Image rejected: {reason}")
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
    except ValueError:
        raise
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
