"""Supabase operations for user customization settings."""
from __future__ import annotations

_DEFAULT_PLAYER_STATS = [
    {"size": 50, "power": 50, "weight": 50, "agility": 50},
    {"size": 50, "power": 50, "weight": 50, "agility": 50},
    {"size": 50, "power": 50, "weight": 50, "agility": 50},
]

KEEPER_STYLES = [
    "default",
    "footwork", "footwork_plus",
    "rush_out", "rush_out_plus",
    "deflector", "deflector_plus",
    "cross_claimer", "cross_claimer_plus",
    "far_reach", "far_reach_plus",
    "far_throw", "far_throw_plus",
]

# Keeper PlayStyle → physics deltas (base + plus). Values tuned from EA descriptions:
# footwork: low-ball reach; rush_out: 1v1 rush speed; deflector: safe deflection;
# cross_claimer: claim/punch; far_reach: dive radius; far_throw: distribution.
KEEPER_STYLE_EFFECTS: dict[str, dict] = {
    "default": {},
    "footwork": {"radius_bonus": 4, "dive_speed_mult": 1.18, "low_ball_bonus": True},
    "footwork_plus": {"radius_bonus": 6, "dive_speed_mult": 1.28, "low_ball_bonus": True},
    "rush_out": {"rush_speed_mult": 1.35, "reaction_bonus": 0.08},
    "rush_out_plus": {"rush_speed_mult": 1.55, "reaction_bonus": 0.13},
    "deflector": {"deflect_speed_mult": 0.62, "safe_deflect": True},
    "deflector_plus": {"deflect_speed_mult": 0.48, "safe_deflect": True, "to_teammate": True},
    "cross_claimer": {"claim_speed_mult": 1.22, "punch_dist_mult": 1.32},
    "cross_claimer_plus": {"claim_speed_mult": 1.38, "punch_dist_mult": 1.52},
    "far_reach": {"radius_bonus": 7, "dive_speed_mult": 1.14, "extended_anim": True},
    "far_reach_plus": {"radius_bonus": 11, "dive_speed_mult": 1.22, "extended_anim": True},
    "far_throw": {"throw_dist_mult": 1.42},
    "far_throw_plus": {"throw_dist_mult": 1.72},
}

DEFAULT_CUSTOMIZATION = {
    "team_a_color": "#3b82f6",
    "team_b_color": "#ef4444",
    "ref_color": "#fde68a",
    "ball_color": "#f8fafc",
    "bg_color": "#2a2518",
    "player_count": 7,
    "formation_a": "3-2-1",
    "formation_b": "3-2-1",
    "team_a": "brazil",
    "team_b": "argentina",
    "grass_shade": "dark",
    "pitch_pattern": "stripes",
    "field_line_color": "#ffffff",
    "corner_flag_style": "normal",
    "crowd_palette": "classic",
    "stadium_seat_color": "#475569",
    "stadium_vignette": 0.6,
    "floodlight_color": "warm",
    "bg_scene": "day",
    "weather": "clear",
    "ball_design": "classic",
    "ball_pattern": "solid",
    "ball_size": "normal",
    "camera_type": "broadcast",
    "camera_height": 10,
    "camera_zoom": 10,
    "stadium_style": "modern",
    "net_color": "#ffffff",
    "keeper_color_a": "#22c55e",
    "keeper_color_b": "#f97316",
    "keeper_style_a": "default",
    "keeper_style_b": "default",
    "highlight_style": "glow",
    "shirt_font": "default",
    "goal_effect": "confetti",
    "trail_color": "#ffffff",
    "power_bar_style": "classic",
    "half_length": 45,
    "power_cap": 100,
    "win_goal_limit": 5,
    "player_stats": {
        "a": _DEFAULT_PLAYER_STATS,
        "b": _DEFAULT_PLAYER_STATS,
    },
    "player_names": {
        "a": ["GK", "DEF 1", "DEF 2", "MID 1", "MID 2", "FWD 1", "FWD 2"],
        "b": ["GK", "DEF 1", "DEF 2", "MID 1", "MID 2", "FWD 1", "FWD 2"],
    },
    "player_colors": {
        "a": ["#3b82f6", "#3b82f6", "#3b82f6", "#3b82f6", "#3b82f6", "#3b82f6", "#3b82f6"],
        "b": ["#ef4444", "#ef4444", "#ef4444", "#ef4444", "#ef4444", "#ef4444", "#ef4444"],
    },
}

_ALLOWED = set(DEFAULT_CUSTOMIZATION.keys())

