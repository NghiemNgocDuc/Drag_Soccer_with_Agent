"""Community gallery — public models with likes + comments, Supabase with mem fallback.

Public models are user_models.is_public=true. Likes/comments are separate tables
(model_likes, model_comments) so we never expose private code via RLS.
"""
from __future__ import annotations
import time
from datetime import datetime, timezone

def _svc():
    from db.supabase_client import service
    return service

# mem fallback for DEV_MODE / tests
_MEM_LIKES: dict[tuple[str,str], str] = {}  # (model_id,user_id) -> iso
_MEM_COMMENTS: list[dict] = []  # {id, model_id, user_id, username, body, created_at}

def _is_dev(uid: str) -> bool:
    return uid.startswith("dev:") or uid.startswith("guest:") or uid.startswith("clerk:")

def list_public_models(limit: int = 50, offset: int = 0, q: str | None = None) -> tuple[list[dict], int]:
    svc=_svc()
    if not svc:
        # dev: list from user_models mem where is_public
        from db.user_models import _MEM
        rows=[v for v in _MEM.values() if v.get("is_public")]
        if q:
            ql=q.lower()
            rows=[r for r in rows if ql in r.get("name","").lower() or ql in r.get("description","").lower()]
        # enrich with likes/comments counts from mem
        out=[]
        for r in sorted(rows, key=lambda x: x.get("created_at",""), reverse=True)[offset:offset+limit]:
            mid=r["id"]
            likes=sum(1 for (m,_) in _MEM_LIKES if m==mid)
            comments=sum(1 for c in _MEM_COMMENTS if c["model_id"]==mid)
            # benchmark if any
            try:
                from db.leaderboard import get_submission
                sub=get_submission(mid)
                score=sub.get("score") if sub else None
            except Exception:
                score=None
            # owner name
            from db.profiles import _MEM_USERS
            owner_name=_MEM_USERS.get(r.get("user_id"), r.get("user_id","")[:8])
            out.append({**r, "likes":likes, "comments":comments, "score":score, "owner_name":owner_name})
        return out, len([v for v in _MEM.values() if v.get("is_public")])
    # supabase
    try:
        query=svc.table("user_models").select("id,user_id,name,description,links,created_at,updated_at").eq("is_public", True)
        if q:
            # ilike on name or description
            query=query.or_(f"name.ilike.%{q}%,description.ilike.%{q}%")
        res=query.order("created_at", desc=True).range(offset, offset+limit-1).execute()
        rows=res.data or []
        # counts
        out=[]
        for r in rows:
            mid=r["id"]
            # likes count
            lc=svc.table("model_likes").select("user_id", count="exact").eq("model_id", mid).execute()
            likes=lc.count or 0
            cc=svc.table("model_comments").select("id", count="exact").eq("model_id", mid).execute()
            comments=cc.count or 0
            # owner
            prof=svc.table("profiles").select("username").eq("id", r["user_id"]).maybe_single().execute()
            owner_name=(prof.data or {}).get("username") if prof and prof.data else r["user_id"][:8]
            # bench score
            try:
                from db.leaderboard import get_submission
                sub=get_submission(mid)
                score=sub.get("score") if sub else None
            except Exception:
                score=None
            out.append({**r, "likes":likes, "comments":comments, "score":score, "owner_name":owner_name})
        # total count
        total=svc.table("user_models").select("id", count="exact").eq("is_public", True).execute().count or len(rows)
        return out, total
    except Exception:
        return [], 0

