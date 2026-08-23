"""Game analytics — trajectory analysis, model benchmarking, stats computation."""
from __future__ import annotations
import math
import importlib
from concurrent.futures import ThreadPoolExecutor, as_completed

from models.soccer_logic import new_soccer_state, apply_kick, FIELD_W, FIELD_H, GOAL_Y1, GOAL_Y2, BALL_R, _MARGIN

GOAL_CENTER_Y = (GOAL_Y1 + GOAL_Y2) / 2

# ── Built-in model registry ────────────────────────────────────────────────
_BUILTIN_MODEL_PATHS: dict[str, str] = {
    "minimax":          "models.minimax",
    "monte_carlo":      "models.monte_carlo",
    "greedy":           "models.greedy_model",
    "bayesian":         "models.bayes",
    "value_iteration":  "models.value_iteration",
    "policy_iteration": "models.policy_iteration",
    "q_learning":       "models.q_learning",
}

MODEL_CATALOG: list[dict] = [
    {"id": "minimax",          "name": "Minimax"},
    {"id": "monte_carlo",      "name": "Monte Carlo"},
    {"id": "greedy",           "name": "Greedy Striker"},
    {"id": "bayesian",         "name": "Bayesian"},
    {"id": "value_iteration",  "name": "Value Iteration"},
    {"id": "policy_iteration", "name": "Policy Iteration"},
    {"id": "q_learning",       "name": "Q-Learning"},
]

_MODULE_CACHE: dict = {}


def _load_model(name: str):
    if name not in _MODULE_CACHE:
        path = _BUILTIN_MODEL_PATHS.get(name)
        if not path:
            return None
        _MODULE_CACHE[name] = importlib.import_module(path)
    return _MODULE_CACHE[name]


def analyze_trajectory(trajectory: list[dict]) -> dict:
    if not trajectory or len(trajectory) < 2:
        return {"distance": 0, "avg_speed": 0, "max_speed": 0, "goal_ward": False, "on_target": False}
    dx = trajectory[-1]["x"] - trajectory[0]["x"]
    dy = trajectory[-1]["y"] - trajectory[0]["y"]
    dist = math.hypot(dx, dy)
    speeds = []
    for i in range(1, len(trajectory)):
        sx = trajectory[i]["x"] - trajectory[i-1]["x"]
        sy = trajectory[i]["y"] - trajectory[i-1]["y"]
        speeds.append(math.hypot(sx, sy))
    end = trajectory[-1]
    on_target = GOAL_Y1 <= end.get("y", 0) <= GOAL_Y2
    return {
        "distance": round(dist, 1),
        "avg_speed": round(sum(speeds) / len(speeds), 1) if speeds else 0,
        "max_speed": round(max(speeds), 1) if speeds else 0,
        "goal_ward": abs(dx) > abs(dy) and dx > 0,
        "on_target": on_target,
    }


def analyze_game(game_state: dict) -> dict:
    mh = game_state.get("move_history", [])
    if not mh:
        return _empty_game_analysis()
    total_shots = len(mh)
    goals = sum(1 for m in mh if m.get("scored"))
    shots_on_target = 0
    shot_distances = []
    kick_powers = []
    trajectory_data = []
    for move in mh:
        traj = move.get("trajectory", [])
        if traj:
            ta = analyze_trajectory(traj)
            if ta["on_target"]:
                shots_on_target += 1
            shot_distances.append(ta["distance"])
            trajectory_data.append(ta)
        if "power" in move:
            kick_powers.append(float(move["power"]))
    avg_shot_dist = round(sum(shot_distances) / len(shot_distances), 1) if shot_distances else 0
    avg_power = round(sum(kick_powers) / len(kick_powers), 1) if kick_powers else 0
    return {
        "total_shots": total_shots,
        "goals": goals,
        "shots_on_target": shots_on_target,
        "shot_accuracy": round(shots_on_target / total_shots * 100, 1) if total_shots else 0,
        "conversion_rate": round(goals / total_shots * 100, 1) if total_shots else 0,
        "avg_shot_distance": avg_shot_dist,
        "avg_power": avg_power,
        "avg_trajectory": {
            "distance": round(sum(t["distance"] for t in trajectory_data) / len(trajectory_data), 1) if trajectory_data else 0,
            "avg_speed": round(sum(t["avg_speed"] for t in trajectory_data) / len(trajectory_data), 1) if trajectory_data else 0,
        } if trajectory_data else {},
    }


