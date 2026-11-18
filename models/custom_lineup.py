"""Validated pre-kickoff positions and stable jobs for five-a-side soccer."""
import math

ROLE_BOUNDS = {"defender": (.13, .22), "support": (.25, .34), "attacker": (.37, .45)}


def normalize_lineup(raw):
    if raw is None:
        return None
    if not isinstance(raw, list) or len(raw) != 5:
        raise ValueError("Choose one goalkeeper and four outfield players")
    result, counts = [], dict.fromkeys(ROLE_BOUNDS, 0)
    for index, player in enumerate(raw):
        if not isinstance(player, dict):
            raise ValueError("Each player needs a role and position")
        role = player.get("role")
        if not isinstance(role, str) or (role != "goalkeeper" if index == 0 else role not in ROLE_BOUNDS):
            raise ValueError("Keep #1 in goal; assign every outfield player a role")
        x, y = player.get("x"), player.get("y")
        if any(type(v) not in (float, int) or not math.isfinite(v) for v in (x, y)):
            raise ValueError("Player positions must be finite numbers")
        if index == 0:
            if abs(x - .062) > 1e-8 or abs(y - .5) > 1e-8:
                raise ValueError("The goalkeeper starts in the goal area")
        else:
            counts[role] += 1
            lo, hi = ROLE_BOUNDS[role]
            if not lo <= x <= hi or not .15 <= y <= .85:
                raise ValueError("Place each player inside their marked role area")
        result.append({"role": role, "x": float(x), "y": float(y)})
    if max(counts.values()) > 2:
        raise ValueError("Defender, midfielder and attacker groups each hold at most two players")
    for i, a in enumerate(result):
        for b in result[i + 1:]:
            if math.hypot((a['x']-b['x'])*1400, (a['y']-b['y'])*875) < 86:
                raise ValueError("Leave space between players; their starting positions overlap")
    return result


def lineup_for(state, side):
    if state.get('sport', 'soccer') != 'soccer' or len(state.get('players_' + side, [])) != 5:
        return None
    try:
        return normalize_lineup(state.get('custom_lineup_' + side))
    except (ValueError, OverflowError):
        return None


def home_positions(state, side):
    from models.soccer_logic import FIELD_W, FIELD_H, _home_positions
    lineup = lineup_for(state, side)
    if lineup:
        return [((p['x'] if side == 'a' else 1-p['x'])*FIELD_W, p['y']*FIELD_H) for p in lineup]
    return _home_positions(state.get('player_count', 5), side, state.get('formation_' + side))


def cache_key(state):
    return tuple(tuple((p['role'], p['x'], p['y']) for p in (lineup_for(state, side) or [])) for side in ('a', 'b'))
