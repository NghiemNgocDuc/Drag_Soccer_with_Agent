"""Clans — persistent via Supabase `clans` / `clan_members` / `clan_requests`, in-memory fallback for DEV_MODE/tests.

Rules:
- One user = one clan at most (enforced app-side; multiple would split identity).
- Create: caller becomes leader + first member.
- Join: if clan.join_type=='open' → immediate member; else → pending request requiring leader approval.
- Leader can approve/decline requests, transfer leadership to a member (then loses leader role — new leader is member, old stays member).
"""
from __future__ import annotations
import uuid
import time
from datetime import datetime, timezone

CLAN_NAME_MIN = 2
CLAN_NAME_MAX = 24
CLAN_DESC_MAX = 120
CLAN_LIMIT_DEFAULT = 20
CLAN_CAP_MIN = 2
CLAN_CAP_MAX = 100

def _svc():
    from db.supabase_client import service
    return service

def _is_dev(uid: str) -> bool:
    return uid.startswith("dev:") or uid.startswith("guest:") or uid.startswith("clerk:")

# ----- in-memory fallback -----
_MEM_CLANS: dict[str, dict] = {}  # id -> {id, name, description, leader_id, join_type, member_limit, created_at}
_MEM_MEMBERS: dict[str, set[str]] = {}  # clan_id -> set(user_id)
_MEM_MEMBER_SINCE: dict[tuple[str,str], str] = {}  # (clan_id, user_id) -> iso ts
_MEM_REQUESTS: dict[str, dict] = {}  # req_id -> {id, clan_id, user_id, username, status, created_at}
_MEM_USER_CLAN: dict[str, str] = {}  # user_id -> clan_id

def _track_history(user_id: str, clan_id: str) -> None:
    try:
        from db.redis_client import r as redis
        redis.sadd(f"clan_history:{user_id}", clan_id)
        redis.expire(f"clan_history:{user_id}", 365*86400)
    except Exception:
        pass

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _user_clan_id(uid: str) -> str | None:
    if _is_dev(uid):
        return _MEM_USER_CLAN.get(uid)
    svc = _svc()
    if not svc:
        return _MEM_USER_CLAN.get(uid)
    try:
        row = svc.table("clan_members").select("clan_id").eq("user_id", uid).maybe_single().execute()
        return (row.data or {}).get("clan_id") if row and row.data else None
    except Exception:
        return _MEM_USER_CLAN.get(uid)

def _hydrate(clan: dict) -> dict:
    cid = clan["id"]
    members = list_members(cid)
    out = dict(clan)
    out["member_count"] = len(members)
    out["members"] = members
    return out

# ----- reads -----
def list_clans() -> list[dict]:
    svc = _svc()
    if not svc:
        return sorted([_hydrate(dict(c)) for c in _MEM_CLANS.values()], key=lambda x: x.get("created_at",""), reverse=True)
    # check if any dev clans exist in mem and supabase is available — return supabase only; dev fallback only when service absent
    # if service present but we have dev uids in mem, they are separate universes — return supabase rows
    try:
        rows = svc.table("clans").select("*").order("created_at", desc=True).execute().data or []
        # if we have mem clans and no supabase rows, fall back to mem (covers dev-only mode where svc exists but empty)
        if not rows and _MEM_CLANS:
            return sorted([_hydrate(dict(c)) for c in _MEM_CLANS.values()], key=lambda x: x.get("created_at",""), reverse=True)
        out = []
        for r in rows:
            out.append(_hydrate(dict(r)))
        return out
    except Exception:
        return sorted([_hydrate(dict(c)) for c in _MEM_CLANS.values()], key=lambda x: x.get("created_at",""), reverse=True)

def get_clan(cid: str) -> dict | None:
    svc = _svc()
    if not svc:
        c = _MEM_CLANS.get(cid)
        return _hydrate(dict(c)) if c else None
    try:
        row = svc.table("clans").select("*").eq("id", cid).maybe_single().execute()
        if not row or not row.data:
            # fallback to mem for dev clans
            c = _MEM_CLANS.get(cid)
            return _hydrate(dict(c)) if c else None
        return _hydrate(dict(row.data))
    except Exception:
        c = _MEM_CLANS.get(cid)
        return _hydrate(dict(c)) if c else None

