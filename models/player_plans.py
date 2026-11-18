"""Player-authored off-ball routes, executed by the ordinary support physics.

Coordinates are fractions of the pitch, always drawn attacking right. Team B
mirrors x. A route advances through at most 16 waypoints across kicks, then
holds its final point. No route can launch the human striker or the kicker.
"""
from __future__ import annotations

import math

MAX_POINTS = 16
ARRIVAL = 24.0


def normalize_plans(raw, count=5):
    if raw is None:
        return [{"mode": "auto", "points": []} for _ in range(count)]
    if not isinstance(raw, list) or len(raw) != count:
        raise ValueError("Choose one instruction for each of the five players.")
    return [_normalize_instruction(plan, index) for index, plan in enumerate(raw)]


def _normalize_instruction(plan, index):
    if not isinstance(plan, dict) or plan.get("mode") not in ("auto", "hold", "route"):
        raise ValueError("Choose Automatic, Hold position, or Draw a run.")
    mode, points = plan["mode"], plan.get("points", [])
    if not isinstance(points, list) or len(points) > MAX_POINTS:
        raise ValueError("Draw a route with at most 16 waypoints.")
    if (mode == "auto" and points) or (mode == "hold" and len(points) != 1) or (mode == "route" and not points):
        raise ValueError("A hold needs one point; a run needs at least one waypoint.")
    clean = []
    for point in points:
        if not isinstance(point, dict) or set(point) != {"x", "y"}:
            raise ValueError("Each waypoint needs an x and y coordinate.")
        if any(type(point[k]) not in (int, float) or not math.isfinite(point[k]) for k in ("x", "y")):
            raise ValueError("Waypoint coordinates must be finite numbers.")
        x, y = point["x"], point["y"]
        if not (.06 <= x <= .94 and .09 <= y <= .91):
            raise ValueError("Keep routes inside the playable pitch.")
        if index == 0 and not (.06 <= x <= .14 and .40 <= y <= .60):
            raise ValueError("Keep the goalkeeper inside the marked keeper area.")
        clean.append({"x": round(x, 5), "y": round(y, 5)})
    return {"mode": mode, "points": clean}


def _plan(state, side, index):
    from models.soccer_logic import human_player_index, team_movement_enabled
    if (state.get("sport", "soccer") != "soccer" or not team_movement_enabled(state)
            or state.get("game_mode", "hvai") not in ("hvai", "aivai")
            or (side and state.get("game_mode") == "hvai" and index == human_player_index(state))):
        return None
    plans = state.get("player_plans_a" if side else "player_plans_b")
    if not isinstance(plans, list) or len(plans) != 5 or not 0 <= index < 5:
        return None
    try:
        # Invalid old/custom states fall back to standard formation tactics.
        plan = _normalize_instruction(plans[index], index)
    except (ValueError, TypeError, OverflowError):
        return None
    return plan if plan["mode"] != "auto" else None


def _cursor(state, side, index, length):
    progress = state.get("player_plan_progress_a" if side else "player_plan_progress_b", [])
    value = progress[index] if isinstance(progress, list) and index < len(progress) else 0
    return max(0, min(length - 1, value)) if type(value) is int else 0


def _world(point, side):
    from models.soccer_logic import FIELD_W, FIELD_H
    return {"x": (point["x"] if side else 1 - point["x"]) * FIELD_W, "y": point["y"] * FIELD_H}


def plan_target(state, side, index):
    plan = _plan(state, side, index)
    if plan is None:
        return None
    points = plan["points"]
    cursor = _cursor(state, side, index, len(points))
    player = state["players_a" if side else "players_b"][index]
    while cursor < len(points) - 1:
        target = _world(points[cursor], side)
        if math.hypot(player["x"] - target["x"], player["y"] - target["y"]) > ARRIVAL:
            break
        cursor += 1
    return _world(points[cursor], side)


def advance_plans(state, trajectory):
    """Commit progress only from real physical positions, never predictions."""
    for side, suffix, track in ((True, "a", "a"), (False, "b", "b")):
        progress = [0] * 5
        any_plan = False
        for index in range(5):
            plan = _plan(state, side, index)
            if plan is None:
                continue
            any_plan = True
            points = plan["points"]
            cursor = _cursor(state, side, index, len(points))
            for frame in trajectory:
                roster = frame.get(track, [])
                if index >= len(roster):
                    continue
                while cursor < len(points) - 1:
                    target = _world(points[cursor], side)
                    if math.hypot(roster[index]["x"] - target["x"], roster[index]["y"] - target["y"]) > ARRIVAL:
                        break
                    cursor += 1
            progress[index] = cursor
        if any_plan:
            state["player_plan_progress_" + suffix] = progress


def reset_progress(state):
    for side in ("a", "b"):
        if "player_plans_" + side in state:
            state["player_plan_progress_" + side] = [0] * 5


def cache_key(state):
    """Bounded immutable key; malformed legacy plans have no effect."""
    values = []
    for side, suffix in ((True, "a"), (False, "b")):
        for index in range(5):
            plan = _plan(state, side, index)
            values.append(None if plan is None else (plan["mode"],
                tuple((point["x"], point["y"]) for point in plan["points"]),
                _cursor(state, side, index, len(plan["points"]))))
    return tuple(values)
