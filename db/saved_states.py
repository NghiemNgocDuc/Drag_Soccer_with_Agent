from __future__ import annotations
from datetime import datetime, timezone


def _svc():
    from db.supabase_client import service
    return service


def upsert_state(user_id: str, state_data: dict) -> bool:
    svc = _svc()
    if not svc:
        return False
    try:
        existing = svc.table("saved_states").select("id").eq("user_id", user_id).limit(1).maybe_single().execute()
        now = datetime.now(timezone.utc).isoformat()
        if existing.data:
            svc.table("saved_states").update({"state": state_data, "updated_at": now}).eq("id", existing.data["id"]).execute()
        else:
            svc.table("saved_states").insert({"user_id": user_id, "state": state_data, "slot_index": 0, "created_at": now, "updated_at": now}).execute()
        return True
    except Exception:
        return False


def get_saved_state(user_id: str) -> dict | None:
    svc = _svc()
    if not svc:
        return None
    try:
        row = svc.table("saved_states").select("*").eq("user_id", user_id).order("slot_index").limit(1).maybe_single().execute()
        if row.data:
            return row.data
    except Exception:
        pass
    return None


def delete_saved_state(user_id: str) -> bool:
    svc = _svc()
    if not svc:
        return False
    try:
        svc.table("saved_states").delete().eq("user_id", user_id).execute()
        return True
    except Exception:
        return False
