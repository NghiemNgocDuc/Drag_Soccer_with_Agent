"""AI-builder tutorial curriculum — lessons, unlock logic, automatic milestones.

The Learn page guides new AI builders from "never written a game AI" to
beating the built-in agents. Every lesson ends in a *machine-checked*
milestone: the user's code is run headlessly for a few full matches
against a fixed opponent and the win rate decides pass/fail — nothing is
self-reported.

Design rules (the codebase contract):
  * The 7 built-in agents are never modified. A lesson can also target
    two small *inline* baselines defined here — a do-nothing bot and a
    random bot — that are NOT registered in `app.MODELS` (so they never
    appear in the arena / playground / leaderboard).
  * Lessons unlock sequentially (`requires`); lesson 6 is optional and
    lesson 7 (capstone) depends only on lesson 5.
  * Milestone checks run headless (fresh `new_soccer_state` + `apply_kick`
    loop, capped at MAX_KICKS per match — the same shape the arena's
    `run_model_battle` uses), so progress persistence is the only side
    effect written by the caller in app.py.
"""
from __future__ import annotations
import random
import time

from models.soccer_logic import new_soccer_state, apply_kick, inject_player_stats
from user_models.runner import validate_code, execute_user_model

MAX_KICKS = 30  # cap per milestone match (mirrors run_model_battle)

# Constants used inside lesson prose / checks (1400×875 field, 3 players).
FIELD_W = 1400
FIELD_H = 875
GOAL_Y1 = 356
GOAL_Y2 = 519
PLAYER_COUNT = 3

BUILTIN_OPPONENTS = ("greedy", "monte_carlo", "bayesian", "q_learning",
                     "value_iteration", "policy_iteration", "minimax")

# Optional-stats lesson: player index 2 (the striker) gets Power 85 so
# "who kicks" genuinely matters; the built-in opponent gets the same build
# (a fair, deterministic test).
_L6_STATS = [
    {"size": 50, "power": 50, "weight": 50, "agility": 50},
    {"size": 50, "power": 50, "weight": 50, "agility": 50},
    {"size": 50, "power": 85, "weight": 50, "agility": 50},
]


#  Inline baseline bots (NOT part of the 7 built-ins) 

class DoNothingBot:
    """Optimally cautious: never kicks the ball anywhere."""

    def get_ai_move(self, state, is_player_a):
        return 0, 0.0, 0.0


class RandomBot:
    """Random legal moves — your first realistic opponent."""

    def get_ai_move(self, state, is_player_a):
        pc = state.get("player_count", PLAYER_COUNT)
        player_idx = random.randint(0, pc - 1)
        angle = random.uniform(-180.0, 180.0)
        power = random.uniform(20.0, 100.0)
        return player_idx, angle, power


def resolve_opponent(target: str | None):
    """Return an opponent with a `get_ai_move(state, is_player_a)` method."""
    if target == "random":
        return RandomBot()
    if target == "do_nothing":
        return DoNothingBot()
    if target in BUILTIN_OPPONENTS:
        from services.game_analytics import _load_model
        return _load_model(target)
    raise ValueError(f"Unknown tutorial opponent: {target}")


#  Lesson catalog (single source of truth for learn.html) 

def _starter(code: str) -> str:
    return code.lstrip("\n")


