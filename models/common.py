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


def aim_through(px: float, py: float, bx: float, by: float, tx: float, ty: float,
                contact_radius: float = PLAYER_R + BALL_R) -> float:
    """Return an absolute launch angle toward the ball's desired impact side.

    A pawn has to contact the ball before it can send it toward a target. Aim
    slightly inside that contact circle so oblique targets cannot produce a
    grazing miss. Close or overlapping pawns aim at the centre instead.
    """
    dx, dy = bx - px, by - py
    distance = math.hypot(dx, dy)
    tx, ty = tx - bx, ty - by
    target_distance = math.hypot(tx, ty)
    if distance < 1e-9:
        return math.degrees(math.atan2(ty, tx)) % 360.0
    if target_distance > 1e-9 and distance > contact_radius:
        # Use the requested collision normal when that face is reachable from
        # this pawn. An occluded face still gets a safe central-contact angle.
        visible = (dx * tx + dy * ty) / target_distance > contact_radius
        offset = max(0.0, contact_radius) * (0.995 if visible else 0.85) / target_distance
        dx -= tx * offset
        dy -= ty * offset
    return math.degrees(math.atan2(dy, dx)) % 360.0


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
        return end_x if is_player_a else (FIELD_W - end_x)


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
