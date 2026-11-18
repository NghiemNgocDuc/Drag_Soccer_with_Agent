"""Step a pack with pymunk. The match and the bot both call resolve()."""
from __future__ import annotations

import math

import pymunk

from games.pack_spec import faceoff_positions, goal_span, opening_state, pocket_anchors

DT = 1.0 / 90.0
SLEEP = 14.0
_STRIKE_STEPS = 210
_POT_STEPS = 300
_BALL = 1
_STRIKER = 2


def resolve(spec: dict, state: dict, side: str, angle_deg: float, power: float) -> tuple[dict, list[dict], dict]:
    """Play one shot on a copy of state. Returns the next state, frames, and a summary."""
    if side not in ("A", "B"):
        raise ValueError("Choose side A or side B.")
    if state.get("game_over"):
        raise ValueError("This table is already finished.")
    if state.get("turn") != side:
        raise ValueError("Wait for your turn.")
    try:
        angle = float(angle_deg)
        strength = float(power)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Aim and power need numbers.") from exc
    if not math.isfinite(angle) or not math.isfinite(strength):
        raise ValueError("Aim and power need finite numbers.")
    strength = max(0.0, min(100.0, strength))

    nxt = _copy_state(state)
    frames, info = _simulate(spec, nxt, side, angle, strength)
    _apply_result(spec, nxt, side, info)
    info["angle"] = round(angle, 2)
    info["power"] = round(strength, 2)
    return nxt, frames, info


def geometry(spec: dict) -> dict:
    w, h = spec["field_w"], spec["field_h"]
    pieces = []
    if spec["family"] == "strike":
        pieces = [
            {"id": "a", "r": spec["striker"]["r"], "color": spec["striker"]["human"]},
            {"id": "b", "r": spec["striker"]["r"], "color": spec["striker"]["bot"]},
            {"id": "ball", "r": spec["ball"]["r"], "color": spec["ball"]["color"]},
        ]
        y1, y2 = goal_span(spec)
        goals = [
            {"x": -34, "y": y1, "w": 34, "h": y2 - y1, "side": "A"},
            {"x": w, "y": y1, "w": 34, "h": y2 - y1, "side": "B"},
        ]
        pockets = []
    else:
        pieces = [
            {"id": "cue", "r": spec["cue"]["r"], "color": spec["cue"]["color"]},
            {"id": "ball", "r": spec["ball"]["r"], "color": spec["ball"]["color"]},
        ]
        goals = []
        gap = _pocket_gap(spec)
        pockets = [{"x": x, "y": y, "r": gap * 0.55} for x, y in pocket_anchors(spec)]
    return {
        "w": w, "h": h,
        "view": {"x": -48, "y": -48, "w": w + 96, "h": h + 96},
        "felt": spec["felt"], "rail": spec["rail"],
        "goals": goals, "pockets": pockets,
        "bumpers": [dict(item) for item in spec["bumpers"]],
        "pieces": pieces,
        "family": spec["family"],
        "gravity": spec["gravity"],
        "name": spec["name"],
        "win_score": spec["win_score"],
    }


def present(spec: dict, state: dict) -> dict:
    bodies = {}
    for key in _piece_ids(spec):
        body = state[key]
        bodies[key] = [round(body["x"], 2), round(body["y"], 2)]
    return {
        "geometry": geometry(spec),
        "bodies": bodies,
        "score_a": state["score_a"],
        "score_b": state["score_b"],
        "turn": state["turn"],
        "game_over": state["game_over"],
        "winner": state["winner"],
        "shot": state["shot"],
        "aim": "a" if spec["family"] == "strike" else "cue",
    }


def check_table(spec: dict) -> dict:
    """Repeat one gentle shot. A playable table agrees with itself and stays finite."""
    state = opening_state(spec)
    side = "A"
    angle, power = (0.0, 42.0)
    _, frames_a, info_a = resolve(spec, state, side, angle, power)
    _, frames_b, info_b = resolve(spec, state, side, angle, power)
    end_a = frames_a[-1]["bodies"] if frames_a else {}
    end_b = frames_b[-1]["bodies"] if frames_b else {}
    deterministic = info_a.get("scored_by") == info_b.get("scored_by") and _near(end_a, end_b)
    finite = _frames_finite(frames_a) and _frames_finite(frames_b)
    if not finite:
        raise ValueError("This table threw an object off to infinity. Reduce the speed or the bounce.")
    if not deterministic:
        raise ValueError("This table did not repeat the same shot. Adjust the sizes and try again.")
    return {"deterministic": True, "bounded": True, "sample_shot": True}


