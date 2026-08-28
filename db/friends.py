"""Persistent friends — Supabase `friends` + `friend_requests` with Redis presence/recent cache.

Design (P0):
- Storage: Supabase `friends` (user_id, friend_id, since, nickname, favorite) + `friend_requests` (id, from_id, to_id).
- Fallback: _MEM_FRIENDS / _MEM_REQS dicts when Supabase absent (DEV_MODE, tests). Redis lists remain as *cache* only and are kept in sync on write.
- Cap 32 friends (FIFA Mobile) checked app-side before insert.
- Presence: Redis `presence:{uid}` = json {status: online|in_match|offline, last_seen: ts, room_id?} TTL 90s (heartbeat). Helper `set_presence` / `get_presence`.
- Recently Met: Redis `recent:{uid}` list max 30 (EA FC pattern), pushed on game_over.
"""
from __future__ import annotations
import time
import json

FRIEND_CAP = 32
PRESENCE_TTL = 90
RECENT_CAP = 30
RECENT_TTL = 30 * 86400

def _svc():
    from db.supabase_client import service
    return service

def _r():
    from db.redis_client import r
    return r

# ----- in-memory fallback (DEV_MODE / tests) -----
_MEM_FRIENDS: dict[str, list[dict]] = {}  # uid -> [{uid, username, since, nickname, favorite}]
_MEM_REQS: dict[str, list[dict]] = {}     # to_uid -> [{id, from_uid, from_username, ts}]

def _now() -> float:
    return time.time()

def _mem_get_friends(uid: str) -> list[dict]:
    return [dict(x) for x in _MEM_FRIENDS.get(uid, [])]

def _mem_set_friends(uid: str, lst: list[dict]) -> None:
    _MEM_FRIENDS[uid] = [dict(x) for x in lst]

# ----- presence -----
def set_presence(uid: str, status: str, room_id: str | None = None) -> None:
    try:
        _r().setex(f"presence:{uid}", PRESENCE_TTL, json.dumps({"status": status, "last_seen": _now(), "room_id": room_id} if room_id else {"status": status, "last_seen": _now()}))
    except Exception:
        pass

