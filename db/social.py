"""Social shares for history video / highlight — stored in Redis (30d) and shown in community feed."""
from __future__ import annotations
import json
import time
import uuid

_KEY = "social:shares"
_TTL = 30*86400

def _r():
    try:
        from db.redis_client import r
        return r
    except Exception:
        return None

_MEM: list[dict] = []

def list_shares(limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
    r=_r()
    try:
        if r:
            raw=r.lrange(_KEY, 0, -1)
            lst=[json.loads(x.decode() if isinstance(x, bytes) else x) for x in raw]
        else:
            lst=list(_MEM)
    except Exception:
        lst=list(_MEM)
    lst = sorted(lst, key=lambda x: x.get("created_at",""), reverse=True)
    total=len(lst)
    return lst[offset:offset+limit], total

def create_share(user_id: str, username: str, avatar_url: str | None, kind: str, replay_id: str, caption: str, meta: dict | None = None) -> dict:
    share={
        "id": uuid.uuid4().hex[:10],
        "user_id": user_id,
        "username": username,
        "avatar_url": avatar_url,
        "kind": kind,  # history or highlight
        "replay_id": replay_id,
        "caption": caption[:280] if caption else "",
        "meta": meta or {},
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "likes": 0,
        "comments": [],
    }
    r=_r()
    try:
        if r:
            r.lpush(_KEY, json.dumps(share))
            r.ltrim(_KEY, 0, 99)
            r.expire(_KEY, _TTL)
        else:
            _MEM.insert(0, share)
            if len(_MEM)>100:
                _MEM.pop()
    except Exception:
        _MEM.insert(0, share)
    return share
