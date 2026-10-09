"""Measure built-in decision latency in disposable, service-free subprocesses.

Usage: python tools/benchmarks/profile_ai.py --output report.json
       python tools/benchmarks/profile_ai.py --decision-mode bounded --compare before.json
Each subprocess has a hard wall-clock limit, including model import. Decision
timings exclude imports and subprocess startup. Keep the computer awake and run
before/after profiles sequentially; a suspended host cannot enforce timeouts.
No training data or production game state is written by this tool.
"""
from __future__ import annotations

import argparse
import ast
import copy
import importlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
PREFIX = "AI_PROFILE "


def catalog() -> dict[str, str]:
    tree = ast.parse((ROOT / "services/game_analytics.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "_BUILTIN_MODEL_PATHS":
                return ast.literal_eval(node.value)
    raise RuntimeError("Built-in registry not found")


def emit(value: dict) -> None:
    print(PREFIX + json.dumps(value, allow_nan=False), flush=True)


def make_state(physics, count: int, scenario: str, side: str) -> dict:
    """Mirror attack/defense positions so either team can be compared."""
    state = physics.new_soccer_state(mode="aivai", player_count=count)
    physics.inject_player_stats(state,
        [dict(physics.DEFAULT_STATS) for _ in range(count)],
        [dict(physics.DEFAULT_STATS) for _ in range(count)])
    is_a = side == "a"
    state["is_player_a"] = is_a
    if scenario != "kickoff":
        fraction = .70 if scenario == "attack" else .22
        bx = physics.FIELD_W * (fraction if is_a else 1 - fraction)
        by = physics.FIELD_H * .50
        direction = 1 if is_a else -1
        ours = state["players_a"] if is_a else state["players_b"]
        theirs = state["players_b"] if is_a else state["players_a"]
        state["ball"].update(x=bx, y=by)
        ours[-1].update(x=bx - direction * 60, y=by)
        theirs[-1].update(x=bx + direction * 100,
                          y=by + (50 if scenario == "attack" else -50))
    return state


def move_outcome(physics, state: dict, move, is_a: bool) -> dict:
    """Check the chosen move with ordinary, full-resolution match physics."""
    current = copy.deepcopy(state)
    bx, by = current["ball"]["x"], current["ball"]["y"]
    trajectory, scored, *_ = physics.apply_kick(current, *move, is_a)
    final = trajectory[-1]
    ours = current["players_a"] if is_a else current["players_b"]
    theirs = current["players_b"] if is_a else current["players_a"]
    ours_distance = min(math.hypot(p["x"] - final["x"], p["y"] - final["y"]) for p in ours)
    theirs_distance = min(math.hypot(p["x"] - final["x"], p["y"] - final["y"]) for p in theirs)
    return {
        "ball_displacement_px": round(math.hypot(final["x"] - bx, final["y"] - by), 3),
        "forward_progress_px": round((final["x"] - bx) * (1 if is_a else -1), 3),
        "scored": scored,
        "our_nearest_distance_px": round(ours_distance, 3),
        "opponent_nearest_distance_px": round(theirs_distance, 3),
        "ours_closer_to_ball": ours_distance < theirs_distance,
    }


def worker(model_id: str, count: int, scenario: str, repeats: int,
           side: str, decision_mode: str) -> int:
    from tools.browser.verify_game_performance import isolated_environment

    isolated_environment()
    os.environ["OLLAMA_HOST"] = ""
    import models.soccer_logic as physics

    sample = {"status": "importing", "model": model_id, "players": count,
              "scenario": scenario, "side": side, "decision_mode": decision_mode,
              "simulations": 0, "simulation_calls": 0, "physics_ms": 0.0}
    emit(sample)
    original = physics.simulate_kick
    original_build = physics._build_space

    def measured_build(*args, **kwargs):
        # Count real physics evaluations, excluding cached/budget-rejected calls.
        if sample["status"] == "running":
            sample["simulations"] += 1
        return original_build(*args, **kwargs)

    physics._build_space = measured_build

    def measured(*args, **kwargs):
        sample["simulation_calls"] += 1
        started = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            sample["physics_ms"] += (time.perf_counter() - started) * 1000
            if sample["simulation_calls"] % 5 == 0:
                emit(sample)

    physics.simulate_kick = measured
    service = None
    bounded_move = None
    if decision_mode == "service":
        import app as service
    elif decision_mode == "bounded":
        from models.search_budget import get_model_move as bounded_move
    if decision_mode != "direct":
        from models.search_budget import DEFAULT_BUDGET_S, DEFAULT_MAX_SIMULATIONS
        sample.update(decision_budget_ms=DEFAULT_BUDGET_S * 1000,
                      max_simulations=DEFAULT_MAX_SIMULATIONS)
    started = time.perf_counter()
    module = importlib.import_module(catalog()[model_id])
    sample["import_ms"] = round((time.perf_counter() - started) * 1000, 3)
    if model_id == "adaptive_learner":
        # Fresh, reproducible learner, without modifying persisted training data.
        module._Q = {}
        module._META = {"games": 0, "wins": 0, "epsilon": module.EPSILON_START}
    state = make_state(physics, count, scenario, side)
    elapsed, cpu_times, simulations, physics_times, calls, fallbacks = [], [], [], [], [], []
    is_a = side == "a"
    for repeat in range(repeats):
        random.seed(100 + repeat)
        sample.update(status="running", repeat=repeat + 1, simulations=0,
                      simulation_calls=0, physics_ms=0.0)
        emit(sample)
        current = copy.deepcopy(state)
        started = time.perf_counter()
        cpu_started = time.process_time()
        info = {}
        if bounded_move is not None:
            move = bounded_move(module, current, is_a, diagnostics=info)
        elif service is None:
            move = module.get_ai_move(current, is_a)
        else:
            move = service._get_ai_move_with_timeout(module, current, is_a,
                                                     fallback_info=info)
        elapsed.append((time.perf_counter() - started) * 1000)
        cpu_times.append((time.process_time() - cpu_started) * 1000)
        simulations.append(sample["simulations"])
        calls.append(sample["simulation_calls"])
        physics_times.append(sample["physics_ms"])
        fallbacks.append(info)
        index, angle, power = move
        ours = state["players_a"] if is_a else state["players_b"]
        sample["legal_move"] = (
            isinstance(index, int) and 0 <= index < len(ours)
            and math.isfinite(float(angle)) and math.isfinite(float(power))
            and 0 <= float(power) <= state.get("power_cap", 100)
        )
        index, angle, power = physics.normalize_kick(state, *move, is_a)
        sample["move"] = [index, angle, power]
        sample["status"] = "checking_outcome"
        sample["outcome"] = move_outcome(physics, state, sample["move"], is_a)
    sample.update(status="ok", repeats=repeats,
                  median_ms=round(statistics.median(elapsed), 3),
                  max_ms=round(max(elapsed), 3),
                  median_cpu_ms=round(statistics.median(cpu_times), 3),
                  wall_to_cpu_ratio=round(statistics.median(elapsed) /
                                          max(.001, statistics.median(cpu_times)), 3),
                  median_simulations=statistics.median(simulations),
                  median_simulation_calls=statistics.median(calls),
                  fallback_info=fallbacks,
                  median_physics_ms=round(statistics.median(physics_times), 3),
                  physics_ms=round(sample["physics_ms"], 3))
    emit(sample)
    return 0


def records(output: str | bytes | None) -> list[dict]:
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    values = []
    for line in (output or "").splitlines():
        if line.startswith(PREFIX):
            try:
                values.append(json.loads(line[len(PREFIX):]))
            except json.JSONDecodeError:
                # A killed subprocess can leave its final diagnostic incomplete.
                continue
    return values


def run_case(model_id: str, count: int, scenario: str, repeats: int,
             timeout: float, side: str, decision_mode: str) -> dict:
    env = dict(os.environ)
    env.update(PYTHONIOENCODING="utf-8", DEV_MODE="1",
               SECRET_KEY="ai-profile-only", OLLAMA_HOST="")
    for key in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_KEY",
                "POSTHOG_API_KEY", "SENTRY_DSN", "RESEND_API_KEY", "PINECONE_API_KEY",
                "OPENAI_API_KEY", "CLERK_SECRET_KEY", "CLERK_PUBLISHABLE_KEY",
                "PRODUCTBRIDGE_API_KEY", "SEMANTIC_SCHOLAR_API_KEY"):
        env[key] = ""
    env["UPSTASH_REDIS_URL"] = "redis://127.0.0.1:1"
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", model_id,
               "--players", str(count), "--scenario", scenario, "--repeats", str(repeats),
               "--side", side, "--decision-mode", decision_mode]
    started = time.perf_counter()
    try:
        process = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                                 text=True, encoding="utf-8", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        partial = records(exc.stdout)
        result = partial[-1] if partial else {"model": model_id, "players": count,
                                             "scenario": scenario}
        result.update(status="timeout", subprocess_limit_s=timeout)
    else:
        values = records(process.stdout)
        result = values[-1] if values else {"model": model_id, "players": count,
                                           "scenario": scenario}
        if process.returncode:
            result.update(status="error", error=process.stderr.strip().splitlines()[-1:])
    result["subprocess_ms"] = round((time.perf_counter() - started) * 1000, 3)
    result.update(side=side, decision_mode=decision_mode)
    if result["subprocess_ms"] > timeout * 1000 + 5000:
        result.update(status="interrupted", note="Host was suspended or benchmark was interrupted.")
    return result


