"""Bounded, monotonic WebRTC signaling for online match participants."""
import json
import logging
import threading
from contextlib import contextmanager

from db.redis_client import r

VOICE_TTL = 3600 * 6
VOICE_MAX = 100
SIGNAL_TYPES = ("offer", "answer", "ice", "mute")
_SIGNAL_LOCKS = tuple(threading.Lock() for _ in range(64))


@contextmanager
def _signal_write_lock(room_id):
    """Serialize append across workers; use bounded local stripes in dev."""
    if hasattr(r, "lock"):
        lock = r.lock(f"voice_signal_lock:{room_id}", timeout=15, blocking_timeout=1)
        if not lock.acquire(blocking=True):
            raise RuntimeError("Voice signaling is busy. Try again.")
        try:
            yield
        finally:
            try:
                lock.release()
            except Exception:
                logging.getLogger(__name__).warning("Voice signaling lock expired for %s", room_id)
    else:
        lock = _SIGNAL_LOCKS[hash(room_id) % len(_SIGNAL_LOCKS)]
        if not lock.acquire(timeout=1):
            raise RuntimeError("Voice signaling is busy. Try again.")
        try:
            yield
        finally:
            lock.release()


def send_voice_signal(room_id: str, sender_id: str, sig_type: str, data: dict) -> dict:
    if sig_type not in SIGNAL_TYPES:
        raise ValueError(f"Invalid signal type: {sig_type!r}")
    if not isinstance(data, dict):
        raise ValueError("Signal data must be a JSON object")
    key = f"voice_signal:{room_id}"
    with _signal_write_lock(room_id):
        raw = r.get(key)
        msgs = json.loads(raw) if raw else []
        # The sequence belongs to the room, not the retained list length.
        # Trimming the oldest ICE candidates must never stall a peer's cursor.
        sequence = int(msgs[-1]["seq"]) + 1 if msgs else 0
        sig = {"seq": sequence, "type": sig_type, "from": sender_id, "data": data}
        msgs.append(sig)
        r.setex(key, VOICE_TTL, json.dumps(msgs[-VOICE_MAX:]))
    return sig


def get_voice_signals(room_id: str, after: int | None,
                      blocked: set | frozenset = frozenset()) -> tuple[list[dict], str]:
    raw = r.get(f"voice_signal:{room_id}")
    msgs = json.loads(raw) if raw else []
    blocked = set(blocked or ())
    cursor = after if after is not None else -1
    out = [{"seq": m["seq"], "type": m["type"], "from": m["from"], "data": m.get("data") or {}}
           for m in msgs if int(m["seq"]) > cursor and m.get("from") not in blocked]
    next_after = str(msgs[-1]["seq"]) if msgs else str(cursor)
    return out, next_after
