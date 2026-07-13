"""
Sandboxed execution of user-uploaded soccer AI code.

Security layers:
  1. AST scan — blocks forbidden imports and dangerous built-in calls
  2. Restricted __builtins__ namespace passed to exec()
  3. Thread-based timeout (5 s per move)
  4. Deep-copied inputs — user code cannot corrupt live game state
"""
from __future__ import annotations
import ast
import copy
import math
import random
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _Timeout

# ── Deny-list ────────────────────────────────────────────────────────────────

_FORBIDDEN_IMPORTS = {
    "os", "sys", "subprocess", "socket", "requests", "urllib", "http",
    "ftplib", "smtplib", "shutil", "pathlib", "glob", "tempfile",
    "importlib", "pkgutil", "threading", "multiprocessing", "ctypes",
    "builtins", "__builtin__", "pickle", "shelve", "sqlite3",
    "psycopg2", "pymongo", "redis", "supabase",
}

_FORBIDDEN_CALLS = {
    "__import__", "open", "eval", "exec", "compile",
    "globals", "locals", "vars", "dir",
    "getattr", "setattr", "delattr",
}

# ── Static validation ─────────────────────────────────────────────────────────

def validate_code(code: str) -> tuple[bool, str]:
    if not code.strip():
        return False, "Code cannot be empty."

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, f"Syntax error on line {exc.lineno}: {exc.msg}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _FORBIDDEN_IMPORTS:
                    return False, f"Import '{alias.name}' is not allowed."
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _FORBIDDEN_IMPORTS:
                return False, f"'from {node.module} import ...' is not allowed."
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
                return False, f"Call to '{node.func.id}()' is not allowed."

    has_fn = any(
        isinstance(n, ast.FunctionDef) and n.name == "get_ai_move"
        for n in ast.walk(tree)
    )
    if not has_fn:
        return False, "Missing required function: get_ai_move(state, is_player_a)"

    return True, "OK"


# ── Restricted execution environment ─────────────────────────────────────────

def _safe_globals() -> dict:
    return {
        "__builtins__": {
            "range": range, "len": len, "list": list, "dict": dict,
            "tuple": tuple, "set": set, "frozenset": frozenset,
            "int": int, "float": float, "str": str, "bool": bool,
            "max": max, "min": min, "sum": sum, "abs": abs, "round": round,
            "sorted": sorted, "reversed": reversed,
            "enumerate": enumerate, "zip": zip, "map": map, "filter": filter,
            "any": any, "all": all, "print": print,
            "isinstance": isinstance, "type": type,
            "None": None, "True": True, "False": False,
            "ValueError": ValueError, "TypeError": TypeError,
            "IndexError": IndexError, "KeyError": KeyError,
        },
        "random": random,
        "math": math,
        "copy": copy,
    }


def execute_user_model(
    code: str,
    state: dict,
    is_player_a: bool,
    timeout_s: float = 5.0,
) -> tuple[int, float, float]:
    """
    Execute user model code and return (player_idx, angle_deg, power).
    Raises RuntimeError on timeout, bad output, or runtime error.
    """
    def _run():
        ns = _safe_globals()
        exec(compile(code, "<user_model>", "exec"), ns)  # noqa: S102
        return ns["get_ai_move"](copy.deepcopy(state), is_player_a)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run)
        try:
            result = future.result(timeout=timeout_s)
        except _Timeout:
            raise RuntimeError(f"Model timed out ({timeout_s}s limit)")
        except Exception as exc:
            raise RuntimeError(f"Runtime error: {exc}") from exc

    if result is None:
        return 0, 0.0, 0.0

    try:
        player_idx, angle_deg, power = result
        player_idx = int(player_idx)
        angle_deg  = float(angle_deg)
        power      = float(power)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("get_ai_move must return (player_idx: int, angle_deg: float, power: float)") from exc

    player_idx = max(0, min(2, player_idx))
    power      = max(0.0, min(100.0, power))
    return player_idx, angle_deg, power


# ── Default code template shown to new users ─────────────────────────────────

TEMPLATE = '''\
# ─────────────────────────────────────────────────────────────────────
#  Soccer AI — write your strategy below!
#
#  Field: 1000 × 625  (Team A → RIGHT, goal at x=1000, y=231–394)
#                    (Team B → LEFT,  goal at x=0,    y=231–394)
#
#  state["ball"]       = {"x": float, "y": float}
#  state["players_a"]  = [{"x": float, "y": float}, ...]  # 3 players
#  state["players_b"]  = [{"x": float, "y": float}, ...]  # 3 players
#  state["score_a"]    = int
#  state["score_b"]    = int
#  state["kick_count"] = int
#  state["game_over"]  = bool
#  state["period"]     = str  # "regular_first", "regular_second", etc.
#
#  Return: (player_idx, angle_deg, power)
#    player_idx    — 0, 1, or 2
#    angle_degrees — 0=right, 90=down, 180=left, 270=up
#    power         — 0.0–100.0
#
#  Available: math, random, copy + common builtins
# ─────────────────────────────────────────────────────────────────────

MODEL_NAME  = "My Soccer AI"
DESCRIPTION = "Describe your strategy here"


def get_ai_move(state, is_player_a):
    bx, by   = state["ball"]["x"], state["ball"]["y"]
    players  = state["players_a"] if is_player_a else state["players_b"]

    # Pick player closest to ball
    best_idx = min(range(3), key=lambda i: math.hypot(
        players[i]["x"] - bx, players[i]["y"] - by
    ))

    # Aim at center of opponent\'s goal
    goal_x = 1000.0 if is_player_a else 0.0
    goal_y = 312.0
    angle  = math.degrees(math.atan2(goal_y - by, goal_x - bx))

    return best_idx, angle, 80.0
'''