def summarize(results: list[dict]) -> dict:
    summary = {}
    for name in dict.fromkeys(result["model"] for result in results):
        entries = [result for result in results if result["model"] == name]
        completed = [result for result in entries if result["status"] == "ok"]
        summary[name] = {
            "completed": len(completed), "total": len(entries),
            "median_ms": round(statistics.median(result["median_ms"] for result in completed), 3)
                         if completed else None,
            "max_ms": max((result["max_ms"] for result in completed), default=None),
            "median_simulations": statistics.median(result["median_simulations"] for result in completed)
                                  if completed else None,
            "moving_ball_cases": sum(result.get("outcome", {}).get("ball_displacement_px", 0) > 1
                                     for result in completed),
            "median_forward_progress_px": round(statistics.median(
                result["outcome"]["forward_progress_px"] for result in completed
                if "outcome" in result), 3)
                if any("outcome" in result for result in completed) else None,
        }
    return summary


def compare(before: dict, after: dict) -> dict:
    import models.soccer_logic as physics

    def key(result):
        # Original profiles predating side selection always measured team A.
        return result["model"], result["players"], result["scenario"], result.get("side", "a")

    baseline = {key(result): result for result in before["results"] if result["status"] == "ok"}
    matches = []
    for result in after["results"]:
        prior = baseline.get(key(result))
        if prior is None or result["status"] != "ok":
            continue
        prior_outcome = prior.get("outcome")
        if prior_outcome is None and "move" in prior:
            state = make_state(physics, result["players"], result["scenario"], result["side"])
            prior_outcome = move_outcome(physics, state, prior["move"], result["side"] == "a")
        matches.append({
            "model": result["model"], "players": result["players"],
            "scenario": result["scenario"], "side": result["side"],
            "before_ms": prior["median_ms"], "after_ms": result["median_ms"],
            "before_simulations": prior["median_simulations"],
            "after_simulations": result["median_simulations"],
            "before_outcome": prior_outcome,
            "after_outcome": result.get("outcome"),
        })
    return {
        "matched_cases": len(matches),
        "before_median_ms": round(statistics.median(row["before_ms"] for row in matches), 3)
                            if matches else None,
        "after_median_ms": round(statistics.median(row["after_ms"] for row in matches), 3)
                           if matches else None,
        "before_moving_ball_cases": sum(row["before_outcome"] is not None
            and row["before_outcome"]["ball_displacement_px"] > 1 for row in matches),
        "after_moving_ball_cases": sum(row["after_outcome"] is not None
            and row["after_outcome"]["ball_displacement_px"] > 1 for row in matches),
        "cases": matches,
        "note": "Matched completed cases only; historical interrupted/timeout cases are excluded. "
                "Historical moves are checked against the same current, unbudgeted match physics."
    }