def list_members(cid: str) -> list[dict]:
    svc = _svc()
    # dev path or no supabase
    if not svc:
        uids = _MEM_MEMBERS.get(cid, set())
        out = []
        for uid in uids:
            # resolve name via mem users
            from db.profiles import _MEM_USERS
            name = _MEM_USERS.get(uid, uid.split(":")[-1][:12] if ":" in uid else uid[:8])
            since = _MEM_MEMBER_SINCE.get((cid, uid), "")
            out.append({"user_id": uid, "username": name, "joined_at": since})
        return out
    # if dev clan stored in mem, serve mem members
    if cid in _MEM_CLANS and not _svc():
        return list_members(cid)
    if cid in _MEM_CLANS:
        # dev clan but service exists — still serve mem members (dev ids are not uuid, supabase query would fail)
        uids = _MEM_MEMBERS.get(cid, set())
        if uids:
            out = []
            for uid in uids:
                from db.profiles import _MEM_USERS
                name = _MEM_USERS.get(uid, uid.split(":")[-1][:12])
                since = _MEM_MEMBER_SINCE.get((cid, uid), "")
                out.append({"user_id": uid, "username": name, "joined_at": since})
            return out
    try:
        rows = svc.table("clan_members").select("user_id,joined_at").eq("clan_id", cid).execute().data or []
        if not rows:
            # check mem fallback for this clan
            if cid in _MEM_CLANS:
                return list_members(cid)
            return []
        uids = [r["user_id"] for r in rows]
        profs = svc.table("profiles").select("id,username").in_("id", uids).execute().data or []
        pmap = {p["id"]: p.get("username") or "Player" for p in profs}
        from db.profiles import _MEM_USERS
        for uid in uids:
            if uid not in pmap and uid in _MEM_USERS:
                pmap[uid] = _MEM_USERS[uid]
        out = []
        for r in rows:
            uid = r["user_id"]
            out.append({"user_id": uid, "username": pmap.get(uid, "Player"), "joined_at": r.get("joined_at")})
        return out
    except Exception:
        # fallback mem
        uids = _MEM_MEMBERS.get(cid, set())
        out = []
        for uid in uids:
            from db.profiles import _MEM_USERS
            name = _MEM_USERS.get(uid, uid[:8])
            since = _MEM_MEMBER_SINCE.get((cid, uid), "")
            out.append({"user_id": uid, "username": name, "joined_at": since})
        return out

def list_requests(cid: str) -> list[dict]:
    svc = _svc()
    if not svc or cid in _MEM_CLANS:
        # mem requests for this clan
        return [dict(v) for v in _MEM_REQUESTS.values() if v.get("clan_id")==cid and v.get("status")=="pending"]
    try:
        rows = svc.table("clan_requests").select("*").eq("clan_id", cid).eq("status", "pending").execute().data or []
        # hydrate username
        if rows:
            uids = [r["user_id"] for r in rows]
            profs = svc.table("profiles").select("id,username").in_("id", uids).execute().data or []
            pmap = {p["id"]: p.get("username") or "Player" for p in profs}
            from db.profiles import _MEM_USERS
            for uid in uids:
                if uid not in pmap and uid in _MEM_USERS:
                    pmap[uid] = _MEM_USERS[uid]
            for r in rows:
                r["username"] = pmap.get(r["user_id"], "Player")
        return rows
    except Exception:
        return [dict(v) for v in _MEM_REQUESTS.values() if v.get("clan_id")==cid and v.get("status")=="pending"]

# ----- writes -----
def _ensure_one_clan(uid: str) -> None:
    if _user_clan_id(uid):
        raise ValueError("You are already in a clan — leave it first")

