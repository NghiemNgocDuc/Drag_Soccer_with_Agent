"""balance_test.py — Fast stat-system balance validation.

Positions kicker right next to the ball (realistic game scenario).
Two tiers: physics unit tests (deterministic) + greedy AI scrimmages.
"""
from __future__ import annotations
import math, time, sys

from models.soccer_logic import (
    new_soccer_state, simulate_kick, apply_kick, inject_player_stats,
    FIELD_W, FIELD_H, BALL_R, PLAYER_R,
)

BUILDS: dict[str, list[dict]] = {}

def _b(sz, pw, wt, ag):
    return [{"size": sz, "power": pw, "weight": wt, "agility": ag}] * 3

for nm, sz, pw, wt, ag in [
    ("Balanced",    50, 50, 50, 50),
    ("Powerhouse",  20, 80, 50, 50),
    ("Tank",        80, 20, 80, 20),
    ("SpeedDemon",  20, 50, 50, 80),
    ("GlassCannon", 20, 80, 20, 80),
    ("BigBruiser",  80, 20, 50, 50),
    ("StoneWall",   50, 20, 80, 50),
]:
    assert sz + pw + wt + ag == 200
    BUILDS[nm] = _b(sz, pw, wt, ag)


def _make_state(home_build, away_build=None):
    """Create a state with specific builds and clear non-target players to non-interfering positions."""
    if away_build is None:
        away_build = BUILDS["Balanced"]
    state = new_soccer_state(player_count=3, half_length=9999, win_goal_limit=5)
    inject_player_stats(state, home_build, away_build)
    return state


# 
# TIER 1: PHYSICS UNIT TESTS
# 

def test_kick_distance(build, power=100.0):
    """Kicker is right next to the ball; measure ball travel."""
    state = _make_state(build)
    # Put kicker (A0) right next to ball, other players away
    state["players_a"][0]["x"] = FIELD_W / 2 - BALL_R - PLAYER_R - 5
    state["players_a"][0]["y"] = FIELD_H / 2
    state["players_a"][1]["x"] = 0.0
    state["players_a"][1]["y"] = 0.0
    state["players_a"][2]["x"] = 0.0
    state["players_a"][2]["y"] = 0.0
    state["players_b"][0]["x"] = FIELD_W
    state["players_b"][0]["y"] = 0.0
    state["players_b"][1]["x"] = FIELD_W
    state["players_b"][1]["y"] = 0.0
    state["players_b"][2]["x"] = FIELD_W
    state["players_b"][2]["y"] = 0.0
    traj, _ = simulate_kick(state, 0, 0.0, power, True)
    if len(traj) <= 1:
        return 0.0
    dx = traj[-1]["x"] - traj[0]["x"]
    return dx


def test_collision_push(victim_build, kicker_build=None):
    """Kicker hits victim from behind; measure victim displacement."""
    if kicker_build is None:
        kicker_build = BUILDS["Balanced"]
    state = _make_state(kicker_build, victim_build)
    bx, by = FIELD_W / 2, FIELD_H / 2
    state["players_a"][0]["x"] = bx - PLAYER_R - 10  # kicker behind ball
    state["players_a"][0]["y"] = by
    state["players_a"][1]["x"] = 0.0
    state["players_a"][1]["y"] = 0.0
    state["players_a"][2]["x"] = 0.0
    state["players_a"][2]["y"] = 0.0
    state["players_b"][0]["x"] = bx + BALL_R + 5       # victim between kicker and goal
    state["players_b"][0]["y"] = by
    state["players_b"][1]["x"] = FIELD_W
    state["players_b"][1]["y"] = 0.0
    state["players_b"][2]["x"] = FIELD_W
    state["players_b"][2]["y"] = 0.0
    state["ball"]["x"] = bx + BALL_R + 5   # put ball at victim position
    state["ball"]["y"] = by
    vx0, vy0 = state["players_b"][0]["x"], state["players_b"][0]["y"]
    apply_kick(state, 0, 0.0, 100.0, True)
    return math.hypot(state["players_b"][0]["x"] - vx0, state["players_b"][0]["y"] - vy0)


