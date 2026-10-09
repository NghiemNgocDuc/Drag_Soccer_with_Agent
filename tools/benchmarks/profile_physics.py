"""Local, service-free benchmark for the physics kick hot path."""
from __future__ import annotations

import argparse
import cProfile
import copy
import json
from pathlib import Path
import pstats
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from models.soccer_logic import FIELD_H, FIELD_W, apply_kick, new_soccer_state, simulate_kick


def run(repeats: int, profile: bool) -> None:
    profiler = cProfile.Profile()
    if profile:
        profiler.enable()
    results = []
    for count in (3, 7, 11):
        for kind in ("miss", "contact", "wall"):
            state = new_soccer_state(player_count=count)
            state["players_a"][-1].update(x=FIELD_W / 2 - 60, y=FIELD_H / 2)
            angle = 90 if kind == "miss" else 0
            if kind == "wall":
                state["ball"].update(x=FIELD_W / 2, y=80)
                state["players_a"][-1].update(x=FIELD_W / 2, y=140)
                angle = -90
            for name, kick in (("simulate", simulate_kick), ("apply", apply_kick)):
                elapsed = []
                for _ in range(repeats):
                    current = copy.deepcopy(state)
                    started = time.perf_counter()
                    result = kick(current, count - 1, angle, 100, True)
                    elapsed.append((time.perf_counter() - started) * 1000)
                results.append(dict(players=count, kind=kind, method=name,
                                    mean_ms=round(statistics.mean(elapsed), 3),
                                    p95_ms=round(sorted(elapsed)[int((len(elapsed)-1)*.95)], 3),
                                    samples=len(result[0]),
                                    final_ball={k: result[0][-1][k] for k in ("x", "y")}))
    if profile:
        profiler.disable()
    print(json.dumps(results, indent=2))
    if profile:
        pstats.Stats(profiler).strip_dirs().sort_stats("cumtime").print_stats(20)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    run(args.repeats, args.profile)