def create_clan(leader_id: str, name: str, description: str = "", join_type: str = "request", member_limit: int = CLAN_LIMIT_DEFAULT) -> dict:
    name = (name or "").strip()
    if len(name) < CLAN_NAME_MIN or len(name) > CLAN_NAME_MAX:
        raise ValueError(f"Clan name must be {CLAN_NAME_MIN}-{CLAN_NAME_MAX} chars")
    import re
    if not re.match(r"^[a-zA-Z0-9_\- ]+$", name):
        raise ValueError("Clan name: letters, numbers, space, _ - only")
    if join_type not in ("open", "request"):
        raise ValueError("join_type must be open or request")
    description = (description or "").strip()[:CLAN_DESC_MAX]
    member_limit = max(CLAN_CAP_MIN, min(CLAN_CAP_MAX, int(member_limit or CLAN_LIMIT_DEFAULT)))
    _ensure_one_clan(leader_id)
    svc = _svc()
    # dev path: always mem if leader is dev uid
    use_mem = (not svc) or _is_dev(leader_id)
    if use_mem:
        # unique name check mem
        if any(c.get("name","").lower()==name.lower() for c in _MEM_CLANS.values()):
            raise ValueError("Clan name already taken")
        cid = str(uuid.uuid4())
        clan = {"id": cid, "name": name, "description": description, "leader_id": leader_id, "join_type": join_type, "member_limit": member_limit, "created_at": _now_iso()}
        _MEM_CLANS[cid] = clan
        _MEM_MEMBERS[cid] = {leader_id}
        _MEM_MEMBER_SINCE[(cid, leader_id)] = _now_iso()
        _MEM_USER_CLAN[leader_id] = cid
        _track_history(leader_id, cid)
        return _hydrate(dict(clan))
    # supabase
    # check name unique
    existing = svc.table("clans").select("id").eq("name", name).maybe_single().execute()
    if existing and existing.data:
        raise ValueError("Clan name already taken")
    row = svc.table("clans").insert({"name": name, "description": description, "leader_id": leader_id, "join_type": join_type, "member_limit": member_limit}).execute()
    clan = (row.data or [{}])[0] if isinstance(row.data, list) else (row.data or {})
    if not clan.get("id"):
        raise RuntimeError("Failed to create clan")
    svc.table("clan_members").insert({"clan_id": clan["id"], "user_id": leader_id}).execute()
    _track_history(leader_id, clan["id"])
    return _hydrate(dict(clan))

def request_join(user_id: str, clan_id: str, message: str = "") -> dict:
    clan = get_clan(clan_id)
    if not clan:
        raise ValueError("Clan not found")
    if _user_clan_id(user_id):
        raise ValueError("You are already in a clan")
    if any(m.get("user_id")==user_id for m in list_members(clan_id)):
        raise ValueError("Already a member")
    # check existing pending
    for r in list_requests(clan_id):
        if r.get("user_id")==user_id:
            raise ValueError("Join request already pending")
    if clan.get("join_type") == "open":
        # auto-join if not full
        if len(list_members(clan_id)) >= int(clan.get("member_limit", CLAN_LIMIT_DEFAULT)):
            raise ValueError("Clan is full")
        svc = _svc()
        use_mem = (not svc) or _is_dev(user_id) or clan_id in _MEM_CLANS
        if use_mem:
            _MEM_MEMBERS.setdefault(clan_id, set()).add(user_id)
            _MEM_MEMBER_SINCE[(clan_id, user_id)] = _now_iso()
            _MEM_USER_CLAN[user_id] = clan_id
            _track_history(user_id, clan_id)
            return {"joined": True, "clan_id": clan_id}
        svc.table("clan_members").insert({"clan_id": clan_id, "user_id": user_id}).execute()
        _track_history(user_id, clan_id)
        return {"joined": True, "clan_id": clan_id}
    # request mode
    svc = _svc()
    use_mem = (not svc) or _is_dev(user_id) or clan_id in _MEM_CLANS
    if use_mem:
        req_id = str(uuid.uuid4())
        from db.profiles import _MEM_USERS
        username = _MEM_USERS.get(user_id, user_id.split(":")[-1][:12])
        _MEM_REQUESTS[req_id] = {"id": req_id, "clan_id": clan_id, "user_id": user_id, "username": username, "message": (message or "")[:120], "status": "pending", "created_at": _now_iso()}
        return {"joined": False, "request_id": req_id, "status": "pending"}
    row = svc.table("clan_requests").insert({"clan_id": clan_id, "user_id": user_id, "message": (message or "")[:120]}).execute()
    req = (row.data or [{}])[0] if isinstance(row.data, list) else (row.data or {})
    return {"joined": False, "request_id": req.get("id"), "status": "pending"}