def get_presence(uids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for uid in uids:
        try:
            raw = _r().get(f"presence:{uid}")
            if raw:
                data = json.loads(raw)
                # janitor: stale > 120s → offline (covers in-memory fallback where expire is no-op)
                if _now() - float(data.get("last_seen", 0)) > PRESENCE_TTL * 1.35:
                    out[uid] = {"status": "offline", "last_seen": data.get("last_seen", 0)}
                else:
                    out[uid] = data
            else:
                out[uid] = {"status": "offline", "last_seen": 0}
        except Exception:
            out[uid] = {"status": "offline", "last_seen": 0}
    return out


def sweep_presence() -> int:
    """Remove stale presence entries (best-effort, for in-memory fallback). Returns swept count."""
    swept = 0
    try:
        # only meaningful for _InMemoryFallback; for real Redis TTL handles it
        store = getattr(_r(), "_store", None)
        if isinstance(store, dict):
            for k in list(store.keys()):
                if k.startswith("presence:"):
                    try:
                        data = json.loads(store[k])
                        if _now() - float(data.get("last_seen", 0)) > PRESENCE_TTL * 1.35:
                            store.pop(k, None)
                            swept += 1
                    except Exception:
                        pass
    except Exception:
        pass
    return swept

def heartbeat(uid: str) -> None:
    """Call on every authed request / poll to keep presence fresh."""
    try:
        # preserve in_match if already set
        pres = get_presence([uid]).get(uid, {})
        if pres.get("status") == "in_match":
            return
        set_presence(uid, "online")
    except Exception:
        pass

# ----- recently met -----
def push_recent(uid: str, other_uid: str, other_username: str) -> None:
    if uid == other_uid or uid.startswith("guest:") or other_uid.startswith("guest:"):
        return
    try:
        key = f"recent:{uid}"
        # store as json list of {uid, username, ts}
        raw = _r().get(key)
        lst = json.loads(raw) if raw else []
        # remove existing
        lst = [x for x in lst if x.get("uid") != other_uid]
        lst.insert(0, {"uid": other_uid, "username": other_username, "ts": _now()})
        lst = lst[:RECENT_CAP]
        _r().setex(key, RECENT_TTL, json.dumps(lst))
    except Exception:
        pass

def get_recent(uid: str) -> list[dict]:
    try:
        raw = _r().get(f"recent:{uid}")
        return json.loads(raw) if raw else []
    except Exception:
        return []

# ----- friends -----
def _is_dev_uid(uid: str) -> bool:
    return uid.startswith("dev:") or uid.startswith("guest:") or uid.startswith("clerk:")

def list_friends(uid: str) -> list[dict]:
    svc = _svc()
    if not svc or _is_dev_uid(uid):
        # try Redis cache first (migration path), then mem
        try:
            raw = _r().get(f"friends:{uid}")
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        return _mem_get_friends(uid)
    try:
        rows = svc.table("friends").select("friend_id,since,nickname,favorite").eq("user_id", uid).execute().data or []
        # hydrate usernames
        if not rows:
            return []
        fids = [r["friend_id"] for r in rows]
        profs = svc.table("profiles").select("id,username,avatar_url").in_("id", fids).execute().data or []
        pmap = {p["id"]: p for p in profs}
        out = []
        for r in rows:
            p = pmap.get(r["friend_id"], {})
            out.append({"uid": r["friend_id"], "username": p.get("username") or "Player", "avatar_url": p.get("avatar_url"), "since": r["since"], "nickname": r.get("nickname"), "favorite": bool(r.get("favorite"))})
        # favorites first
        out.sort(key=lambda x: (not x.get("favorite"), x.get("since") or ""))
        return out
    except Exception:
        return _mem_get_friends(uid)

def _count_friends(uid: str) -> int:
    svc = _svc()
    if not svc or _is_dev_uid(uid):
        return len(_mem_get_friends(uid))
    try:
        res = svc.table("friends").select("friend_id", count="exact").eq("user_id", uid).execute()
        return res.count or 0
    except Exception:
        return 0

def are_friends(a: str, b: str) -> bool:
    if not a or not b or a == b:
        return False
    svc = _svc()
    if not svc or _is_dev_uid(a) or _is_dev_uid(b):
        # check both mem and legacy Redis cache (tests use appmod._save_friends → Redis)
        def _has(uid, other):
            if any(f["uid"] == other for f in _mem_get_friends(uid)):
                return True
            try:
                raw = _r().get(f"friends:{uid}")
                if raw:
                    lst = json.loads(raw)
                    return any(x.get("uid") == other for x in lst)
            except Exception:
                pass
            return False
        return _has(a, b) and _has(b, a)
    try:
        r1 = svc.table("friends").select("friend_id").eq("user_id", a).eq("friend_id", b).maybe_single().execute()
        if not (r1 and r1.data):
            return False
        r2 = svc.table("friends").select("friend_id").eq("user_id", b).eq("friend_id", a).maybe_single().execute()
        return bool(r2 and r2.data)
    except Exception:
        return False

def add_friend_pair(a_uid: str, a_name: str, b_uid: str, b_name: str) -> None:
    ts = time.time()
    svc = _svc()
    if not svc or _is_dev_uid(a_uid) or _is_dev_uid(b_uid):
        for uid, other_uid, other_name in [(a_uid, b_uid, b_name), (b_uid, a_uid, a_name)]:
            lst = _mem_get_friends(uid)
            if not any(f["uid"] == other_uid for f in lst):
                lst.append({"uid": other_uid, "username": other_name, "since": ts})
                _mem_set_friends(uid, lst)
        return
    # insert both directions (ignore duplicate)
    for uid, fid in [(a_uid, b_uid), (b_uid, a_uid)]:
        try:
            svc.table("friends").upsert({"user_id": uid, "friend_id": fid}, on_conflict="user_id,friend_id").execute()
        except Exception:
            pass
    # keep Redis cache in sync for legacy readers
    for uid, fid, name in [(a_uid, b_uid, b_name), (b_uid, a_uid, a_name)]:
        try:
            raw = _r().get(f"friends:{uid}")
            lst = json.loads(raw) if raw else []
            if not any(x.get("uid")==fid for x in lst):
                lst.append({"uid": fid, "username": name, "since": ts})
                _r().setex(f"friends:{uid}", 30*86400, json.dumps(lst))
        except Exception:
            pass

def remove_friend_pair(a_uid: str, b_uid: str) -> None:
    svc = _svc()
    if not svc or _is_dev_uid(a_uid) or _is_dev_uid(b_uid):
        for uid, other in [(a_uid, b_uid), (b_uid, a_uid)]:
            _mem_set_friends(uid, [f for f in _mem_get_friends(uid) if f["uid"] != other])
        return
    for uid, fid in [(a_uid, b_uid), (b_uid, a_uid)]:
        try:
            svc.table("friends").delete().eq("user_id", uid).eq("friend_id", fid).execute()
        except Exception:
            pass
        try:
            raw = _r().get(f"friends:{uid}")
            if raw:
                lst = [x for x in json.loads(raw) if x.get("uid") != fid]
                _r().setex(f"friends:{uid}", 30*86400, json.dumps(lst))
        except Exception:
            pass


def update_friend(uid: str, friend_id: str, nickname: str | None = None, favorite: bool | None = None) -> dict | None:
    """Update nickname/favorite for a friend row. Returns updated row dict or None if not friends."""
    if not are_friends(uid, friend_id):
        return None
    if nickname is not None:
        nickname = nickname.strip()[:24]
        if nickname == "":
            nickname = None
    svc = _svc()
    if not svc or _is_dev_uid(uid):
        lst = _mem_get_friends(uid)
        for f in lst:
            if f["uid"] == friend_id:
                if nickname is not None:
                    f["nickname"] = nickname
                if favorite is not None:
                    f["favorite"] = bool(favorite)
                _mem_set_friends(uid, lst)
                # keep Redis cache in sync
                try:
                    _r().setex(f"friends:{uid}", 30*86400, json.dumps(lst))
                except Exception:
                    pass
                return f
        return None
    # Supabase path
    patch: dict = {}
    if nickname is not None:
        patch["nickname"] = nickname
    if favorite is not None:
        patch["favorite"] = bool(favorite)
    if not patch:
        return next((x for x in list_friends(uid) if x["uid"] == friend_id), None)
    try:
        svc.table("friends").update(patch).eq("user_id", uid).eq("friend_id", friend_id).execute()
        # also sync Redis cache if present
        try:
            raw = _r().get(f"friends:{uid}")
            if raw:
                lst = json.loads(raw)
                for x in lst:
                    if x.get("uid") == friend_id:
                        if nickname is not None:
                            x["nickname"] = nickname
                        if favorite is not None:
                            x["favorite"] = bool(favorite)
                _r().setex(f"friends:{uid}", 30*86400, json.dumps(lst))
        except Exception:
            pass
        return next((x for x in list_friends(uid) if x["uid"] == friend_id), None)
    except Exception:
        return None

# ----- friend requests -----
def list_requests(to_uid: str) -> list[dict]:
    svc = _svc()
    if not svc or _is_dev_uid(to_uid):
        try:
            raw = _r().get(f"friend_reqs:{to_uid}")
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        return [dict(x) for x in _MEM_REQS.get(to_uid, [])]
    try:
        rows = svc.table("friend_requests").select("id,from_id,created_at").eq("to_id", to_uid).execute().data or []
        if not rows:
            return []
        from_ids = [r["from_id"] for r in rows]
        profs = svc.table("profiles").select("id,username").in_("id", from_ids).execute().data or []
        pmap = {p["id"]: p.get("username") or "Player" for p in profs}
        # fallback to _MEM_USERS for dev ids
        from db.profiles import _MEM_USERS
        for fid in from_ids:
            if fid not in pmap and fid in _MEM_USERS:
                pmap[fid] = _MEM_USERS[fid]
        return [{"id": r["id"], "from_uid": r["from_id"], "from_username": pmap.get(r["from_id"], "Player"), "ts": r["created_at"]} for r in rows]
    except Exception:
        return [dict(x) for x in _MEM_REQS.get(to_uid, [])]

def has_pending(from_uid: str, to_uid: str) -> bool:
    svc = _svc()
    if not svc or _is_dev_uid(from_uid) or _is_dev_uid(to_uid):
        return any(r.get("from_uid")==from_uid for r in _MEM_REQS.get(to_uid, []))
    try:
        res = svc.table("friend_requests").select("id").eq("from_id", from_uid).eq("to_id", to_uid).maybe_single().execute()
        return bool(res and res.data)
    except Exception:
        return False

def create_request(from_uid: str, from_name: str, to_uid: str) -> dict:
    import uuid as _uuid
    req_id = _uuid.uuid4().hex[:12]
    svc = _svc()
    if not svc or _is_dev_uid(from_uid) or _is_dev_uid(to_uid):
        lst = _MEM_REQS.get(to_uid, [])
        lst.append({"id": req_id, "from_uid": from_uid, "from_username": from_name, "ts": time.time()})
        _MEM_REQS[to_uid] = lst
        # also mirror to Redis for legacy
        try:
            _r().setex(f"friend_reqs:{to_uid}", 30*86400, json.dumps(lst))
        except Exception:
            pass
        return {"id": req_id}
    try:
        svc.table("friend_requests").insert({"id": req_id, "from_id": from_uid, "to_id": to_uid}).execute()
    except Exception as e:
        # duplicate
        msg = str(e).lower()
        if "duplicate" in msg or "unique" in msg:
            raise ValueError("Request already sent")
        raise
    return {"id": req_id}

def delete_request(to_uid: str, req_id: str) -> dict | None:
    svc = _svc()
    if not svc or _is_dev_uid(to_uid):
        lst = _MEM_REQS.get(to_uid, [])
        found = next((x for x in lst if x["id"]==req_id), None)
        _MEM_REQS[to_uid] = [x for x in lst if x["id"]!=req_id]
        try:
            _r().setex(f"friend_reqs:{to_uid}", 30*86400, json.dumps(_MEM_REQS[to_uid]))
        except Exception:
            pass
        return found
    try:
        # fetch first
        res = svc.table("friend_requests").select("id,from_id").eq("id", req_id).eq("to_id", to_uid).maybe_single().execute()
        if not res or not res.data:
            return None
        svc.table("friend_requests").delete().eq("id", req_id).execute()
        return {"id": req_id, "from_uid": res.data["from_id"]}
    except Exception:
        return None

# ----- convenience for stats -----
def head_to_head(a_uid: str, b_uid: str) -> dict:
    """W/D/L for a_uid vs b_uid from ranked + casual games (best effort)."""
    try:
        from db.summaries import list_summaries
        from db.ranked import get_all_ranked_matches
        w=d=l=0
        for s in list_summaries():
            if {s.get("player_a"), s.get("player_b")} == {a_uid, b_uid}:
                if s.get("winner") == "A" and s.get("player_a")==a_uid: w+=1
                elif s.get("winner") == "B" and s.get("player_b")==a_uid: w+=1
                elif s.get("winner") in ("A","B"): l+=1
                else: d+=1
        # ranked is already in summaries via _save_match_summary, so avoid double count
        return {"w": w, "l": l, "d": d}
    except Exception:
        return {"w": 0, "l": 0, "d": 0}
