"""Comprehensive penalty shootout tests."""
import math
import time
from models.soccer_logic import (
    new_soccer_state, apply_penalty_kick, apply_kick, simulate_kick,
    _setup_penalty_positions, _build_penalty_space, _sim_penalty,
    FIELD_W, FIELD_H, GOAL_Y1, GOAL_Y2, BALL_R, _MARGIN,
    _PENALTY_SPOT_X_A, _PENALTY_SPOT_X_B, _PENALTY_SPOT_Y,
    _PENALTY_KICKER_BEHIND, _PENALTY_KEEPER_X_A, _PENALTY_KEEPER_X_B,
)

passed = 0
failed = 0

def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        msg = f"  FAIL  {name}"
        if detail:
            msg += f"  ({detail})"
        print(msg)

def make_penalty_state(score_a=0, score_b=0, kick_num=0, is_player_a=True):
    st = new_soccer_state()
    st["penalty_shootout"] = True
    st["period"] = "penalties"
    st["penalty_kick_num"] = kick_num
    st["penalty_a_score"] = score_a
    st["penalty_b_score"] = score_b
    st["penalty_kicks"] = []
    st["penalty_goalkeeper_move"] = None
    st["is_player_a"] = is_player_a
    _setup_penalty_positions(st, is_player_a)
    return st


# ── 1. _setup_penalty_positions ──────────────────────────────────────────────

def test_setup_positions():
    st = make_penalty_state(is_player_a=True)
    check("Team A ball at spot",
          st["ball"]["x"] == _PENALTY_SPOT_X_A and st["ball"]["y"] == _PENALTY_SPOT_Y)
    check("Team A kicker behind ball",
          st["players_a"][0]["x"] == _PENALTY_SPOT_X_A - _PENALTY_KICKER_BEHIND)
    check("Team A keeper at right goal", st["players_b"][0]["x"] == _PENALTY_KEEPER_X_A)

    st2 = make_penalty_state(is_player_a=False)
    check("Team B kicker behind ball (other side)",
          st2["players_b"][0]["x"] == _PENALTY_SPOT_X_B + _PENALTY_KICKER_BEHIND)
    check("Team B keeper at left goal", st2["players_a"][0]["x"] == _PENALTY_KEEPER_X_B)

    check("Goalkeeper move reset", st["penalty_goalkeeper_move"] is None)

# ── 2. _build_penalty_space ──────────────────────────────────────────────────

def test_penalty_space_has_bodies():
    st = make_penalty_state(is_player_a=True)
    space, kicker, ball, keeper = _build_penalty_space(st, True)
    check("3 bodies in space", len(space.bodies) == 3)
    check("Kicker at correct x",
          abs(kicker.position.x - (_PENALTY_SPOT_X_A - _PENALTY_KICKER_BEHIND)) < 0.1)
    check("Ball at penalty spot", abs(ball.position.x - _PENALTY_SPOT_X_A) < 0.1)
    check("Keeper at right goal", abs(keeper.position.x - _PENALTY_KEEPER_X_A) < 0.1)

    st2 = make_penalty_state(is_player_a=False)
    space2, kicker2, ball2, keeper2 = _build_penalty_space(st2, False)
    check("Team B keeper at left goal", abs(keeper2.position.x - _PENALTY_KEEPER_X_B) < 0.1)
    check("Team B ball at left spot", abs(ball2.position.x - _PENALTY_SPOT_X_B) < 0.1)

# ── 3. _sim_penalty — goal detection via physcis ─────────────────────────────

def test_angled_shot_scores():
    """Angled shot past the keeper should score."""
    st = make_penalty_state(is_player_a=True)
    space, kicker, ball, keeper = _build_penalty_space(st, True)
    kicker.velocity = (math.cos(math.radians(-25)) * 100 * 10,
                       math.sin(math.radians(-25)) * 100 * 10)
    traj, scored = _sim_penalty(space, kicker, ball, keeper, "center")
    check("Angled -25deg scores", scored == True,
          f"end=({traj[-1]['x']:.0f},{traj[-1]['y']:.0f})")

def test_straight_shot_blocked():
    """Straight shot at keeper center should be saved."""
    st = make_penalty_state(is_player_a=True)
    space, kicker, ball, keeper = _build_penalty_space(st, True)
    kicker.velocity = (math.cos(0) * 100 * 10, math.sin(0) * 100 * 10)
    traj, scored = _sim_penalty(space, kicker, ball, keeper, "center")
    check("Straight shot saved", scored == False)