def handle_request(leader_id: str, clan_id: str, request_id: str, approve: bool) -> dict:
    clan = get_clan(clan_id)
    if not clan or clan.get("leader_id") != leader_id:
        raise ValueError("Only the leader can handle requests")
    svc = _svc()
    use_mem = (not svc) or _is_dev(leader_id) or clan_id in _MEM_CLANS
    if use_mem:
        req = _MEM_REQUESTS.get(request_id)
        if not req or req.get("clan_id") != clan_id:
            raise ValueError("Request not found")
        if req.get("status") != "pending":
            raise ValueError("Request already handled")
        if approve:
            if len(list_members(clan_id)) >= int(clan.get("member_limit", CLAN_LIMIT_DEFAULT)):
                raise ValueError("Clan is full")
            _MEM_MEMBERS.setdefault(clan_id, set()).add(req["user_id"])
            _MEM_MEMBER_SINCE[(clan_id, req["user_id"])] = _now_iso()
            _MEM_USER_CLAN[req["user_id"]] = clan_id
            _track_history(req["user_id"], clan_id)
            req["status"] = "approved"
        else:
            req["status"] = "declined"
        return req
    # supabase
    row = svc.table("clan_requests").select("*").eq("id", request_id).eq("clan_id", clan_id).maybe_single().execute()
    if not row or not row.data or row.data.get("status") != "pending":
        raise ValueError("Request not found")
    if approve:
        if len(list_members(clan_id)) >= int(clan.get("member_limit", CLAN_LIMIT_DEFAULT)):
            raise ValueError("Clan is full")
        svc.table("clan_members").insert({"clan_id": clan_id, "user_id": row.data["user_id"]}).execute()
        _track_history(row.data["user_id"], clan_id)
        svc.table("clan_requests").update({"status": "approved"}).eq("id", request_id).execute()
    else:
        svc.table("clan_requests").update({"status": "declined"}).eq("id", request_id).execute()
    return row.data

def leave_clan(user_id: str, clan_id: str) -> None:
    clan = get_clan(clan_id)
    if not clan:
        raise ValueError("Clan not found")
    members = list_members(clan_id)
    if not any(m.get("user_id")==user_id for m in members):
        raise ValueError("Not a member")
    if clan.get("leader_id") == user_id:
        raise ValueError("Leader cannot leave — transfer leadership first")
    svc = _svc()
    use_mem = (not svc) or _is_dev(user_id) or clan_id in _MEM_CLANS
    if use_mem:
        _MEM_MEMBERS.get(clan_id, set()).discard(user_id)
        _MEM_MEMBER_SINCE.pop((clan_id, user_id), None)
        _MEM_USER_CLAN.pop(user_id, None)
        return
    svc.table("clan_members").delete().eq("clan_id", clan_id).eq("user_id", user_id).execute()

def transfer_leader(current_leader_id: str, clan_id: str, new_leader_id: str) -> dict:
    clan = get_clan(clan_id)
    if not clan or clan.get("leader_id") != current_leader_id:
        raise ValueError("Only the leader can transfer")
    if new_leader_id == current_leader_id:
        raise ValueError("Already the leader")
    if not any(m.get("user_id")==new_leader_id for m in list_members(clan_id)):
        raise ValueError("New leader must be a member")
    svc = _svc()
    use_mem = (not svc) or _is_dev(current_leader_id) or clan_id in _MEM_CLANS
    if use_mem:
        _MEM_CLANS[clan_id]["leader_id"] = new_leader_id
        return _hydrate(dict(_MEM_CLANS[clan_id]))
    svc.table("clans").update({"leader_id": new_leader_id}).eq("id", clan_id).execute()
    # refresh
    return get_clan(clan_id)

def delete_clan(leader_id: str, clan_id: str) -> None:
    clan = get_clan(clan_id)
    if not clan or clan.get("leader_id") != leader_id:
        raise ValueError("Only the leader can delete")
    svc = _svc()
    use_mem = (not svc) or _is_dev(leader_id) or clan_id in _MEM_CLANS
    if use_mem:
        members = list(_MEM_MEMBERS.get(clan_id, set()))
        for uid in members:
            _MEM_USER_CLAN.pop(uid, None)
            _MEM_MEMBER_SINCE.pop((clan_id, uid), None)
        _MEM_MEMBERS.pop(clan_id, None)
        # remove requests
        for rid in [k for k,v in _MEM_REQUESTS.items() if v.get("clan_id")==clan_id]:
            _MEM_REQUESTS.pop(rid, None)
        _MEM_CLANS.pop(clan_id, None)
        return
    svc.table("clans").delete().eq("id", clan_id).execute()