LESSONS: list[dict] = [
    {
        "id": 1,
        "slug": "first-kick",
        "title": "Your First Kick",
        "icon": "",
        "tagline": "Learn the code contract and get a valid model onto the field.",
        "kind": "runs_error_free",
        "games": 1,
        "threshold": 1,
        "opponent": "do_nothing",
        "opponent_label": "a do-nothing opponent",
        "target_choice": None,
        "requires": [],
        "sections": [
            ("The deal", "Your model is a Python function that is called once per "
                          "turn and decides a kick. The game engine does the physics; "
                          "you do the thinking."),
            ("The contract",
             "Define exactly one function:\n\n"
             "    def get_ai_move(state, is_player_a):\n"
             "        ...\n"
             "        return player_idx, angle_degrees, power\n\n"
             "`state` tells you where everything is: `state[\"ball\"]` is "
             "`{\"x\": float, \"y\": float}`, `state[\"players_a\"]` and "
             "`state[\"players_b\"]` are lists of 3 players "
             "`{\"x\": float, \"y\": float}`, and `state[\"field\"]` gives the "
             "real field size. `is_player_a` is True when you are Team A.\n\n"
             "Return which of YOUR 3 players kicks (`0`, `1` or `2`), the kick "
             "direction in degrees (0 = right, 90 = down, 180 = left, 270 = up) "
             "and a power from 0 to 100."),
            ("How to check yourself",
             "The editor already has a working model. Press **Check milestone** "
             "and the server will run one full match against a do-nothing "
             "opponent. If your code runs every turn without erroring, the "
             "lesson is complete."),
        ],
        "milestone": "Your model runs a full match against a do-nothing opponent "
                     "without erroring (any result).",
        "hint": "The starter code already passes. Try reading it until it makes sense.",
        "starter": _starter('''
# Lesson 1 — Your First Kick
# The server calls get_ai_move every turn. Return (player_idx, angle_deg, power).

def get_ai_move(state, is_player_a):
    bx, by = state["ball"]["x"], state["ball"]["y"]
    players = state["players_a"] if is_player_a else state["players_b"]

    # Who kicks? The player closest to the ball.
    best_idx = min(range(len(players)), key=lambda i: math.hypot(
        players[i]["x"] - bx, players[i]["y"] - by))

    # Aim at the middle of the opponent's goal.
    goal_x = state["field"]["width"] if is_player_a else 0.0
    goal_y = state["field"]["height"] / 2
    angle = math.degrees(math.atan2(goal_y - by, goal_x - bx))

    return best_idx, angle, 80.0
'''),
    },
    {
        "id": 2,
        "slug": "beat-random",
        "title": "Beat the Random Bot",
        "icon": "",
        "tagline": "A random opponent only scores by luck — aim and finish.",
        "kind": "win_rate",
        "games": 5,
        "threshold": 3,
        "opponent": "random",
        "opponent_label": "the Random Bot",
        "target_choice": None,
        "requires": [1],
        "sections": [
            ("Why a Random Bot is beatable",
             "The Random Bot kicks with a random player, a random angle and a "
             "random power. It has no plan. A model that *always* sends the ball "
             "towards the goal will usually win — three of five matches and this "
             "lesson is done."),
            ("Reading the field from state",
             "Never hardcode 1400 or 875 — read `state[\"field\"]`. The opponent's "
             "goal is at `x = field.width` (Team A) or `x = 0` (Team B), spanning "
             "`y = 356` to `y = 519`."),
            ("Aiming at a single point",
             "`angle = degrees(atan2(target_y - by, target_x - bx))` points the "
             "kick straight at that target. Pick the player nearest the ball and "
             "send it at the goal centre with firm power (75–95)."),
            ("Stamina is random — consistency wins",
             "The Random Bot occasionally gets lucky and pokes it in. Don't "
             "chase clusters of short kicks; every turn should carry the ball "
             "forward and on target."),
        ],
        "milestone": "Win at least 3 of 5 matches against the Random Bot.",
        "hint": "The Lesson 1 starter already aims at goal centre — it usually "
                "passes this too. If it wobbles, add a touch more power.",
        "starter": _starter('''
# Lesson 2 — Beat the Random Bot
# Same plan as Lesson 1, but a little more power and a corner finish.

def get_ai_move(state, is_player_a):
    bx, by = state["ball"]["x"], state["ball"]["y"]
    players = state["players_a"] if is_player_a else state["players_b"]

    best_idx = min(range(len(players)), key=lambda i: math.hypot(
        players[i]["x"] - bx, players[i]["y"] - by))

    goal_x = state["field"]["width"] if is_player_a else 0.0
    goal_y = (356 + 519) / 2  # goal mouth centre — mid-goal is safer at range
    angle = math.degrees(math.atan2(goal_y - by, goal_x - bx))

    return best_idx, angle, 90.0
'''),
    },
    {
        "id": 3,
        "slug": "beat-greedy",
        "title": "Beat Greedy Striker",
        "icon": "",
        "tagline": "Greedy simulates kicks and aims at corners. Do the same, better.",
        "kind": "win_rate",
        "games": 5,
        "threshold": 3,
        "opponent": "greedy",
        "opponent_label": "Greedy Striker",
        "target_choice": None,
        "requires": [1, 2],
        "sections": [
            ("What Greedy does",
             "Greedy Striker is not dumb: it tests many angles with a physics "
             "simulation and picks the kick that gets the ball furthest toward "
             "the goal, rewarding shots that finish inside the goal mouth "
             "(y 356–519). It has no defensive care — it only attacks."),
            ("Attack the far corner when close",
             "From close range, shoot at a corner (y ≈ 365 or 510) instead of "
             "the centre — the keeper-side net is bigger angle-wise and Greedy's "
             "simulation doesn't defend for itself."),
            ("Pick the best player, not just the closest",
             "Iterate every player, estimate each kick's quality, and choose the "
             "best. A simple score: how much closer the ball would land to the "
             "goal than its current position."),
            ("Keep it deterministic",
             "Greedy is deterministic: the same state always gets the same kick. "
             "A deterministic strategy of yours is easier to reason about and "
             "reproduces wins."),
        ],
        "milestone": "Win at least 3 of 5 matches against Greedy Striker.",
        "hint": "The built-ins use `aim_through` — draw a line from your player "
                "through the ball toward a corner. That close-to-corner shot "
                "is the anti-Greedy move.",
        "starter": _starter('''
# Lesson 3 — Beat Greedy Striker
# Evaluate each of your players and chase the closest corner.

def get_ai_move(state, is_player_a):
    bx, by = state["ball"]["x"], state["ball"]["y"]
    players = state["players_a"] if is_player_a else state["players_b"]
    goal_x = state["field"]["width"] if is_player_a else 0.0

    def line_angle(px, py, tx, ty):
        return math.degrees(math.atan2(ty - py, tx - px))

    # Two corners + centre of the opponent goal.
    targets = [(goal_x, 365.0), (goal_x, 510.0), (goal_x, (356 + 519) / 2)]

    best = None
    for i, p in enumerate(players):
        dist = math.hypot(p["x"] - bx, p["y"] - by)
        for tx, ty in targets:
            angle = line_angle(p["x"], p["y"], tx, ty)
            # Rough quality: closer player + more central aim chart = better.
            score = -dist + abs(ty - (356 + 519) / 2)
            if best is None or score > best[0]:
                best = (score, i, angle)
    _, player_idx, angle = best
    return player_idx, angle, 90.0
'''),
    },
    {
        "id": 4,
        "slug": "beat-stochastic",
        "title": "Beat Monte Carlo (or Bayesian)",
        "icon": "",
        "tagline": "Randomised opponents are sloppy. A steady, robust plan wins.",
        "kind": "win_rate",
        "games": 5,
        "threshold": 3,
        "opponent": None,
        "opponent_label": "Monte Carlo / Bayesian",
        "target_choice": ["monte_carlo", "bayesian"],
        "requires": [1, 2, 3],
        "sections": [
            ("Choose your target",
             "Monte Carlo samples angles randomly around the corners; Bayesian "
             "weights a fine sweep with a bell-curve prior. Both are probabilistic "
             "— occasionally they pass up a sure thing."),
            ("Play the percentages",
             "Their randomness means they sometimes kick too softly or into "
             "traffic. If *your* model makes the same high-quality kick every "
             "turn — near the corners, firm power — you win the law-of-large-"
             "numbers war."),
            ("Defend the danger zone",
             "When the ball is inside your own 250px, clear it: kick it hard "
             "toward the opponent half. Letting the ball sit near your goal hands "
             "the opponent free chances."),
            ("Deterministic beats random over 5 games",
             "A repeatable plan converts the opponent's bad rolls into your "
             "goals. After this lesson you can take on either target."),
        ],
        "milestone": "Win at least 3 of 5 matches against Monte Carlo OR Bayesian.",
        "hint": "Add a danger-zone clear: if `is_player_a` and the ball is left of "
                "x=250 (or mirrored), kick hard toward midfield instead of attacking.",
        "starter": _starter('''
# Lesson 4 — Beat Monte Carlo (or Bayesian)
# A robust attacker that also clears its own danger zone.

DANGER = 250  # within this many px of your own goal line, clear it

def get_ai_move(state, is_player_a):
    bx, by = state["ball"]["x"], state["ball"]["y"]
    players = state["players_a"] if is_player_a else state["players_b"]
    goal_x = state["field"]["width"] if is_player_a else 0.0

    in_danger = bx < DANGER if is_player_a else bx > state["field"]["width"] - DANGER
    if in_danger:
        # Clear toward midfield, hard.
        clear_x = state["field"]["width"] / 2 if is_player_a else state["field"]["width"] / 2
        clear_y = state["field"]["height"] / 2
        p = min(range(len(players)), key=lambda i: math.hypot(players[i]["x"] - bx,
                                                              players[i]["y"] - by))
        return p, math.degrees(math.atan2(clear_y - by, clear_x - bx)), 95.0

    # Attack: nearest player, corner finish.
    p = min(range(len(players)), key=lambda i: math.hypot(players[i]["x"] - bx,
                                                          players[i]["y"] - by))
    target_y = 365.0 if abs(by - 437.5) < 60 else 510.0
    angle = math.degrees(math.atan2(target_y - by, goal_x - bx))
    return p, angle, 88.0
'''),
    },
    {
        "id": 5,
        "slug": "beat-minimax",
        "title": "Beat Minimax",
        "icon": "",
        "tagline": "Minimax searches hard and punishes deflections. Out-plan it.",
        "kind": "win_rate",
        "games": 5,
        "threshold": 3,
        "opponent": "minimax",
        "opponent_label": "Minimax",
        "target_choice": None,
        "requires": [1, 2, 3, 4],
        "sections": [
            ("What Minimax does",
             "Minimax tries the densest grid of angles and powers against full "
             "physics, rewarding goals hugely (1500) and penalising own-goals "
             "(-500). It is almost the best pure attacker in the game."),
            ("Two weaknesses to exploit",
             "1) It *penalises* shots that deflect off players (direction "
             "reversals cost 20), so in traffic it will pass up angles you can "
             "take. 2) It picks the single best simulated kick — a model that "
             "steadily feeds the ball into the box is more consistent than one "
             "that occasionally finds a perfect simulation result."),
            ("Feed the box, finish hard",
             "Keep the ball moving forward every turn and finish with max power "
             "once you're inside the mouth range. Minimax's penalty for bouncing "
             "off bodies makes it shun the very shots you can squeeze through."),
            ("Expect a fight",
             "Back-to-back corners, big power at range, and never kicking "
             "backwards. Five matches is a long series — every goal counts."),
        ],
        "milestone": "Win at least 3 of 5 matches against Minimax.",
        "hint": "Aim slightly through the ball (line from player through ball to "
                "corner) and prefer the player with the clearest path to goal.",
        "starter": _starter('''
# Lesson 5 — Beat Minimax
# Deterministic corners + line-of-sight player pick + maximum finish.

def get_ai_move(state, is_player_a):
    bx, by = state["ball"]["x"], state["ball"]["y"]
    players = state["players_a"] if is_player_a else state["players_b"]
    goal_x = state["field"]["width"] if is_player_a else 0.0

    targets = [(goal_x, 365.0), (goal_x, 510.0)]

    best = None
    for i, p in enumerate(players):
        for tx, ty in targets:
            # Aim line from player -> ball -> target (shot through traffic).
            dx, dy = tx - bx, ty - by
            length = math.hypot(dx, dy) or 1.0
            aim_x = bx + dx / length * 200
            aim_y = by + dy / length * 200
            angle = math.degrees(math.atan2(aim_y - p["y"], aim_x - p["x"]))
            dist = math.hypot(p["x"] - bx, p["y"] - by)
            if best is None or dist < best[0]:
                best = (dist, i, angle)
    _, player_idx, angle = best
    return player_idx, angle, 98.0
'''),
    },
    {
        "id": 6,
        "slug": "read-the-stats",
        "title": "Read the Stats (optional)",
        "icon": "",
        "tagline": "Players aren't identical. Size, Power, Weight and Agility change everything.",
        "kind": "win_rate",
        "games": 5,
        "threshold": 3,
        "opponent": "value_iteration",
        "opponent_label": "Value Iteration",
        "target_choice": None,
        "requires": [5],
        "optional": True,
        "stats_inject": _L6_STATS,
        "sections": [
            ("Every player carries stats",
             "Each player dict has a `stats` key: "
             "`{size, power, weight, agility}` (0–100). In this lesson the "
             "striker (index 2) has Power 85 — it strikes harder and covers more "
             "ground per kick. The built-ins already weigh stats; now you will."),
            ("Power = reach",
             "Higher Power means a farther, harder kick. When the ball sits far "
             "from goal but your Power player can still reach it, let that player "
             "shoot instead of the nearest (weak) teammate."),
            ("Pick the kicker by reachability",
             "Score each player with something like "
             "`distance_to_ball / (power/50)` — a Power-85 player is 'closer' "
             "than a Power-50 player at the same spot. Then aim at the corner."),
            ("Why optional",
             "You can finish the curriculum without this lesson (the capstone "
             "does not require it). But models that use stats genuinely play "
             "differently from ones that ignore them."),
        ],
        "milestone": "Win at least 3 of 5 matches against Value Iteration with "
                     "the stats build injected.",
        "hint": "`state[\"players_a\"][i].get(\"stats\", {})` gives Size/Power/"
                "Weight/Agility. Weight raises mass (harder to shove), Agility "
                "raises control (less drift after a kick).",
        "starter": _starter('''
# Lesson 6 (optional) — Read the Stats
# Pick your kicker by reachability, then finish at the corners.

def reach(player, bx, by):
    power = (player.get("stats") or {}).get("power", 50)
    dist = math.hypot(player["x"] - bx, player["y"] - by)
    return dist / max(0.5, power / 50.0)

def get_ai_move(state, is_player_a):
    bx, by = state["ball"]["x"], state["ball"]["y"]
    players = state["players_a"] if is_player_a else state["players_b"]
    goal_x = state["field"]["width"] if is_player_a else 0.0

    # Lowest reachability = best kicker right now.
    player_idx = min(range(len(players)), key=lambda i: reach(players[i], bx, by))
    p = players[player_idx]

    target_y = 365.0 if abs(by - 437.5) < 80 else 510.0
    angle = math.degrees(math.atan2(target_y - by, goal_x - p["x"]))
    return player_idx, angle, 95.0
'''),
    },
    {
        "id": 7,
        "slug": "capstone",
        "title": "Capstone: Into the Arena",
        "icon": "",
        "tagline": "Prove your model against every built-in and rank it publicly.",
        "kind": "leaderboard_submit",
        "games": 0,
        "threshold": 1,
        "opponent": None,
        "opponent_label": "the leaderboard benchmark",
        "target_choice": None,
        "requires": [5],
        "sections": [
            ("The final challenge",
             "Save your best model to **My Models** (the Save button is in the "
             "playground and My Models), then open its card and press **Submit to "
             "Leaderboard**. That runs a fair 7-opponent benchmark in the "
             "background."),
            ("What you have learned",
             "You can now: read the match state, pick a kicker, aim at goal "
             "corners, defend your danger zone, and reason about your opponents' "
             "search styles. That is the whole arc of building a soccer AI here."),
            ("Marking the capstone",
             "This lesson completes as soon as you have a model submitted to the "
             "leaderboard. Your rank waits for weeks of other builders — the "
             "benchmark itself is the graduation test."),
        ],
        "milestone": "Have a model submitted to the public leaderboard.",
        "hint": "Submission is one click on any model card in My Models. Your "
                "scored model appears on the leaderboard automatically.",
        "starter": None,
    },
]

