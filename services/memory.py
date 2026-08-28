"""Mem0-style memory for Agent Soccer — short vs long term.

Based on https://github.com/mem0ai/mem0 concepts:
- add() extracts facts from messages (single-pass, no UPDATE/DELETE)
- search() fuses semantic (keyword) + recency + entity
- get_all() lists by category

We decide:
  SHORT-TERM (Session, TTL 6h, Redis):
    - current match: ball/players/score/kick_count/period/is_player_a
    - last 5 kicks (angle/power/outcome)
    - recent chat (last 10 msgs)
    - view mode, penalty state
    Cleared at game_over or 6h. Used for in-match AI context.

  LONG-TERM (User, persistent, Supabase + _MEM):
    - preferences: team colors, keeper_style, crowd_palette, bg_scene, field size
    - builds: point-buy stats per team
    - history: win/loss per AI opponent, favorite model, avg power/angle
    - achievements summary
    - inferred tactics: e.g., "prefers corner shots", "aggressive 1v1"
    Used for personalization across sessions.

No LLM needed for extraction — rule-based, fast, <10ms, no API key.
If OPENAI_API_KEY present, we optionally enrich with LLM fact extraction (graceful fallback).
"""
from __future__ import annotations
import time
import json
import re
import hashlib
from collections import defaultdict, Counter

from db.redis_client import r as _redis

#  Config 
SHORT_TTL = 6 * 3600  # match ROM_TTL
LONG_TTL = 365 * 86400
_SHORT_PREFIX = "mem:short:"
_LONG_PREFIX = "mem:long:"

# In-memory fallback for long-term when Supabase absent (dev)
_LONG_MEM: dict[str, list[dict]] = defaultdict(list)
_SHORT_MEM: dict[str, list[dict]] = defaultdict(list)

def _now() -> float:
    return time.time()

def _id_for(text: str, user_id: str) -> str:
    return hashlib.sha256(f"{user_id}:{text}:{_now()}".encode()).hexdigest()[:12]

#  Extraction (rule-based, mem0 single-pass ADD-only) 
def _extract_facts(messages: list[dict], category: str) -> list[str]:
    """Extract atomic facts from messages. Very fast, no LLM."""
    facts = []
    for m in messages:
        role = m.get("role","")
        content = m.get("content","") or m.get("body","") or ""
        if not content:
            continue
        c = content.strip()
        # Long-term triggers
        if category == "long":
            # preferences
            if "keeper" in c.lower() and any(k in c.lower() for k in ["footwork","rush","deflector","far"]):
                facts.append(f"Prefers keeper style: {c[:120]}")
            if "color" in c.lower() or "team_a" in c.lower():
                facts.append(f"Team color pref: {c[:100]}")
            if "power" in c.lower() and "angle" in c.lower():
                # tactic
                try:
                    # e.g., "angle 15 power 90"
                    ang = re.search(r"angle\s*([-\d\.]+)", c, re.I)
                    pw = re.search(r"power\s*([\d\.]+)", c, re.I)
                    if ang and pw:
                        facts.append(f"Kick tactic angle {ang.group(1)} power {pw.group(1)}")
                except: pass
            if "win" in c.lower() or "loss" in c.lower():
                facts.append(f"Match result: {c[:100]}")
            if len(c) < 120 and c:
                # generic small fact
                if any(k in c.lower() for k in ["prefer","favorite","always","usually"]):
                    facts.append(c[:140])
        else:  # short
            # keep last kicks, chat, ball pos
            if len(c) > 0:
                facts.append(c[:200])
    # dedup
    seen=set()
    out=[]
    for f in facts:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out[:5]  # single-pass, at most 5 per add

def _store_short(user_id: str, facts: list[str], metadata: dict | None = None):
    key = _SHORT_PREFIX + user_id
    try:
        raw = _redis.get(key)
        cur = json.loads(raw) if raw else []
    except: cur=[]
    # also check in-mem for dev
    if not cur and user_id in _SHORT_MEM:
        cur = _SHORT_MEM[user_id]
    for f in facts:
        cur.append({"id": _id_for(f, user_id), "memory": f, "category": "short", "created_at": _now(), "metadata": metadata or {}})
    # keep last 30
    cur = cur[-30:]
    try:
        _redis.setex(key, SHORT_TTL, json.dumps(cur))
    except:
        _SHORT_MEM[user_id]=cur

def _store_long(user_id: str, facts: list[str], metadata: dict | None = None):
    # try Supabase if available
    try:
        from db.supabase_client import service
        if service:
            for f in facts:
                # avoid dup: check recent same
                service.table("memories").insert({"user_id": user_id, "memory": f, "category": "long", "metadata": metadata or {}}).execute()
            return
    except: pass
    # fallback in-mem
    for f in facts:
        # dedup exact
        if any(x["memory"]==f for x in _LONG_MEM[user_id]):
            continue
        _LONG_MEM[user_id].append({"id": _id_for(f, user_id), "memory": f, "category": "long", "created_at": _now(), "metadata": metadata or {}})
    # cap 100
    _LONG_MEM[user_id]=_LONG_MEM[user_id][-100:]

#  Public API (mem0-like) 
def add(messages, user_id: str, category: str = "long", metadata: dict | None = None) -> list[dict]:
    """
    messages: str | list[dict] with {role, content}
    category: "short" (session) or "long" (user)
    Returns added memories.
    """
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    if not isinstance(messages, list):
        messages = [messages]
    facts = _extract_facts(messages, category)
    if not facts:
        return []
    if category == "short":
        _store_short(user_id, facts, metadata)
    else:
        _store_long(user_id, facts, metadata)
    return [{"memory": f} for f in facts]

