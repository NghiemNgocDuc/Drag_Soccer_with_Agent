"""Search the pack's own step. The bot has no separate model of the table."""
from __future__ import annotations

import math
import time

from games.pack_engine import resolve
from games.pack_spec import goal_span, pocket_anchors

# A move should feel immediate. The 20s figure is the table appearing, not the shot.
BUDGET_S = 0.32
_MAX_CANDIDATES = 72


def best_action(spec: dict, state: dict, side: str, budget_s: float = BUDGET_S) -> dict:
    """Return the highest-scoring legal shot found before the budget runs out."""
    started = time.perf_counter()
    candidates = _candidates(spec, state, side)[:_MAX_CANDIDATES]
    best = None
    sims = 0

    def consider(angle: float, power: float) -> None:
        nonlocal best, sims
        after, _frames, info = resolve(spec, state, side, angle, power)
        sims += 1
        value = _value(spec, state, after, side, info)
        if best is None or value > best["value"]:
            best = {
                "angle": round(angle % 360, 2),
                "power": round(power, 2),
                "value": value,
                "found_goal": info.get("scored_by") == side,
                "sims": sims,
            }

    for angle, power in candidates:
        if best is not None and time.perf_counter() - started > budget_s:
            break
        consider(angle, power)

    if best is not None and time.perf_counter() - started < budget_s:
        base_angle = best["angle"]
        base_power = best["power"]
        for delta_angle in (-7, -3, 3, 7):
            for delta_power in (-14, 14):
                if time.perf_counter() - started > budget_s:
                    break
                consider((base_angle + delta_angle) % 360, min(100.0, max(18.0, base_power + delta_power)))

    if best is None:
        best = {"angle": 0.0, "power": 70.0, "value": 0.0, "found_goal": False, "sims": 0}
    best["sims"] = sims
    best["ms"] = round((time.perf_counter() - started) * 1000, 1)
    return best


def _candidates(spec: dict, state: dict, side: str) -> list[tuple[float, float]]:
    if spec["family"] == "pot":
        raw = _pot_candidates(spec, state)
    else:
        raw = _strike_candidates(spec, state, side)
    seen = set()
    ordered = []
    for angle, power in raw:
        key = (round(angle) % 360, int(power))
        if key in seen:
            continue
        seen.add(key)
        ordered.append((angle % 360, float(power)))
    return ordered


def _strike_candidates(spec: dict, state: dict, side: str) -> list[tuple[float, float]]:
    ball = state["ball"]
    w = spec["field_w"]
    y1, y2 = goal_span(spec)
    enemy_x = w if side == "A" else 0.0
    shots = []
    powers = (72.0, 90.0, 100.0)
    for index in range(9):
        gy = y1 + (y2 - y1) * index / 8
        dx, dy = _unit(enemy_x - ball["x"], gy - ball["y"])
        shots.append((_aim(spec, state, side, dx, dy),))
    for wall_y in (0.0, spec["field_h"]):
        for index in range(5):
            gy = y1 + (y2 - y1) * index / 4
            bank = _bank_direction(ball, enemy_x, gy, wall_y, w)
            if bank is not None:
                shots.append((_aim(spec, state, side, bank[0], bank[1]),))
    ordered = [(angle, power) for (angle,) in shots for power in powers]
    if spec["gravity"] > 80:
        for deg in range(0, 360, 18):
            dx, dy = math.cos(math.radians(deg)), math.sin(math.radians(deg))
            ordered.append((_aim(spec, state, side, dx, dy), 94.0))
    me = state["a" if side == "A" else "b"]
    bearing = math.degrees(math.atan2(ball["y"] - me["y"], ball["x"] - me["x"]))
    for delta in range(-50, 51, 12):
        ordered.append(((bearing + delta) % 360, 90.0))
    return ordered


def _pot_candidates(spec: dict, state: dict) -> list[tuple[float, float]]:
    cue = state["cue"]
    ball = state["ball"]
    reach = spec["cue"]["r"] + spec["ball"]["r"]
    ordered = []
    pockets = pocket_anchors(spec)
    for cut in (0.0, -3.5, 3.5, -8.0, 8.0):
        for px, py in pockets:
            dx, dy = _unit(px - ball["x"], py - ball["y"])
            ghost_x = ball["x"] - dx * reach
            ghost_y = ball["y"] - dy * reach
            base = math.degrees(math.atan2(ghost_y - cue["y"], ghost_x - cue["x"]))
            for power in (66.0, 100.0):
                ordered.append(((base + cut) % 360, power))
    bearing = math.degrees(math.atan2(ball["y"] - cue["y"], ball["x"] - cue["x"]))
    for delta in range(-18, 19, 6):
        ordered.append(((bearing + delta) % 360, 88.0))
    return ordered


def _unit(dx: float, dy: float) -> tuple[float, float]:
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return 1.0, 0.0
    return dx / length, dy / length


def _aim(spec: dict, state: dict, side: str, dx: float, dy: float) -> float:
    ball = state["ball"]
    me = state["a" if side == "A" else "b"]
    reach = spec["striker"]["r"] + spec["ball"]["r"]
    contact_x = ball["x"] - dx * reach
    contact_y = ball["y"] - dy * reach
    return math.degrees(math.atan2(contact_y - me["y"], contact_x - me["x"])) % 360


def _bank_direction(ball, enemy_x, goal_y, wall_y, field_w):
    mirror_y = (2 * wall_y) - goal_y
    dx = enemy_x - ball["x"]
    dy = mirror_y - ball["y"]
    if abs(dy) < 1e-6:
        return None
    travel = (wall_y - ball["y"]) / dy
    if travel <= 0.08 or travel >= 0.92:
        return None
    bank_x = ball["x"] + travel * dx
    if bank_x < 24 or bank_x > field_w - 24:
        return None
    return _unit(bank_x - ball["x"], wall_y - ball["y"])


def _value(spec, before, after, side, info) -> float:
    scorer = info.get("scored_by")
    if scorer == side:
        steps = info.get("steps", 0)
        bonus = 0.0
        mouth = info.get("mouth_y")
        if mouth is not None and spec["family"] == "strike":
            y1, y2 = goal_span(spec)
            bonus = 28 - abs(mouth - (y1 + y2) / 2) * 0.12
        return 100000 - steps * 0.35 + bonus
    if scorer:
        return -100000
    if spec["family"] == "pot":
        progress = _nearest_pocket(spec, before) - _nearest_pocket(spec, after)
        if info.get("scratch"):
            return -4000 + progress
        return progress * 5
    return (_mouth_distance(spec, before, side) - _mouth_distance(spec, after, side)) * 2.4


def _mouth_distance(spec, state, side) -> float:
    ball = state["ball"]
    y1, y2 = goal_span(spec)
    goal_x = spec["field_w"] if side == "A" else 0.0
    return math.hypot(ball["x"] - goal_x, ball["y"] - (y1 + y2) / 2)


def _nearest_pocket(spec, state) -> float:
    ball = state["ball"]
    return min(math.hypot(ball["x"] - px, ball["y"] - py) for px, py in pocket_anchors(spec))