_LESSON_BY_ID = {l["id"]: l for l in LESSONS}


def get_lesson(lesson_id: int) -> dict | None:
    return _LESSON_BY_ID.get(lesson_id)


def unlock_state(completed: set[int] | dict) -> dict[int, str]:
    """Map every lesson id to 'locked' | 'unlocked' | 'completed'."""
    done = set(completed) if isinstance(completed, dict) else set(completed or ())
    result: dict[int, str] = {}
    for lesson in LESSONS:
        lid = lesson["id"]
        if lid in done:
            result[lid] = "completed"
        elif all(req in done for req in lesson["requires"]):
            result[lid] = "unlocked"
        else:
            result[lid] = "locked"
    return result


def is_unlocked(lesson_id: int, completed: set[int] | dict) -> bool:
    lesson = get_lesson(lesson_id)
    if lesson is None:
        return False
    done = set(completed) if isinstance(completed, dict) else set(completed or ())
    return all(req in done for req in lesson["requires"])


#  Headless milestone check 

def _run_one_match(code: str, opponent, lesson: dict) -> tuple[str | None, dict]:
    """Play one match. Returns (error, result); error != None on a failed run."""
    stats_inject = lesson.get("stats_inject")
    st = new_soccer_state(mode="tutorial", model_a="user_model",
                          model_b=lesson["opponent"] or "tutorial_bot",
                          player_count=PLAYER_COUNT)
    st["move_history"] = []
    st["kick_count"] = 0
    if stats_inject:
        inject_player_stats(st, stats_inject, stats_inject)

    for _ in range(MAX_KICKS):
        if st.get("game_over"):
            break
        is_a = st["is_player_a"]
        try:
            if is_a:
                player_idx, angle, power = execute_user_model(code, st, True)
            else:
                player_idx, angle, power = opponent.get_ai_move(st, False)
        except Exception as exc:  # noqa: BLE001 — user code errors must fail the check
            return f"Your model errored: {exc}", {}
        try:
            apply_kick(st, player_idx, angle, power, is_a)
        except Exception:  # noqa: BLE001
            return "The match hit a physics error — please try again.", {}

    winner = st.get("winner", "Draw")
    return None, {
        "winner": winner,
        "score_a": st.get("score_a", 0),
        "score_b": st.get("score_b", 0),
        "kicks": st.get("kick_count", 0),
        "game_over": bool(st.get("game_over")),
    }