def test_recoil(build):
    """How far kicker moves backward when kicking."""
    state = _make_state(build)
    state["players_a"][0]["x"] = FIELD_W / 2 - BALL_R - PLAYER_R - 5
    state["players_a"][0]["y"] = FIELD_H / 2
    state["players_a"][1]["x"] = 0.0
    state["players_a"][1]["y"] = 0.0
    state["players_a"][2]["x"] = 0.0
    state["players_a"][2]["y"] = 0.0
    state["players_b"][0]["x"] = FIELD_W
    state["players_b"][0]["y"] = 0.0
    state["players_b"][1]["x"] = FIELD_W
    state["players_b"][1]["y"] = 0.0
    state["players_b"][2]["x"] = FIELD_W
    state["players_b"][2]["y"] = 0.0
    ax0, ay0 = state["players_a"][0]["x"], state["players_a"][0]["y"]
    apply_kick(state, 0, 0.0, 100.0, True)
    return math.hypot(state["players_a"][0]["x"] - ax0, state["players_a"][0]["y"] - ay0)


def test_player_slide(victim_build, kicker_build=None):
    """How far a player slides after being hit (agility/friction)."""
    if kicker_build is None:
        kicker_build = BUILDS["Balanced"]
    state = _make_state(kicker_build, victim_build)
    bx, by = FIELD_W / 2, FIELD_H / 2
    state["players_a"][0]["x"] = bx - PLAYER_R - 10
    state["players_a"][0]["y"] = by
    state["players_a"][1]["x"] = 0.0
    state["players_a"][1]["y"] = 0.0
    state["players_a"][2]["x"] = 0.0
    state["players_a"][2]["y"] = 0.0
    state["players_b"][0]["x"] = bx                   # right next to kicker
    state["players_b"][0]["y"] = by
    state["players_b"][1]["x"] = FIELD_W
    state["players_b"][1]["y"] = 0.0
    state["players_b"][2]["x"] = FIELD_W
    state["players_b"][2]["y"] = 0.0
    state["ball"]["x"] = bx
    state["ball"]["y"] = by
    vx0, vy0 = state["players_b"][0]["x"], state["players_b"][0]["y"]
    apply_kick(state, 0, 0.0, 100.0, True)
    return math.hypot(state["players_b"][0]["x"] - vx0, state["players_b"][0]["y"] - vy0)


def run_physics():
    print("=" * 60)
    print("PHYSICS UNIT TESTS (kicker next to ball, players cleared)")
    print("=" * 60)

    print("\n[Kick distance] (power=100, angle=0)")
    d = {}
    for nm in BUILDS:
        d[nm] = test_kick_distance(BUILDS[nm])
        print(f"  {nm:15s}  {d[nm]:5.0f} px")
    print(f"  -> Powerhouse/Balanced ratio: {d['Powerhouse']/max(d['Balanced'],1):.2f}  (expect >1)")
    print(f"  -> Tank/Balanced ratio: {d['Tank']/max(d['Balanced'],1):.2f}  (expect <1)")

    print("\n[Collision push] (Balanced kicker vs each build)")
    c = {}
    for nm in BUILDS:
        c[nm] = test_collision_push(BUILDS[nm])
        print(f"  {nm:15s} pushed {c[nm]:5.0f} px")
    print(f"  -> GlassCannon vs Tank: {c['GlassCannon']:.0f} vs {c['Tank']:.0f}  (expect GC >> Tank)")

    print("\n[Recoil] (kicker displacement)")
    r = {}
    for nm in BUILDS:
        r[nm] = test_recoil(BUILDS[nm])
        print(f"  {nm:15s}  {r[nm]:5.0f} px")
    print(f"  -> Powerhouse vs Tank: {r['Powerhouse']:.0f} vs {r['Tank']:.0f}  (expect PH > Tank)")

    print("\n[Player slide] (victim agility/friction)")
    s = {}
    for nm in BUILDS:
        s[nm] = test_player_slide(BUILDS[nm])
        print(f"  {nm:15s}  {s[nm]:5.0f} px")
    print(f"  -> SpeedDemon vs Tank: {s['SpeedDemon']:.0f} vs {s['Tank']:.0f}  (expect SD << Tank)")

    print()
    return d, c, r, s


# 
# TIER 2: AI SENSITIVITY
# 