def probe_worker(model_id: str, requests: int, count: int) -> int:
    """Exercise actual local HTTP handlers under a burst of isolated users."""
    from collections import Counter
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from tools.browser.verify_game_performance import isolated_environment

    isolated_environment()
    os.environ["OLLAMA_HOST"] = ""
    import app as service
    import models.soccer_logic as physics

    service._load_model(model_id)  # Keep cold imports out of endpoint latency.
    state = make_state(physics, count, "attack", "a")
    state.update(model_name_a=model_id, model_name_b=model_id)
    barrier = threading.Barrier(requests)

    def request(index: int) -> dict:
        user_id = f"dev:ai-profile-{index}"
        service.save_game(user_id, copy.deepcopy(state))
        with service.app.test_client() as client:
            with client.session_transaction() as session:
                session["user_id"] = user_id
                session["username"] = "AI profile"
            barrier.wait(timeout=5)
            started = time.perf_counter()
            response = client.post("/ai_move", json={})
            elapsed = (time.perf_counter() - started) * 1000
            body = response.get_json() or {}
            move = body.get("ai_result", {})
            return {
                "http_status": response.status_code,
                "endpoint_ms": round(elapsed, 3),
                "think_ms": move.get("think_ms"),
                "fallback_reason": move.get("fallback_reason"),
                "kick_count": body.get("kick_count"),
                "legal_move": isinstance(move.get("player_idx"), int)
                              and 0 <= move["player_idx"] < count
                              and 0 <= move.get("power", -1) <= 100,
            }

    with ThreadPoolExecutor(max_workers=requests) as pool:
        results = list(pool.map(request, range(requests)))
    slots = 0
    while service._ai_slots.acquire(blocking=False):
        slots += 1
    for _ in range(slots):
        service._ai_slots.release()
    elapsed = sorted(row["endpoint_ms"] for row in results)
    emit({
        "status": "ok", "probe": "live_endpoint_burst", "model": model_id,
        "players": count, "requests": requests, "external_services": False,
        "median_endpoint_ms": round(statistics.median(elapsed), 3),
        "p95_endpoint_ms": elapsed[math.ceil(.95 * len(elapsed)) - 1],
        "max_endpoint_ms": max(elapsed),
        "available_worker_slots_after_responses": slots,
        "fallback_reasons": dict(Counter(row["fallback_reason"] or "none" for row in results)),
        "all_http_200": all(row["http_status"] == 200 for row in results),
        "all_moves_legal": all(row["legal_move"] for row in results),
        "all_turns_advanced_once": all(row["kick_count"] == 1 for row in results),
        "results": results,
    })
    return 0