def _empty_game_analysis() -> dict:
    return {
        "total_shots": 0, "goals": 0, "shots_on_target": 0,
        "shot_accuracy": 0, "conversion_rate": 0,
        "avg_shot_distance": 0, "avg_power": 0, "avg_trajectory": {},
    }


def analyze_player_positions(state: dict) -> dict:
    pa = state.get("players_a", [])
    pb = state.get("players_b", [])
    def _stats(players, label):
        if not players:
            return {}
        xs = [p["x"] for p in players]
        ys = [p["y"] for p in players]
        return {
            f"{label}_avg_x": round(sum(xs) / len(xs), 1),
            f"{label}_avg_y": round(sum(ys) / len(ys), 1),
            f"{label}_spread": round(math.sqrt(sum((x - sum(xs)/len(xs))**2 for x in xs) / len(xs)), 1),
        }
    result = {}
    result.update(_stats(pa, "team_a"))
    result.update(_stats(pb, "team_b"))
    ball = state.get("ball", {})
    result["ball_x"] = ball.get("x", 0)
    result["ball_y"] = ball.get("y", 0)
    return result


def run_model_battle(
    model_a,
    model_b,
    n_games: int = 10,
    max_kicks: int = 30,
    parallel: bool = True,
    progress_callback=None,
    tracer: dict | None = None,
) -> dict | None:
    if isinstance(model_a, str):
        model_a = _load_model(model_a)
    if isinstance(model_b, str):
        model_b = _load_model(model_b)
    if model_a is None or model_b is None:
        return None
    if tracer is None:
        tracer = None
    wins_a = wins_b = draws = 0
    all_stats_a = []
    all_stats_b = []
    scores_a = []
    scores_b = []
    kick_counts = []
    game_results = []

    def _play_one(game_idx: int) -> dict:
        st = new_soccer_state()
        st["move_history"] = []
        traced: list[dict] = []
        turn_n = 0  # monotonic kick counter (penalty kicks don't advance kick_count)
        for _ in range(max_kicks):
            if st.get("game_over"):
                break
            is_a = st["is_player_a"]
            mod = model_a if is_a else model_b
            try:
                pidx, ang, pwr = mod.get_ai_move(st, is_a)
            except Exception:
                st["winner"] = "B" if is_a else "A"
                st["game_over"] = True
                break
            # Snapshot the exact pre-kick state if this turn belongs to the
            # traced side (deep copy BEFORE apply_kick mutates `st` in place).
            snap = None
            if tracer and ((tracer["side"] == "A") == is_a):
                snap = dict(st)
                snap.pop("move_history", None)
            try:
                traj, scored, desc, kep, push = apply_kick(st, pidx, ang, pwr, is_a)
            except Exception:
                st["winner"] = "B" if is_a else "A"
                st["game_over"] = True
                break
            if snap is not None:
                traced.append({
                    "turn": turn_n,
                    "mover": "a" if is_a else "b",
                    "snapshot": snap,
                    "decision": {"player_idx": pidx, "angle": round(ang, 1), "power": round(pwr, 1)},
                    "scored": scored,
                    "trajectory": traj,
                })
            turn_n += 1
            st["move_history"].append({
                "mover": "a" if is_a else "b",
                "player_idx": pidx, "angle": round(ang, 1), "power": round(pwr, 1),
                "trajectory": traj, "scored": scored,
            })
        w = st.get("winner", "Draw")
        ga_a = analyze_game({**st, "move_history": [m for m in st["move_history"] if m.get("mover") == "a"]})
        ga_b = analyze_game({**st, "move_history": [m for m in st["move_history"] if m.get("mover") == "b"]})
        gr = {
            "game_idx": game_idx,
            "winner": w,
            "score_a": st["score_a"],
            "score_b": st["score_b"],
            "kick_count": st.get("kick_count", 0),
            "stats_a": ga_a,
            "stats_b": ga_b,
            "move_history": st.get("move_history", []),
        }
        if tracer is not None:
            gr["traced_turns"] = traced
        return gr

    if parallel and n_games > 1:
        with ThreadPoolExecutor(max_workers=min(n_games, 8)) as pool:
            futures = {pool.submit(_play_one, i): i for i in range(n_games)}
            for f in as_completed(futures):
                gr = f.result()
                game_results.append(gr)
                if progress_callback:
                    progress_callback(len(game_results), n_games)
        game_results.sort(key=lambda x: x["game_idx"])
    else:
        for i in range(n_games):
            gr = _play_one(i)
            game_results.append(gr)
            if progress_callback:
                progress_callback(i + 1, n_games)

    for gr in game_results:
        w = gr["winner"]
        if w == "A":
            wins_a += 1
        elif w == "B":
            wins_b += 1
        else:
            draws += 1
        scores_a.append(gr["score_a"])
        scores_b.append(gr["score_b"])
        kick_counts.append(gr["kick_count"])
        all_stats_a.append(gr["stats_a"])
        all_stats_b.append(gr["stats_b"])

    def _avg_stats(stats_list: list) -> dict:
        if not stats_list:
            return {}
        keys = ["total_shots", "goals", "shots_on_target", "shot_accuracy", "conversion_rate", "avg_shot_distance", "avg_power"]
        return {k: round(sum(s.get(k, 0) for s in stats_list) / len(stats_list), 1) for k in keys}

    return {
        "n_games": n_games,
        "wins_a": wins_a,
        "wins_b": wins_b,
        "draws": draws,
        "win_rate_a": round(wins_a / n_games * 100, 1),
        "win_rate_b": round(wins_b / n_games * 100, 1),
        "avg_score_a": round(sum(scores_a) / n_games, 1),
        "avg_score_b": round(sum(scores_b) / n_games, 1),
        "total_goals": sum(scores_a) + sum(scores_b),
        "avg_kicks": round(sum(kick_counts) / n_games, 1),
        "avg_stats_a": _avg_stats(all_stats_a),
        "avg_stats_b": _avg_stats(all_stats_b),
        "games": game_results,
    }