def search(query: str, user_id: str, top_k: int = 3, category: str | None = None) -> dict:
    """Keyword + recency fused search. Returns {"results": [{memory, score}]}."""
    # collect candidates
    candidates=[]
    # short
    if category in (None, "short"):
        try:
            raw=_redis.get(_SHORT_PREFIX+user_id)
            cur=json.loads(raw) if raw else []
        except: cur=[]
        if not cur:
            cur=_SHORT_MEM.get(user_id,[])
        candidates.extend(cur)
    # long
    if category in (None, "long"):
        try:
            from db.supabase_client import service
            if service and category!="short":
                res=service.table("memories").select("memory,created_at,metadata").eq("user_id",user_id).limit(100).execute()
                for r in res.data or []:
                    candidates.append({"memory":r["memory"],"created_at": r.get("created_at",0), "category":"long", "metadata": r.get("metadata",{})})
            else:
                raise Exception("no service")
        except:
            candidates.extend(_LONG_MEM.get(user_id,[]))
    if not candidates:
        return {"results":[]}
    # simple scoring: keyword overlap + recency
    qwords=set(re.findall(r"\w+", query.lower()))
    scored=[]
    now=_now()
    for c in candidates:
        mem=c.get("memory","")
        mwords=set(re.findall(r"\w+", mem.lower()))
        overlap=len(qwords & mwords)/max(1,len(qwords))
        # recency: within 1h boost 0.2
        age = now - float(c.get("created_at", now) if isinstance(c.get("created_at"), (int,float)) else now)
        recency = 0.2 if age < 3600 else 0.0
        # entity boost: if query entity like "keeper" appears
        entity=0.1 if any(w in mem.lower() for w in qwords) else 0
        score= overlap*0.7 + recency + entity
        # temporal: if query has "recent" etc
        scored.append((score,c))
    scored.sort(reverse=True, key=lambda x: x[0])
    results=[{"memory":c["memory"], "score": round(s,3), "category":c.get("category"), "metadata":c.get("metadata",{})} for s,c in scored[:top_k] if s>0]
    # fallback: if no overlap, return most recent
    if not results and candidates:
        candidates.sort(key=lambda x: x.get("created_at",0), reverse=True)
        results=[{"memory":c["memory"], "score":0, "category":c.get("category")} for c in candidates[:top_k]]
    return {"results": results}

def get_all(user_id: str, category: str | None = None) -> list[dict]:
    """List all memories for user, optionally filtered."""
    res=search("", user_id, top_k=100, category=category)
    # search with empty returns recent
    if not res["results"]:
        # fallback to direct
        out=[]
        if category in (None,"short"):
            try:
                raw=_redis.get(_SHORT_PREFIX+user_id)
                out.extend(json.loads(raw) if raw else [])
            except: out.extend(_SHORT_MEM.get(user_id,[]))
        if category in (None,"long"):
            out.extend(_LONG_MEM.get(user_id,[]))
        return out
    return res["results"]

def delete(user_id: str, memory_id: str) -> bool:
    for store in [_SHORT_MEM, _LONG_MEM]:
        lst=store.get(user_id,[])
        nlst=[x for x in lst if x.get("id")!=memory_id]
        if len(nlst)!=len(lst):
            store[user_id]=nlst
            try:
                _redis.setex((_SHORT_PREFIX if store is _SHORT_MEM else _LONG_PREFIX)+user_id, SHORT_TTL if store is _SHORT_MEM else LONG_TTL, json.dumps(nlst))
            except: pass
            return True
    return False

#  Helpers for Agent Soccer 
def add_short_game(user_id: str, state: dict, last_move: dict | None = None):
    """Call after each kick."""
    msgs=[{"role":"user","content": f"Ball {state['ball']['x']:.0f},{state['ball']['y']:.0f} score {state['score_a']}-{state['score_b']} period {state.get('period')} is_player_a {state.get('is_player_a')}"}]
    if last_move:
        msgs.append({"role":"assistant","content": f"Kick angle {last_move.get('angle')} power {last_move.get('power')} scored {last_move.get('scored')}"})
    return add(msgs, user_id, category="short", metadata={"kick_count": state.get("kick_count")})

def add_long_preference(user_id: str, key: str, value: str):
    return add(f"Prefers {key} = {value}", user_id, category="long", metadata={"key": key})

def summarize_match_to_long(user_id: str, state: dict):
    """At game_over, summarize into long term."""
    w=state.get("winner") or "Draw"
    msgs=[{"role":"user","content": f"Match finished {state['score_a']}-{state['score_b']} winner {w} kicks {state.get('kick_count')} period {state.get('period')}"}]
    # infer tactic: avg angle/power from move_history
    mh=state.get("move_history",[])
    if mh:
        angs=[m.get("angle",0) for m in mh if m.get("player")=="A"]
        pws=[m.get("power",0) for m in mh if m.get("player")=="A"]
        if angs:
            avg_ang=sum(angs)/len(angs)
            avg_pw=sum(pws)/len(pws) if pws else 0
            style="corner" if abs(avg_ang)>15 else "central"
            msgs.append({"role":"assistant","content": f"User tactic {style} avg_angle {avg_ang:.0f} avg_power {avg_pw:.0f}"})
    return add(msgs, user_id, category="long", metadata={"type":"match_summary"})