def run_milestone_check(code: str, lesson_id: int, target: str | None = None) -> dict:
    """Evaluate a lesson milestone. Pure orchestration — no persistence here.

    Returns a summary dict:
      {passed, kind, wins, losses, draws, games, target, error, results, elapsed_s}
    For `kind == "leaderboard_submit"` this returns passed=False with a hint
    (the app layer answers that milestone from the leaderboard store).
    """
    elapsed_s = time.time()
    lesson = get_lesson(lesson_id)
    if lesson is None:
        return {"passed": False, "kind": "unknown", "error": "Unknown lesson."}

    if lesson["kind"] == "leaderboard_submit":
        return {"passed": False, "kind": lesson["kind"], "error": None,
                "games": 0, "wins": 0, "losses": 0, "draws": 0,
                "results": [], "elapsed_s": 0}

    ok, msg = validate_code(code)
    if not ok:
        return {"passed": False, "kind": lesson["kind"], "error": f"Code error: {msg}",
                "games": 0, "wins": 0, "losses": 0, "draws": 0,
                "results": [], "elapsed_s": time.time() - elapsed_s}

    # Opponent: hardcoded or user's choice for the 2-way lesson.
    opponent_id = lesson["opponent"]
    if lesson.get("target_choice"):
        if target not in lesson["target_choice"]:
            return {"passed": False, "kind": lesson["kind"],
                    "error": f"Pick a target: {', '.join(lesson['target_choice'])}.",
                    "games": 0, "wins": 0, "losses": 0, "draws": 0,
                    "results": [], "elapsed_s": time.time() - elapsed_s}
        opponent_id = target
    try:
        opponent = resolve_opponent(opponent_id or "do_nothing")
    except ValueError as exc:
        return {"passed": False, "kind": lesson["kind"], "error": str(exc),
                "games": 0, "wins": 0, "losses": 0, "draws": 0,
                "results": [], "elapsed_s": time.time() - elapsed_s}

    n = lesson["games"]
    wins = losses = draws = 0
    results: list[dict] = []
    for _ in range(max(1, n)):
        error, res = _run_one_match(code, opponent, lesson)
        if error:
            return {"passed": False, "kind": lesson["kind"], "error": error,
                    "games": n, "wins": wins, "losses": losses, "draws": draws,
                    "results": results, "elapsed_s": time.time() - elapsed_s}
        results.append(res)
        w = res["winner"]
        if w == "A":
            wins += 1
        elif w == "B":
            losses += 1
        else:
            draws += 1

    if lesson["kind"] == "runs_error_free":
        passed = True
    else:
        passed = wins >= max(1, lesson["threshold"])

    return {
        "passed": passed,
        "kind": lesson["kind"],
        "games": n,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "target": opponent_id,
        "results": results,
        "elapsed_s": round(time.time() - elapsed_s, 1),
    }