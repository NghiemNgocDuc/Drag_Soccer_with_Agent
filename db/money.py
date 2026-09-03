"""Money / coins — simple economy for entire game. Stored in profiles.coins (fallback _MEM)."""
from __future__ import annotations

_DEFAULT = 100
_MEM: dict[str, int] = {}
_SHOP = {
    "ball_galaxy": 80,
    "ball_rainbow": 60,
    "stadium_cyber": 120,
    "net_gold": 50,
}

def _svc():
    try:
        from db.supabase_client import service
        return service
    except Exception:
        return None

def get_coins(user_id: str) -> int:
    svc=_svc()
    if not svc:
        return _MEM.get(user_id, _DEFAULT)
    try:
        row=svc.table("profiles").select("coins").eq("id", user_id).maybe_single().execute()
        if row.data and "coins" in row.data and row.data["coins"] is not None:
            return int(row.data["coins"])
    except Exception:
        pass
    return _MEM.get(user_id, _DEFAULT)

def add_coins(user_id: str, amount: int, reason: str = "") -> int:
    if amount==0:
        return get_coins(user_id)
    svc=_svc()
    cur=get_coins(user_id)
    nxt=max(0, cur+amount)
    if not svc:
        _MEM[user_id]=nxt
        return nxt
    try:
        svc.table("profiles").update({"coins": nxt}).eq("id", user_id).execute()
        # also log to _MEM for fast read
        _MEM[user_id]=nxt
    except Exception:
        _MEM[user_id]=nxt
    return nxt

def spend_coins(user_id: str, amount: int, item: str = "") -> tuple[bool,int]:
    cur=get_coins(user_id)
    if cur < amount:
        return False, cur
    return True, add_coins(user_id, -amount, item)

def shop_price(item: str) -> int | None:
    return _SHOP.get(item)
