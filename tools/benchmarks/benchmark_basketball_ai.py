"""Reproduce basketball decision fitness against the pre-fitness local policy.

Run ``python tools/benchmarks/benchmark_basketball_ai.py``. These are targeted
physical scenarios, not a claim of a human or online-match win rate.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import random
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from models import basketball_logic as court


def baseline_move(state, is_player_a):
    """Frozen original policy, retained only for an inspectable comparison."""
    side = "A" if is_player_a else "B"
    roster = state["players_" + side.lower()]
    possession = court._read_possession(state)
    cap = min(100.0, max(0.0, float(state.get("power_cap", 100))))
    if possession and possession[0] == side:
        index = possession[1]
        player, hoop = roster[index], court.HOOPS[side]
        stats = court._get_player_stats(state, is_player_a, index)
        dx, dy = hoop["x"] - player["x"], hoop["y"] - player["y"]
        distance = math.hypot(dx, dy)
        shot_power = distance / (court._throw_factor(stats) * court._flight_time(court.HOOP_HEIGHT))
        if shot_power <= cap:
            return index, math.degrees(math.atan2(dy, dx)) % 360, shot_power
        opponents = state["players_b" if is_player_a else "players_a"]
        options = []
        for target_index, target in enumerate(roster):
            if target_index == index:
                continue
            target_distance = math.hypot(target["x"] - hoop["x"], target["y"] - hoop["y"])
            pass_distance = math.hypot(target["x"] - player["x"], target["y"] - player["y"])
            pass_power = pass_distance / (court._throw_factor(stats) * court._flight_time(court._CATCH_HEIGHT))
            clearance = min(math.hypot(target["x"] - other["x"], target["y"] - other["y"]) for other in opponents)
            if pass_power <= cap and target_distance < distance - 50 and clearance > 140:
                options.append((target_distance - min(clearance, 150) * .25, target_index, pass_power))
        if options:
            _, target_index, pass_power = min(options)
            target = roster[target_index]
            return index, math.degrees(math.atan2(target["y"] - player["y"], target["x"] - player["x"])) % 360, pass_power
        return index, math.degrees(math.atan2(dy, dx)) % 360, cap
    target = state["ball"]
    if possession:
        target = state["players_" + possession[0].lower()][possession[1]]
    index = court.human_basketball_player_index(state, is_player_a)
    if possession:
        eligible = [i for i, player in enumerate(roster)
                    if math.hypot(player["x"] - target["x"], player["y"] - target["y"]) >
                    court._get_player_radius(court._get_player_stats(state, is_player_a, i)) +
                    court._get_player_radius(court._get_player_stats(state, possession[0] == "A", possession[1])) + 12]
        if eligible:
            index = min(eligible, key=lambda i: math.hypot(roster[i]["x"] - target["x"], roster[i]["y"] - target["y"]))
    return court._move_to(state, side, index, (target["x"], target["y"]))


def fitness_cases():
    for side in ("A", "B"):
        for held in (False, True):
            state = court.new_basketball_state(team_movement=False, power_cap=80)
            own, other = state["players_" + side.lower()], state["players_" + ("b" if side == "A" else "a")]
            for i, player in enumerate(own):
                player.update(x=100 + i * 55, y=100)
            for i, player in enumerate(other):
                player.update(x=950 + i * 65, y=750)
            own[0].update(x=640, y=280, stats={"size": 50, "power": 0, "weight": 50, "agility": 100})
            own[1].update(x=490, y=400, stats={"size": 50, "power": 100, "weight": 50, "agility": 0})
            state["ball"] = {"x": 800, "y": 400, "z": 24 if held else 0}
            if held:
                other[4].update(x=800, y=400)
            state["possession"] = court._possession("B" if side == "A" else "A", 4) if held else None
            yield f"{side}-{'steal' if held else 'rebound'}-stat-reach", state, side, "possession", 1
        state = court.new_basketball_state(team_movement=False)
        own, other = state["players_" + side.lower()], state["players_" + ("b" if side == "A" else "a")]
        for i, player in enumerate(own):
            player.update(x=100 + i * 55, y=100)
        for i, player in enumerate(other):
            player.update(x=1000 + i * 55, y=750)
        own[0].update(x=600, y=400)
        own[1].update(x=650, y=560)
        other[0].update(x=680, y=400, stats={"size": 100, "power": 0, "weight": 100, "agility": 0})
        state["ball"], state["possession"] = {"x": 800, "y": 400, "z": 0}, None
        yield f"{side}-blocked-rebound", state, side, "possession", 1
        state = court.new_basketball_state(team_movement=False)
        own = state["players_" + side.lower()]
        own[4].update(x=court.HOOPS[side]["x"], y=court.HOOPS[side]["y"])
        state["possession"] = court._possession(side, 4)
        state["ball"] = {"x": own[4]["x"], "y": own[4]["y"], "z": 24}
        yield f"{side}-under-basket", state, side, "basket", 1


def run():
    report = {}
    for label, policy in (("baseline", baseline_move), ("current", court.basketball_ai_move)):
        cases, durations = [], []
        for name, state, side, goal, turns in fitness_cases():
            live = copy.deepcopy(state)
            before = copy.deepcopy(live)
            for _ in range(turns):
                started = time.perf_counter()
                move = policy(live, side == "A")
                durations.append((time.perf_counter() - started) * 1000)
                assert live == before, f"{label} mutated its input"
                court.apply_basketball_move(live, *move, side == "A")
            possession = court._read_possession(live)
            success = bool(live["score_" + side.lower()]) if goal == "basket" else bool(possession and possession[0] == side)
            cases.append({"case": name, "success": success, "player_idx": move[0]})
        report[label] = {"successes": sum(case["success"] for case in cases), "cases": cases,
                         "mean_decision_ms": round(statistics.mean(durations), 3),
                         "max_decision_ms": round(max(durations), 3)}
    rng = random.Random(24021)
    durations = []
    for cap in (0, 35, 60, 100):
        for moving in (False, True):
            for possession_side in (None, "A", "B"):
                for side in ("A", "B"):
                    state = court.new_basketball_state(power_cap=cap, team_movement=moving)
                    # Non-overlapping, shuffled court positions with a legal
                    # 200-point stat budget on every player.
                    points = rng.sample([(x, y) for x in (150, 400, 650, 900, 1150)
                                         for y in (180, 430, 680)], 10)
                    for index, player in enumerate(state["players_a"] + state["players_b"]):
                        power = rng.randint(20, 80)
                        player.update(x=points[index][0], y=points[index][1],
                                      stats={"size": 50, "power": power, "weight": 50, "agility": 100 - power})
                    if possession_side:
                        index = rng.randrange(5)
                        player = state["players_" + possession_side.lower()][index]
                        state["ball"] = {"x": player["x"], "y": player["y"], "z": 24}
                        state["possession"] = court._possession(possession_side, index)
                    else:
                        state["ball"], state["possession"] = {"x": 700, "y": 400, "z": 0}, None
                    before = copy.deepcopy(state)
                    started = time.perf_counter()
                    move = court.basketball_ai_move(state, side == "A")
                    durations.append((time.perf_counter() - started) * 1000)
                    assert state == before and 0 <= move[0] < 5 and 0 <= move[2] <= cap
                    assert all(math.isfinite(value) for value in move)
                    trajectory, *_ = court.apply_basketball_move(state, *move, side == "A")
                    assert len(trajectory) <= court._MAX_STEPS + 2
                    assert all(math.isfinite(frame[axis]) for frame in trajectory for axis in ("x", "y", "z"))
    report["seeded_stress"] = {"legal_physical_cases": len(durations),
                                "mean_decision_ms": round(statistics.mean(durations), 3),
                                "max_decision_ms": round(max(durations), 3)}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    run()