def test_team_b_scores():
    """Team B angled shot toward left goal should score."""
    st = make_penalty_state(is_player_a=False)
    space, kicker, ball, keeper = _build_penalty_space(st, False)
    kicker.velocity = (math.cos(math.radians(205)) * 100 * 10,
                       math.sin(math.radians(205)) * 100 * 10)
    traj, scored = _sim_penalty(space, kicker, ball, keeper, "center")
    check("Team B angled shot scores", scored == True,
          f"end=({traj[-1]['x']:.0f},{traj[-1]['y']:.0f})")

def test_keeper_dive_matters():
    """Gentle shot saved by center-diving keeper, same shot scored if keeper dives away."""
    st = make_penalty_state(is_player_a=True)
    space, kicker, ball, keeper = _build_penalty_space(st, True)
    # Gentle upward shot, keeper stays center → saved
    kicker.velocity = (math.cos(math.radians(-5)) * 100 * 10,
                       math.sin(math.radians(-5)) * 100 * 10)
    traj, scored = _sim_penalty(space, kicker, ball, keeper, "center")
    check("Gentle shot saved by center dive", scored == False,
          f"end=({traj[-1]['x']:.0f},{traj[-1]['y']:.0f})")

def test_keeper_dives_wrong_way():
    """Same gentle shot scores when keeper dives away from ball."""
    st = make_penalty_state(is_player_a=True)
    space, kicker, ball, keeper = _build_penalty_space(st, True)
    kicker.velocity = (math.cos(math.radians(-5)) * 100 * 10,
                       math.sin(math.radians(-5)) * 100 * 10)
    traj, scored = _sim_penalty(space, kicker, ball, keeper, "left")
    check("Keeper dives wrong way, goal scored", scored == True,
          f"end=({traj[-1]['x']:.0f},{traj[-1]['y']:.0f})")

# ── 4. apply_penalty_kick ────────────────────────────────────────────────────

def test_apply_penalty_scored():
    st = make_penalty_state(is_player_a=True)
    traj, scored, desc = apply_penalty_kick(st, 0, -25, 100, True)
    check("apply scores with angled shot", scored == True)
    check("Penalty score updated", st["penalty_a_score"] == 1)
    check("Kick count incremented to 1", st["penalty_kick_num"] == 1)
    check("Not game over after 1 kick", not st["game_over"])
    check("Description has GOAL", "GOAL" in desc)
    check("Trajectory has kicker data", "kicker" in traj[0])
    check("Trajectory has keeper data", "keeper" in traj[0])

def test_apply_penalty_saved():
    st = make_penalty_state(is_player_a=True, kick_num=3)
    st["penalty_goalkeeper_move"] = "center"
    traj, scored, desc = apply_penalty_kick(st, 0, 0, 60, True)
    check("Straight weak shot saved", scored == False)
    check("Description has SAVED", "SAVED" in desc)
    check("No score added", st["penalty_a_score"] == 0)

def test_penalty_trajectory_decimated():
    st = make_penalty_state(is_player_a=True)
    traj, scored, desc = apply_penalty_kick(st, 0, -25, 100, True)
    check("Trajectory decimated (<=80 pts)", len(traj) <= 80)

# ── 5. Alternating kickers ──────────────────────────────────────────────────

def test_alternating_kickers():
    st = make_penalty_state(is_player_a=True)
    apply_penalty_kick(st, 0, -25, 100, True)
    check("After A kicks, next is B", st["is_player_a"] == False)
    apply_penalty_kick(st, 0, 205, 100, False)
    check("After B kicks, next is A", st["is_player_a"] == True)

# ── 6. Shootout end conditions ──────────────────────────────────────────────

def test_shootout_ends_when_winner_after_5_each():
    """After 5 rounds each with different scores, game should end."""
    st = make_penalty_state(is_player_a=True)
    for _ in range(5):
        apply_penalty_kick(st, 0, -25, 100, True)   # A scores
        apply_penalty_kick(st, 0, 0, 60, False)      # B misses (straight at keeper)
    check("A wins 5-0 after 5 rounds each",
          st["game_over"] and st["winner"] == "A",
          f"game_over={st['game_over']}, winner={st['winner']}")

def test_shootout_tied_after_5_each():
    """5-5 after 10 kicks should NOT end game (goes to sudden death)."""
    st = make_penalty_state(is_player_a=True)
    for _ in range(5):
        apply_penalty_kick(st, 0, -25, 100, True)   # A scores
        apply_penalty_kick(st, 0, 205, 100, False)   # B scores
    check("5-5 after 10 kicks, game continues",
          not st["game_over"],
          f"score={st['penalty_a_score']}-{st['penalty_b_score']}, over={st['game_over']}")
    check("Kick count is 10", st["penalty_kick_num"] == 10)