def compute_head_to_head_matrix(n_games: int = 5) -> list[dict]:
    model_names = list(_BUILTIN_MODEL_PATHS.keys())
    matrix = []
    for i, name_a in enumerate(model_names):
        for j, name_b in enumerate(model_names):
            if i >= j:
                continue
            result = run_model_battle(name_a, name_b, n_games=n_games, parallel=True)
            if result is None:
                continue
            matrix.append({
                "model_a": name_a,
                "model_b": name_b,
                "wins_a": result["wins_a"],
                "wins_b": result["wins_b"],
                "draws": result["draws"],
                "win_rate_a": result["win_rate_a"],
                "win_rate_b": result["win_rate_b"],
                "avg_score_a": result["avg_score_a"],
                "avg_score_b": result["avg_score_b"],
            })
    return matrix


def compute_position_heatmap(move_history: list[dict]) -> dict:
    grid_size = 20
    cols = FIELD_W // grid_size
    rows = FIELD_H // grid_size
    heatmap = [[0] * cols for _ in range(rows)]
    for move in move_history:
        traj = move.get("trajectory", [])
        for pt in traj:
            cx = int(pt.get("x", 0) // grid_size)
            cy = int(pt.get("y", 0) // grid_size)
            if 0 <= cx < cols and 0 <= cy < rows:
                heatmap[cy][cx] += 1
    max_val = max(max(row) for row in heatmap) if heatmap else 1
    normalized = [[round(v / max_val * 100, 1) for v in row] for row in heatmap] if max_val else heatmap
    return {
        "grid_size": grid_size,
        "cols": cols,
        "rows": rows,
        "data": normalized,
        "field_w": FIELD_W,
        "field_h": FIELD_H,
    }


def compute_goal_zones(move_history: list[dict]) -> dict:
    zones = {"top_left": 0, "top_right": 0, "center_left": 0, "center_right": 0, "bottom_left": 0, "bottom_right": 0}
    for move in move_history:
        traj = move.get("trajectory", [])
        if not traj:
            continue
        for pt in traj:
            x, y = pt.get("x", 0), pt.get("y", 0)
            zone_x = "left" if x < FIELD_W / 2 else "right"
            zone_y = "top" if y < FIELD_H / 3 else ("bottom" if y > FIELD_H * 2 / 3 else "center")
            key = f"{zone_y}_{zone_x}"
            zones[key] = zones.get(key, 0) + 1
    total = sum(zones.values()) or 1
    return {k: round(v / total * 100, 1) for k, v in zones.items()}


# ── Leaderboard benchmark (orchestration only; reuses run_model_battle) ──────

def benchmark_model_vs_builtins(
    model,
    n_games: int = 5,
    opponents: list | None = None,
    progress_callback=None,
    tracer: dict | None = None,
) -> dict:
    """Benchmark `model` (team A) against every built-in agent.

    Aggregates `run_model_battle` results into the leaderboard score:
    the arithmetic mean of the per-opponent win rates (0-100), plus the
    per-opponent breakdown and aggregate shot stats. `opponents` accepts
    catalog ids or model objects (default: all 7 built-ins). Returns
    {"score", "n_games", "details": [...], "avg_stats": {...}}.

    When `tracer` is given (dict with a "side" — see run_model_battle), the
    per-game results carrying `traced_turns` are also returned under
    "traced_games" (list of {"opponent", "opponent_label", "games"}).
    """
    if opponents is None:
        opponents = [m["id"] for m in MODEL_CATALOG]
    total_games = len(opponents) * n_games
    offset = 0
    details = []
    stats_acc: dict[str, list[float]] = {}
    traced_payload: list[dict] = [] if tracer is not None else None

    for opp in opponents:
        if isinstance(opp, str):
            opp_id = opp
            opp_name = next((m["name"] for m in MODEL_CATALOG if m["id"] == opp), opp)
        else:
            opp_id = getattr(opp, "MODEL_NAME", "opponent")
            opp_name = getattr(opp, "MODEL_NAME", "Opponent")
        if progress_callback:
            def _pb(d, _n, off=offset):
                progress_callback(off + d, total_games)
        else:
            _pb = None
        result = run_model_battle(
            model, opp, n_games=n_games, parallel=True, progress_callback=_pb,
            tracer=tracer,
        )
        if result is None:
            continue
        offset += result["n_games"]
        details.append({
            "opponent": opp_name,
            "win_rate": result["win_rate_a"],
            "wins": result["wins_a"],
            "draws": result["draws"],
            "losses": result["n_games"] - result["wins_a"] - result["draws"],
            "n_games": result["n_games"],
        })
        if traced_payload is not None:
            traced_payload.append({
                "opponent": opp_id,
                "opponent_label": opp_name,
                "games": result.get("games") or [],
            })
        for k, v in (result.get("avg_stats_a") or {}).items():
            stats_acc.setdefault(k, []).append(v)

    score = round(sum(d["win_rate"] for d in details) / len(details), 1) if details else 0.0
    avg_stats = {k: round(sum(vs) / len(vs), 1) for k, vs in stats_acc.items()} if stats_acc else {}
    out = {"score": score, "n_games": n_games, "details": details, "avg_stats": avg_stats}
    if traced_payload is not None:
        out["traced_games"] = traced_payload
    return out
