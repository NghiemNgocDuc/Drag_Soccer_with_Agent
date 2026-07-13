"""Supabase operations for user customization settings."""
from __future__ import annotations

DEFAULT_CUSTOMIZATION = {
    "team_a_color": "#3b82f6",
    "team_b_color": "#ef4444",
    "ref_color": "#fde68a",
    "ball_color": "#f8fafc",
    "bg_color": "#2a2518",
    "player_count": 3,
    "grass_shade": "dark",
    "pitch_pattern": "stripes",
    "field_line_color": "#ffffff",
    "corner_flag_style": "normal",
    "crowd_palette": "classic",
    "stadium_vignette": 0.6,
    "floodlight_color": "warm",
    "bg_scene": "night",
    "ball_design": "classic",
    "keeper_color_a": "#22c55e",
    "keeper_color_b": "#f97316",
    "highlight_style": "glow",
    "shirt_font": "default",
    "goal_effect": "confetti",
    "trail_color": "#ffffff",
    "power_bar_style": "classic",
    "half_length": 45,
    "power_cap": 100,
    "win_goal_limit": 5,
}

_ALLOWED = set(DEFAULT_CUSTOMIZATION.keys())

def _svc():
    from db.supabase_client import service
    return service


def get_customization(user_id: str) -> dict:
    svc = _svc()
    if not svc:
        return dict(DEFAULT_CUSTOMIZATION)
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
        return False
    current = get_customization(user_id)
    current.update(settings)
    cleaned = {k: v for k, v in current.items() if k in _ALLOWED}
    try:
        svc.table("profiles").update({"customization": cleaned}).eq("id", user_id).execute()
        return True
    except Exception:
        return False