def test_sudden_death_both_must_kick():
    """SD: A scoring alone should NOT end game — B must respond."""
    st = make_penalty_state(is_player_a=True)
    for _ in range(5):
        apply_penalty_kick(st, 0, -25, 100, True)
        apply_penalty_kick(st, 0, 205, 100, False)

    check("Entering sudden death at 5-5", st["game_over"] == False)

    # SD kick 1: Team A scores
    apply_penalty_kick(st, 0, -25, 100, True)
    check("A scores in SD, B still to kick — game continues",
          not st["game_over"],
          f"game_over={st['game_over']}, score={st['penalty_a_score']}-{st['penalty_b_score']}")
    check("Next is Team B", st["is_player_a"] == False)

    # SD kick 2: Team B misses
    apply_penalty_kick(st, 0, 0, 60, False)
    check("B misses, A wins now",
          st["game_over"] and st["winner"] == "A",
          f"over={st['game_over']}, winner={st['winner']}")

def test_sudden_death_multi_round():
    """Multiple sudden death rounds until one team leads."""
    st = make_penalty_state(is_player_a=True)
    for _ in range(5):
        apply_penalty_kick(st, 0, -25, 100, True)
        apply_penalty_kick(st, 0, 205, 100, False)
    # 5-5, sudden death

    # Round 1: both score
    apply_penalty_kick(st, 0, -25, 100, True)
    apply_penalty_kick(st, 0, 205, 100, False)
    check("Round 1 SD both score, game continues",
          not st["game_over"],
          f"score={st['penalty_a_score']}-{st['penalty_b_score']}")

    # Round 2: A scores, B misses
    apply_penalty_kick(st, 0, -25, 100, True)
    apply_penalty_kick(st, 0, 0, 60, False)
    check("Round 2 SD: B misses, A wins",
          st["game_over"] and st["winner"] == "A",
          f"over={st['game_over']}, winner={st['winner']}")

def test_shootout_all_5_rounds_played():
    """Currently always plays 5 rounds each (early win not implemented)."""
    st = make_penalty_state(is_player_a=True)
    for _ in range(3):
        apply_penalty_kick(st, 0, -25, 100, True)
        apply_penalty_kick(st, 0, 0, 60, False)
    check("A leads 3-0 after 3 each, game still continues (plays all 5 rounds)",
          not st["game_over"],
          f"game_over={st['game_over']}")
    # Play last 2 rounds each
    for _ in range(2):
        apply_penalty_kick(st, 0, -25, 100, True)
        apply_penalty_kick(st, 0, 0, 60, False)
    check("After all 5 rounds, A wins 5-0",
          st["game_over"] and st["winner"] == "A",
          f"over={st['game_over']}, winner={st['winner']}")

# ── 7. Transition from regular time ──────────────────────────────────────────

def _safe_kick_no_score(st, is_player_a):
    """Kick that will not score (weak, wrong angle)."""
    return apply_kick(st, 0, 90, 10, is_player_a)

# ── 10. Halftime transition ─────────────────────────────────────────────────

def test_halftime_switch():
    """At 45 min (135s), teams switch sides and kicker flips."""
    st = new_soccer_state()
    st["start_time"] = time.time() - 136
    check("Starts in regular_first", st.get("period") == "regular_first")
    check("Starts with is_player_a=True", st["is_player_a"] == True)
    _safe_kick_no_score(st, True)
    check("After halftime, period is regular_second", st.get("period") == "regular_second")
    check("Kicker flipped after halftime (B kicks off 2nd half)", st["is_player_a"] == False)
    check("Ball reset to center", st["ball"]["x"] == 400.0 and st["ball"]["y"] == 250.0)

def test_no_halftime_before_45():
    """Before 45 min, no halftime switch."""
    st = new_soccer_state()
    st["start_time"] = time.time() - 130
    _safe_kick_no_score(st, True)
    check("Still regular_first before 45 min", st.get("period") == "regular_first")

def test_full_time_after_second_half():
    """Regular time ends at 90 min after both halves."""
    st = new_soccer_state()
    st["start_time"] = time.time() - 271
    st["score_a"] = 2
    st["score_b"] = 2
    _safe_kick_no_score(st, True)
    check("Period transitions to et_first after full time",
          st.get("period") == "et_first",
          f"period={st.get('period')}")


def test_et_transition_to_penalties():
    """After extra time, tied game enters shootout."""
    st = new_soccer_state()
    st["start_time"] = time.time() - 361
    st["score_a"] = 3
    st["score_b"] = 3
    _safe_kick_no_score(st, True)
    check("Transitions to penalty shootout", st.get("penalty_shootout") == True)
    check("Period is penalties", st.get("period") == "penalties")
    check("Ball at Team A penalty spot", st["ball"]["x"] == _PENALTY_SPOT_X_A)
    check("is_player_a reset to True", st["is_player_a"] == True)

