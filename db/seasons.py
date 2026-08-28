"""Ranked seasons: periodic soft-reset, archived standings, season rewards.

Storage lives in two Supabase tables (see migration_seasons.sql):
  - `seasons`           one row per season: number, start/end, status
                        (active/completed), leaderboard_snapshot (jsonb)
  - `season_ratings`    per-user rows for a season: rating_start, rating,
                        games_played, wins/losses, peak_rating
The existing `ratings` table stays the ALL-TIME/career record (career
games, wins, peak) and is never reset except for the `rating` number,
which follows the soft-reset formula below. `rating_history` gains a
nullable `season_id` so season boundaries show up in the rating log.

Soft reset (confirmed design): new_rating = START_RATING + (old - START_RATING) * 0.5
  (1400 -> 1300, 1600 -> 1400, 1800 -> 1500, 1200 -> 1200).
Only the rating number resets. Achievements, cosmetic unlocks, career
games/wins/losses/peak and match history are never touched.

Season rewards (confirmed design, reuses db.achievements + the cosmetic
gating from db/customization — a season-end reward is just a special
case of "award this achievement"):
  - participant: >= 5 ranked games that season      -> sn_participant badge
  - top 10 (placed players)                          -> sn_top_10 badge (+
    "aurora" goal effect cosmetic via the existing gate map)
  - rank #1 (placed players)                         -> sn_champion badge (+
    "titanium" ball cosmetic via the existing gate map)

Double-processing safety: the transition marks the season `completed`
(and stores the frozen leaderboard snapshot) as its FIRST write; every
later step (awards, reset, next season) is idempotent — awards are
guarded by the unique (user_id, key) constraint, the reset recomputes
from the snapshot (exactly-once per player), and the next season is
created only if it doesn't exist. A second transition call against an
already-completed season is a no-op; if a process crashes mid-transition,
`initialize()` resumes the tail (awards + reset from snapshot + create
next season) when it finds a completed season with no successor.

All functions degrade to an in-memory registry when Supabase is
unavailable (local dev / tests), mirroring db/ranked.py.
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone

from db.ranked import START_RATING, PLACEMENT_GAMES

SEASON_LENGTH_DAYS     = 56           # 8 weeks (confirmed)
PARTICIPANT_MIN_GAMES  = 5            # ranked games to earn the participant badge
SOFT_RESET_COMPRESSION = 0.5          # pull toward START_RATING (confirmed)

_BADGE_PARTICIPANT = "sn_participant"
_BADGE_TOP_10      = "sn_top_10"
_BADGE_CHAMPION    = "sn_champion"


def _svc():
    from db.supabase_client import service
    return service


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


#  In-memory fallback (dev: no Supabase) 
_MEM_SEASONS: dict[int, dict] = {}                    # id -> season row
_MEM_SEASON_RATINGS: dict[tuple[str, int], dict] = {}  # (user_id, season_id) -> row
_SEQ = 0
_LOCK = False


def _next_id() -> int:
    global _SEQ
    _SEQ += 1
    return _SEQ


def reset_mem() -> None:
    """Test/dev helper: wipe the in-memory registry."""
    global _SEQ, _LOCK
    _MEM_SEASONS.clear()
    _MEM_SEASON_RATINGS.clear()
    _SEQ = 0
    _LOCK = False


#  Soft reset 

def soft_reset_rating(rating: int) -> int:
    """Pull a rating toward START_RATING by the compression factor."""
    return int(round(START_RATING + (rating - START_RATING) * SOFT_RESET_COMPRESSION))


#  Season lifecycle (reads) 

def _new_season_row(number: int, starts_at: datetime, ends_at: datetime) -> dict:
    return {
        "id": None, "number": number, "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(), "status": "active",
        "leaderboard_snapshot": None,
    }


def _ensure_active_season_db(svc, now: datetime) -> dict:
    res = (svc.table("seasons").select("*")
           .eq("status", "active").order("number", desc=True).limit(1).execute())
    if res.data:
        return res.data[0]
    # No active season: resume the tail of the most recent completed one
    # (crash mid-transition), else create Season 1.
    done = (svc.table("seasons").select("*")
            .eq("status", "completed").order("number", desc=True).limit(1).execute())
    if done.data:
        last = done.data[0]
        if _parse_dt(last.get("ends_at")) <= now:
            _resume_transition(svc, last, now)
            res = (svc.table("seasons").select("*")
                   .eq("status", "active").order("number", desc=True).limit(1).execute())
            if res.data:
                return res.data[0]
        # completed but not yet due (clock skew): still create the next season
        number = int(last["number"]) + 1
        starts_at = max(_parse_dt(last.get("ends_at")), now)
    else:
        number = 1
        starts_at = now
    row = _new_season_row(number, starts_at, starts_at + timedelta(days=SEASON_LENGTH_DAYS))
    ins = svc.table("seasons").insert({
        "number": row["number"], "starts_at": row["starts_at"],
        "ends_at": row["ends_at"], "status": "active",
    }).execute()
    row["id"] = ins.data[0]["id"] if ins.data else None
    return row


def _ensure_active_season_mem(now: datetime) -> dict:
    for s in _MEM_SEASONS.values():
        if s["status"] == "active":
            return s
    done = [s for s in _MEM_SEASONS.values() if s["status"] == "completed"]
    if done:
        last = max(done, key=lambda s: s["number"])
        if _parse_dt(last["ends_at"]) <= now:
            _resume_transition(None, last, now)
            for s in _MEM_SEASONS.values():
                if s["status"] == "active":
                    return s
        number = int(last["number"]) + 1
        starts_at = max(_parse_dt(last["ends_at"]), now)
    else:
        number = 1
        starts_at = now
    row = _new_season_row(number, starts_at, starts_at + timedelta(days=SEASON_LENGTH_DAYS))
    row["id"] = _next_id()
    _MEM_SEASONS[row["id"]] = row
    return row


def initialize(now: datetime | None = None) -> dict:
    """Make sure an active season exists (creates Season 1 on first run,
    or resumes a crashed transition's tail). Idempotent; called at app
    startup and lazily from ranked request paths."""
    now = now or datetime.now(timezone.utc)
    svc = _svc()
    if svc:
        return _ensure_active_season_db(svc, now)
    return _ensure_active_season_mem(now)


def _parse_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def get_current_season(now: datetime | None = None) -> dict:
    """The active season (creating it if missing)."""
    return initialize(now)


def list_seasons() -> list[dict]:
    """All seasons, newest first (snapshot included for completed ones)."""
    svc = _svc()
    if not svc:
        return [dict(s) for s in sorted(_MEM_SEASONS.values(),
                                        key=lambda s: s["number"], reverse=True)]
    res = svc.table("seasons").select("*").order("number", desc=True).execute()
    return list(res.data or [])


def get_season(season_id: int) -> dict | None:
    svc = _svc()
    if not svc:
        return _MEM_SEASONS.get(season_id)
    res = svc.table("seasons").select("*").eq("id", season_id).maybe_single().execute()
    return res.data if res and res.data else None


def get_season_by_number(number: int) -> dict | None:
    """Resolve a season by its public number (what the API exposes)."""
    svc = _svc()
    if not svc:
        for s in _MEM_SEASONS.values():
            if s["number"] == number:
                return dict(s)
        return None
    res = svc.table("seasons").select("*").eq("number", number).maybe_single().execute()
    return res.data if res and res.data else None


def season_for_time(ts) -> dict | None:
    """The season whose window contains the given timestamp, else None.

    Used to attribute a finished match to the season it was played in
    (read-only; never creates a season). `ts` is a unix epoch or a
    datetime; the returned row keeps the module's own dict shape.
    """
    if isinstance(ts, (int, float)):
        ts = datetime.fromtimestamp(ts, timezone.utc)
    svc = _svc()
    if not svc:
        for s in _MEM_SEASONS.values():
            if _parse_dt(s["starts_at"]) <= ts < _parse_dt(s["ends_at"]):
                return dict(s)
        return None
    res = (svc.table("seasons").select("*").order("number", desc=True).execute())
    for s in (res.data or []):
        if _parse_dt(s.get("starts_at")) <= ts < _parse_dt(s.get("ends_at")):
            return s
    return None


#  Per-season rating accounting (written on every ranked result) 

def apply_match(season: dict, user_id: str, rating_before: int,
                rating_after: int, won: bool) -> None:
    """Update a player's season-scoped row after a ranked match.

    Creates the row on the player's first match of the season with
    `rating_start` = their live rating at that moment (post-reset or
    post-previous-season value).
    """
    season_id = int(season["id"])
    svc = _svc()
    if not svc:
        row = _MEM_SEASON_RATINGS.get((user_id, season_id))
        if row is None:
            row = {"user_id": user_id, "season_id": season_id,
                   "rating_start": rating_before, "rating": rating_before,
                   "games_played": 0, "wins": 0, "losses": 0,
                   "peak_rating": rating_before}
            _MEM_SEASON_RATINGS[(user_id, season_id)] = row
        row["rating"] = rating_after
        row["games_played"] += 1
        if won:
            row["wins"] += 1
        else:
            row["losses"] += 1
        row["peak_rating"] = max(row["peak_rating"], rating_after)
        row["updated_at"] = _now_iso()
        return
    row = (svc.table("season_ratings")
           .select("rating_start,games_played,wins,losses")
           .eq("user_id", user_id).eq("season_id", season_id)
           .maybe_single().execute())
    if row and row.data:
        cur = row.data
        svc.table("season_ratings").update({
            "rating": rating_after,
            "games_played": int(cur.get("games_played", 0)) + 1,
            "wins": int(cur.get("wins", 0)) + (1 if won else 0),
            "losses": int(cur.get("losses", 0)) + (0 if won else 1),
            "peak_rating": max(int(cur.get("peak_rating", rating_before)), rating_after),
            "updated_at": _now_iso(),
        }).eq("user_id", user_id).eq("season_id", season_id).execute()
    else:
        svc.table("season_ratings").insert({
            "user_id": user_id, "season_id": season_id,
            "rating_start": rating_before, "rating": rating_after,
            "games_played": 1, "wins": 1 if won else 0, "losses": 0 if won else 1,
            "peak_rating": rating_after,
        }).execute()


#  Standings / history 

def _season_rows(season_id: int) -> list[dict]:
    svc = _svc()
    if not svc:
        return [dict(r) for r in _MEM_SEASON_RATINGS.values()
                if r["season_id"] == season_id]
    res = (svc.table("season_ratings").select("*")
           .eq("season_id", season_id).execute())
    return list(res.data or [])


def build_snapshot(season_id: int) -> list[dict]:
    """Frozen final standings for a season: all players with >= 1 ranked
    game, ordered rating desc, wins desc, user_id asc (deterministic).
    Placed players (>= PLACEMENT_GAMES season games) get a `placed` flag."""
    rows = _season_rows(season_id)
    rows.sort(key=lambda r: (-int(r["rating"]), -int(r["wins"]), str(r["user_id"])))
    out = []
    for i, r in enumerate(rows):
        games = int(r.get("games_played", 0))
        out.append({
            "rank": i + 1,
            "user_id": r["user_id"],
            "rating": int(r.get("rating", START_RATING)),
            "rating_start": int(r.get("rating_start", START_RATING)),
            "games_played": games,
            "wins": int(r.get("wins", 0)),
            "losses": int(r.get("losses", 0)),
            "peak_rating": int(r.get("peak_rating", START_RATING)),
            "placed": games >= PLACEMENT_GAMES,
        })
    return out


def season_standings(season_id: int, limit: int = 20, offset: int = 0,
                     placed_only: bool = True) -> tuple[list[dict], int]:
    """Paginated board for one season. Placed players only by default
    (mirrors the career leaderboard). Completed seasons read the frozen
    snapshot so a late page re-render can't drift from the archive."""
    season = get_season(season_id)
    if season is None:
        return [], 0
    if season.get("status") == "completed" and season.get("leaderboard_snapshot"):
        rows = json.loads(season["leaderboard_snapshot"])
    else:
        rows = build_snapshot(season_id)
    if placed_only:
        rows = [r for r in rows if r.get("placed")]
    total = len(rows)
    page = rows[offset:offset + limit]
    return _decorate(page, offset), total


def _decorate(rows: list[dict], offset: int) -> list[dict]:
    if not rows:
        return []
    svc = _svc()
    users = {r["user_id"] for r in rows}
    usernames: dict[str, str] = {}
    avatars: dict[str, str] = {}
    if svc:
        try:
            prof = svc.table("profiles").select("id,username,avatar_url")\
                .in_("id", list(users)).execute().data or []
            for p in prof:
                usernames[p["id"]] = p.get("username") or "Player"
                if p.get("avatar_url"):
                    avatars[p["id"]] = p["avatar_url"]
        except Exception:
            pass
    out = []
    for i, r in enumerate(rows):
        uid_ = r["user_id"]
        games = int(r.get("games_played", 0))
        wins = int(r.get("wins", 0))
        losses = int(r.get("losses", 0))
        out.append({
            "rank": offset + i + 1,
            "user_id": uid_,
            "username": usernames.get(uid_, "Player"),
            "avatar_url": avatars.get(uid_),
            "rating": int(r.get("rating", START_RATING)),
            "rating_start": int(r.get("rating_start", START_RATING)),
            "games_played": games,
            "wins": wins,
            "losses": losses,
            "win_rate": round(100.0 * wins / games, 1) if games else 0.0,
            "peak_rating": int(r.get("peak_rating", START_RATING)),
            "placed": bool(r.get("placed", games >= PLACEMENT_GAMES)),
        })
    return out


def career_summary(user_id: str) -> dict:
    """All-time season stats for a player: seasons played, best rank,
    best rating. Never resets."""
    svc = _svc()
    if not svc:
        rows = [r for k, r in _MEM_SEASON_RATINGS.items() if k[0] == user_id]
    else:
        res = (svc.table("season_ratings").select("*")
               .eq("user_id", user_id).execute())
        rows = list(res.data or [])
    played = [r for r in rows if int(r.get("games_played", 0)) > 0]
    seasons_played = len({r["season_id"] for r in played})
    best_rating = max((int(r.get("peak_rating", 0)) for r in played), default=0)
    best_rank = None
    for r in played:
        snap = _season_snapshot_for(r["season_id"])
        for e in snap:
            if e["user_id"] == user_id and e.get("placed"):
                best_rank = e["rank"] if best_rank is None else min(best_rank, e["rank"])
                break
    return {"seasons_played": seasons_played,
            "best_rank": best_rank,
            "best_rating": best_rating}


def _season_snapshot_for(season_id: int) -> list[dict]:
    season = get_season(season_id)
    if season and season.get("leaderboard_snapshot"):
        try:
            return json.loads(season["leaderboard_snapshot"])
        except (TypeError, ValueError):
            pass
    return build_snapshot(season_id)


#  Season transition (the periodic boundary job) 

def _acquire_lock() -> bool:
    """Cross-process guard so two workers can't transition the same
    boundary. Best-effort; the status check is the real safety net."""
    try:
        from db.redis_client import r as redis
        if redis.get("season:lock"):
            return False
        redis.setex("season:lock", 300, "1")
        return True
    except Exception:
        return True


def _release_lock() -> None:
    try:
        from db.redis_client import r as redis
        redis.delete("season:lock")
    except Exception:
        pass


def run_transition_if_due(now: datetime | None = None) -> dict | None:
    """Transition the active season if its end date has passed. Returns a
    summary dict, or None when there is nothing to do (no season or not
    yet due). Safe to call from any request path / the watcher thread."""
    now = now or datetime.now(timezone.utc)
    season = get_current_season(now)
    if season.get("status") != "active":
        return None
    if _parse_dt(season.get("ends_at")) > now:
        return None
    return transition(int(season["id"]))


def transition(season_id: int) -> dict | None:
    """Freeze + archive + reward + soft-reset + open the next season.

    Safety (confirmed design):
      - mark `completed` + store the snapshot FIRST (a crash here means
        the next run no-ops: status is no longer 'active')
      - awards are idempotent (unique user/achievement constraint)
      - the soft reset recomputes from the frozen snapshot (each player
        is compressed exactly once)
      - the next season is created only if it doesn't already exist
    A second call against an already-completed season returns None.
    """
    season = get_season(season_id)
    if season is None or season.get("status") != "active":
        return None
    if not _acquire_lock():
        return None
    try:
        season = get_season(season_id)
        if season is None or season.get("status") != "active":
            return None
        snapshot = build_snapshot(season_id)
        _mark_completed(season, snapshot)
        awards = _award_season_rewards(season, snapshot)
        reset = _reset_ratings_from_snapshot(season, snapshot)
        next_season = _create_next_season(season)
        return {
            "ended_season": int(season["number"]),
            "status": "completed",
            "snapshot_players": len(snapshot),
            "awards": awards,
            "reset_players": reset,
            "next_season": int(next_season["number"]),
        }
    finally:
        _release_lock()


def _mark_completed(season: dict, snapshot: list[dict]) -> None:
    payload = json.dumps(snapshot)
    svc = _svc()
    if not svc:
        season["status"] = "completed"
        season["leaderboard_snapshot"] = payload
        return
    res = (svc.table("seasons").update({"status": "completed",
                                        "leaderboard_snapshot": payload})
           .eq("id", season["id"]).eq("status", "active").execute())
    if not (res.data and res.data[0].get("status") == "completed"):
        raise RuntimeError("Season transition raced another worker")
    season["status"] = "completed"
    season["leaderboard_snapshot"] = payload


def _award_season_rewards(season: dict, snapshot: list[dict]) -> dict:
    """Award badges + season-summary toasts. Awards are idempotent, so a
    resumed tail cannot double-award. Guests are never awarded."""
    number = int(season["number"])
    counts = {_BADGE_PARTICIPANT: 0, _BADGE_TOP_10: 0, _BADGE_CHAMPION: 0}
    for entry in snapshot:
        uid_ = entry.get("user_id")
        if not uid_ or str(uid_).startswith("guest:"):
            continue
        rank = int(entry.get("rank", 0))
        if int(entry.get("games_played", 0)) >= PARTICIPANT_MIN_GAMES:
            counts[_BADGE_PARTICIPANT] += _grant(uid_, _BADGE_PARTICIPANT, number, rank)
        if entry.get("placed"):
            if rank <= 10:
                counts[_BADGE_TOP_10] += _grant(uid_, _BADGE_TOP_10, number, rank)
            if rank == 1:
                counts[_BADGE_CHAMPION] += _grant(uid_, _BADGE_CHAMPION, number, rank)
    return counts


def _grant(user_id: str, badge_key: str, season_number: int, rank: int) -> int:
    """Award + push the season-end summary toast; returns 1 on first award."""
    from db.achievements import award, push_toast
    badge = award(user_id, badge_key)
    if not badge:
        return 0
    try:
        push_toast(user_id, {
            "key": "season_summary",
            "emoji": "",
            "name": f"Season {season_number} ended",
            "description": f"You finished rank #{rank} — {badge['name']} unlocked.",
            "season_summary": True,
        })
    except Exception:
        pass
    return 1


def _reset_ratings_from_snapshot(season: dict, snapshot: list[dict]) -> int:
    """Compress each snapshot player's rating toward START_RATING and log
    the boundary in rating_history (match_id null, season_id set)."""
    changed = 0
    for entry in snapshot:
        uid_ = entry.get("user_id")
        if not uid_ or str(uid_).startswith("guest:"):
            continue
        old = int(entry["rating"])
        new = soft_reset_rating(old)
        if new == old:
            continue
        _write_reset(uid_, old, new, season)
        changed += 1
    return changed


def _write_reset(user_id: str, old: int, new: int, season: dict) -> None:
    svc = _svc()
    if not svc:
        from db import ranked
        row = ranked._MEM.get(user_id)
        if row is not None:
            row["rating"] = new
            row["updated_at"] = _now_iso()
        ranked._MEM_HISTORY.append({
            "user_id": user_id, "match_id": None,
            "rating_before": old, "rating_after": new,
            "delta": new - old, "season_id": int(season["id"]),
            "created_at": _now_iso(),
        })
        return
    svc.table("ratings").update({"rating": new}).eq("user_id", user_id).execute()
    svc.table("rating_history").insert({
        "user_id": user_id, "match_id": None,
        "rating_before": old, "rating_after": new, "delta": new - old,
        "season_id": int(season["id"]),
    }).execute()


def _create_next_season(season: dict) -> dict:
    ends_at = _parse_dt(season["ends_at"])
    starts_at = ends_at
    number = int(season["number"]) + 1
    svc = _svc()
    if svc:
        existing = (svc.table("seasons").select("id")
                    .eq("number", number).maybe_single().execute())
        if existing and existing.data:
            return get_season(existing.data["id"])
        ins = svc.table("seasons").insert({
            "number": number, "starts_at": starts_at.isoformat(),
            "ends_at": (starts_at + timedelta(days=SEASON_LENGTH_DAYS)).isoformat(),
            "status": "active",
        }).execute()
        row = dict(ins.data[0]) if ins.data else {}
        row.setdefault("id", None)
        return row
    for s in _MEM_SEASONS.values():
        if s["number"] == number:
            return s
    row = _new_season_row(number, starts_at,
                          starts_at + timedelta(days=SEASON_LENGTH_DAYS))
    row["id"] = _next_id()
    _MEM_SEASONS[row["id"]] = row
    return row


def _resume_transition(svc, season: dict, now: datetime) -> None:
    """Crash-tail resume: a completed season with no successor still needs
    its awards, reset and next season. Every step is idempotent."""
    try:
        snapshot = json.loads(season.get("leaderboard_snapshot") or "[]")
    except (TypeError, ValueError):
        snapshot = []
    if not snapshot:
        snapshot = build_snapshot(int(season["id"]))
    _award_season_rewards(season, snapshot)
    _reset_ratings_from_snapshot(season, snapshot)
    _create_next_season(season)