def _simulate(spec: dict, state: dict, side: str, angle: float, power: float) -> tuple[list[dict], dict]:
    space, actors = _build(spec, state)
    actor = actors["a" if side == "A" and spec["family"] == "strike" else
                   "b" if side == "B" and spec["family"] == "strike" else "cue"]
    speed = _launch_speed(spec, power)
    radians = math.radians(angle)
    actor.velocity = (math.cos(radians) * speed, math.sin(radians) * speed)

    info = {"scored_by": None, "potted": False, "scratch": False, "steps": 0, "mouth_y": None}
    frames = [_frame(0, actors)]
    limit = _STRIKE_STEPS if spec["family"] == "strike" else _POT_STEPS
    slow = 0
    previous = (actors["ball"].position.x, actors["ball"].position.y)
    previous_cue = actors["cue"].position if "cue" in actors else None

    for step in range(1, limit + 1):
        space.step(DT)
        for body in actors.values():
            _cap_velocity(body, getattr(body, "speed_cap", 1400))
        info["steps"] = step
        if spec["family"] == "strike":
            _clamp_striker(actors["a"], spec)
            _clamp_striker(actors["b"], spec)
            scored, mouth_y = _strike_score(previous, actors["ball"], spec)
            if scored:
                info["scored_by"] = scored
                info["mouth_y"] = mouth_y
        else:
            potted = _fell_in(actors["ball"], spec)
            scratched = _fell_in(actors["cue"], spec)
            if potted:
                info["potted"] = True
                _park(actors["ball"])
            if scratched:
                info["scratch"] = True
                _park(actors["cue"])
        previous = (actors["ball"].position.x, actors["ball"].position.y)
        if step % 2 == 0 or info["scored_by"] or info["potted"] or info["scratch"]:
            frames.append(_frame(step, actors))
        if info["scored_by"] or info["potted"] or info["scratch"]:
            break
        if _settled(actors):
            slow += 1
            if slow >= 8 and step > 10:
                break
        else:
            slow = 0
        previous_cue = actors["cue"].position if "cue" in actors else previous_cue

    _write_back(spec, state, actors, info, previous_cue)
    if len(frames) > 110:
        frames = _decimate(frames, 110)
    return frames, info


def _apply_result(spec: dict, state: dict, side: str, info: dict) -> None:
    state["shot"] = int(state.get("shot", 0)) + 1
    other = "B" if side == "A" else "A"
    if spec["family"] == "strike":
        scorer = info["scored_by"]
        if scorer:
            state["score_" + scorer.lower()] += 1
            state["turn"] = "B" if scorer == "A" else "A"
            faceoff_positions(spec, state)
        else:
            state["turn"] = other
    else:
        clean_pot = info["potted"] and not info["scratch"]
        if clean_pot:
            info["scored_by"] = side
            state["score_" + side.lower()] += 1
            state["layout"] = int(state.get("layout", 0)) + 1
            _reset_rack(spec, state)
        elif info["scratch"]:
            kitchen_x = spec["field_w"] * (0.27 if side == "A" else 0.73)
            state["cue"] = {"x": round(kitchen_x, 3), "y": round(spec["field_h"] * 0.5, 3), "vx": 0.0, "vy": 0.0}
            if info["potted"]:
                state["layout"] = int(state.get("layout", 0)) + 1
                _reset_rack(spec, state)
        state["turn"] = other
    if state["score_a"] >= spec["win_score"] or state["score_b"] >= spec["win_score"]:
        state["game_over"] = True
        state["winner"] = "A" if state["score_a"] > state["score_b"] else "B"


def _build(spec: dict, state: dict):
    space = pymunk.Space()
    space.gravity = (0.0, float(spec["gravity"]))
    space.damping = float(spec["damping"])
    _add_rails(space, spec)
    _add_bumpers(space, spec)
    actors = {}
    if spec["family"] == "strike":
        actors["ball"] = _dynamic(space, state["ball"], spec["ball"], _BALL, 1400)
        actors["a"] = _dynamic(space, state["a"], _striker_material(spec, "human"), _STRIKER, spec["striker"]["speed"])
        actors["b"] = _dynamic(space, state["b"], _striker_material(spec, "bot"), _STRIKER, spec["striker"]["speed"])
        _tune_hit(space)
    else:
        actors["ball"] = _dynamic(space, state["ball"], spec["ball"], _BALL, 1500)
        actors["cue"] = _dynamic(space, state["cue"], spec["cue"], _STRIKER, spec["cue"]["speed"])
        _tune_hit(space)
    return space, actors


