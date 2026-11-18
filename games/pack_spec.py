"""Turn a small table description into a bounded, playable pack.

A pack is data: field, materials, and a scoring family. The compiler does not
run uploaded code. Soccer and basketball stay on their own engines; this is
the door for a new table that can be drawn and played immediately.
"""
from __future__ import annotations

import math
import re

_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
FAMILIES = ("strike", "pot")
RENDER_BUDGET_S = 20.0

_FELT = "#1e6b45"
_RAIL = "#3f1727"
_BALL = "#f4efe4"
_OBJECT = "#e2b43a"
_HUMAN = "#7b2943"
_BOT = "#1d4e89"
_CUE = "#fffdf8"


def presets() -> dict[str, dict]:
    """Named tables the form can load. Values are already inside the limits."""
    return {
        "air_hockey": {
            "family": "strike",
            "name": "Air hockey",
            "field_w": 1000, "field_h": 560,
            "gravity": 0, "damping": 0.86, "win_score": 5,
            "ball_r": 16, "ball_mass": 1.05, "ball_bounce": 0.96,
            "striker_r": 30, "striker_mass": 6.5, "striker_speed": 1100,
            "goal_opening": 0.36,
            "bumpers": 0,
            "felt": "#0f6e78", "rail": _RAIL,
            "ball_color": "#f7f1e7", "human_color": _HUMAN, "bot_color": _BOT,
        },
        "gravity_duel": {
            "family": "strike",
            "name": "Gravity duel",
            "field_w": 920, "field_h": 560,
            "gravity": 760, "damping": 0.8, "win_score": 5,
            "ball_r": 15, "ball_mass": 1.2, "ball_bounce": 0.86,
            "striker_r": 28, "striker_mass": 7.0, "striker_speed": 980,
            "goal_opening": 0.4,
            "bumpers": 0,
            "felt": "#245c86", "rail": _RAIL,
            "ball_color": "#f7f1e7", "human_color": _HUMAN, "bot_color": "#c4552a",
        },
        "bumper_table": {
            "family": "strike",
            "name": "Bumper table",
            "field_w": 980, "field_h": 600,
            "gravity": 0, "damping": 0.9, "win_score": 4,
            "ball_r": 15, "ball_mass": 1.15, "ball_bounce": 0.94,
            "striker_r": 26, "striker_mass": 6.0, "striker_speed": 1040,
            "goal_opening": 0.3,
            "bumpers": 4,
            "felt": "#1e6b45", "rail": _RAIL,
            "ball_color": _BALL, "human_color": _HUMAN, "bot_color": _BOT,
        },
        "pocket_table": {
            "family": "pot",
            "name": "Pocket table",
            "field_w": 960, "field_h": 500,
            "gravity": 0, "damping": 0.93, "win_score": 3,
            "ball_r": 12, "ball_mass": 1.0, "ball_bounce": 0.97,
            "pocket_r": 22,
            "bumpers": 0,
            "felt": "#1b5c3a", "rail": "#3a2218",
            "ball_color": _OBJECT, "cue_color": _CUE,
        },
    }


def native_games() -> list[dict]:
    return [
        {"id": "soccer", "name": "Soccer", "href": "/play2d",
         "note": "Five-a-side on the main pitch, including online matches."},
        {"id": "basketball", "name": "Basketball", "href": "/play2d",
         "note": "Local hoops. Choose it in match setup."},
    ]


def normalize(raw) -> dict:
    """Clamp a request into a canonical pack, or raise ValueError."""
    if not isinstance(raw, dict):
        raise ValueError("Describe the table as fields: size, ball, and how a point is scored.")
    preset_id = raw.get("preset")
    if preset_id is not None and (not isinstance(preset_id, str) or preset_id not in presets()):
        raise ValueError("Choose a known table preset.")
    base = {}
    if isinstance(preset_id, str) and preset_id in presets():
        base.update(presets()[preset_id])
    # Explicit fields override a preset so the form can edit one.
    merged = dict(base)
    for key, value in raw.items():
        if key != "preset" and value is not None:
            merged[key] = value

    # Accept the canonical spec returned by preview/state without silently
    # replacing its materials. Flat editor fields intentionally take precedence.
    for section, fields in {
        "ball": {"r": "ball_r", "mass": "ball_mass", "bounce": "ball_bounce", "color": "ball_color"},
        "striker": {"r": "striker_r", "mass": "striker_mass", "speed": "striker_speed", "human": "human_color", "bot": "bot_color"},
        "cue": {"mass": "cue_mass", "speed": "striker_speed", "color": "cue_color"},
    }.items():
        nested = raw.get(section)
        if isinstance(nested, dict):
            for source, target in fields.items():
                if source in nested and target not in raw:
                    merged[target] = nested[source]
    if isinstance(raw.get("bumpers"), list) and "bumper_list" not in raw:
        merged["bumper_list"] = raw["bumpers"]

    family = merged.get("family", "strike")
    if family not in FAMILIES:
        raise ValueError("Choose a strike table or a pocket table.")

    field_w = _num(merged.get("field_w", 1000), 520, 1400, "Width")
    field_h = _num(merged.get("field_h", 560), 320, 900, "Height")
    spec = {
        "family": family,
        "name": _name(merged.get("name")),
        "field_w": field_w,
        "field_h": field_h,
        "gravity": 0.0 if family == "pot" else _num(merged.get("gravity", 0), 0, 1400, "Gravity"),
        "damping": _num(merged.get("damping", 0.88), 0.55, 0.98, "Glide"),
        "win_score": int(_num(merged.get("win_score", 5), 1, 11, "Points to win")),
        "ball": {
            "r": _num(merged.get("ball_r", 15), 8, 28, "Ball size"),
            "mass": _num(merged.get("ball_mass", 1.1), 0.4, 4, "Ball weight"),
            "bounce": _num(merged.get("ball_bounce", 0.94), 0.2, 0.99, "Ball bounce"),
            "color": _color(merged.get("ball_color"), _OBJECT if family == "pot" else _BALL),
        },
        "bumpers": _bumpers(merged, field_w, field_h),
        "felt": _color(merged.get("felt"), _FELT),
        "rail": _color(merged.get("rail"), _RAIL),
    }
    if family == "strike":
        spec["striker"] = {
            "r": _num(merged.get("striker_r", 28), 16, 42, "Mallet size"),
            "mass": _num(merged.get("striker_mass", 6.5), 2, 14, "Mallet weight"),
            "speed": _num(merged.get("striker_speed", 1040), 420, 1400, "Mallet speed"),
            "human": _color(merged.get("human_color"), _HUMAN),
            "bot": _color(merged.get("bot_color"), _BOT),
        }
        spec["goal_opening"] = _num(merged.get("goal_opening", 0.36), 0.22, 0.55, "Goal size")
    else:
        spec["cue"] = {
            "r": spec["ball"]["r"],
            "mass": _num(merged.get("cue_mass", spec["ball"]["mass"]), 0.4, 4, "Cue weight"),
            "speed": _num(merged.get("striker_speed", 1200), 500, 1600, "Cue speed"),
            "color": _color(merged.get("cue_color"), _CUE),
        }
        spec["pocket_r"] = _num(merged.get("pocket_r", 22), 14, 34, "Pocket size")
    return spec