def run_ai_sensitivity():
    print("=" * 60)
    print("AI SENSITIVITY (stat change -> AI decision change?)")
    print("=" * 60)

    import importlib
    from models.soccer_logic import _get_player_radius

    targets = {
        "greedy": "models.greedy_model",
        "q_learning": "models.q_learning",
        "bayesian": "models.bayes",
        "minimax": "models.minimax",
        "value_iteration": "models.value_iteration",
        "policy_iteration": "models.policy_iteration",
        "monte_carlo": "models.monte_carlo",
    }

    for nm, path in targets.items():
        sys.stdout.write(f"  Loading {nm}...")
        sys.stdout.flush()
        mod = importlib.import_module(path)
        if hasattr(mod, 'init_policy'):
            mod.init_policy()

        ref_s = _make_state(BUILDS["Balanced"], BUILDS["Balanced"])
        ref = mod.get_ai_move(ref_s, True)
        sys.stdout.write(f" ref=({ref[0]},{ref[1]:.0f},{ref[2]:.0f})")
        sys.stdout.flush()

        diffs = 0
        dec_responses = {}
        for bn in BUILDS:
            s = _make_state(BUILDS[bn], BUILDS["Balanced"])
            m = mod.get_ai_move(s, True)
            dec_responses[bn] = m
            if m != ref:
                diffs += 1

        pct = 100 * diffs // len(BUILDS)
        print(f"  {diffs}/{len(BUILDS)} differ ({pct}%)")
        for bn in BUILDS:
            if bn == "Balanced":
                continue
            mr = dec_responses[bn]
            rr = dec_responses["Balanced"]
            if mr != rr:
                r = _get_player_radius(BUILDS[bn][0])
                print(f"    {bn:15s} -> ({mr[0]},{mr[1]:.0f},{mr[2]:.0f}) radius={r:.0f}")
    print()


# 
# TIER 3: QUICK SCRIMMAGES
# 

def _realistic_game_state(build_a=None, build_b=None):
    """Create state with players in home positions (realistic)."""
    if build_a is None:
        build_a = BUILDS["Balanced"]
    if build_b is None:
        build_b = BUILDS["Balanced"]
    s = new_soccer_state(mode="aivai", player_count=3, win_goal_limit=3, half_length=9999)
    inject_player_stats(s, build_a, build_b)
    return s


def _fast_game(ba, bb):
    s = _realistic_game_state(ba, bb)
    import importlib
    mod = importlib.import_module("models.greedy_model")
    for _ in range(200):
        if s.get("game_over"):
            return s.get("winner", "draw")
        pidx, ang, pwr = mod.get_ai_move(s, s["is_player_a"])
        apply_kick(s, pidx, ang, pwr, s["is_player_a"])
    sa, sb = s["score_a"], s["score_b"]
    return "A" if sa > sb else "B" if sb > sa else "draw"


def run_scrimmages():
    print("=" * 60)
    print("QUICK SCRIMMAGES (greedy, first-to-3, 5 games each)")
    print("=" * 60)

    t0 = time.time()
    N = 5

    print("\n[Self-play baseline]")
    w = {"A": 0, "B": 0, "draw": 0}
    for _ in range(N):
        r = _fast_game(BUILDS["Balanced"], BUILDS["Balanced"])
        w[r] = w.get(r, 0) + 1
    print(f"  Balanced vs Balanced: A={w['A']} B={w['B']} draw={w['draw']}")

    print("\n[Build vs Balanced]")
    for bn in BUILDS:
        if bn == "Balanced":
            continue
        w = {"A": 0, "B": 0, "draw": 0}
        for _ in range(N):
            r = _fast_game(BUILDS[bn], BUILDS["Balanced"])
            w[r] = w.get(r, 0) + 1
        awp = 100 * w["A"] // (w["A"] + w["B"] + 1)
        print(f"  {bn:15s}  A={w['A']} B={w['B']} draw={w['draw']}  (build win%={awp}%)")

    print(f"\n  Time: {time.time()-t0:.1f}s")
    print()


# 

def main():
    t0 = time.time()
    sys.stdout = sys.stdout  # ensure flush
    run_physics()
    run_ai_sensitivity()
    run_scrimmages()
    print(f"Total: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