def _add_rails(space: pymunk.Space, spec: dict) -> None:
    w, h = spec["field_w"], spec["field_h"]
    bounce = 0.92
    if spec["family"] == "strike":
        y1, y2 = goal_span(spec)
        segments = [
            ((0, 0), (w, 0)),
            ((0, h), (w, h)),
            ((0, 0), (0, y1)),
            ((0, y2), (0, h)),
            ((w, 0), (w, y1)),
            ((w, y2), (w, h)),
        ]
    else:
        gap = _pocket_gap(spec)
        mid = w * 0.5
        segments = [
            ((gap, 0), (mid - gap * 0.9, 0)),
            ((mid + gap * 0.9, 0), (w - gap, 0)),
            ((gap, h), (mid - gap * 0.9, h)),
            ((mid + gap * 0.9, h), (w - gap, h)),
            ((0, gap), (0, h - gap)),
            ((w, gap), (w, h - gap)),
        ]
    static = space.static_body
    for start, end in segments:
        shape = pymunk.Segment(static, start, end, 3)
        shape.elasticity = bounce
        shape.friction = 0.35
        space.add(shape)


def _add_bumpers(space: pymunk.Space, spec: dict) -> None:
    for bumper in spec["bumpers"]:
        body = pymunk.Body(body_type=pymunk.Body.STATIC)
        body.position = (bumper["x"], bumper["y"])
        shape = pymunk.Circle(body, bumper["r"])
        shape.elasticity = 0.98
        shape.friction = 0.02
        space.add(body, shape)


def _dynamic(space, blob, material, collision_type, speed_cap):
    radius = float(material["r"])
    mass = float(material["mass"])
    moment = pymunk.moment_for_circle(mass, 0, radius)
    body = pymunk.Body(mass, moment)
    body.position = (float(blob["x"]), float(blob["y"]))
    body.velocity = (float(blob.get("vx", 0.0)), float(blob.get("vy", 0.0)))
    shape = pymunk.Circle(body, radius)
    shape.elasticity = float(material.get("bounce", 0.9))
    shape.friction = 0.04
    shape.collision_type = collision_type
    body.speed_cap = speed_cap
    space.add(body, shape)
    return body


def _tune_hit(space: pymunk.Space) -> None:
    def pre_solve(arbiter, _space, _data):
        arbiter.elasticity = 0.9
        arbiter.friction = 0.015
        return True

    if hasattr(space, "on_collision"):
        space.on_collision(_BALL, _STRIKER, pre_solve=pre_solve)
    else:
        handler = space.add_collision_handler(_BALL, _STRIKER)
        handler.pre_solve = pre_solve


def _striker_material(spec: dict, which: str) -> dict:
    striker = spec["striker"]
    return {"r": striker["r"], "mass": striker["mass"], "bounce": 0.72, "color": striker[which]}


def _launch_speed(spec: dict, power: float) -> float:
    cap = spec["striker"]["speed"] if spec["family"] == "strike" else spec["cue"]["speed"]
    return (power / 100.0) * cap


def _clamp_striker(body, spec: dict) -> None:
    radius = spec["striker"]["r"]
    w, h = spec["field_w"], spec["field_h"]
    x = min(max(body.position.x, radius + 2), w - radius - 2)
    y = min(max(body.position.y, radius + 2), h - radius - 2)
    vx, vy = body.velocity
    if x != body.position.x:
        vx = 0.0
    if y != body.position.y:
        vy = 0.0
    body.position = (x, y)
    _cap_velocity(body, spec["striker"]["speed"], vx, vy)


def _strike_score(previous, ball, spec: dict) -> tuple[str | None, float | None]:
    x, y = ball.position.x, ball.position.y
    px, py = previous
    w = spec["field_w"]
    y1, y2 = goal_span(spec)
    crossed = None
    mouth_y = None
    if (px > 0 >= x) or (px >= 0 > x):
        mouth_y = _cross_y(px, py, x, y, 0.0)
        if y1 <= mouth_y <= y2:
            crossed = "B"
    elif (px < w <= x) or (px <= w < x):
        mouth_y = _cross_y(px, py, x, y, w)
        if y1 <= mouth_y <= y2:
            crossed = "A"
    if crossed:
        return crossed, mouth_y
    # A ball already resting in the mouth still counts.
    if x <= 0 and y1 <= y <= y2:
        return "B", y
    if x >= w and y1 <= y <= y2:
        return "A", y
    if x < 0 or x > w:
        ball.position = (min(max(x, spec["ball"]["r"]), w - spec["ball"]["r"]), min(max(y, spec["ball"]["r"]), spec["field_h"] - spec["ball"]["r"]))
        vx, vy = ball.velocity
        ball.velocity = (abs(vx) if x < 0 else -abs(vx), vy)
    return None, None