def opening_state(spec: dict) -> dict:
    w, h = spec["field_w"], spec["field_h"]
    state = {
        "score_a": 0, "score_b": 0, "turn": "A", "shot": 0,
        "game_over": False, "winner": None,
    }
    if spec["family"] == "strike":
        state["ball"] = _body(w * 0.5, h * 0.5)
        state["a"] = _body(w * 0.16, h * 0.5)
        state["b"] = _body(w * 0.84, h * 0.5)
    else:
        state["cue"] = _body(w * 0.27, h * 0.5)
        state["ball"] = _body(w * 0.67, h * 0.5)
        state["layout"] = 0
    return state


def faceoff_positions(spec: dict, state: dict) -> None:
    """Place the ball and both mallets for the side that is about to restart."""
    w, h = spec["field_w"], spec["field_h"]
    starter = state["turn"]
    if spec["family"] != "strike":
        return
    # The restarting mallet gets the ball just ahead of it, still on its side.
    if starter == "A":
        state["a"] = _body(w * 0.16, h * 0.5)
        state["b"] = _body(w * 0.84, h * 0.5)
        state["ball"] = _body(w * 0.30, h * 0.5)
    else:
        state["a"] = _body(w * 0.16, h * 0.5)
        state["b"] = _body(w * 0.84, h * 0.5)
        state["ball"] = _body(w * 0.70, h * 0.5)


def pocket_anchors(spec: dict) -> list[tuple[float, float]]:
    w, h = spec["field_w"], spec["field_h"]
    return [(0.0, 0.0), (w, 0.0), (0.0, h), (w, h), (w * 0.5, 0.0), (w * 0.5, h)]


def goal_span(spec: dict) -> tuple[float, float]:
    opening = spec["goal_opening"] * spec["field_h"]
    y1 = (spec["field_h"] - opening) / 2
    return y1, y1 + opening


def _body(x: float, y: float) -> dict:
    return {"x": round(x, 3), "y": round(y, 3), "vx": 0.0, "vy": 0.0}


def _name(value) -> str:
    text = value if isinstance(value, str) else ""
    text = "".join(ch for ch in text if ch.isprintable()).strip()
    return (text[:40] or "Table game")


def _color(value, default: str) -> str:
    if isinstance(value, str) and _COLOR.fullmatch(value.strip()):
        return value.strip().lower()
    return default


def _num(value, lo: float, hi: float, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} needs a number.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} needs a finite number.")
    return max(lo, min(hi, number))


def _bumpers(raw: dict, field_w: float, field_h: float) -> list[dict]:
    supplied = raw.get("bumper_list")
    items = []
    if isinstance(supplied, list):
        items = supplied[:6]
    else:
        count = int(_num(raw.get("bumpers", 0), 0, 6, "Bumpers"))
        spots = (
            (0.38, 0.30), (0.62, 0.70), (0.38, 0.70), (0.62, 0.30),
            (0.50, 0.18), (0.50, 0.82),
        )
        radius = max(16.0, min(field_w, field_h) * 0.04)
        items = [{"x": field_w * sx, "y": field_h * sy, "r": radius} for sx, sy in spots[:count]]
    clean = []
    margin = 70
    for item in items:
        if not isinstance(item, dict):
            continue
        radius = _num(item.get("r", 20), 12, 40, "Bumper size")
        x = _num(item.get("x", field_w / 2), margin, field_w - margin, "Bumper position")
        y = _num(item.get("y", field_h / 2), margin, field_h - margin, "Bumper position")
        # Keep the opening ball and both mallets clear of a bumper.
        if math.hypot(x - field_w * 0.5, y - field_h * 0.5) < radius + 48:
            continue
        if math.hypot(x - field_w * 0.16, y - field_h * 0.5) < radius + 48:
            continue
        if math.hypot(x - field_w * 0.84, y - field_h * 0.5) < radius + 48:
            continue
        clean.append({"x": round(x, 2), "y": round(y, 2), "r": round(radius, 2)})
    return clean[:6]
