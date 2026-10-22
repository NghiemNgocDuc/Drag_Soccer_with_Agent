"""Five-a-side formations shared by setup previews and the physics engine."""

DEFAULT_FORMATION_5 = "2-1-1"
FORMATIONS_5 = {
    "2-1-1": (2, 1, 1),
    "1-2-1": (1, 2, 1),
    "2-0-2": (2, 0, 2),
    "1-1-2": (1, 1, 2),
}
_DETAILS = {
    "2-1-1": ("Balanced", "Two defenders, a passing outlet and one striker."),
    "1-2-1": ("Diamond", "One defender, two midfield outlets and one striker."),
    "2-0-2": ("Box", "Two defenders cover a pair of forwards."),
    "1-1-2": ("Forward pair", "One defender and one support player feed two forwards."),
}


def formation_id(value):
    """Normalize legacy metadata; request handlers validate unknown IDs first."""
    return value if isinstance(value, str) and value in FORMATIONS_5 else DEFAULT_FORMATION_5


def five_player_positions(width, height, side="a", formation=None):
    """Keep the original 2-1-1 geometry and mirror the other team's x values."""
    rows = FORMATIONS_5[formation_id(formation)]
    center_y = height / 2
    if side == "a":
        keeper_x = width * .062
        line_x = (width * .162, width * .281, width / 2 - 95)
    else:
        keeper_x = width * .938
        line_x = (width * .838, width * .719, width / 2 + 95)
    positions = [(keeper_x, center_y)]
    y_range = min(height * .6, 180)
    for x, count in zip(line_x, rows):
        for index in range(count):
            y = center_y - y_range / 2 + (index + .5) / count * y_range
            positions.append((x, y))
    return positions


def formation_catalog(width=1400, height=875):
    """Return fresh JSON-ready previews; positions are normalized for team A."""
    catalog = []
    for key, rows in FORMATIONS_5.items():
        roles = ["goalkeeper"]
        for role, count in zip(("defender", "support", "attacker"), rows):
            roles.extend([role] * count)
        catalog.append({
            "id": key,
            "name": _DETAILS[key][0],
            "description": _DETAILS[key][1],
            "rows": list(rows),
            "positions": [
                {"x": x / width, "y": y / height, "number": index + 1, "role": roles[index]}
                for index, (x, y) in enumerate(five_player_positions(width, height, formation=key))
            ],
        })
    return catalog