#  Achievement-gated cosmetics 
# (field, value) -> achievement_key that unlocks it. Only "special variant"
# values are gated; every category keeps at least one always-available option,
# and the defaults in DEFAULT_CUSTOMIZATION are never gated. Availability is a
# DERIVED property (query user_achievements) — no separate unlock-state table.

UNLOCK_REQUIREMENTS: dict[tuple[str, str], str] = {
    ("ball_design",      "gold"):      "rk_first_win",
    ("ball_design",      "pixel"):     "ai_first_bench",
    ("ball_design",      "titanium"):  "sn_champion",
    ("crowd_palette",    "rainbow"):   "sk_big_win",
    ("bg_scene",         "cyber"):     "ai_first_model",
    ("grass_shade",      "neon"):      "friend_match",
    ("goal_effect",      "fireworks"): "tour_champion",
    ("goal_effect",      "aurora"):    "sn_top_10",
    ("floodlight_color", "red"):       "rk_streak_3",
    ("floodlight_color", "purple"):    "rk_rating_1400",
}

# Human-readable reward labels for toast notifications ("Unlocked: Gold ball").
_REWARD_LABELS: dict[tuple[str, str], str] = {
    ("ball_design",      "gold"):      "Gold ball",
    ("ball_design",      "pixel"):     "Pixel ball",
    ("ball_design",      "titanium"):  "Titanium ball",
    ("crowd_palette",    "rainbow"):   "Rainbow crowd",
    ("bg_scene",         "cyber"):     "Cyberpunk scene",
    ("grass_shade",      "neon"):      "Neon pitch",
    ("goal_effect",      "fireworks"): "Fireworks goal effect",
    ("goal_effect",      "aurora"):    "Aurora goal effect",
    ("floodlight_color", "red"):       "Red floodlights",
    ("floodlight_color", "purple"):    "Purple floodlights",
}

# achievement_key -> [reward labels] (reverse of UNLOCK_REQUIREMENTS).
COSMETIC_REWARDS: dict[str, list[str]] = {}
for _pair, _key in UNLOCK_REQUIREMENTS.items():
    COSMETIC_REWARDS.setdefault(_key, []).append(_REWARD_LABELS[_pair])


def locked_values(user_id: str) -> set[tuple[str, str]]:
    """(field, value) pairs the user cannot select (unlock not yet earned).

    Fast lookup: a single `get_earned` read of user_achievements; empty
    user_id (or unknown users) gets everything locked.
    """
    if not user_id:
        return set(UNLOCK_REQUIREMENTS)
    try:
        from db.achievements import get_earned
        earned = get_earned(user_id)
    except Exception:
        return set(UNLOCK_REQUIREMENTS)
    return {(f, v) for (f, v), key in UNLOCK_REQUIREMENTS.items() if key not in earned}


def is_unlocked(user_id: str, field: str, value: str) -> bool:
    """True if `value` is not gated, or the user earned the gate achievement."""
    req = UNLOCK_REQUIREMENTS.get((field, value))
    if not req:
        return True
    try:
        from db.achievements import get_earned
        return req in get_earned(user_id)
    except Exception:
        return False


def _svc():
    from db.supabase_client import service
    return service

# In-memory fallback (dev/tests: no Supabase) — mirrors the other db modules.
_MEM: dict[str, dict] = {}


def get_customization(user_id: str) -> dict:
    svc = _svc()
    if not svc:
        saved = _MEM.get(user_id)
        if saved is None:
            return dict(DEFAULT_CUSTOMIZATION)
        result = dict(DEFAULT_CUSTOMIZATION)
        result.update(saved)
        return result
    try:
        row = svc.table("profiles").select("customization").eq("id", user_id).maybe_single().execute()
        if row.data and row.data.get("customization"):
            cust = row.data["customization"]
            if isinstance(cust, dict):
                result = dict(DEFAULT_CUSTOMIZATION)
                result.update(cust)
                return result
    except Exception:
        pass
    return dict(DEFAULT_CUSTOMIZATION)


def save_customization(user_id: str, settings: dict) -> bool:
    svc = _svc()
    if not svc:
        saved = dict(_MEM.get(user_id) or {})
        saved.update(settings)
        _MEM[user_id] = saved
        return True
    current = get_customization(user_id)
    current.update(settings)
    cleaned = {k: v for k, v in current.items() if k in _ALLOWED}
    try:
        svc.table("profiles").update({"customization": cleaned}).eq("id", user_id).execute()
        return True
    except Exception:
        return False