def run_probe(model_id: str, requests: int, count: int, timeout: float) -> dict:
    command = [sys.executable, str(Path(__file__).resolve()), "--probe-worker", model_id,
               "--probe-requests", str(requests), "--players", str(count)]
    try:
        process = subprocess.run(command, cwd=ROOT, capture_output=True,
                                 text=True, encoding="utf-8", timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "probe": "live_endpoint_burst",
                "subprocess_limit_s": timeout}
    values = records(process.stdout)
    result = values[-1] if values else {"status": "error", "probe": "live_endpoint_burst"}
    if process.returncode:
        result.update(status="error", error=process.stderr.strip().splitlines()[-1:])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", default=list(catalog()))
    parser.add_argument("--counts", nargs="+", type=int, default=[3, 7, 11])
    parser.add_argument("--scenarios", nargs="+", choices=["kickoff", "attack", "defense"],
                        default=["kickoff", "attack", "defense"])
    parser.add_argument("--sides", nargs="+", choices=["a", "b"], default=["a", "b"])
    parser.add_argument("--decision-mode", choices=["direct", "bounded", "service"], default="bounded",
                        help="bounded uses cooperative search; service also includes the app worker pool; "
                             "direct calls the raw model.")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=5,
                        help="Hard limit per subprocess, including import time, in seconds.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare", type=Path, help="Compare matching cases against a prior JSON report.")
    parser.add_argument("--probe-model", choices=list(catalog()),
                        help="Also measure a simultaneous burst of real /ai_move requests.")
    parser.add_argument("--probe-requests", type=int, default=12)
    parser.add_argument("--probe-worker", choices=list(catalog()), help=argparse.SUPPRESS)
    parser.add_argument("--worker", choices=list(catalog()), help=argparse.SUPPRESS)
    parser.add_argument("--players", type=int, default=7, help=argparse.SUPPRESS)
    parser.add_argument("--scenario", default="kickoff", help=argparse.SUPPRESS)
    parser.add_argument("--side", choices=["a", "b"], default="a", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.probe_worker:
        return probe_worker(args.probe_worker, args.probe_requests, args.players)
    if args.worker:
        return worker(args.worker, args.players, args.scenario, args.repeats,
                      args.side, args.decision_mode)
    if args.repeats < 1 or args.timeout <= 0 or any(not 1 <= count <= 11 for count in args.counts):
        parser.error("Use positive repeat/timeout values and player counts between 1 and 11.")
    if not 1 <= args.probe_requests <= 32:
        parser.error("Probe request count must be between 1 and 32.")
    unknown = set(args.models) - catalog().keys()
    if unknown:
        parser.error("Unknown models: " + ", ".join(sorted(unknown)))
    report = {"scenario_notes": {"kickoff": "Default formation, fresh match",
                                 "attack": "Ball at 70% width, striker 60px behind, marker 100px ahead",
                                 "defense": "Ball at 22% width, striker 60px behind, marker 100px ahead"},
              "external_services": False, "decision_timings_exclude_import": True,
              "repeats": args.repeats, "subprocess_limit_s": args.timeout,
              "decision_mode": args.decision_mode, "results": []}
    for count in args.counts:
        for scenario in args.scenarios:
            for side in args.sides:
                for model_id in args.models:
                    result = run_case(model_id, count, scenario, args.repeats,
                                      args.timeout, side, args.decision_mode)
                    report["results"].append(result)
                    print(f"{count:2d} {scenario:7s} {side} {model_id:17s} {result['status']:11s} "
                          f"{result.get('median_ms', '-'):>8} ms "
                          f"{result.get('median_simulations', result.get('simulations', '-')):>5} sims",
                          flush=True)
                    if args.output:
                        args.output.parent.mkdir(parents=True, exist_ok=True)
                        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["summary"] = summarize(report["results"])
    if args.compare:
        report["comparison"] = compare(json.loads(args.compare.read_text(encoding="utf-8")), report)
    if args.probe_model:
        report["endpoint_probe"] = run_probe(args.probe_model, args.probe_requests,
                                             max(args.counts), max(args.timeout, 10))
    if args.output:
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    summary = report.get("comparison", report["summary"])
    if "cases" in summary:
        summary = {key: value for key, value in summary.items() if key != "cases"}
    print(json.dumps(summary, indent=2))
    if args.probe_model:
        print(json.dumps({key: value for key, value in report["endpoint_probe"].items()
                          if key != "results"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
