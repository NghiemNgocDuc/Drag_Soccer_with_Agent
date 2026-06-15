"""CRUD operations for user-uploaded AI models in Supabase."""
from __future__ import annotations
from datetime import datetime, timezone


def _svc():
    from db.supabase_client import service
    return service


def get_user_models(user_id: str) -> list[dict]:
    svc = _svc()
    if not svc:
        return []
    rows = (
        svc.table("user_models")
        .select("id, name, description, is_public, created_at, updated_at")
        .eq("user_id", user_id)
        .order("created_at")
        .execute()
    )
    return rows.data or []


def get_model_by_id(model_id: str, requesting_user_id: str | None = None) -> dict | None:
    """Return model if it belongs to requesting_user_id OR is public."""
    svc = _svc()
    if not svc:
        return None
    row = svc.table("user_models").select("*").eq("id", model_id).maybe_single().execute()
    if not row.data:
        return None
    m = row.data
    # Access check: own model or public
    if m["user_id"] == requesting_user_id or m.get("is_public"):
        return m
    return None


def create_model(user_id: str, name: str, description: str, code: str) -> dict:
    svc = _svc()
    if not svc:
        return {"id": "dev", "name": name, "description": description, "code": code}
    row = svc.table("user_models").insert({
        "user_id":     user_id,
        "name":        name,
        "description": description,
        "code":        code,
        "is_public":   False,
    }).execute()
    return (row.data or [{}])[0]


def update_model(model_id: str, user_id: str, **fields) -> bool:
    svc = _svc()
    if not svc:
        return True
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
        return True
    svc.table("user_models").delete().eq("id", model_id).eq("user_id", user_id).execute()
    return True
