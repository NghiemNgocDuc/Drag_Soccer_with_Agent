import uuid
import json
from datetime import datetime
from db.redis_client import r

def _get_tournaments_list():
    raw = r.get("tournaments_list")
    return json.loads(raw) if raw else []

def _save_tournaments_list(lst):
    r.setex("tournaments_list", 86400, json.dumps(lst))

def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None

def _now_utc() -> datetime:
    from datetime import timezone
    return datetime.now(timezone.utc)

def create_tournament(user_id: str, name: str, clan_id: str | None = None, scheduled_at: str | None = None) -> dict:
    tid = str(uuid.uuid4())
    t = {
        "id": tid,
        "creator_id": user_id,
        "name": name,
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
        "clan_id": clan_id,
        "scheduled_at": scheduled_at,
    }
    lst = _get_tournaments_list()
    lst.append(t)
    _save_tournaments_list(lst)
    r.setex(f"tournament:{tid}:parts", 86400, "[]")
    r.setex(f"tournament:{tid}:matches", 86400, "[]")
    return t

def check_disqualifications(tid: str) -> bool:
    """If match scheduled_at +10min passed and player hasn't joined, disqualify."""
    # Direct read without calling get_tournament to avoid recursion
    lst = _get_tournaments_list()
    t = next((x for x in lst if x["id"] == tid), None)
    if not t:
        return False
    matches = json.loads(r.get(f"tournament:{tid}:matches") or "[]")
    if not matches:
        return False
    changed = False
    now = _now_utc()
    for m in matches:
        if m.get("status") != "pending":
            continue
        sched = _parse_iso(m.get("scheduled_at"))
        if not sched:
            continue
        if sched.tzinfo is None:
            from datetime import timezone
            sched = sched.replace(tzinfo=timezone.utc)
        deadline = sched.timestamp() + 600
        if now.timestamp() < deadline:
            continue
        ja = m.get("joined_a", False)
        jb = m.get("joined_b", False)
        if ja and not jb:
            m["winner"] = m.get("participant_a")
            m["status"] = "completed"
            m["disqualified"] = m.get("participant_b")
            changed = True
        elif jb and not ja:
            m["winner"] = m.get("participant_b")
            m["status"] = "completed"
            m["disqualified"] = m.get("participant_a")
            changed = True
        elif not ja and not jb:
            if m.get("participant_a"):
                m["winner"] = m.get("participant_a")
                m["status"] = "completed"
                m["disqualified"] = m.get("participant_b")
                changed = True
    if changed:
        r.setex(f"tournament:{tid}:matches", 86400, json.dumps(matches))
        advance_tournament(tid)
    return changed

def get_tournament(tid: str) -> dict | None:
    for t in _get_tournaments_list():
        if t["id"] == tid:
            parts = json.loads(r.get(f"tournament:{tid}:parts") or "[]")
            matches = json.loads(r.get(f"tournament:{tid}:matches") or "[]")
            t["participants"] = parts
            t["matches"] = matches
            check_disqualifications(tid)
            matches = json.loads(r.get(f"tournament:{tid}:matches") or "[]")
            t["matches"] = matches
            return t
    return None

def get_tournaments(clan_id: str | None = None) -> list[dict]:
    all_t = _get_tournaments_list()
    if clan_id is not None:
        all_t = [x for x in all_t if x.get("clan_id") == clan_id]
    return sorted(all_t, key=lambda x: x.get("created_at", ""), reverse=True)

def add_participant(tid: str, part_id: str, name: str) -> dict:
    parts = json.loads(r.get(f"tournament:{tid}:parts") or "[]")
    pid = str(uuid.uuid4())
    p = {"id": pid, "tournament_id": tid, "participant_id": part_id, "name": name}
    parts.append(p)
    r.setex(f"tournament:{tid}:parts", 86400, json.dumps(parts))
    return p

def generate_bracket(tid: str, match_scheduled_at: str | None = None) -> bool:
    t = get_tournament(tid)
    if not t or t["status"] != "pending":
        return False
    parts = t.get("participants", [])
    import random
    random.shuffle(parts)
    matches = []
    for i in range(0, len(parts), 2):
        p1 = parts[i]["id"]
        p2 = parts[i+1]["id"] if i+1 < len(parts) else None
        matches.append({
            "id": str(uuid.uuid4()),
            "tournament_id": tid,
            "round_num": 1,
            "match_index": i//2,
            "participant_a": p1,
            "participant_b": p2,
            "winner": p1 if p2 is None else None,
            "status": "completed" if p2 is None else "pending",
            "replay_data": None,
            "scheduled_at": match_scheduled_at,
            "joined_a": False,
            "joined_b": False,
        })
    r.setex(f"tournament:{tid}:matches", 86400, json.dumps(matches))
    lst = _get_tournaments_list()
    for tr in lst:
        if tr["id"] == tid:
            tr["status"] = "active"
            break
    _save_tournaments_list(lst)
    return True

def mark_joined(tid: str, match_id: str, participant_id: str) -> bool:
    matches = json.loads(r.get(f"tournament:{tid}:matches") or "[]")
    for m in matches:
        if m["id"] == match_id:
            # find which side
            if m.get("participant_a") == participant_id:
                m["joined_a"] = True
            elif m.get("participant_b") == participant_id:
                m["joined_b"] = True
            else:
                return False
            r.setex(f"tournament:{tid}:matches", 86400, json.dumps(matches))
            return True
    return False

def advance_tournament(tid: str):
    t = get_tournament(tid)
    if not t:
        return
    matches = t["matches"]
    pending = [m for m in matches if m["status"] == "pending"]
    if pending:
        return
    max_round = max([m["round_num"] for m in matches]) if matches else 0
    round_matches = [m for m in matches if m["round_num"] == max_round]
    if len(round_matches) <= 1:
        lst = _get_tournaments_list()
        for tr in lst:
            if tr["id"] == tid:
                tr["status"] = "completed"
                break
        _save_tournaments_list(lst)
        return
    next_round_matches = []
    for i in range(0, len(round_matches), 2):
        m1 = round_matches[i]
        m2 = round_matches[i+1] if i+1 < len(round_matches) else None
        w1 = m1["winner"]
        w2 = m2["winner"] if m2 else None
        next_round_matches.append({
            "id": str(uuid.uuid4()),
            "tournament_id": tid,
            "round_num": max_round + 1,
            "match_index": i//2,
            "participant_a": w1,
            "participant_b": w2,
            "winner": w1 if w2 is None else None,
            "status": "completed" if w2 is None else "pending",
            "replay_data": None,
            "scheduled_at": None,
            "joined_a": False,
            "joined_b": False,
        })
    matches.extend(next_round_matches)
    r.setex(f"tournament:{tid}:matches", 86400, json.dumps(matches))

def get_match(tid: str, match_id: str):
    matches = json.loads(r.get(f"tournament:{tid}:matches") or "[]")
    for m in matches:
        if m["id"] == match_id:
            return m
    return None

def save_match_result(tid: str, match_id: str, winner: str, replay_data: dict):
    matches = json.loads(r.get(f"tournament:{tid}:matches") or "[]")
    for m in matches:
        if m["id"] == match_id:
            m["winner"] = winner
            m["status"] = "completed"
            m["replay_data"] = replay_data
            break
    r.setex(f"tournament:{tid}:matches", 86400, json.dumps(matches))
    advance_tournament(tid)
