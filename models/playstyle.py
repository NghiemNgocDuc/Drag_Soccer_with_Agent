"""Validated per-team mentality, independent of formation and player jobs."""

PLAYSTYLE_LABELS = {
    1: "Deep defend",
    2: "Defend",
    3: "Balanced",
    4: "Attack",
    5: "All-out attack",
}


def playstyle_level(state, side):
    """Read the team's saved level, keeping old or malformed states balanced."""
    value = state.get("playstyle_a" if side else "playstyle_b", 3)
    return value if type(value) is int and 1 <= value <= 5 else 3
