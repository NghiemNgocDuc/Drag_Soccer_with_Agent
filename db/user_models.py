"""CRUD operations for user-uploaded AI models in Supabase."""
from __future__ import annotations
from datetime import datetime, timezone


def _svc():
    from db.supabase_client import service
    return service


# In-memory fallback (dev / tests: no Supabase), same pattern as ranked and
# achievements so the loss-analysis flow works end-to-end locally.
_MEM: dict[str, dict] = {}
_MEM_SEQ = [0]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_user_models(user_id: str) -> list[dict]:
    svc = _svc()
    if not svc:
        return [dict(m) for m in _MEM.values() if m["user_id"] == user_id]
    rows = (
        svc.table("user_models")
        .select("id, name, description, code, is_public, links, created_at, updated_at")
        .eq("user_id", user_id)
        .order("created_at")
        .execute()
    )
    return rows.data or []


def get_model_by_id(model_id: str, requesting_user_id: str | None = None) -> dict | None:
    """Return model if it belongs to requesting_user_id OR is public."""
    svc = _svc()
    if not svc:
        m = _MEM.get(model_id)
        if not m:
            return None
        if m["user_id"] == requesting_user_id or m.get("is_public"):
            return dict(m)
        return None
    row = svc.table("user_models").select("*").eq("id", model_id).maybe_single().execute()
    if not row.data:
        return None
    m = row.data
    # Access check: own model or public
    if m["user_id"] == requesting_user_id or m.get("is_public"):
        return m
    return None


def create_model(user_id: str, name: str, description: str, code: str, links: list | None = None) -> dict:
    svc = _svc()
    if links is None:
        links = []
    # validate links
    clean_links = []
    for l in links:
        if not isinstance(l, dict):
            continue
        title = (l.get("title") or "").strip()[:80]
        url = (l.get("url") or "").strip()[:500]
        if not url or not title:
            continue
        if not (url.startswith("http://") or url.startswith("https://")):
            continue
        clean_links.append({"title": title, "url": url})
    if not svc:
        _MEM_SEQ[0] += 1
        mid = f"dev-{_MEM_SEQ[0]}"
        now = _now_iso()
        m = {"id": mid, "user_id": user_id, "name": name, "description": description,
             "code": code, "is_public": False, "links": clean_links, "created_at": now, "updated_at": now}
        _MEM[mid] = m
        return dict(m)
    row = svc.table("user_models").insert({
        "user_id":     user_id,
        "name":        name,
        "description": description,
        "code":        code,
        "is_public":   False,
        "links":       clean_links,
    }).execute()
    return (row.data or [{}])[0]


def update_model(model_id: str, user_id: str, **fields) -> bool:
    # validate links if present
    if "links" in fields:
        raw = fields["links"]
        clean = []
        if isinstance(raw, list):
            for l in raw[:5]:
                if not isinstance(l, dict):
                    continue
                title = (l.get("title") or "").strip()[:80]
                url = (l.get("url") or "").strip()[:500]
                if not url or not title:
                    continue
                if not (url.startswith("http://") or url.startswith("https://")):
                    continue
                clean.append({"title": title, "url": url})
        fields["links"] = clean
    svc = _svc()
    if not svc:
        m = _MEM.get(model_id)
        if m and m["user_id"] == user_id:
            m.update(fields)
            m["updated_at"] = _now_iso()
            return True
        return False
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = (
        svc.table("user_models")
        .update(fields)
        .eq("id", model_id)
        .eq("user_id", user_id)
        .execute()
    )
    return bool(result.data)


def delete_model(model_id: str, user_id: str) -> bool:
    svc = _svc()
    if not svc:
        m = _MEM.get(model_id)
        if m and m["user_id"] == user_id:
            _MEM.pop(model_id, None)
            return True
        return False
    svc.table("user_models").delete().eq("id", model_id).eq("user_id", user_id).execute()
    return True
