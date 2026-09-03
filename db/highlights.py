"""Auto-detected match highlights (shareable replay clips, no video).

Highlights are pointers into an already-saved tournament match's
`replay_data`: a start/end *entry index* into the interleaved move list
plus a label. Detection runs on demand from the stored trajectory data
(first request caches the result in Redis for the replay's 24h TTL) and
needs no changes to match simulation.

Everything is a pointer + metadata — no video, no files, no encoding.
"""
import hashlib
import json
import math

from db.redis_client import r

# Tunable detection constants (calibrated against real AI sims)
# Ball speeds are derived from consecutive decimated frames:
#   speed ≈ dist * 60 / step_est,  step_est = round((len-1)/100)
# Real kicks measured 400–1060 px/s; ~50% of AI kicks are zero-speed no-ops.
# Highlights only when a shot was actually taken (research: PlayerTV/MatchVision detect shots via power + speed + direction; not passes/rolls)
SHOT_MIN_POWER      = 50   # kick power must be a shot (not a 20-30 tap/dribble) — base_replay uses 50, so 50 is shot
GOAL_LEAD_MOVES     = 1    # clip window: kicks before the scoring kick
GOAL_TAIL_MOVES     = 1    # clip window: kicks after the scoring kick
NEAR_MIN_SPEED      = 400  # px/s — near miss must be a genuine shot, not a slow roll
NEAR_LINE_MARGIN    = 100  # px — ball must get this close to the goal line
NEAR_MOUTH_DIST     = 100  # px — closest approach to the goal-mouth segment
FAST_PLAY_MIN_SPEED = 700  # px/s — absolute floor (percentiles are unreliable here)

HL_TTL = 86400  # same as tournament match replays (24h)

TYPE_LABEL = {
    "goal": "Goal",
    "near": "Near miss",
    "fast": "Fast play",
}

# Priority for "the best highlight from a match" (confirmed design):
# a goal beats a near miss, which beats a fast play. Lower = better.
TYPE_PRIORITY = {"goal": 0, "near": 1, "fast": 2}


def best_highlight(hls: list[dict]) -> dict | None:
    """The single "best" highlight from a detected list, or None.

    Selection-only (no detection): first goal if any, else first
    near-miss, else first fast play; ties keep detection/kick order.
    """
    if not hls:
        return None
    return min(hls, key=lambda h: (TYPE_PRIORITY.get(h.get("type"), 3),
                                   h.get("kick", 0)))


def _real_moves(replay_data):
    """The trajectory-bearing entries (the route's moves), with entry index."""
    return [(i, m) for i, m in enumerate(replay_data or [])
            if m.get("trajectory") and len(m["trajectory"]) >= 2]


def _step_est(traj_len):
    return max(1, round((traj_len - 1) / 100))


def max_speed(trajectory):
    """Peak horizontal ball speed (px/s) from decimated frames."""
    if not trajectory or len(trajectory) < 2:
        return 0.0
    st = _step_est(len(trajectory))
    mx = 0.0
    for j in range(1, len(trajectory)):
        p0, p1 = trajectory[j - 1], trajectory[j]
        d = math.hypot(p1["x"] - p0["x"], p1["y"] - p0["y"])
        mx = max(mx, d * 60.0 / st)
    return mx


def _min_mouth_dist(trajectory, target_x, line_margin, mouth_y1, mouth_y2):
    """(reached_line_margin, min distance from ball to goal-mouth segment).

    target_x is the goal line (1380 for team A attacking right, 20 for B).
    Only frames on the attacking side of the line count.
    """
    sign = 1 if target_x == 20 else -1  # mover a → x grows; mover b → x shrinks
    reached = False
    best = float("inf")
    for p in trajectory:
        if (p["x"] - target_x) * sign > line_margin:
            continue
        reached = True
        yc = min(max(p["y"], mouth_y1), mouth_y2)
        best = min(best, math.hypot(p["x"] - target_x, p["y"] - yc))
    return reached, best


def detect_highlights(replay_data, tid="", match_id=""):
    """Return the highlight list for a match's replay_data (entry indices). Highlights only when a shot was taken."""
    moves = _real_moves(replay_data)
    n = len(moves)
    hls = []
    for ki, (entry_idx, m) in enumerate(moves):
        if m.get("scored"):
            # goals always highlight (shot filter still applies via speed fallback, but power gate not needed — a goal is by definition a shot)
            pass
        else:
            # research-backed shot filter: only near/fast when a shot was taken (Stanford 2024 + PlayerTV). Power + speed gate.
            power = float(m.get("power", 100) or 0)
            is_shot = power >= SHOT_MIN_POWER
            if not is_shot:
                continue
        if m.get("scored"):
            start = moves[max(0, ki - GOAL_LEAD_MOVES)][0]
            end = moves[min(n - 1, ki + GOAL_TAIL_MOVES)][0]
            hls.append({
                "type": "goal",
                "kick": ki + 1,
                "start": start,
                "end": end,
                "label": f"Goal — kick {ki + 1}",
            })
            continue
        sp = max_speed(m["trajectory"])
        if sp < NEAR_MIN_SPEED:
            continue
        target_x = 20 if m["mover"] == "b" else 1380
        reached, best = _min_mouth_dist(m["trajectory"], target_x,
                                        NEAR_LINE_MARGIN, 356, 519)
        if reached and best <= NEAR_MOUTH_DIST:
            hls.append({
                "type": "near",
                "kick": ki + 1,
                "start": entry_idx,
                "end": entry_idx,
                "label": f"Near miss — kick {ki + 1}",
            })
            continue
        if sp >= FAST_PLAY_MIN_SPEED:
            hls.append({
                "type": "fast",
                "kick": ki + 1,
                "start": entry_idx,
                "end": entry_idx,
                "label": f"Fast play — kick {ki + 1}",
            })
    return hls


def highlight_id(tid, match_id, hl):
    raw = f"{tid}:{match_id}:{hl['type']}:{hl['start']}:{hl['end']}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def get_highlights(tid, match_id):
    """Detect (once) and cache a match's highlights + share registry."""
    key = f"highlights:{tid}:{match_id}"
    raw = r.get(key)
    if raw:
        return json.loads(raw)
    from db.tournaments import get_match
    m = get_match(tid, match_id)
    if not m or m.get("status") != "completed" or not m.get("replay_data"):
        return None
    hls = detect_highlights(m["replay_data"], tid, match_id)
    for hl in hls:
        hl["id"] = highlight_id(tid, match_id, hl)
    r.setex(key, HL_TTL, json.dumps(hls))
    for hl in hls:
        r.setex(f"highlight:{hl['id']}", HL_TTL, json.dumps({
            "tid": tid, "match_id": match_id,
            "type": hl["type"], "kick": hl["kick"],
            "start": hl["start"], "end": hl["end"], "label": hl["label"],
        }))
    return hls


def resolve_highlight(hid):
    """Look up a shareable highlight id → {tid, match_id, type, start, end, ...}."""
    raw = r.get(f"highlight:{hid}")
    return json.loads(raw) if raw else None
