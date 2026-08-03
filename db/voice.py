"""Voice-chat signaling relay for 1v1 online matches.

Signaling piggybacks on the existing HTTP-poll match sync: messages are
queued in a Redis list per room and drained by the same `/state` poll that
syncs match state. Only the two room participants may write or read, and
blocked users' signals are never delivered (both directions).
"""
import json

from db.redis_client import r

VOICE_TTL      = 3600 * 6    # match room TTL
VOICE_MAX      = 100         # cap queue length per room
SIGNAL_TYPES   = ("offer", "answer", "ice", "mute")


def send_voice_signal(room_id: str, sender_id: str, sig_type: str,
                      data: dict) -> dict:
    if sig_type not in SIGNAL_TYPES:
        raise ValueError(f"Invalid signal type: {sig_type!r}")
    key = f"voice_signal:{room_id}"
    raw = r.get(key)
    msgs = json.loads(raw) if raw else []
    if len(msgs) >= VOICE_MAX:
        msgs = msgs[-(VOICE_MAX - 1):]
    sig = {
        "seq":  len(msgs),
        "type": sig_type,
        "from": sender_id,
        "data": data or {},
    }
    msgs.append(sig)
    r.setex(key, VOICE_TTL, json.dumps(msgs))
    return sig


def get_voice_signals(room_id: str, after: int | None,
                      blocked: set | frozenset = frozenset()
                      ) -> tuple[list[dict], str]:
    key = f"voice_signal:{room_id}"
    raw = r.get(key)
    msgs = json.loads(raw) if raw else []
    blocked = set(blocked or ())
    if after is None or after < 0:
        start = 0
    else:
        start = after + 1
    out = []
    for m in msgs[start:]:
        if m.get("from") in blocked:
            continue
        out.append({
            "seq":  m["seq"],
            "type": m["type"],
            "from": m["from"],
            "data": m.get("data") or {},
        })
    next_after = str(msgs[-1]["seq"]) if msgs else str(after if after is not None else -1)
    return out, next_after
