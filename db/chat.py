"""Chat storage & helpers.

Two stores, one message shape:
- Ephemeral (match, tournament-lobby): Redis list ``chat:{scope}:{scope_id}``
  with a TTL matching the surrounding room/tournament lifetime. Message id
  (``mid``) is the 0-based index in the list — a monotonic poll cursor.
- Persisted (friend DMs): Supabase ``messages`` table; ``mid`` is the
  global ``seq`` column (also monotonic). Conversation id = "uidA|uidB"
  (sorted, "|" separator).

Also: profanity filter, per-user send rate limiting, block lists and the
report queue (``reported_messages``).
"""
from __future__ import annotations
import json
import re
import time

from db.redis_client import r
from db.supabase_client import service

try:
    from better_profanity import Profanity
    _profanity = Profanity()
    _profanity.add_censor_words([
        "fck", "fuk", "fuvk", "fak", "sh1t", "shyt", "sht", "b1tch", "bitchh",
        "assh0le", "a55hole", "d1ck", "dik", "c0ck", "kunt", "tw4t", "w4nk",
        "n1gger", "nigg3r", "ret4rd", "r3tard", "aut1sm",
    ])
except Exception:
    _profanity = None


class ChatUnavailable(Exception):
    """Chat storage (Supabase) is not configured."""


MATCH_TTL  = 6 * 3600     # matches ROOM_TTL
LOBBY_TTL  = 24 * 3600    # matches tournament key TTL
GLOBAL_TTL = 24 * 3600   # server-wide chat
READ_TTL   = 30 * 86400   # read cursors
MAX_BODY_LEN   = 280
CHAT_RATE_MAX  = 10       # messages per window per user
CHAT_RATE_WINDOW = 10     # seconds
FETCH_LIMIT    = 60
CONV_LIMIT     = 50

_EMOJI_RE = re.compile(
    "[" 
    "\U0001F000-\U0001FAFF"      # misc symbols & pictographs + emoji
    "\U0001F1E6-\U0001F1FF"      # regional indicators (flags)
    "\U00002600-\U000027BF"      # misc symbols + dingbats
    "\U00002300-\U000023FF"      # misc technical (watch, hourglass…)
    "\U00002B00-\U00002BFF"      # misc arrows/geometry blocks
    "\uFE0F\u200D\u20E3"         # variation selectors, ZWJ, keycaps
    "\u00A9\u00AE\u203C\u2049\u2122"  # © ® ⁉ ™
    "]"
)


def _ttl(scope: str) -> int:
    if scope == "match":
        return MATCH_TTL
    if scope == "global":
        return GLOBAL_TTL
    return LOBBY_TTL


def _now() -> float:
    return time.time()


def is_emoji_only(text: str) -> bool:
    stripped = re.sub(_EMOJI_RE, "", text).strip()
    return stripped == "" and bool(text.strip())


def contains_profanity(text: str) -> bool:
    if _profanity is None:
        return False
    if _profanity.contains_profanity(text):
        return True
    # Light evasion handling: strip separators/asterisks between letters
    # (e.g. "f-ck", "s h i t", "a**hole") before the second pass.
    stripped = re.sub(r"[\s\-_.*'`]+", "", text.lower())
    return _profanity.contains_profanity(stripped)


#  Rate limiting 

def check_rate_limit(user_key: str) -> tuple[bool, int]:
    """Sliding window: CHAT_RATE_MAX messages per CHAT_RATE_WINDOW seconds.
    Returns (allowed, retry_after_seconds)."""
    key = f"chat_rl:{user_key}"
    now = _now()
    try:
        raw = r.get(key)
        stamps = json.loads(raw) if raw else []
    except Exception:
        stamps = []
    stamps = [t for t in stamps if now - t < CHAT_RATE_WINDOW]
    if len(stamps) >= CHAT_RATE_MAX:
        return False, max(1, int(CHAT_RATE_WINDOW - (now - stamps[0])))
    stamps.append(now)
    r.setex(key, CHAT_RATE_WINDOW, json.dumps(stamps))
    return True, 0


#  Ephemeral (Redis) — match & tournament-lobby chat 

def send_ephemeral(scope: str, scope_id: str, sender_id: str,
                   sender_name: str, body: str) -> dict:
    key = f"chat:{scope}:{scope_id}"
    raw = r.get(key)
    msgs = json.loads(raw) if raw else []
    msg = {
        "mid":         str(len(msgs)),
        "sender_id":   sender_id,
        "sender_name": sender_name,
        "scope":       scope,
        "scope_id":    scope_id,
        "body":        body,
        "emoji_only":  is_emoji_only(body),
        "ts":          _now(),
    }
    msgs.append(msg)
    r.setex(key, _ttl(scope), json.dumps(msgs))
    return msg