def rename_clan(leader_id: str, clan_id: str, new_name: str) -> dict:
    clan = get_clan(clan_id)
    if not clan or clan.get("leader_id") != leader_id:
        raise ValueError("Only leader can rename")
    new_name = (new_name or "").strip()
    if len(new_name) < CLAN_NAME_MIN or len(new_name) > CLAN_NAME_MAX:
        raise ValueError(f"Clan name must be {CLAN_NAME_MIN}-{CLAN_NAME_MAX} chars")
    import re
    if not re.match(r"^[a-zA-Z0-9_\- ]+$", new_name):
        raise ValueError("Clan name: letters, numbers, space, _ - only")
    svc = _svc()
    use_mem = (not svc) or _is_dev(leader_id) or clan_id in _MEM_CLANS
    if use_mem:
        if any(c.get("name","").lower()==new_name.lower() and c.get("id")!=clan_id for c in _MEM_CLANS.values()):
            raise ValueError("Clan name already taken")
        _MEM_CLANS[clan_id]["name"] = new_name
        return _hydrate(dict(_MEM_CLANS[clan_id]))
    # check unique
    existing = svc.table("clans").select("id").eq("name", new_name).maybe_single().execute()
    if existing and existing.data and existing.data.get("id") != clan_id:
        raise ValueError("Clan name already taken")
    svc.table("clans").update({"name": new_name}).eq("id", clan_id).execute()
    return get_clan(clan_id)

def add_member_direct(leader_id: str, clan_id: str, target_username: str) -> dict:
    clan = get_clan(clan_id)
    if not clan or clan.get("leader_id") != leader_id:
        raise ValueError("Only leader can add members")
    target_username = (target_username or "").strip()
    if not target_username:
        raise ValueError("Username required")
    # lookup target user id via profiles or mem
    target_id = None
    target_name = None
    svc = _svc()
    try:
        if svc:
            row = svc.table("profiles").select("id,username").eq("username", target_username).maybe_single().execute()
            if row and row.data:
                target_id = row.data["id"]
                target_name = row.data["username"]
            else:
                # try ilike
                res = svc.table("profiles").select("id,username").ilike("username", target_username).limit(1).execute()
                if res.data:
                    target_id = res.data[0]["id"]
                    target_name = res.data[0]["username"]
        if not target_id:
            from db.profiles import _MEM_USERS
            for uid, name in _MEM_USERS.items():
                if name.lower() == target_username.lower():
                    target_id = uid
                    target_name = name
                    break
        if not target_id:
            raise ValueError("User not found")
    except ValueError:
        raise
    except Exception:
        raise ValueError("User lookup failed")
    if _user_clan_id(target_id):
        raise ValueError("User already in a clan")
    if len(list_members(clan_id)) >= int(clan.get("member_limit", CLAN_LIMIT_DEFAULT)):
        raise ValueError("Clan is full")
    use_mem = (not svc) or _is_dev(target_id) or clan_id in _MEM_CLANS
    if use_mem:
        _MEM_MEMBERS.setdefault(clan_id, set()).add(target_id)
        _MEM_MEMBER_SINCE[(clan_id, target_id)] = _now_iso()
        _MEM_USER_CLAN[target_id] = clan_id
        _track_history(target_id, clan_id)
        return {"user_id": target_id, "username": target_name}
    svc.table("clan_members").insert({"clan_id": clan_id, "user_id": target_id}).execute()
    _track_history(target_id, clan_id)
    # clear any pending request
    try:
        svc.table("clan_requests").delete().eq("clan_id", clan_id).eq("user_id", target_id).execute()
    except Exception:
        pass
    return {"user_id": target_id, "username": target_name}

def create_invite_link(leader_id: str, clan_id: str) -> str:
    clan = get_clan(clan_id)
    if not clan or clan.get("leader_id") != leader_id:
        raise ValueError("Only leader can create invite link")
    token = uuid.uuid4().hex[:12]
    from db.redis_client import r as redis
    try:
        redis.setex(f"clan_invite:{token}", 7*86400, clan_id)
    except Exception:
        pass
    return token

def join_via_invite(user_id: str, token: str) -> dict:
    from db.redis_client import r as redis
    try:
        clan_id = redis.get(f"clan_invite:{token}")
        if isinstance(clan_id, bytes):
            clan_id = clan_id.decode()
    except Exception:
        clan_id = None
    if not clan_id:
        raise ValueError("Invalid or expired invite link")
    # use request_join logic which handles open vs request
    return request_join(user_id, clan_id)

def my_clan(user_id: str) -> dict | None:
    cid = _user_clan_id(user_id)
    return get_clan(cid) if cid else None