def test_et_no_penalties_if_winner():
    """Extra time with winner should not go to shootout."""
    st = new_soccer_state()
    st["start_time"] = time.time() - 361
    st["score_a"] = 4
    st["score_b"] = 3
    _safe_kick_no_score(st, True)
    check("Game over with winner A",
          st["game_over"] and st["winner"] == "A",
          f"over={st['game_over']}, winner={st['winner']}")
    check("No penalty shootout", not st.get("penalty_shootout"))

def test_et_second_half():
    """After first ET period, switches to second ET period."""
    st = new_soccer_state()
    st["start_time"] = time.time() - 316
    st["period"] = "et_first"
    st["score_a"] = 2
    st["score_b"] = 2
    _safe_kick_no_score(st, True)
    check("Transitions to et_second", st.get("period") == "et_second")

# ── 8. Regular goal detection still works ────────────────────────────────────

def test_regular_kick_returns_valid():
    """Regular apply_kick returns valid structure (scoring depends on defenders)."""
    st = new_soccer_state()
    traj, scored, desc, ep, push = apply_kick(st, 1, 0, 80, True)
    check("Regular kick returns trajectory", len(traj) > 0)
    check("Regular kick returns scored type (None or str)", scored is None or isinstance(scored, str))
    check("Regular kick returns description", isinstance(desc, str) and len(desc) > 0)

def test_regular_goal_via_simulate():
    """simulate_kick can detect a goal."""
    st = new_soccer_state()
    traj, scored = simulate_kick(st, 1, 0, 100, True)
    check("simulate_kick returns trajectory", len(traj) > 0)
    check("simulate_kick returns scored type", scored is None or isinstance(scored, str))

# ── 9. Penalty keeper state management ──────────────────────────────────────

def test_goalkeeper_move_reset():
    """Goalkeeper move should reset after each kick."""
    st = make_penalty_state(is_player_a=True, kick_num=0)
    st["penalty_goalkeeper_move"] = "left"
    apply_penalty_kick(st, 0, -25, 100, True)
    check("Goalkeeper move reset for next kick",
          st["penalty_goalkeeper_move"] is None)

def test_penalty_kick_history():
    st = make_penalty_state(is_player_a=True)
    apply_penalty_kick(st, 0, -25, 100, True)
    check("Penalty kick recorded", len(st["penalty_kicks"]) == 1)
    entry = st["penalty_kicks"][0]
    check("Kick entry has team", "team" in entry)
    check("Kick entry has keeper move", "keeper_move" in entry)
    check("Kick entry has goal status", "goal" in entry)

# ── Run all ──────────────────────────────────────────────────────────────────

def mark_failed():
    global failed
    failed += 1

if __name__ == "__main__":
    print("=" * 60)
    print("Penalty Shootout Tests")
    print("=" * 60)

    tests = [
        ("setup positions",          test_setup_positions),
        ("penalty space bodies",     test_penalty_space_has_bodies),
        ("angled shot scores",       test_angled_shot_scores),
        ("straight shot blocked",    test_straight_shot_blocked),
        ("team B scores",            test_team_b_scores),
        ("keeper dive matters",      test_keeper_dive_matters),
        ("keeper wrong way",         test_keeper_dives_wrong_way),
        ("apply scored",             test_apply_penalty_scored),
        ("apply saved",              test_apply_penalty_saved),
        ("trajectory decimated",     test_penalty_trajectory_decimated),
        ("alternating kickers",      test_alternating_kickers),
        ("ends after 5 each",        test_shootout_ends_when_winner_after_5_each),
        ("tied after 5 each",        test_shootout_tied_after_5_each),
        ("sudden death both kick",   test_sudden_death_both_must_kick),
        ("sudden death multi round", test_sudden_death_multi_round),
        ("all 5 rounds played",      test_shootout_all_5_rounds_played),
        ("halftime switch",          test_halftime_switch),
        ("no halftime before 45",    test_no_halftime_before_45),
        ("full time after 2nd half", test_full_time_after_second_half),
        ("ET to penalties",          test_et_transition_to_penalties),
        ("ET no penalties",          test_et_no_penalties_if_winner),
        ("ET second half",           test_et_second_half),
        ("regular kick valid",       test_regular_kick_returns_valid),
        ("regular simulate kick",    test_regular_goal_via_simulate),
        ("goalkeeper move reset",    test_goalkeeper_move_reset),
        ("penalty kick history",     test_penalty_kick_history),
    ]

    for name, fn in tests:
        print(f"\n[{name}]")
        try:
            fn()
        except Exception as e:
            mark_failed()
            print(f"  FAIL  {name} — exception: {e}")

    print(f"\n{'=' * 40}")
    print(f"  {passed} passed, {failed} failed out of {passed + failed}")
    print(f"{'=' * 40}")
    exit(0 if failed == 0 else 1)