def get_ephemeral(scope: str, scope_id: str, after: int | None,
                  limit: int = FETCH_LIMIT, blocked: set | frozenset = frozenset()
                  ) -> tuple[list[dict], str]:
    key = f"chat:{scope}:{scope_id}"
    raw = r.get(key)
    msgs = json.loads(raw) if raw else []
    blocked = set(blocked or ())
    clean = lambda m: {k: m.get(k) for k in
                       ("mid", "sender_id", "sender_name", "scope", "scope_id",
                        "body", "emoji_only", "ts")}
    last_mid = str(len(msgs) - 1) if msgs else "-1"
    if after is None or after < 0:
        tail = [clean(m) for m in msgs if m.get("sender_id") not in blocked]
        return tail[-limit:], last_mid
    out = [clean(m) for m in msgs[after + 1:] if m.get("sender_id") not in blocked]
    if len(out) > limit:
        out = out[-limit:]
    return out, last_mid


#  Persisted (Supabase) — friend DMs 

def conv_id(uid_a: str, uid_b: str) -> str:
    return "|".join(sorted([uid_a, uid_b]))


def conv_parties(conv: str) -> tuple[str, str]:
    parts = conv.split("|")
    return (parts[0], parts[1]) if len(parts) == 2 else ("", "")


def send_dm(sender_id: str, sender_name: str, conv: str, body: str) -> dict:
    if service is None:
        raise ChatUnavailable("Chat storage unavailable")
    try:
        row = service.table("messages").insert({
            "sender_id":   sender_id,
            "sender_name": sender_name,
            "scope":       "dm",
            "scope_id":    conv,
            "body":        body,
            "emoji_only":  is_emoji_only(body),
        }).execute()
    except Exception:
        raise ChatUnavailable("Chat storage unavailable") from None
    data = row.data or []
    saved = data[0] if data else {}
    return {
        "mid":         str(saved.get("seq") or ""),
        "sender_id":   sender_id,
        "sender_name": sender_name,
        "scope":       "dm",
        "scope_id":    conv,
        "body":        body,
        "emoji_only":  is_emoji_only(body),
        "ts":          saved.get("created_at") or time.time(),
    }


def _iso_ts(value) -> float | str:
    return value if isinstance(value, str) else time.time()


def get_dm(conv: str, after: int | None, limit: int = FETCH_LIMIT,
           blocked: set | frozenset = frozenset()) -> tuple[list[dict], str]:
    if service is None:
        raise ChatUnavailable("Chat storage unavailable")
    try:
        q = (service.table("messages")
             .select("seq,sender_id,sender_name,scope_id,body,emoji_only,created_at")
             .eq("scope_id", conv))
        if after is not None and after >= 0:
            q = q.gt("seq", after)
        rows = (q.order("seq", desc=False).limit(limit).execute().data or [])
    except Exception:
        raise ChatUnavailable("Chat storage unavailable") from None
    blocked = set(blocked or ())
    out = []
    for row in rows:
        if row.get("sender_id") in blocked:
            continue
        out.append({
            "mid":         str(row["seq"]),
            "sender_id":   row["sender_id"],
            "sender_name": row["sender_name"],
            "scope":       "dm",
            "scope_id":    conv,
            "body":        row["body"],
            "emoji_only":  bool(row.get("emoji_only")),
            "ts":          _iso_ts(row.get("created_at")),
        })
    next_after = str(rows[-1]["seq"]) if rows else str(after if after is not None else -1)
    return out, next_after


def _read_cursor(user_id: str) -> dict:
    raw = r.get(f"chat_read:{user_id}")
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _save_read_cursor(user_id: str, cursors: dict) -> None:
    r.setex(f"chat_read:{user_id}", READ_TTL, json.dumps(cursors))


def mark_read(user_id: str, conv: str, mid: str) -> None:
    cursors = _read_cursor(user_id)
    cursors[conv] = str(mid)
    _save_read_cursor(user_id, cursors)


