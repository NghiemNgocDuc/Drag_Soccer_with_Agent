from __future__ import annotations
import math
from models.soccer_logic import FIELD_W, FIELD_H, GOAL_Y1, GOAL_Y2, BALL_R, PLAYER_R, _MARGIN

DANGER_X_A = 250
DANGER_X_B = FIELD_W - 250

GOAL_TOP    = GOAL_Y1 + 8
GOAL_BOTTOM = GOAL_Y2 - 8
GOAL_CENTER = (GOAL_Y1 + GOAL_Y2) / 2


def needs_clear(state: dict, is_player_a: bool) -> bool:
    bx = state["ball"]["x"]
    return (is_player_a and bx < DANGER_X_A) or (not is_player_a and bx > DANGER_X_B)


def goal_targets(is_player_a: bool) -> list[tuple[float, float]]:
    gx = float(FIELD_W) + 30 if is_player_a else -30.0
    return [(gx, GOAL_TOP), (gx, GOAL_BOTTOM), (gx, GOAL_CENTER)]


def aim_through(px: float, py: float, bx: float, by: float, tx: float, ty: float) -> float:
    dx1 = bx - px
    dy1 = by - py
    len1 = math.hypot(dx1, dy1)
    if len1 < 1:
        return 0.0
    dx2 = tx - bx
    dy2 = ty - by
    len2 = math.hypot(dx2, dy2)
    if len2 < 1:
        return math.degrees(math.atan2(dy1, dx1))
    dot = dx1 * dx2 + dy1 * dy2
    cos_a = dot / (len1 * len2)
    cos_a = max(-1.0, min(1.0, cos_a))
    cross = dx1 * dy2 - dy1 * dx2
    sign = 1.0 if cross >= 0 else -1.0
    return math.degrees(math.acos(cos_a) * sign)


def progress_score(end_x: float, is_player_a: bool, defensive: bool) -> float:
    if defensive:
        if is_player_a:
            if end_x >= FIELD_W * 0.6:
                return 600.0
            return end_x * 0.8
        else:
            if end_x <= FIELD_W * 0.4:
                return 600.0
            return (FIELD_W - end_x) * 0.8
    else:
        return (FIELD_W - end_x) if is_player_a else end_x


def dist_to_goal(px: float, py: float, is_player_a: bool) -> float:
    gx = FIELD_W if is_player_a else 0
    gy = GOAL_CENTER
    return math.hypot(px - gx, py - gy)


def suggested_powers(dist: float) -> list[float]:
    if dist < 150:
        return [55.0, 65.0, 78.0]
    elif dist < 300:
        return [65.0, 78.0, 88.0, 95.0]
    elif dist < 500:
        return [78.0, 88.0, 95.0, 100.0]
    else:
        return [88.0, 95.0, 100.0]