def get_public_model(model_id: str) -> dict | None:
    svc=_svc()
    if not svc:
        from db.user_models import _MEM
        r=_MEM.get(model_id)
        if not r or not r.get("is_public"):
            return None
        likes=sum(1 for (m,_) in _MEM_LIKES if m==model_id)
        comments=[c for c in _MEM_COMMENTS if c["model_id"]==model_id]
        try:
            from db.leaderboard import get_submission
            sub=get_submission(model_id)
            score=sub.get("score") if sub else None
            details=sub.get("details") if sub else None
        except Exception:
            score=None; details=None
        from db.profiles import _MEM_USERS
        owner_name=_MEM_USERS.get(r.get("user_id"), r.get("user_id","")[:8])
        return {**r, "likes":likes, "comments":comments, "score":score, "details":details, "owner_name":owner_name}
    try:
        row=svc.table("user_models").select("id,user_id,name,description,code,links,created_at").eq("id", model_id).eq("is_public", True).maybe_single().execute()
        if not row or not row.data:
            return None
        r=row.data
        lc=svc.table("model_likes").select("user_id", count="exact").eq("model_id", model_id).execute()
        likes=lc.count or 0
        comm=svc.table("model_comments").select("id,user_id,body,created_at").eq("model_id", model_id).order("created_at", desc=True).limit(100).execute().data or []
        # hydrate comment usernames
        if comm:
            uids=list({c["user_id"] for c in comm})
            profs=svc.table("profiles").select("id,username").in_("id", uids).execute().data or []
            pmap={p["id"]:p["username"] for p in profs}
            for c in comm:
                c["username"]=pmap.get(c["user_id"], c["user_id"][:8])
        try:
            from db.leaderboard import get_submission
            sub=get_submission(model_id)
            score=sub.get("score") if sub else None
            details=sub.get("details") if sub else None
        except Exception:
            score=None; details=None
        prof=svc.table("profiles").select("username").eq("id", r["user_id"]).maybe_single().execute()
        owner_name=(prof.data or {}).get("username") if prof and prof.data else r["user_id"][:8]
        return {**r, "likes":likes, "comments":comm, "score":score, "details":details, "owner_name":owner_name}
    except Exception:
        return None

def toggle_like(model_id: str, user_id: str) -> tuple[bool, int]:
    """Toggle like, return (liked, total_likes). liked True means now liked."""
    svc=_svc()
    if not svc or _is_dev(user_id):
        key=(model_id,user_id)
        if key in _MEM_LIKES:
            del _MEM_LIKES[key]
            liked=False
        else:
            _MEM_LIKES[key]=datetime.now(timezone.utc).isoformat()
            liked=True
        total=sum(1 for (m,_) in _MEM_LIKES if m==model_id)
        return liked, total
    # check existing
    ex=svc.table("model_likes").select("user_id").eq("model_id", model_id).eq("user_id", user_id).maybe_single().execute()
    if ex and ex.data:
        svc.table("model_likes").delete().eq("model_id", model_id).eq("user_id", user_id).execute()
        liked=False
    else:
        svc.table("model_likes").insert({"model_id": model_id, "user_id": user_id}).execute()
        liked=True
    cnt=svc.table("model_likes").select("user_id", count="exact").eq("model_id", model_id).execute().count or 0
    return liked, cnt

def add_comment(model_id: str, user_id: str, username: str, body: str) -> dict:
    body=(body or "").strip()
    if not body or len(body)>500:
        raise ValueError("Comment must be 1-500 chars")
    svc=_svc()
    if not svc or _is_dev(user_id):
        import uuid
        cid=str(uuid.uuid4())
        c={"id":cid,"model_id":model_id,"user_id":user_id,"username":username,"body":body,"created_at":datetime.now(timezone.utc).isoformat()}
        _MEM_COMMENTS.append(c)
        return c
    row=svc.table("model_comments").insert({"model_id": model_id, "user_id": user_id, "body": body}).execute()
    data=(row.data or [{}])[0] if isinstance(row.data, list) else (row.data or {})
    # hydrate username
    data["username"]=username
    return data

def has_liked(model_id: str, user_id: str) -> bool:
    svc=_svc()
    if not svc or _is_dev(user_id):
        return (model_id,user_id) in _MEM_LIKES
    ex=svc.table("model_likes").select("user_id").eq("model_id", model_id).eq("user_id", user_id).maybe_single().execute()
    return bool(ex and ex.data)