def get_conversations(user_id: str) -> list[dict]:
    """Recent DM conversations for a user, newest activity first."""
    if service is None:
        raise ChatUnavailable("Chat storage unavailable")
    me = user_id
    try:
        rows = (service.table("messages")
                .select("seq,sender_id,sender_name,scope_id,body,created_at")
                .or_(f"sender_id.eq.{me},scope_id.like.{me}|%,scope_id.like.%|{me}")
                .order("seq", desc=True).limit(300).execute().data or [])
    except Exception:
        raise ChatUnavailable("Chat storage unavailable") from None
    by_conv: dict[str, dict] = {}
    for row in rows:
        conv = row.get("scope_id") or ""
        if not conv:
            continue
        cur = by_conv.get(conv)
        if cur is None:
            me_uid, other_uid = conv_parties(conv)
            other_uid = other_uid if me_uid == me else me_uid
            by_conv[conv] = {
                "conv_id":    conv,
                "other_uid":  other_uid,
                "other_name": row.get("sender_name") if row.get("sender_id") != me else "",
                "last_mid":   str(row.get("seq") or ""),
                "last_body":  row.get("body") or "",
                "last_ts":    _iso_ts(row.get("created_at")),
            }
        cur["other_name"] = cur["other_name"] or (row.get("sender_name")
                                                  if row.get("sender_id") != me else "")
        if int(str(row.get("seq") or 0)) > int(cur["last_mid"] or 0):
            cur["last_mid"] = str(row.get("seq") or "")
            cur["last_body"] = row.get("body") or ""
            cur["last_ts"] = _iso_ts(row.get("created_at"))
    cursors = _read_cursor(user_id)
    result = []
    for conv, info in by_conv.items():
        read_mid = int(cursors.get(conv, -1) or -1)
        # approximate unread count from the sampled window
        unread = sum(1 for row in rows
                     if (row.get("scope_id") == conv
                         and int(str(row.get("seq") or 0)) > read_mid))
        info["unread"] = unread
        result.append(info)
    result.sort(key=lambda c: c.get("last_ts") or "", reverse=True)
    return result[:CONV_LIMIT]


#  Blocks 

def block_user(blocker_id: str, blocked_id: str) -> None:
    if service is None:
        raise ChatUnavailable("Chat storage unavailable")
    try:
        service.table("blocks").upsert(
            {"blocker_id": blocker_id, "blocked_id": blocked_id}
        ).execute()
    except Exception:
        raise ChatUnavailable("Chat storage unavailable") from None


def unblock_user(blocker_id: str, blocked_id: str) -> None:
    if service is None:
        raise ChatUnavailable("Chat storage unavailable")
    try:
        (service.table("blocks")
         .delete().eq("blocker_id", blocker_id).eq("blocked_id", blocked_id)
         .execute())
    except Exception:
        raise ChatUnavailable("Chat storage unavailable") from None


def get_blocked(user_id: str) -> set[str]:
    if service is None:
        return set()
    try:
        rows = (service.table("blocks").select("blocked_id")
                .eq("blocker_id", user_id).execute().data or [])
    except Exception:
        return set()
    return {row["blocked_id"] for row in rows}


#  Reports 

def _lookup_message(scope: str, scope_id: str, mid) -> dict | None:
    if scope in ("match", "tournament"):
        raw = r.get(f"chat:{scope}:{scope_id}")
        msgs = json.loads(raw) if raw else []
        for m in msgs:
            if str(m.get("mid")) == str(mid):
                return m
        return None
    if scope == "dm":
        if service is None:
            raise ChatUnavailable("Chat storage unavailable")
        try:
            seq = int(mid)
        except (TypeError, ValueError):
            return None
        rows = (service.table("messages")
                .select("seq,sender_id,sender_name,scope_id,body")
                .eq("scope_id", scope_id).eq("seq", seq)
                .limit(1).execute().data or [])
        if not rows:
            return None
        row = rows[0]
        return {"sender_id": row["sender_id"], "sender_name": row["sender_name"],
                "body": row["body"]}
    return None


def report_message(reporter_id: str, scope: str, scope_id: str,
                   mid: str, reason: str) -> bool:
    msg = _lookup_message(scope, scope_id, mid)
    if not msg:
        return False
    if service is None:
        raise ChatUnavailable("Chat storage unavailable")
    try:
        service.table("reported_messages").insert({
            "reporter_id": reporter_id,
            "scope":       scope,
            "scope_id":    scope_id,
            "mid":         str(mid),
            "sender_id":   msg.get("sender_id", ""),
            "sender_name": msg.get("sender_name", ""),
            "body":        msg.get("body", ""),
            "reason":      reason or "",
        }).execute()
    except Exception:
        raise ChatUnavailable("Chat storage unavailable") from None
    return True
