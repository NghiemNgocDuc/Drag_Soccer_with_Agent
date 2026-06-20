import uuid
import json
from db.redis_client import r

def _get_tournaments_list():
    raw = r.get("tournaments_list")
    return json.loads(raw) if raw else []

def _save_tournaments_list(lst):
    r.setex("tournaments_list", 86400, json.dumps(lst))

def get_tournaments():
    # Sort by created_at desc
    return sorted(_get_tournaments_list(), key=lambda x: x.get("created_at", ""), reverse=True)

def create_tournament(user_id: str, name: str) -> dict:
    tid = str(uuid.uuid4())
    t = {
        "id": tid,
        "creator_id": user_id,
        "name": name,
        "status": "pending",
        "created_at": str(uuid.uuid1()) # quick timestamp
    }
    lst = _get_tournaments_list()
    lst.append(t)
    _save_tournaments_list(lst)
    r.setex(f"tournament:{tid}:parts", 86400, "[]")
    r.setex(f"tournament:{tid}:matches", 86400, "[]")
    return t

def get_tournament(tid: str) -> dict:
    for t in _get_tournaments_list():
        if t["id"] == tid:
            parts = json.loads(r.get(f"tournament:{tid}:parts") or "[]")
            matches = json.loads(r.get(f"tournament:{tid}:matches") or "[]")
            t["participants"] = parts
            t["matches"] = matches
            return t
    return None

def add_participant(tid: str, part_id: str, name: str) -> dict:
    parts = json.loads(r.get(f"tournament:{tid}:parts") or "[]")
    pid = str(uuid.uuid4())
    p = {"id": pid, "tournament_id": tid, "participant_id": part_id, "name": name}
    parts.append(p)
    r.setex(f"tournament:{tid}:parts", 86400, json.dumps(parts))
    return p

def generate_bracket(tid: str) -> bool:
    t = get_tournament(tid)
    if not t or t["status"] != "pending": return False
    
    parts = t.get("participants", [])
    import random
    random.shuffle(parts)
    
    # Simple single elimination: pair up participants
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
            "winner": p1 if p2 is None else None, # auto-advance if bye
            "status": "completed" if p2 is None else "pending",
            "replay_data": None
        })
    
    r.setex(f"tournament:{tid}:matches", 86400, json.dumps(matches))
    
    # Update status to active
    lst = _get_tournaments_list()
    for tr in lst:
        if tr["id"] == tid:
            tr["status"] = "active"
            break
    _save_tournaments_list(lst)
    return True

def advance_tournament(tid: str):
    t = get_tournament(tid)
    if not t: return
    matches = t["matches"]
    
    # Check if all matches in the current round are completed
    pending = [m for m in matches if m["status"] == "pending"]
    if pending:
        return # Not ready for next round
        
    # Find max round
    max_round = max([m["round_num"] for m in matches]) if matches else 0
    round_matches = [m for m in matches if m["round_num"] == max_round]
    
    if len(round_matches) <= 1:
        # Tournament complete
        lst = _get_tournaments_list()
        for tr in lst:
            if tr["id"] == tid:
                tr["status"] = "completed"
                break
        _save_tournaments_list(lst)
        return
        
    # Generate next round
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
            "replay_data": None
        })
        
    matches.extend(next_round_matches)
    r.setex(f"tournament:{tid}:matches", 86400, json.dumps(matches))

def get_match(tid: str, match_id: str):
    matches = json.loads(r.get(f"tournament:{tid}:matches") or "[]")
    for m in matches:
        if m["id"] == match_id: return m
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