def _fell_in(body, spec: dict) -> bool:
    x, y = body.position.x, body.position.y
    w, h = spec["field_w"], spec["field_h"]
    if 0 <= x <= w and 0 <= y <= h:
        return False
    gap = _pocket_gap(spec)
    for px, py in pocket_anchors(spec):
        if math.hypot(x - px, y - py) <= gap:
            return True
    body.position = (
        min(max(x, spec["ball"]["r"]), w - spec["ball"]["r"]),
        min(max(y, spec["ball"]["r"]), h - spec["ball"]["r"]),
    )
    return False


def _pocket_gap(spec: dict) -> float:
    return max(spec["pocket_r"], spec["ball"]["r"] * 2.0 + 8.0)


def _park(body) -> None:
    body.position = (-800.0, -800.0)
    body.velocity = (0.0, 0.0)


def _settled(actors) -> bool:
    return all(_speed(body) < SLEEP for body in actors.values())


def _speed(body) -> float:
    return math.hypot(body.velocity.x, body.velocity.y)


def _cap_velocity(body, limit, vx=None, vy=None) -> None:
    if vx is None:
        vx, vy = body.velocity
    speed = math.hypot(vx, vy)
    if speed > limit:
        scale = limit / speed
        vx, vy = vx * scale, vy * scale
    body.velocity = (vx, vy)


def _cross_y(x1, y1, x2, y2, x_line) -> float:
    span = x2 - x1
    if abs(span) < 1e-9:
        return y2
    return y1 + (x_line - x1) / span * (y2 - y1)


def _frame(step: int, actors) -> dict:
    return {
        "t": round(step * DT, 4),
        "bodies": {key: [round(body.position.x, 2), round(body.position.y, 2)] for key, body in actors.items()},
    }


def _write_back(spec, state, actors, info, previous_cue) -> None:
    if spec["family"] == "strike":
        if info["scored_by"]:
            return
        for key in ("ball", "a", "b"):
            state[key] = _read_body(actors[key])
        return
    if not info["potted"]:
        state["ball"] = _read_body(actors["ball"])
    if info["scratch"]:
        return
    if "cue" in actors and actors["cue"].position.x > -100:
        state["cue"] = _read_body(actors["cue"])
    elif previous_cue is not None and not info["scratch"]:
        state["cue"] = {"x": round(previous_cue.x, 3), "y": round(previous_cue.y, 3), "vx": 0.0, "vy": 0.0}


def _read_body(body) -> dict:
    vx, vy = body.velocity
    if math.hypot(vx, vy) < SLEEP:
        vx, vy = 0.0, 0.0
    return {"x": round(body.position.x, 3), "y": round(body.position.y, 3), "vx": round(vx, 3), "vy": round(vy, 3)}


def _reset_rack(spec: dict, state: dict) -> None:
    w, h = spec["field_w"], spec["field_h"]
    offsets = ((0, 0), (36, -28), (-42, 24), (0, -48), (54, 16), (-24, 40))
    ox, oy = offsets[int(state.get("layout", 0)) % len(offsets)]
    state["cue"] = {"x": round(w * 0.27, 3), "y": round(h * 0.5, 3), "vx": 0.0, "vy": 0.0}
    state["ball"] = {"x": round(w * 0.67 + ox, 3), "y": round(h * 0.5 + oy, 3), "vx": 0.0, "vy": 0.0}


def _copy_state(state: dict) -> dict:
    import copy
    return copy.deepcopy(state)


def _piece_ids(spec: dict) -> tuple[str, ...]:
    return ("ball", "a", "b") if spec["family"] == "strike" else ("ball", "cue")


def _near(left: dict, right: dict) -> bool:
    if left.keys() != right.keys():
        return False
    for key, point in left.items():
        other = right[key]
        if abs(point[0] - other[0]) > 0.05 or abs(point[1] - other[1]) > 0.05:
            return False
    return True


def _frames_finite(frames: list[dict]) -> bool:
    for frame in frames:
        for point in frame["bodies"].values():
            if not all(math.isfinite(value) for value in point):
                return False
    return True


def _decimate(frames: list[dict], limit: int) -> list[dict]:
    stride = max(1, math.ceil(len(frames) / limit))
    kept = frames[::stride]
    if kept[-1] is not frames[-1]:
        kept.append(frames[-1])
    return kept
