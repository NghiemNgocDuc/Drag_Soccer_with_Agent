"""Achievements / badges: permanent, account-bound recognition.

Two Supabase tables (see migration_achievements.sql):
  - `achievement_definitions`  the full catalog (key -> category/name/desc/emoji)
  - `user_achievements`        one row per earned badge; the unique
                               (user_id, achievement_key) constraint is the
                               DB-level double-award guard.

Awards are purely cosmetic recognition — no gameplay effect, no unlocks.
Earned rows are inserted by the server (service role) from the gameplay
hooks in app.py; every detector calls `award()` which returns the badge
only on the first grant. All functions degrade to an in-memory registry
when Supabase is unavailable (local dev / tests).

Newly earned badges are pushed to a short-lived Redis list (`ach:new:{uid}`
with a 7-day TTL) that existing response paths drain and render as toasts.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

from db.redis_client import r as _redis

TOAST_TTL = 7 * 86400  # 7 days: matches the "pending toasts" visibility window


def _svc():
    from db.supabase_client import service
    return service


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── The catalog (single source of truth, seeded idempotently) ──────────────

ACHIEVEMENTS: dict[str, dict] = {
    # ── Ranked ─────────────────────────────────────────────────────────────
    "rk_first_win":    {"category": "ranked", "order": 1,  "emoji": "🏟️",
                        "name": "Ranked Debut",
                        "description": "Win your first ranked match."},
    "rk_game_10":      {"category": "ranked", "order": 2,  "emoji": "🎮",
                        "name": "Placement Done",
                        "description": "Play 10 ranked matches."},
    "rk_game_50":      {"category": "ranked", "order": 3,  "emoji": "🥈",
                        "name": "Seasoned",
                        "description": "Play 50 ranked matches."},
    "rk_game_100":     {"category": "ranked", "order": 4,  "emoji": "🏅",
                        "name": "Centurion",
                        "description": "Play 100 ranked matches."},
    "rk_rating_1400":  {"category": "ranked", "order": 5,  "emoji": "🥉",
                        "name": "Silver Ladder",
                        "description": "Reach a 1400 ranked rating."},
    "rk_rating_1600":  {"category": "ranked", "order": 6,  "emoji": "🥇",
                        "name": "Gold Ladder",
                        "description": "Reach a 1600 ranked rating."},
    "rk_rating_1800":  {"category": "ranked", "order": 7,  "emoji": "💎",
                        "name": "Platinum Ladder",
                        "description": "Reach a 1800 ranked rating."},
    "rk_streak_3":     {"category": "ranked", "order": 8,  "emoji": "🔥",
                        "name": "On a Roll",
                        "description": "Win 3 ranked matches in a row."},
    "rk_streak_5":     {"category": "ranked", "order": 9,  "emoji": "⚡",
                        "name": "Unstoppable",
                        "description": "Win 5 ranked matches in a row."},
    "rk_streak_10":    {"category": "ranked", "order": 10, "emoji": "🌟",
                        "name": "Demigod",
                        "description": "Win 10 ranked matches in a row."},
    "rk_clean_sheet":  {"category": "ranked", "order": 11, "emoji": "🧱",
                        "name": "Rock Solid",
                        "description": "Win a ranked match without conceding a goal."},
    # ── Skill & style ──────────────────────────────────────────────────────
    "sk_pan_trick":    {"category": "skill", "order": 1,  "emoji": "🎯",
                        "name": "Hat-Trick Hero",
                        "description": "Score 3 goals with the same player in a single match."},
    "sk_clean_sheet":  {"category": "skill", "order": 2,  "emoji": "🛡️",
                        "name": "Iron Wall",
                        "description": "Win a casual match without conceding a goal."},
    "sk_big_win":      {"category": "skill", "order": 3,  "emoji": "💣",
                        "name": "Rout",
                        "description": "Win a casual match by 5 or more goals."},
    "sk_extreme_build":{"category": "skill", "order": 4,  "emoji": "🎲",
                        "name": "One-Trick Pony",
                        "description": "Win with a team built on a single extreme stat "
                                       "(one stat ≥ 60, all others ≤ 40, every player)."},
    "sk_rocket":       {"category": "skill", "order": 5,  "emoji": "🚀",
                        "name": "Rocket Strike",
                        "description": "Score on a kick that peaks at 700+ px/s."},
    "sk_loft":         {"category": "skill", "order": 6,  "emoji": "🎪",
                        "name": "Top Corner",
                        "description": "Score on a lofted kick that climbs 40+ px high."},
    # ── AI building ────────────────────────────────────────────────────────
    "ai_first_model":  {"category": "ai", "order": 1,  "emoji": "🤖",
                        "name": "Hello World",
                        "description": "Create your first custom AI model."},
    "ai_first_bench":  {"category": "ai", "order": 2,  "emoji": "⚔️",
                        "name": "Into the Arena",
                        "description": "Finish your first leaderboard benchmark."},
    "ai_beat_all":     {"category": "ai", "order": 3,  "emoji": "👑",
                        "name": "Undisputed",
                        "description": "Beat every built-in AI in the leaderboard benchmark "
                                       "(all 7 win rates above 50%)."},
    "ai_top_5":        {"category": "ai", "order": 4,  "emoji": "🏆",
                        "name": "Top Five",
                        "description": "Rank in the top 5 on the model leaderboard."},
    "ai_rank_one":     {"category": "ai", "order": 5,  "emoji": "💯",
                        "name": "Number One",
                        "description": "Reach rank #1 on the model leaderboard."},
    # ── Tournaments ────────────────────────────────────────────────────────
    "tour_playmaker":  {"category": "tour", "order": 1,  "emoji": "📋",
                        "name": "Playmaker",
                        "description": "Create a tournament."},
    "tour_champion":   {"category": "tour", "order": 2,  "emoji": "🏆",
                        "name": "Champion",
                        "description": "Win a tournament as a human participant."},
    # ── Social ─────────────────────────────────────────────────────────────
    "friend_match":    {"category": "social", "order": 1,  "emoji": "🤝",
                        "name": "Rivals to Friends",
                        "description": "Finish an online match against someone on your "
                                       "friends list."},
    # ── Exploration ────────────────────────────────────────────────────────
    "exp_first_goal":  {"category": "explore", "order": 1, "emoji": "⚽",
                        "name": "Opening Scorer",
                        "description": "Score your first goal — any game, any mode."},
    "exp_photo":       {"category": "explore", "order": 2, "emoji": "📸",
                        "name": "Fresh Face",
                        "description": "Add a profile photo."},
    "exp_sense4":      {"category": "explore", "order": 3, "emoji": "🌐",
                        "name": "Time Traveler",
                        "description": "Play under at least 4 different scene/weather "
                                       "presets."},
    # ── Seasons ────────────────────────────────────────────────────────────
    "sn_participant":  {"category": "season", "order": 1, "emoji": "🏁",
                        "name": "Season Player",
                        "description": "Play at least 5 ranked matches in a season."},
    "sn_top_10":       {"category": "season", "order": 2, "emoji": "🎖️",
                        "name": "Season Top 10",
                        "description": "Finish a season ranked in the top 10."},
    "sn_champion":     {"category": "season", "order": 3, "emoji": "👑",
                        "name": "Season Champion",
                        "description": "Finish a season ranked #1."},
    # ── Tutorial ─────────────────────────────────────────────────────────
    "tut_first_lesson": {"category": "tutorial", "order": 1, "emoji": "🎓",
                         "name": "First Lesson",
                         "description": "Complete your first tutorial lesson."},
    "tut_curriculum_done": {"category": "tutorial", "order": 2, "emoji": "🎖️",
                             "name": "Curriculum Graduate",
                             "description": "Finish the AI-builder tutorial and "
                                            "reach the capstone."},
    # ── Progress — play more, unlock as you go ───────────────────────────────
    "exp_games_50":    {"category": "progress", "order": 1,  "emoji": "🎮",
                        "name": "Getting Started",
                        "description": "Play 50 total matches (any mode)."},
    "exp_games_100":   {"category": "progress", "order": 2,  "emoji": "🕹️",
                        "name": "Regular",
                        "description": "Play 100 total matches."},
    "exp_games_200":   {"category": "progress", "order": 3,  "emoji": "🏟️",
                        "name": "Veteran",
                        "description": "Play 200 total matches."},
    "exp_games_500":   {"category": "progress", "order": 4,  "emoji": "🏅",
                        "name": "Legend",
                        "description": "Play 500 total matches."},
    "exp_goals_50":    {"category": "progress", "order": 5,  "emoji": "⚽",
                        "name": "Finisher",
                        "description": "Score 50 total goals."},
    "exp_goals_100":   {"category": "progress", "order": 6,  "emoji": "🥅",
                        "name": "Poacher",
                        "description": "Score 100 total goals."},
    "exp_goals_250":   {"category": "progress", "order": 7,  "emoji": "🔥",
                        "name": "Goal Machine",
                        "description": "Score 250 total goals."},
    "exp_wins_25":     {"category": "progress", "order": 8,  "emoji": "🏆",
                        "name": "Winner",
                        "description": "Win 25 matches."},
    "exp_wins_50":     {"category": "progress", "order": 9,  "emoji": "👑",
                        "name": "Champion",
                        "description": "Win 50 matches."},
    "exp_wins_100":    {"category": "progress", "order": 10, "emoji": "💎",
                        "name": "Invincible",
                        "description": "Win 100 matches."},
    "social_friends_5":  {"category": "social", "order": 2,  "emoji": "👥",
                          "name": "Social Butterfly",
                          "description": "Have 5 friends."},
    "social_friends_10": {"category": "social", "order": 3,  "emoji": "🌟",
                          "name": "Popular",
                          "description": "Have 10 friends."},
    "clan_joined_3":   {"category": "social", "order": 4,  "emoji": "🛡️",
                        "name": "Clan Hopper",
                        "description": "Join 3 different clans (over time)."},
    "clan_created_2":  {"category": "social", "order": 5,  "emoji": "🏰",
                        "name": "Founder",
                        "description": "Create 2 clans."},
    "ai_models_5":     {"category": "ai", "order": 6,  "emoji": "🧠",
                        "name": "Tinkerer",
                        "description": "Create 5 custom AI models."},
    "ai_models_10":    {"category": "ai", "order": 7,  "emoji": "🔬",
                        "name": "Inventor",
                        "description": "Create 10 custom AI models."},
}

CATEGORY_LABELS = {
    "ranked":  "Ranked",
    "skill":   "Skill & Style",
    "ai":      "AI Arena",
    "tour":    "Tournaments",
    "social":  "Social",
    "explore": "Exploration",
    "season":  "Seasons",
    "tutorial": "Tutorial",
    "progress": "Progress",
}

# ── In-memory fallback (dev: no Supabase) ────────────────────────────────
_MEM: dict[str, dict] = {}  # user_id -> {achievement_key: awarded_at}
_SEEDED = False


def _ensure_seeded() -> None:
    """Idempotently upsert the catalog into Supabase (once per process)."""
    global _SEEDED
    if _SEEDED:
        return
    svc = _svc()
    if svc:
        try:
            rows = [dict(v, key=k) for k, v in ACHIEVEMENTS.items()]
            svc.table("achievement_definitions").upsert(
                rows, on_conflict="key").execute()
        except Exception:
            pass  # catalog is always readable via ACHIEVEMENTS anyway
    _SEEDED = True


def definitions() -> list[dict]:
    """The full catalog, ordered (category + order), each with its `key`."""
    return [dict(v, key=k)
            for k, v in sorted(ACHIEVEMENTS.items(),
                               key=lambda x: (x[1]["category"], x[1]["order"]))]


def _owned_row(svc, user_id: str, achievement_key: str):
    row = (svc.table("user_achievements")
           .select("achievement_key,awarded_at")
           .eq("user_id", user_id).eq("achievement_key", achievement_key)
           .maybe_single().execute())
    return row.data if row and row.data else None


# ── Awarding ──────────────────────────────────────────────────────────────

def award(user_id: str, achievement_key: str, toast: bool = True) -> dict | None:
    """Grant a badge exactly once; returns the badge dict on first award,
    None otherwise (already earned, unknown key, or storage unavailable).

    On success also pushes a toast payload onto `ach:new:{user_id}` so the
    existing response paths can render it without any new polling.
    """
    if not user_id:
        return None
    defn = ACHIEVEMENTS.get(achievement_key)
    if not defn:
        return None
    _ensure_seeded()
    now = _now_iso()
    badge = dict(defn, key=achievement_key, awarded_at=now)
    svc = _svc()
    if svc:
        if _owned_row(svc, user_id, achievement_key):
            return None
        try:
            (svc.table("user_achievements")
             .insert({"user_id": user_id, "achievement_key": achievement_key})
             .execute())
        except Exception:
            # Unique-constraint race or outage — never double-award.
            if _owned_row(svc, user_id, achievement_key):
                return None
            return None
    else:
        owned = _MEM.setdefault(user_id, {})
        if achievement_key in owned:
            return None
        owned[achievement_key] = now
    if toast:
        _push_toast(user_id, badge)
    return badge


def get_earned(user_id: str) -> dict[str, str]:
    """{achievement_key: awarded_at} for one user (empty for unknown users)."""
    svc = _svc()
    if not svc:
        return dict(_MEM.get(user_id) or {})
    rows = (svc.table("user_achievements")
            .select("achievement_key,awarded_at")
            .eq("user_id", user_id).execute().data or [])
    return {r["achievement_key"]: r.get("awarded_at") for r in rows}


def list_for_user(user_id: str) -> list[dict]:
    """Every badge with an `earned` flag + `awarded_at` (unearned kept visible)."""
    earned = get_earned(user_id)
    out = []
    for d in definitions():
        key = d["key"]
        out.append({**d, "earned": key in earned, "awarded_at": earned.get(key)})
    return out


def count_earned(user_id: str) -> int:
    if not user_id:
        return 0
    svc = _svc()
    if not svc:
        return len(_MEM.get(user_id) or {})
    try:
        res = (svc.table("user_achievements")
               .select("achievement_key").eq("user_id", user_id).execute())
        return len(res.data or [])
    except Exception:
        return len(get_earned(user_id))


# ── Toast queue (drained by existing response paths, no new polling) ────────

def _toast_key(user_id: str) -> str:
    return f"ach:new:{user_id}"


def _push_toast(user_id: str, badge: dict) -> None:
    try:
        _redis.lpush(_toast_key(user_id), json.dumps(badge))
        _redis.expire(_toast_key(user_id), TOAST_TTL)
    except Exception:
        pass


def push_toast(user_id: str, payload: dict) -> None:
    """Queue an arbitrary badge-shaped toast (e.g. the season-end summary
    rendered by the same AW.handle flow). Public twin of _push_toast."""
    if not user_id:
        return
    _push_toast(user_id, payload)


def drain_toasts(user_id: str) -> list[dict]:
    """Pop and return all pending badge toasts for a user (oldest first)."""
    if not user_id:
        return []
    key = _toast_key(user_id)
    try:
        raw = _redis.lrange(key, 0, -1) or []  # newest first
        for s in raw:
            _redis.lrem(key, 0, s)
        out = []
        for s in raw:
            try:
                out.append(json.loads(s))
            except (TypeError, ValueError):
                pass
        return list(reversed(out))  # oldest first
    except Exception:
        return []
