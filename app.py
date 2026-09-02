"""app.py — Soccer AI Platform (Flask)"""
from __future__ import annotations
import importlib
import threading
import time
import logging
from functools import wraps

from flask import (
    Flask, jsonify, redirect, render_template,
    request, session, url_for, flash,
)

from config import SECRET_KEY, SITE_URL
from game.session import (
    get_game, save_game, new_game_state, push_snapshot, pop_snapshot,
    new_pg_state, get_pg, save_pg,
)
from models.soccer_logic import apply_kick, apply_penalty_kick, _setup_penalty_positions

#  Mem0-style memory (short/long) 
try:
    from services.memory import add_short_game as _mem_short, add_long_preference as _mem_long, summarize_match_to_long as _mem_summ, search as _mem_search, get_all as _mem_all
    _MEM_ENABLED=True
except Exception:
    _MEM_ENABLED=False
    def _mem_short(*a,**k): return []
    def _mem_long(*a,**k): return []
    def _mem_summ(*a,**k): return []
    def _mem_search(*a,**k): return {"results":[]}
    def _mem_all(*a,**k): return []

#  External service initialisation 
from services.sentry import init as _sentry_init
_sentry_init()

import services.posthog as _ph

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
_dev_mode = __import__("config").DEV_MODE
if not _dev_mode:
    app.config["SESSION_COOKIE_SECURE"] = True


@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if not _dev_mode:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    # CDN cache for versioned static (Three.js, workflow.png, design-system.css) — 1y immutable
    if request.path.startswith("/static/"):
        if request.path.startswith("/static/vendor/") or request.path.endswith(".png"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            resp.headers["Cache-Control"] = "public, max-age=3600, must-revalidate"
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    # Backpressure hint
    resp.headers.setdefault("X-RateLimit-Remaining", "100")
    return resp


@app.route("/health")
def health():
    # liveness — no DB
    return jsonify({"ok": True, "ts": time.time()})


@app.route("/ready")
def ready():
    # readiness — checks Redis + Supabase (best-effort)
    ok = True
    checks = {}
    try:
        from db.redis_client import r as _r
        _r.ping() if hasattr(_r, "ping") else _r.get("health:check")
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"fail: {e}"
        ok = False
    try:
        from db.supabase_client import service as _svc
        checks["supabase"] = "ok" if _svc else "not_configured"
    except Exception as e:
        checks["supabase"] = f"fail: {e}"
    return jsonify({"ok": ok, "checks": checks}), (200 if ok else 503)


@app.before_request
def _presence_heartbeat():
    if "user_id" in session and request.path not in ("/static/workflow.png",):
        try:
            from db.friends import heartbeat as _hb_presence
            _hb_presence(session["user_id"])
        except Exception:
            pass

MODELS: dict[str, str] = {
    "minimax":          "models.minimax",
    "monte_carlo":      "models.monte_carlo",
    "greedy":           "models.greedy_model",
    "bayesian":         "models.bayes",
    "value_iteration":  "models.value_iteration",
    "policy_iteration": "models.policy_iteration",
    "q_learning":       "models.q_learning",
    "langchain":        "models.langchain_model",
    "tactic_transformer": "models.tactic_transformer",
    "graph_gnn":        "models.graph_gnn",
    "ppo_actor_critic": "models.ppo_actor_critic",
    "dqn_relative":     "models.dqn_relative",
    "genetic_fuzzy":    "models.genetic_fuzzy",
}

# AI off-thread pool so slow simulate_kick (2-3s) does not block gunicorn worker
from concurrent.futures import ThreadPoolExecutor as _AIPool
_ai_pool = _AIPool(max_workers=4, thread_name_prefix="ai")

_builtin_cache: dict = {}
USER_MODEL_PREFIX = "user_model:"


class _UserModelWrapper:
    def __init__(self, model_id: str, name: str, code: str):
        self._id   = model_id
        self._code = code
        self.MODEL_NAME  = name
        self.DESCRIPTION = "User-uploaded model"

    def get_ai_move(self, state, is_player_a):
        from user_models.runner import execute_user_model
        return execute_user_model(self._code, state, is_player_a)


def _load_model(name: str):
    if name.startswith(USER_MODEL_PREFIX):
        return _load_user_model(name[len(USER_MODEL_PREFIX):])
    if name not in _builtin_cache:
        mod = importlib.import_module(MODELS[name])
        if hasattr(mod, "init_policy"):
            threading.Thread(target=mod.init_policy, daemon=True).start()
        _builtin_cache[name] = mod
    return _builtin_cache[name]


def _load_user_model(model_id: str) -> _UserModelWrapper:
    from flask import g
    cache = getattr(g, "_um_cache", {}) or {}
    if model_id not in cache:
        from db.user_models import get_model_by_id
        data = get_model_by_id(model_id, requesting_user_id=uid())
        if not data:
            raise ValueError(f"User model '{model_id}' not found or access denied.")
        cache[model_id] = _UserModelWrapper(model_id, data["name"], data["code"])
        g._um_cache = cache
    return cache[model_id]


#  Rate limiter (Redis-backed) 

_RATE_LIMITS: dict[str, tuple[int, int]] = {
    "/auth/login":    (5, 60),     # 5 requests per 60 seconds
    "/auth/register": (3, 300),    # 3 registrations per 5 minutes
    "/api/auth/forgot-password": (3, 300),   # 3 reset emails per 5 minutes
    "/api/auth/reset-password":  (10, 300),  # token-gated, but still bounded
    "/api/feedback/about": (5, 300),  # 5 about feedbacks per 5 minutes
}


def _check_rate_limit(endpoint: str) -> bool:
    try:
        from db.redis_client import r
        key = f"ratelimit:{endpoint}:{uid() or request.remote_addr}"
        count = r.get(key)
        max_req, window = _RATE_LIMITS.get(endpoint, (100, 1))
        if count and int(count) >= max_req:
            return False
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, window)
        pipe.execute()
        return True
    except Exception:
        return True


def rate_limited(f):
    @wraps(f)
    def _inner(*args, **kwargs):
        if not _check_rate_limit(request.path):
            return jsonify({"error": "Too many requests. Try again later."}), 429
        return f(*args, **kwargs)
    return _inner


def login_required(f):
    @wraps(f)
    def _inner(*args, **kwargs):
        if "user_id" not in session:
            if request.is_json:
                return jsonify({"error": "Not authenticated"}), 401
            return redirect(url_for("login_page"))
        _ph.track_pageview(session["user_id"], request.path)
        return f(*args, **kwargs)
    return _inner


def uid() -> str:
    return session.get("user_id", "")


def _full_state(state: dict, extra: dict | None = None) -> dict:
    result = {
        "ball":         state["ball"],
        "players_a":    state["players_a"],
        "players_b":    state["players_b"],
        "score_a":      state["score_a"],
        "score_b":      state["score_b"],
        "is_player_a":  state["is_player_a"],
        "kick_count":   state.get("kick_count", 0),
        "start_time":   state.get("start_time", 0),
        "turn_start_time": state.get("turn_start_time", 0),
        "game_over":    state.get("game_over", False),
        "winner":       state.get("winner"),
        "game_mode":    state.get("game_mode", "hvai"),
        "player_count": state.get("player_count", 3),
        "period":       state.get("period", "regular_first"),
        "penalty_shootout": state.get("penalty_shootout", False),
        "penalty_kick_num": state.get("penalty_kick_num", 0),
        "penalty_a_score":  state.get("penalty_a_score", 0),
        "penalty_b_score":  state.get("penalty_b_score", 0),
        "penalty_kicks":    state.get("penalty_kicks", []),
        "ai_model":     state.get("model_name_b", "greedy"),
        "ai_model_a":   state.get("model_name_a", "greedy"),
        "move_history": state.get("move_history", []),
        "referee":     state.get("referee", {"x": 400.0, "y": 420.0}),
        "referee_name": state.get("referee_name"),
        "team_a":      state.get("team_a"),
        "team_a_name": state.get("team_a_name"),
        "team_a_crest": state.get("team_a_crest"),
        "manager_a":   state.get("manager_a"),
        "formation_a": state.get("formation_a"),
        "team_b":      state.get("team_b"),
        "team_b_name": state.get("team_b_name"),
        "team_b_crest": state.get("team_b_crest"),
        "manager_b":   state.get("manager_b"),
        "formation_b": state.get("formation_b"),
    }
    if extra:
        result.update(extra)
    if state.get("game_over") and state.get("game_mode") == "hvai" and not state.get("_finalized"):
        _persist_result(state)
        state["_finalized"] = True
    result["achievements"] = _ach_toasts()
    return result


def _persist_result(state: dict) -> None:
    try:
        from db.games import save_game_result
        winner = state.get("winner", "Draw")
        save_game_result(
            user_id    = uid(),
            mode       = state.get("game_mode", "hvai"),
            ai_model   = state.get("model_name_b", "greedy"),
            winner     = winner,
            score_a    = state["score_a"],
            score_b    = state["score_b"],
            total_moves= state.get("kick_count", 0),
        )
        _ph.track_game_end(uid(), state.get("game_mode", "hvai"), winner, state["score_a"], state["score_b"])
        _check_casual_achievements(state, uid())
        _check_progress_achievements(uid())
        try: _mem_summ(uid(), state)
        except: pass
        _auto_clear_state(uid())
    except Exception as e:
        app.logger.warning("Failed to save game result: %s", e)


#  Achievements (badges) — detection helpers 
# Award hooks live at natural completion points: ranked result application,
# casual match persist, online game-over, goal moments, bench completion,
# tournament create/final, avatar upload, model create, match start (scene).

def _ach_grant(user_id: str, achievement_key: str) -> None:
    """Fire-and-forget badge grant (double-award guarded inside db.achievements)."""
    if not user_id or user_id.startswith("guest:"):
        return
    try:
        from db.achievements import award
        award(user_id, achievement_key)
    except Exception:
        pass


def _ach_toasts() -> list[dict]:
    """Drain this session user's pending badge toasts for the response body."""
    try:
        from db.achievements import drain_toasts
        toasts = drain_toasts(uid())
        try:
            from db.customization import COSMETIC_REWARDS
            for t in toasts:
                rewards = COSMETIC_REWARDS.get(t.get("key"))
                if rewards:
                    t["unlock"] = rewards
        except Exception:
            pass
        return toasts
    except Exception:
        return []


def _hat_trick_side(moves: list) -> str | None:
    """'A'/'B' if that team has a player with 3+ own-team goals, else None."""
    counts: dict[tuple, int] = {}
    for m in moves or []:
        if not m.get("scored") or m.get("scored") != m.get("player"):
            continue
        key = (m.get("player"), m.get("player_idx"))
        counts[key] = counts.get(key, 0) + 1
        if counts[key] >= 3:
            return m.get("player")
    return None


def _extreme_build(stats_list) -> bool:
    """True if every player has exactly one stat >= 60 and the rest <= 40."""
    if not stats_list:
        return False
    for st in stats_list:
        vals = [int(st.get(k, 50)) for k in ("size", "power", "weight", "agility")]
        if sum(1 for v in vals if v >= 60) != 1 or sum(1 for v in vals if v <= 40) != 3:
            return False
    return True


def _goal_moment_achievements(trajectory: list) -> None:
    """Goal-moment badges for the session user (first goal / rocket / loft)."""
    if not trajectory:
        return
    _ach_grant(uid(), "exp_first_goal")
    try:
        from db.highlights import max_speed
        if max_speed(trajectory) >= 700:
            _ach_grant(uid(), "sk_rocket")
        apex = max((p.get("z", 0.0) for p in trajectory), default=0.0)
        if apex >= 40:
            _ach_grant(uid(), "sk_loft")
    except Exception:
        pass


def _check_casual_achievements(state: dict, user_id: str) -> None:
    """Match-end badges for a finished hvai (human vs AI) game."""
    winner = state.get("winner")
    score_a = state.get("score_a", 0)
    score_b = state.get("score_b", 0)
    if _hat_trick_side(state.get("move_history", [])):
        _ach_grant(user_id, "sk_pan_trick")
    if winner == "A":
        if score_b == 0:
            _ach_grant(user_id, "sk_clean_sheet")
        if score_a - score_b >= 5:
            _ach_grant(user_id, "sk_big_win")
        if _extreme_build((state.get("player_stats") or {}).get("a")):
            _ach_grant(user_id, "sk_extreme_build")


def _check_online_achievements(room: dict, game: dict) -> None:
    """Match-end badges for a finished online (hvh) game — both participants."""
    pa, pb = room.get("player_a"), room.get("player_b")
    if not pa or not pb or pa.startswith("guest:") or pb.startswith("guest:"):
        return
    winner = game.get("winner")
    sa = game.get("score_a", 0)
    sb = game.get("score_b", 0)
    ht = _hat_trick_side(game.get("move_history", []))
    if ht == "A":
        _ach_grant(pa, "sk_pan_trick")
    elif ht == "B":
        _ach_grant(pb, "sk_pan_trick")
    if winner == "A":
        if sb == 0:
            _ach_grant(pa, "sk_clean_sheet")
        if sa - sb >= 5:
            _ach_grant(pa, "sk_big_win")
        if _extreme_build((game.get("player_stats") or {}).get("a")):
            _ach_grant(pa, "sk_extreme_build")
    elif winner == "B":
        if sa == 0:
            _ach_grant(pb, "sk_clean_sheet")
        if sb - sa >= 5:
            _ach_grant(pb, "sk_big_win")
        if _extreme_build((game.get("player_stats") or {}).get("b")):
            _ach_grant(pb, "sk_extreme_build")
    if _is_friends_with(pa, pb):
        _ach_grant(pa, "friend_match")
        _ach_grant(pb, "friend_match")


def _check_ranked_achievements(pa: str, pb: str, res: dict) -> None:
    """Ranked badges for both participants after a result is recorded."""
    if not res:
        return
    try:
        from db.ranked import get_rating, get_win_streak
    except Exception:
        return
    winner = res.get("winner")
    for user_id, side in ((pa, "a"), (pb, "b")):
        if not user_id or user_id.startswith("guest:"):
            continue
        row = get_rating(user_id)
        games = int(row.get("games_played", 0))
        wins = int(row.get("wins", 0))
        rating = int(row.get("rating", 1200))
        won = winner == side.upper()
        conceded = res.get("score_a" if side == "b" else "score_b", 0)
        for key, need in (("rk_game_10", 10), ("rk_game_50", 50), ("rk_game_100", 100)):
            if games >= need:
                _ach_grant(user_id, key)
        for key, need in (("rk_rating_1400", 1400), ("rk_rating_1600", 1600),
                          ("rk_rating_1800", 1800)):
            if rating >= need:
                _ach_grant(user_id, key)
        if won and wins == 1:
            _ach_grant(user_id, "rk_first_win")
        if won and conceded == 0:
            _ach_grant(user_id, "rk_clean_sheet")
        streak = get_win_streak(user_id)
        for key, need in (("rk_streak_3", 3), ("rk_streak_5", 5), ("rk_streak_10", 10)):
            if streak >= need:
                _ach_grant(user_id, key)


def _check_progress_achievements(user_id: str) -> None:
    if not user_id or user_id.startswith("guest:"):
        return
    try:
        from db.games import get_user_stats
        stats = get_user_stats(user_id)
        gp = int(stats.get("games_played", 0))
        gf = int(stats.get("goals_for", 0))
        w = int(stats.get("wins", 0))
        for key, need in (("exp_games_50", 50), ("exp_games_100", 100), ("exp_games_200", 200), ("exp_games_500", 500)):
            if gp >= need:
                _ach_grant(user_id, key)
        for key, need in (("exp_goals_50", 50), ("exp_goals_100", 100), ("exp_goals_250", 250)):
            if gf >= need:
                _ach_grant(user_id, key)
        for key, need in (("exp_wins_25", 25), ("exp_wins_50", 50), ("exp_wins_100", 100)):
            if w >= need:
                _ach_grant(user_id, key)
    except Exception:
        pass


def _check_social_achievements(user_id: str) -> None:
    if not user_id or user_id.startswith("guest:"):
        return
    try:
        from db.friends import list_friends
        cnt = len(list_friends(user_id))
        for key, need in (("social_friends_5", 5), ("social_friends_10", 10)):
            if cnt >= need:
                _ach_grant(user_id, key)
    except Exception:
        pass
    try:
        from db.redis_client import r as redis
        hist = redis.smembers(f"clan_history:{user_id}") if hasattr(redis, "smembers") else set()
        # normalize bytes
        hist = {h.decode() if isinstance(h, bytes) else h for h in hist}
        if len(hist) >= 3:
            _ach_grant(user_id, "clan_joined_3")
        from db.clans import list_clans
        created = [c for c in list_clans() if c.get("leader_id") == user_id]
        if len(created) >= 2:
            _ach_grant(user_id, "clan_created_2")
    except Exception:
        pass


def _check_ai_count_achievements(user_id: str) -> None:
    if not user_id or user_id.startswith("guest:"):
        return
    try:
        from db.user_models import get_user_models
        cnt = len(get_user_models(user_id))
        for key, need in (("ai_models_5", 5), ("ai_models_10", 10)):
            if cnt >= need:
                _ach_grant(user_id, key)
    except Exception:
        pass


def _track_scene_usage(user_id: str) -> None:
    """Record the active scene preset at match start (Night-Owl badge, set-based)."""
    try:
        from db.customization import get_customization
        from db.redis_client import r as redis
        scene = (get_customization(user_id).get("bg_scene") or "night")
        key = f"ach:scenes:{user_id}"
        redis.sadd(key, scene)
        redis.expire(key, 7 * 86400)
        if len(redis.smembers(key)) >= 4:
            _ach_grant(user_id, "exp_sense4")
    except Exception:
        pass


def _model_rank(model_id: str) -> int | None:
    """1-based rank of a model on the score-sorted model leaderboard."""
    try:
        from db.leaderboard import list_leaderboard
        entries, _total = list_leaderboard(limit=200, offset=0, sort="score")
        for i, e in enumerate(entries):
            if e.get("model_id") == model_id:
                return i + 1
    except Exception:
        pass
    return None


def _apply_move(state: dict, player_idx: int, angle: float, power: float, is_player_a: bool) -> dict:
    push_snapshot(state)
    trajectory, scored, desc, kick_endpoint, push_result = apply_kick(state, player_idx, angle, power, is_player_a)
    return {
        "trajectory":    trajectory,
        "scored":        scored,
        "desc":          desc,
        "player_idx":    player_idx,
        "angle":         round(angle, 1),
        "power":         round(power, 1),
        "kick_endpoint": kick_endpoint,
        "push_result":   push_result,
    }


def _do_penalty_ai(state: dict, model_name: str, is_player_a: bool) -> dict:
    """AI takes its penalty kick or chooses goalkeeper direction."""
    import random
    if state.get("penalty_goalkeeper_move") is None:
        state["penalty_goalkeeper_move"] = random.choice(["left", "center", "right"])
        return {"goalkeeper_move": state["penalty_goalkeeper_move"]}
    model = _load_model(model_name)
    player_idx, angle, power = model.get_ai_move(state, is_player_a)
    result = _apply_move(state, player_idx, angle, power, is_player_a)
    # Redo as penalty
    traj, scored, desc = apply_penalty_kick(state, player_idx, angle, power, is_player_a)
    return {
        "trajectory": traj,
        "scored": scored,
        "desc": desc,
        "player_idx": player_idx,
        "angle": round(angle, 1),
        "power": round(power, 1),
        "kick_endpoint": traj[-1].get("kicker", {"x": traj[-1]["x"], "y": traj[-1]["y"]}),
        "push_result": None,
    }


def _get_ai_move_with_timeout(model, state, is_player_a, timeout=2.0):
    """Run get_ai_move with 2s hard limit, fallback to greedy on timeout."""
    try:
        fut = _ai_pool.submit(model.get_ai_move, state, is_player_a)
        return fut.result(timeout=timeout)
    except Exception as e:
        # Timeout or error -> fallback to greedy (fast, <150ms)
        try:
            fallback = _load_model("greedy")
            return fallback.get_ai_move(state, is_player_a)
        except Exception:
            raise e

def _do_ai_move(state: dict, model_name: str, is_player_a: bool) -> dict:
    model = _load_model(model_name)
    t0    = time.time()
    try:
        player_idx, angle, power = _get_ai_move_with_timeout(model, state, is_player_a, timeout=2.0)
        timed_out = False
    except Exception:
        # final fallback
        m2 = _load_model("greedy")
        player_idx, angle, power = m2.get_ai_move(state, is_player_a)
        timed_out = True
    elapsed = round((time.time() - t0) * 1000)
    # enforce 2s cap on reported think time
    elapsed = min(elapsed, 2000)
    result  = _apply_move(state, player_idx, angle, power, is_player_a)
    result["think_ms"] = elapsed
    if timed_out or elapsed >= 1950:
        result["timeout_fallback"] = True
    return result


@app.route("/login")
def login_page():
    if "user_id" in session:
        return redirect(url_for("index"))
    from config import CLERK_PUBLISHABLE_KEY, DEV_MODE
    return render_template("login.html", clerk_publishable_key=CLERK_PUBLISHABLE_KEY, dev_mode=DEV_MODE)


@app.route("/register")
def register_page():
    if "user_id" in session:
        return redirect(url_for("index"))
    from config import CLERK_PUBLISHABLE_KEY
    return render_template("register.html", clerk_publishable_key=CLERK_PUBLISHABLE_KEY)


@app.route("/auth/register", methods=["POST"])
@rate_limited
def auth_register():
    from db.supabase_client import anon, service
    data       = request.get_json(silent=True) or request.form
    email      = (data.get("email") or "").strip().lower()
    password   = data.get("password") or ""
    confirm    = data.get("confirm") or ""
    username   = (data.get("username") or "").strip()

    def _err(msg):
        if request.is_json:
            return jsonify({"error": msg}), 400
        flash(msg, "error")
        return redirect(url_for("register_page"))

    if not email or not password or not username:
        return _err("Email, username and password are required.")
    if password != confirm:
        return _err("Passwords do not match.")
    if len(password) < 6:
        return _err("Password must be at least 6 characters.")
    if len(username) < 2 or len(username) > 30:
        return _err("Username must be 2-30 characters.")
    import re
    if not re.match(r"^[a-zA-Z0-9_\-\. ]+$", username):
        return _err("Username can only contain letters, numbers, spaces, and . - _")
    username = username.strip()

    if anon is None:
        session["user_id"]  = f"dev:{email}"
        session["username"] = username
        from db.profiles import register_user
        register_user(session["user_id"], username)
        _ph.track_signup(session["user_id"], email)
        if request.is_json:
            return jsonify({"ok": True, "username": username})
        return redirect(url_for("index"))

    try:
        if service:
            try:
                existing = service.auth.admin.get_user_by_email(email)
                if existing:
                    return _err("Email already registered. Try logging in instead.")
            except (AttributeError, NotImplementedError):
                pass
            except Exception as e:
                if "not found" in str(e).lower():
                    pass
                elif "user_not_found" in str(e).lower():
                    pass
                elif "already" in str(e).lower() and "registered" in str(e).lower():
                    return _err("Email already registered. Try logging in instead.")
        res  = anon.auth.sign_up({"email": email, "password": password})
        user = res.user
        if not user:
            return _err("Registration failed. Try again.")
        if not user.identities or len(user.identities) == 0:
            return _err("Email already registered. Try logging in instead.")
        service.table("profiles").insert({"id": user.id, "username": username}).execute()
        session["user_id"]  = user.id
        session["username"] = username
        _ph.track_signup(user.id, email)
        threading.Thread(
            target=lambda: __import__("services.resend", fromlist=["send_welcome"]).send_welcome(email, username),
            daemon=True,
        ).start()
        if request.is_json:
            return jsonify({"ok": True, "username": username})
        return redirect(url_for("index"))
    except Exception as exc:
        msg = str(exc).lower()
        if "already" in msg and "registered" in msg:
            return _err("Email already registered. Try logging in instead.")
        if "unique" in msg or "duplicate" in msg:
            return _err("Username already taken.")
        return _err("Registration failed. Please try again.")


@app.route("/auth/login", methods=["POST"])
@rate_limited
def auth_login():
    from db.supabase_client import anon, service
    data     = request.get_json(silent=True) or request.form
    email    = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    def _err(msg):
        if request.is_json:
            return jsonify({"error": msg}), 401
        flash(msg, "error")
        return redirect(url_for("login_page"))

    if not email or not password:
        return _err("Email and password are required.")

    if anon is None:
        session["user_id"]  = f"dev:{email}"
        session["username"] = email.split("@")[0]
        from db.profiles import register_user
        register_user(session["user_id"], session["username"])
        if request.is_json:
            return jsonify({"ok": True})
        return redirect(url_for("index"))

    try:
        res  = anon.auth.sign_in_with_password({"email": email, "password": password})
        user = res.user
        prof = service.table("profiles").select("username").eq("id", user.id).maybe_single().execute()
        username = (prof.data or {}).get("username", email.split("@")[0])
        session["user_id"]  = user.id
        session["username"] = username
        if request.is_json:
            return jsonify({"ok": True, "username": username})
        return redirect(url_for("index"))
    except Exception:
        return _err("Invalid email or password.")


@app.route("/auth/logout", methods=["GET", "POST"])
def auth_logout():
    session.clear()
    return redirect(url_for("index"))


#  Password reset (Supabase Auth recovery link flow) 

_EMAIL_RE = r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"


@app.route("/forgot-password")
def forgot_password_page():
    if "user_id" in session:
        return redirect(url_for("index"))
    return render_template("forgot_password.html")


@app.route("/reset-password")
def reset_password_page():
    return render_template("reset_password.html")


@app.route("/api/auth/forgot-password", methods=["POST"])
@rate_limited
def api_forgot_password():
    import re
    from db.supabase_client import anon
    data  = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not re.match(_EMAIL_RE, email):
        return jsonify({"error": "Enter a valid email address."}), 400
    if anon is None:
        return jsonify({"error": "Password reset is not available in dev mode."}), 503
    try:
        anon.auth.reset_password_for_email(email, {"redirect_to": f"{SITE_URL}/reset-password"})
    except Exception as exc:
        logging.warning("reset_password_for_email failed: %s", exc)
        return jsonify({"error": "Could not send the reset email. Try again later."}), 502
    # Always succeed on the surface — never reveal whether the address exists.
    return jsonify({"ok": True})


@app.route("/api/auth/reset-password", methods=["POST"])
@rate_limited
def api_reset_password():
    from config import SUPABASE_ANON_KEY, SUPABASE_URL
    data     = request.get_json(silent=True) or {}
    at       = (data.get("access_token") or "").strip()
    rt       = (data.get("refresh_token") or "").strip()
    password = data.get("password") or ""
    if not at or not rt:
        return jsonify({"error": "Invalid or expired reset link. Request a new one."}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters."}), 400
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        return jsonify({"error": "Password reset is not available in dev mode."}), 503
    from supabase import create_client
    client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)  # fresh client: token owns the session
    try:
        client.auth.set_session(at, rt)  # validates the token against Supabase
    except Exception:
        return jsonify({"error": "Invalid or expired reset link. Request a new one."}), 400
    try:
        client.auth.update_user({"password": password})
    except Exception as exc:
        msg = str(exc).lower()
        if "weak" in msg or "password" in msg:
            return jsonify({"error": "Password does not meet the requirements (at least 6 characters)."}), 400
        logging.warning("reset update_user failed: %s", exc)
        return jsonify({"error": "Could not update the password. Try again."}), 502
    return jsonify({"ok": True})


@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("index_3d"))
    return render_template("landing.html", username=session.get("username", "Player"))


@app.route("/play3d")
@login_required
def index_3d():
    return render_template("index_3d.html", username=session.get("username", "Player"))





@app.route("/models")
@login_required
def list_models():
    result = []
    for key, path in MODELS.items():
        try:
            mod = importlib.import_module(path)
            result.append({
                "id":   key,
                "name": getattr(mod, "MODEL_NAME",  key),
                "desc": getattr(mod, "DESCRIPTION", ""),
                "type": "builtin",
            })
        except Exception:
            pass
    try:
        from db.user_models import get_user_models
        for m in get_user_models(uid()):
            result.append({
                "id":   USER_MODEL_PREFIX + m["id"],
                "name": m["name"],
                "desc": (m.get("description") or "") + "  [My model]",
                "type": "user",
            })
    except Exception:
        pass
    return jsonify(result)


@app.route("/move", methods=["POST"])
@login_required
def human_move():
    user_id = uid()
    state   = get_game(user_id)

    if state.get("game_over"):
        return jsonify(_full_state(state, {"error": "Game is over"}))
    if state["game_mode"] == "hvai" and not state["is_player_a"]:
        return jsonify(_full_state(state, {"error": "Not your turn"}))

    data       = request.get_json(silent=True) or {}
    player_idx = max(0, min(2, int(data.get("player_idx", 0))))
    angle      = float(data.get("angle", 0.0))
    power      = max(0.0, min(100.0, float(data.get("power", 80.0))))

    if state.get("penalty_shootout"):
        traj, scored, desc = apply_penalty_kick(state, player_idx, angle, power, True)
        result = {
            "trajectory": traj,
            "scored": scored,
            "desc": desc,
            "player_idx": player_idx,
            "angle": round(angle, 1),
            "power": round(power, 1),
            "kick_endpoint": traj[-1].get("kicker", {"x": traj[-1]["x"], "y": traj[-1]["y"]}),
            "push_result": None,
        }
        if scored:
            _goal_moment_achievements(traj)
        extra = {"move_result": result}
    else:
        result = _apply_move(state, player_idx, angle, power, True)
        if result.get("scored"):
            _goal_moment_achievements(result["trajectory"])
        extra = {"move_result": result}

    save_game(user_id, state)
    _auto_save_state(user_id, state)
    try:
        _mem_short(user_id, state, result)
        if state.get("game_over"):
            _mem_summ(user_id, state)
    except: pass
    return jsonify(_full_state(state, extra))


@app.route("/ai_move", methods=["POST"])
@login_required
def trigger_ai_move():
    user_id = uid()
    state   = get_game(user_id)
    if state.get("game_over"):
        return jsonify(_full_state(state, {"error": "Game is over"}))
    is_player_a = state["is_player_a"]
    model_name  = state["model_name_a"] if is_player_a else state["model_name_b"]
    if state.get("penalty_shootout"):
        if state.get("penalty_goalkeeper_move") is None and not is_player_a:
            return jsonify({"error": "Goalkeeper must choose direction first"}), 400
        if state.get("penalty_goalkeeper_move") is None:
            import random
            state["penalty_goalkeeper_move"] = random.choice(["left", "center", "right"])
        model = _load_model(model_name)
        pidx, ang, pwr = model.get_ai_move(state, is_player_a)
        traj, scored, desc = apply_penalty_kick(state, pidx, ang, pwr, is_player_a)
        result = {
            "trajectory": traj, "scored": scored, "desc": desc,
            "player_idx": pidx, "angle": round(ang, 1), "power": round(pwr, 1),
            "kick_endpoint": traj[-1].get("kicker", {"x": traj[-1]["x"], "y": traj[-1]["y"]}),
            "push_result": None,
        }
    else:
        result = _do_ai_move(state, model_name, is_player_a)
    if result.get("scored"):
        _goal_moment_achievements(result.get("trajectory"))
    save_game(user_id, state)
    _auto_save_state(user_id, state)
    try:
        _mem_short(user_id, state, result)
        if state.get("game_over"):
            _mem_summ(user_id, state)
    except: pass
    return jsonify(_full_state(state, {"ai_result": result}))


@app.route("/switch_model", methods=["POST"])
@login_required
def switch_model():
    user_id = uid()
    state   = get_game(user_id)
    data    = request.get_json(silent=True) or {}
    target  = data.get("target", "b")
    name    = data.get("model", "greedy")
    if not name.startswith(USER_MODEL_PREFIX) and name not in MODELS:
        return jsonify({"error": "Unknown model"}), 400
    _load_model(name)
    if target == "a":
        state["model_name_a"] = name
    else:
        state["model_name_b"] = name
    save_game(user_id, state)
    return jsonify({"status": f"Model {target} -> {name}"})


@app.route("/goalkeeper_move", methods=["POST"])
@login_required
def goalkeeper_move():
    user_id = uid()
    state = get_game(user_id)
    if not state.get("penalty_shootout"):
        return jsonify({"error": "Not in penalty shootout"}), 400
    data = request.get_json(silent=True) or {}
    direction = data.get("direction", "center")
    if direction not in ("left", "center", "right"):
        return jsonify({"error": "Invalid direction"}), 400
    state["penalty_goalkeeper_move"] = direction
    save_game(user_id, state)
    return jsonify({"ok": True})


@app.route("/set_mode", methods=["POST"])
@login_required
def set_mode():
    user_id = uid()
    state   = get_game(user_id)
    mode    = (request.get_json(silent=True) or {}).get("mode", "hvai")
    if mode not in ("hvai", "hvh", "aivai"):
        return jsonify({"error": "Invalid mode"}), 400
    state["game_mode"] = mode
    save_game(user_id, state)
    return jsonify({"status": f"Mode set to {mode}"})


@app.route("/reset", methods=["POST"])
@login_required
def reset_game():
    user_id   = uid()
    old_state = get_game(user_id)
    data = request.get_json(silent=True) or {}
    _track_scene_usage(user_id)
    penalty_mode = data.get("penalty_mode", False)
    if penalty_mode:
        pc = 5
        state = new_game_state(
            mode    = old_state.get("game_mode", "hvai"),
            model_b = old_state.get("model_name_b", "greedy"),
            model_a = old_state.get("model_name_a", "greedy"),
            player_count = pc,
        )
        state["penalty_shootout"] = True
        state["period"] = "penalties"
        state["penalty_kick_num"] = 0
        state["penalty_a_score"] = 0
        state["penalty_b_score"] = 0
        state["penalty_kicks"] = []
        state["penalty_goalkeeper_move"] = None
        state["score_a"] = 0
        state["score_b"] = 0
        from db.customization import get_customization as _gc
        _cust = _gc(user_id)
        state["keeper_style_a"] = _cust.get("keeper_style_a", "default")
        state["keeper_style_b"] = _cust.get("keeper_style_b", "default")
        _setup_penalty_positions(state, True)
    else:
        pc = int(data.get("player_count", old_state.get("player_count", 7)))
        pc = max(1, min(11, pc))
        from db.customization import get_customization
        cust = get_customization(user_id)
        hl = int(cust.get("half_length", 45))
        wl = int(cust.get("win_goal_limit", 5))
        pcap = int(cust.get("power_cap", 100))
        state = new_game_state(
            mode    = old_state.get("game_mode", "hvai"),
            model_b = old_state.get("model_name_b", "greedy"),
            model_a = old_state.get("model_name_a", "greedy"),
            player_count = pc,
            half_length = hl,
            win_goal_limit = wl,
            power_cap = pcap,
        )
        from models.soccer_logic import inject_player_stats
        pstats = cust.get("player_stats", {})
        inject_player_stats(state, pstats.get("a"), pstats.get("b"))
        # Teams (choose team) — inject names and colors + formation tied to manager
        try:
            from db.teams import get_team, team_for_players
            from db.managers import get_manager, DEFAULT_FORMATION_7, pick_referee
            # allow client to pass team_a/team_b and formation_a/b in reset payload
            team_a_id = (data.get("team_a") or cust.get("team_a") or "brazil")
            team_b_id = (data.get("team_b") or cust.get("team_b") or "argentina")
            # formation: client can override manager's default
            form_a = data.get("formation_a") or cust.get("formation_a") or get_manager(team_a_id).get("formation", DEFAULT_FORMATION_7)
            form_b = data.get("formation_b") or cust.get("formation_b") or get_manager(team_b_id).get("formation", DEFAULT_FORMATION_7)
            ref_info = pick_referee(team_a_id + team_b_id)
            # enforce not same (server authoritative)
            if team_a_id == team_b_id:
                from db.teams import TEAMS as _TL
                for _cand in _TL:
                    if _cand["id"] != team_a_id:
                        team_b_id = _cand["id"]
                        break
            ta = get_team(team_a_id)
            tb = get_team(team_b_id)
            if ta:
                state["team_a"] = ta["id"]
                state["team_a_name"] = ta["name"]
                state["team_a_crest"] = ta["crest"]
                state["manager_a"] = get_manager(ta["id"])["name"]
                state["formation_a"] = form_a
                # inject player names
                names_a = team_for_players(ta["id"], pc)
                for i, pl in enumerate(state["players_a"]):
                    if i < len(names_a):
                        pl["name"] = names_a[i]
                # team colors override customization if team chosen
                cust["team_a_color"] = ta["primary"]
            if tb:
                state["team_b"] = tb["id"]
                state["team_b_name"] = tb["name"]
                state["team_b_crest"] = tb["crest"]
                state["manager_b"] = get_manager(tb["id"])["name"]
                state["formation_b"] = form_b
                names_b = team_for_players(tb["id"], pc)
                for i, pl in enumerate(state["players_b"]):
                    if i < len(names_b):
                        pl["name"] = names_b[i]
                cust["team_b_color"] = tb["primary"]
            # referee
            state["referee_name"] = ref_info["name"]
            # re-apply formation-aware positions (new_game_state already did, but ensure)
            from models.soccer_logic import _home_positions
            if pc == 7:
                ha = _home_positions(pc, "a", form_a)
                hb = _home_positions(pc, "b", form_b)
                for i, (x,y) in enumerate(ha):
                    state["players_a"][i]["x"] = float(x); state["players_a"][i]["y"] = float(y)
                for i, (x,y) in enumerate(hb):
                    state["players_b"][i]["x"] = float(x); state["players_b"][i]["y"] = float(y)
            # custom per-player names/colors override team defaults if set
            try:
                pn = cust.get("player_names") or {}
                pcust = cust.get("player_colors") or {}
                for side, key in [("a", "a"), ("b", "b")]:
                    names = pn.get(key)
                    if isinstance(names, list):
                        players = state["players_a"] if side == "a" else state["players_b"]
                        for i, n in enumerate(names[:len(players)]):
                            if n and str(n).strip():
                                players[i]["name"] = str(n).strip()[:20]
                    colors = pcust.get(key)
                    if isinstance(colors, list):
                        players = state["players_a"] if side == "a" else state["players_b"]
                        for i, col in enumerate(colors[:len(players)]):
                            if col and isinstance(col, str) and col.startswith("#") and len(col) == 7:
                                players[i]["color"] = col.lower()
            except Exception:
                pass
        except Exception as e:
            pass
        # Keeper PlayStyles (EA FC 25 — 6 keeper styles)
        state["keeper_style_a"] = cust.get("keeper_style_a", "default")
        state["keeper_style_b"] = cust.get("keeper_style_b", "default")
        # also carry custom names/colors in state for rendering even if not via team path (e.g. no team)
        try:
            pn = cust.get("player_names") or {}
            pcust = cust.get("player_colors") or {}
            if pn or pcust:
                for side in ["a", "b"]:
                    names = (pn.get(side) or []) if pn else []
                    colors = (pcust.get(side) or []) if pcust else []
                    players = state["players_a"] if side == "a" else state["players_b"]
                    for i, pl in enumerate(players):
                        if i < len(names) and names[i] and str(names[i]).strip():
                            pl["name"] = str(names[i]).strip()[:20]
                        if i < len(colors) and colors[i] and str(colors[i]).startswith("#"):
                            pl["color"] = str(colors[i]).lower()
        except Exception:
            pass
    _auto_clear_state(user_id)
    save_game(user_id, state)
    _ph.track_game_start(uid(), state.get("game_mode", "hvai"), state.get("model_name_b", ""))
    return jsonify(_full_state(state))


@app.route("/undo", methods=["POST"])
@login_required
def undo():
    user_id = uid()
    state   = get_game(user_id)
    snap    = pop_snapshot(state)
    if snap is None:
        return jsonify(_full_state(state, {"error": "Nothing to undo"}))
    state["ball"]        = snap["ball"]
    state["players_a"]   = snap["players_a"]
    state["players_b"]   = snap["players_b"]
    state["score_a"]     = snap["score_a"]
    state["score_b"]     = snap["score_b"]
    state["is_player_a"] = snap["is_player_a"]
    state["kick_count"]  = snap["kick_count"]
    state["game_over"]   = False
    state["winner"]      = None
    state["_finalized"]  = False
    if state["move_history"]:
        state["move_history"].pop()
    save_game(user_id, state)
    _auto_save_state(user_id, state)
    return jsonify(_full_state(state, {"undone": True}))


@app.route("/history")
@login_required
def history():
    state = get_game(uid())
    return jsonify({"history": state.get("move_history", [])})


#  Auto-save / load game progress 

def _auto_save_state(user_id: str, state: dict) -> None:
    if state.get("game_over"):
        return
    try:
        from db.saved_states import upsert_state
        upsert_state(user_id, state)
    except Exception:
        pass


def _auto_clear_state(user_id: str) -> None:
    try:
        from db.saved_states import delete_saved_state
        delete_saved_state(user_id)
    except Exception:
        pass


@app.route("/state")
@login_required
def get_state_route():
    user_id = uid()
    raw = __import__("db.redis_client", fromlist=["r"]).r.get(f"game:{user_id}")
    if not raw:
        try:
            from db.saved_states import get_saved_state
            saved = get_saved_state(user_id)
            if saved:
                st = saved["state"]
                if isinstance(st, str):
                    import json
                    st = json.loads(st)
                if not st.get("game_over"):
                    save_game(user_id, st)
        except Exception:
            pass
    state = get_game(user_id)
    return jsonify(_full_state(state))


@app.route("/benchmark", methods=["POST"])
@login_required
def benchmark():
    from models.soccer_logic import new_soccer_state, apply_kick as _kick
    data    = request.get_json(silent=True) or {}
    name_a  = data.get("model_a", "greedy")
    name_b  = data.get("model_b", "minimax")
    n_games = min(int(data.get("games", 5)), 20)
    if name_a not in MODELS or name_b not in MODELS:
        return jsonify({"error": "Unknown model(s)"}), 400
    mod_a = importlib.import_module(MODELS[name_a])
    mod_b = importlib.import_module(MODELS[name_b])
    wins_a, wins_b, draws, total_kicks = 0, 0, 0, 0
    for _ in range(n_games):
        st = new_soccer_state()
        for __ in range(30):
            if st["game_over"]:
                break
            is_a  = st["is_player_a"]
            model = mod_a if is_a else mod_b
            pidx, ang, pwr = model.get_ai_move(st, is_a)
            _kick(st, pidx, ang, pwr, is_a)
        total_kicks += st.get("kick_count", 0)
        w = st.get("winner")
        if w == "A":   wins_a += 1
        elif w == "B": wins_b += 1
        else:          draws  += 1
    return jsonify({
        "model_a": name_a, "model_b": name_b, "games": n_games,
        "wins_a": wins_a, "wins_b": wins_b, "draws": draws,
        "win_rate_a": round(wins_a / n_games * 100, 1),
        "win_rate_b": round(wins_b / n_games * 100, 1),
        "avg_kicks":  round(total_kicks / n_games, 1),
    })


@app.route("/about")
def about_page():
    return render_template("about.html", username=session.get("username", "Player"))


@app.route("/workflow")
@app.route("/about/workflow")
def workflow_page():
    """Workflow overview — displays static/workflow.png. Under About."""
    return render_template("workflow.html", username=session.get("username", "Player"))


#  Clans — create, open/request joins, leader transfer

@app.route("/clans")
@login_required
def clans_page():
    from db.clans import my_clan
    mine = my_clan(uid())
    return render_template("clans.html", username=session.get("username", "Player"), my_clan=mine)


@app.route("/clans/<clan_id>")
@login_required
def clan_detail(clan_id):
    from db.clans import get_clan, list_requests
    c = get_clan(clan_id)
    if not c:
        flash("Clan not found")
        return redirect(url_for("clans_page"))
    reqs = list_requests(clan_id) if c.get("leader_id") == uid() else []
    return render_template("clan.html", username=session.get("username", "Player"), clan=c, pending=reqs)


#  Hubs — combined pages (Play/Workshop/Compete/Social/Learn) — logical IA
@app.route("/hub/play")
@login_required
def hub_play():
    return render_template("hub_play.html", username=session.get("username", "Player"))

@app.route("/hub/workshop")
@login_required
def hub_workshop():
    return render_template("hub_workshop.html", username=session.get("username", "Player"))

@app.route("/hub/compete")
@login_required
def hub_compete():
    return render_template("hub_compete.html", username=session.get("username", "Player"))

@app.route("/hub/social")
@login_required
def hub_social():
    return render_template("hub_social.html", username=session.get("username", "Player"))

@app.route("/hub/learn")
@login_required
def hub_learn():
    return render_template("hub_learn.html", username=session.get("username", "Player"))

@app.route("/api/clans", methods=["GET"])
@login_required
def api_clans_list():
    from db.clans import list_clans, my_clan
    return jsonify({"clans": list_clans(), "my_clan": my_clan(uid())})


@app.route("/api/clans", methods=["POST"])
@login_required
def api_clans_create():
    from db.clans import create_clan
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    desc = (data.get("description") or "").strip()
    join_type = (data.get("join_type") or "request").strip()
    limit = int(data.get("member_limit") or 20)
    try:
        clan = create_clan(uid(), name, description=desc, join_type=join_type, member_limit=limit)
        return jsonify({"ok": True, "clan": clan}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": "Failed to create clan"}), 500


@app.route("/api/clans/<clan_id>/join", methods=["POST"])
@login_required
def api_clans_join(clan_id):
    from db.clans import request_join
    data = request.get_json(silent=True) or {}
    try:
        res = request_join(uid(), clan_id, message=data.get("message") or "")
        return jsonify({"ok": True, **res})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/clans/<clan_id>/requests/<req_id>/approve", methods=["POST"])
@login_required
def api_clans_approve(clan_id, req_id):
    from db.clans import handle_request
    try:
        handle_request(uid(), clan_id, req_id, True)
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/clans/<clan_id>/requests/<req_id>/decline", methods=["POST"])
@login_required
def api_clans_decline(clan_id, req_id):
    from db.clans import handle_request
    try:
        handle_request(uid(), clan_id, req_id, False)
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/clans/<clan_id>/leave", methods=["POST"])
@login_required
def api_clans_leave(clan_id):
    from db.clans import leave_clan
    try:
        leave_clan(uid(), clan_id)
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/clans/<clan_id>/transfer", methods=["POST"])
@login_required
def api_clans_transfer(clan_id):
    from db.clans import transfer_leader
    data = request.get_json(silent=True) or {}
    new_id = (data.get("new_leader_id") or "").strip()
    if not new_id:
        return jsonify({"error": "new_leader_id required"}), 400
    try:
        clan = transfer_leader(uid(), clan_id, new_id)
        return jsonify({"ok": True, "clan": clan})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/clans/<clan_id>", methods=["DELETE"])
@login_required
def api_clans_delete(clan_id):
    from db.clans import delete_clan
    try:
        delete_clan(uid(), clan_id)
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/clans/<clan_id>", methods=["GET"])
@login_required
def api_clans_get(clan_id):
    from db.clans import get_clan, list_requests
    c = get_clan(clan_id)
    if not c:
        return jsonify({"error": "Clan not found"}), 404
    # leader sees pending
    pending = list_requests(clan_id) if c.get("leader_id") == uid() else []
    return jsonify({"clan": c, "pending": pending})


@app.route("/api/clans/<clan_id>", methods=["PATCH"])
@login_required
def api_clans_rename(clan_id):
    from db.clans import rename_clan
    data = request.get_json(silent=True) or {}
    new_name = (data.get("name") or "").strip()
    if not new_name:
        return jsonify({"error": "Name required"}), 400
    try:
        clan = rename_clan(uid(), clan_id, new_name)
        return jsonify({"ok": True, "clan": clan})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/clans/<clan_id>/add-member", methods=["POST"])
@login_required
def api_clans_add_member(clan_id):
    from db.clans import add_member_direct
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    if not username:
        return jsonify({"error": "Username required"}), 400
    try:
        member = add_member_direct(uid(), clan_id, username)
        return jsonify({"ok": True, "member": member})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/clans/<clan_id>/invite-link", methods=["POST"])
@login_required
def api_clans_invite_link(clan_id):
    from db.clans import create_invite_link
    try:
        token = create_invite_link(uid(), clan_id)
        link = f"{SITE_URL}/clans/join/{token}"
        return jsonify({"ok": True, "token": token, "link": link})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/clans/join/<token>")
def clan_join_via_link(token):
    if "user_id" not in session:
        flash("Please log in to join the clan")
        return redirect(url_for("login_page"))
    from db.clans import join_via_invite
    try:
        result = join_via_invite(uid(), token)
        if result.get("joined"):
            flash(f"Joined clan via invite!")
        else:
            flash(f"Join request sent — waiting for leader approval")
        # try to get clan_id from token
        from db.redis_client import r as redis
        clan_id = redis.get(f"clan_invite:{token}")
        if isinstance(clan_id, bytes):
            clan_id = clan_id.decode()
        if clan_id:
            return redirect(url_for("clan_detail", clan_id=clan_id))
        return redirect(url_for("clans_page"))
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("clans_page"))

@app.route("/profile")
@login_required
def profile():
    from db.games import get_user_stats
    from db.profiles import get_avatar_url
    from db.ranked import get_rating, PLACEMENT_GAMES
    from db.achievements import count_earned, list_for_user
    from db.seasons import career_summary, get_current_season
    # lazy progress check so playing more auto-unlocks on profile view
    try:
        _check_progress_achievements(uid())
        _check_social_achievements(uid())
        _check_ai_count_achievements(uid())
    except Exception:
        pass
    stats = get_user_stats(uid())
    username = session.get("username", "Player")
    joined_days = session.get("joined_at")
    avatar_url = get_avatar_url(uid())
    rating = get_rating(uid())
    achievement_count = count_earned(uid())
    season_career = career_summary(uid())
    current_season = int(get_current_season()["number"])
    achievements = list_for_user(uid())
    return render_template("profile.html", username=username, stats=stats,
                           joined_days=joined_days, avatar_url=avatar_url,
                           rating=rating, placement_games=PLACEMENT_GAMES,
                           achievement_count=achievement_count,
                           season_career=season_career,
                           current_season=current_season,
                           achievements=achievements)


@app.route("/api/achievements/toasts")
@login_required
def achievements_toasts():
    """Drain pending badge toasts for the current session user (page-load pump)."""
    return jsonify({"achievements": _ach_toasts()})


@app.route("/achievements")
@login_required
def achievements_page():
    from db.achievements import list_for_user, count_earned, definitions, CATEGORY_LABELS
    user_list = list_for_user(uid())
    earned = count_earned(uid())
    total = len(definitions())
    return render_template("achievements.html",
                           username=session.get("username", "Player"),
                           achievements=user_list, earned=earned, total=total,
                           categories=CATEGORY_LABELS)


#  Profile photo (Supabase Storage, per-user scoped) 

@app.route("/api/profile/photo", methods=["POST"])
@login_required
def api_upload_photo():
    import re
    from db.profile_photos import ALLOWED_EXTENSIONS, MAX_AVATAR_BYTES, upload_avatar
    from db.profiles import set_avatar_url
    user_id = uid()
    if user_id.startswith("clerk:") or user_id.startswith("dev:"):
        return jsonify({"error": "Profile photos are not available for this account type."}), 400
    file = request.files.get("photo")
    if not file or not file.filename:
        return jsonify({"error": "No file selected."}), 400
    ext = (file.filename.rsplit(".", 1)[-1] if "." in file.filename else "").lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "Only JPG, PNG or WebP images are allowed."}), 400
    data = file.read(MAX_AVATAR_BYTES + 1)
    if len(data) > MAX_AVATAR_BYTES:
        return jsonify({"error": "Image must be 5 MB or smaller."}), 400
    try:
        url = upload_avatar(user_id, data, ext)  # path scoped to this user's session, includes NSFW check
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    if not url:
        return jsonify({"error": "Photo upload failed. Storage may not be configured."}), 503
    if not set_avatar_url(user_id, url):
        return jsonify({"error": "Photo uploaded but saving it to your profile failed."}), 502
    _ph.capture(user_id, "profile_photo_upload")
    _ach_grant(user_id, "exp_photo")
    return jsonify({"ok": True, "avatar_url": url})


@app.route("/api/profile/photo", methods=["DELETE"])
@login_required
def api_remove_photo():
    from db.profile_photos import remove_avatar
    from db.profiles import set_avatar_url
    user_id = uid()
    remove_avatar(user_id)
    set_avatar_url(user_id, None)
    return jsonify({"ok": True, "avatar_url": None})


#  Change email (confirmation sent to the new address) 

@app.route("/api/account/change-email", methods=["POST"])
@login_required
def api_change_email():
    import re
    from db.supabase_client import anon, service
    user_id = uid()
    if user_id.startswith("clerk:") or user_id.startswith("dev:"):
        return jsonify({"error": "Email changes are not available for this account type."}), 400
    data = request.get_json(silent=True) or {}
    current_password = data.get("current_password") or ""
    new_email = (data.get("new_email") or "").strip().lower()
    if not current_password:
        return jsonify({"error": "Enter your current password to confirm the change."}), 400
    if not re.match(_EMAIL_RE, new_email):
        return jsonify({"error": "Enter a valid new email address."}), 400
    if not anon or not service:
        return jsonify({"error": "Account changes are not available in dev mode."}), 503
    try:
        user = service.auth.admin.get_user_by_id(user_id)
        current_email = (user.user.email or "").lower()
    except Exception as exc:
        logging.warning("get_user_by_id failed: %s", exc)
        return jsonify({"error": "Could not look up your account."}), 502
    if current_email == new_email:
        return jsonify({"error": "That is already your email address."}), 400
    try:
        res = anon.auth.sign_in_with_password({"email": current_email, "password": current_password})
    except Exception:
        return jsonify({"error": "Current password is incorrect."}), 401
    if not (res.user and res.session):
        return jsonify({"error": "Current password is incorrect."}), 401
    # No email_confirm: Supabase emails a confirmation link to the new address,
    # and the email only changes once it is clicked.
    service.auth.admin.update_user_by_id(user_id, {"email": new_email})
    _ph.capture(user_id, "email_change_requested", {"pending_email": new_email})
    # store pending for resend (24h)
    try:
        from db.redis_client import r as _r
        _r.setex(f"pending_email:{user_id}", 86400, new_email)
    except Exception:
        pass
    return jsonify({"ok": True, "pending_email": new_email})


@app.route("/api/account/pending-email", methods=["GET"])
@login_required
def api_pending_email():
    try:
        from db.redis_client import r as _r
        raw = _r.get(f"pending_email:{uid()}")
        if isinstance(raw, bytes):
            raw = raw.decode()
        if raw:
            return jsonify({"pending_email": raw})
    except Exception:
        pass
    return jsonify({"pending_email": None})


@app.route("/api/account/change-email/resend", methods=["POST"])
@login_required
def api_resend_email_change():
    from db.supabase_client import service
    user_id = uid()
    if user_id.startswith("clerk:") or user_id.startswith("dev:"):
        return jsonify({"error": "Email changes are not available for this account type."}), 400
    if not service:
        return jsonify({"error": "Account changes are not available in dev mode."}), 503
    # get pending from Redis
    pending = None
    try:
        from db.redis_client import r as _r
        raw = _r.get(f"pending_email:{user_id}")
        if isinstance(raw, bytes):
            raw = raw.decode()
        pending = raw
    except Exception:
        pass
    if not pending:
        return jsonify({"error": "No pending email change to resend"}), 400
    # re-trigger Supabase confirmation email
    try:
        service.auth.admin.update_user_by_id(user_id, {"email": pending})
        _ph.capture(user_id, "email_change_resend", {"pending_email": pending})
        return jsonify({"ok": True, "pending_email": pending})
    except Exception as exc:
        logging.warning("resend failed: %s", exc)
        return jsonify({"error": "Could not resend. Try again."}), 502


@app.route("/leaderboard")
@login_required
def leaderboard():
    from db.games import get_leaderboard
    entries = get_leaderboard()
    return render_template("leaderboard.html", entries=entries, username=session.get("username", ""))


@app.route("/customize")
@login_required
def customize_page():
    from db.customization import get_customization, locked_values, UNLOCK_REQUIREMENTS
    from db.achievements import ACHIEVEMENTS
    cust = get_customization(uid())
    lock_map: dict[str, dict] = {}
    for (field, value), key in UNLOCK_REQUIREMENTS.items():
        defn = ACHIEVEMENTS.get(key) or {}
        lock_map.setdefault(field, {})[value] = {
            "key": key,
            "name": defn.get("name", key),
            "description": defn.get("description", ""),
        }
    locked = [f"{f}:{v}" for f, v in sorted(locked_values(uid()))]
    return render_template("customize.html", username=session.get("username", "Player"),
                           cust=cust, lock_map=lock_map, locked=locked)


@app.route("/customize/save", methods=["POST"])
@login_required
def customize_save():
    from db.customization import save_customization, _ALLOWED, UNLOCK_REQUIREMENTS
    from db.achievements import ACHIEVEMENTS, get_earned
    data = request.get_json(silent=True) or {}
    cleaned = {k: v for k, v in data.items() if k in _ALLOWED}
    if cleaned:
        earned = get_earned(uid())
        for (field, value), key in UNLOCK_REQUIREMENTS.items():
            if field in cleaned and cleaned[field] == value and key not in earned:
                defn = ACHIEVEMENTS.get(key) or {}
                return jsonify({
                    "ok": False,
                    "error": (f"{field.replace('_', ' ').title()} value "
                              f"'{value}' is locked — unlock by earning "
                              f"'{defn.get('name', key)}' "
                              f"({defn.get('description', '')})."),
                }), 403
    ok = save_customization(uid(), cleaned)
    if ok and cleaned:
        try:
            for k,v in cleaned.items():
                if k != "player_stats":
                    _mem_long(uid(), k, str(v))
        except: pass
    return jsonify({"ok": ok})


@app.route("/api/customization")
@login_required
def api_customization():
    from db.customization import get_customization
    cust = get_customization(uid())
    return jsonify(cust)

#  Mem0-style memory API (short vs long) 
@app.route("/api/memory/search")
@login_required
def api_memory_search():
    q = request.args.get("q","")
    cat = request.args.get("category")  # long/short or None
    try: topk=int(request.args.get("top_k",3))
    except: topk=3
    if not q:
        return jsonify({"error":"q required"}),400
    return jsonify(_mem_search(q, uid(), top_k=topk, category=cat))

@app.route("/api/memory/all")
@login_required
def api_memory_all():
    cat = request.args.get("category")
    return jsonify({"memories": _mem_all(uid(), category=cat)})

@app.route("/api/chatbot", methods=["POST"])
def api_chatbot():
    # offline, no API key needed — uses services/chatbot.py (rule + local DialoGPT)
    try:
        from services.chatbot import get_response
    except Exception as e:
        return jsonify({"response": "Chatbot not available."}), 500
    data = request.get_json(silent=True) or {}
    msg = (data.get("message") or "").strip()
    if not msg:
        return jsonify({"error": "message required"}), 400
    # short-term memory for context (mem0)
    try:
        _mem_short(uid() or "guest:chatbot", [{"role": "user", "content": msg}], category="short")
    except: pass
    resp = get_response(msg)
    try:
        _mem_short(uid() or "guest:chatbot", [{"role": "assistant", "content": resp}], category="short")
    except: pass
    return jsonify({"response": resp})


@app.route("/my-models")
@login_required
def my_models_page():
    from db.user_models import get_user_models
    from db.leaderboard import list_user_submissions
    from user_models.runner import TEMPLATE, detect_old_field_literals
    import pathlib
    models = get_user_models(uid())
    subs = list_user_submissions(uid()) if models else {}
    for m in models:
        m["maybe_outdated"] = bool(detect_old_field_literals(m.get("code") or ""))
        m["lb"] = subs.get(m.get("id"))
    # builtins with code + benchmark hint (grouped by latency, all <2s after Turbo)
    builtin_bench = {
        "greedy": {"lat": 88, "note": "Turbo 88ms Fast"},
        "genetic_fuzzy": {"lat": 74, "note": "74ms Fast"},
        "monte_carlo": {"lat": 158, "note": "Turbo 158ms Fast"},
        "tactic_transformer": {"lat": 188, "note": "922ms PPO Mid"},
        "graph_gnn": {"lat": 209, "note": "962ms Mid"},
        "dqn_relative": {"lat": 276, "note": "276ms Mid"},
        "ppo_actor_critic": {"lat": 258, "note": "258ms Mid"},
        "q_learning": {"lat": 536, "note": "536ms Mid"},
        "minimax": {"lat": 689, "note": "689ms Mid"},
        "bayesian": {"lat": 806, "note": "806ms Mid"},
        "policy_iteration": {"lat": 983, "note": "983ms Mid"},
        "value_iteration": {"lat": 1049, "note": "1049ms Slow"},
    }
    builtins = []
    for bid, mod_path in MODELS.items():
        try:
            mod = importlib.import_module(mod_path)
            bench = builtin_bench.get(bid, {})
            # try read source for code preview
            p = pathlib.Path(mod.__file__).read_text(encoding="utf-8", errors="ignore")[:4000] if hasattr(mod, "__file__") and mod.__file__ else ""
            builtins.append({"id": bid, "name": getattr(mod, "MODEL_NAME", bid), "desc": getattr(mod, "DESCRIPTION", ""), "bench": bench, "code_preview": p[:800]})
        except Exception:
            pass
    return render_template("my_models.html", username=session.get("username", "Player"), models=models, template_code=TEMPLATE, builtin_models=builtins)


@app.route("/community")
def community_page():
    return render_template("community.html", username=session.get("username", "Player"))


@app.route("/community/<model_id>")
def community_detail(model_id):
    from db.community import get_public_model
    m = get_public_model(model_id)
    if not m:
        flash("Model not found or not shared")
        return redirect(url_for("community_page"))
    return render_template("community_detail.html", username=session.get("username", "Player"), model=m)


@app.route("/api/community", methods=["GET"])
def api_community_list():
    from db.community import list_public_models
    q = (request.args.get("q") or "").strip() or None
    try:
        limit = max(1, min(50, int(request.args.get("limit", 20))))
        offset = max(0, int(request.args.get("offset", 0)))
    except Exception:
        limit, offset = 20, 0
    models, total = list_public_models(limit=limit, offset=offset, q=q)
    return jsonify({"models": models, "total": total, "limit": limit, "offset": offset})


@app.route("/api/community/<model_id>/like", methods=["POST"])
def api_community_like(model_id):
    if "user_id" not in session:
        return jsonify({"error": "Not authenticated"}), 401
    from db.community import toggle_like, get_public_model
    if not get_public_model(model_id):
        return jsonify({"error": "Model not found"}), 404
    liked, total = toggle_like(model_id, uid())
    return jsonify({"ok": True, "liked": liked, "likes": total})


@app.route("/api/community/<model_id>/comment", methods=["POST"])
def api_community_comment(model_id):
    if "user_id" not in session:
        return jsonify({"error": "Not authenticated"}), 401
    from db.community import add_comment, get_public_model
    if not get_public_model(model_id):
        return jsonify({"error": "Model not found"}), 404
    data = request.get_json(silent=True) or {}
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"error": "Comment required"}), 400
    try:
        c = add_comment(model_id, uid(), session.get("username", "Player"), body)
        return jsonify({"ok": True, "comment": c})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/models/user/validate", methods=["POST"])
@login_required
def api_validate_model():
    from user_models.runner import validate_code, execute_user_model, detect_old_field_literals
    from models.soccer_logic import new_soccer_state
    code = (request.get_json(silent=True) or {}).get("code", "")
    ok, msg = validate_code(code)
    if not ok:
        return jsonify({"ok": False, "error": msg})
    try:
        st = new_soccer_state()
        pidx, ang, pwr = execute_user_model(code, st, True, timeout_s=5.0)
        resp = {"ok": True, "test_move": f"player {pidx}, angle {round(ang)}, power {round(pwr)}"}
        hits = detect_old_field_literals(code)
        if hits:
            resp["warning"] = (
                f"Code contains {', '.join(str(h) for h in hits)} — a field coordinate from "
                "before the pitch was resized to 1400×875. Read the size from state['field'] instead."
            )
        return jsonify(resp)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/api/models/user", methods=["POST"])
@login_required
def api_create_model():
    from user_models.runner import validate_code, detect_old_field_literals
    from db.user_models import create_model
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    desc = (data.get("description") or "").strip()
    code = (data.get("code") or "").strip()
    links = data.get("links") if isinstance(data.get("links"), list) else []
    # validate links
    clean_links=[]
    for l in links[:5]:
        if not isinstance(l, dict): continue
        t=(l.get("title") or "").strip()[:80]
        u=(l.get("url") or "").strip()[:500]
        if not t or not u: continue
        if not (u.startswith("http://") or u.startswith("https://")): continue
        clean_links.append({"title": t, "url": u})
    if not name:
        return jsonify({"error": "Name is required."}), 400
    ok, msg = validate_code(code)
    if not ok:
        return jsonify({"error": f"Code error: {msg}"}), 400
    model = create_model(uid(), name, desc, code, links=clean_links)
    resp = {"ok": True, "model": model}
    _ach_grant(uid(), "ai_first_model")
    resp["achievements"] = _ach_toasts()
    hits = detect_old_field_literals(code)
    if hits:
        resp["warning"] = (
            f"Code contains {', '.join(str(h) for h in hits)} — a field coordinate from "
            "before the pitch was resized to 1400×875. Read the size from state['field'] instead."
        )
    return jsonify(resp), 201


@app.route("/api/models/user/<model_id>", methods=["PUT"])
@login_required
def api_update_model(model_id: str):
    from user_models.runner import validate_code, detect_old_field_literals
    from db.user_models import update_model
    data = request.get_json(silent=True) or {}
    fields: dict = {}
    if "name" in data:
        fields["name"] = (data["name"] or "").strip()
        if not fields["name"]:
            return jsonify({"error": "Name is required."}), 400
    if "description" in data:
        fields["description"] = (data["description"] or "").strip()
    if "code" in data:
        code = (data["code"] or "").strip()
        ok, msg = validate_code(code)
        if not ok:
            return jsonify({"error": f"Code error: {msg}"}), 400
        fields["code"] = code
    if "is_public" in data:
        fields["is_public"] = bool(data["is_public"])
    if "links" in data and isinstance(data["links"], list):
        clean=[]
        for l in data["links"][:5]:
            if not isinstance(l, dict): continue
            t=(l.get("title") or "").strip()[:80]
            u=(l.get("url") or "").strip()[:500]
            if not t or not u: continue
            if not (u.startswith("http://") or u.startswith("https://")): continue
            clean.append({"title": t, "url": u})
        fields["links"] = clean
    updated = update_model(model_id, uid(), **fields)
    if not updated:
        return jsonify({"error": "Model not found or access denied."}), 404
    resp = {"ok": True}
    if "code" in fields:
        hits = detect_old_field_literals(fields["code"])
        if hits:
            resp["warning"] = (
                f"Code contains {', '.join(str(h) for h in hits)} — a field coordinate from "
                "before the pitch was resized to 1400×875. Read the size from state['field'] instead."
            )
    return jsonify(resp)


@app.route("/api/models/user/<model_id>/code")
@login_required
def api_get_model_code(model_id: str):
    from db.user_models import get_model_by_id
    data = get_model_by_id(model_id, requesting_user_id=uid())
    if not data:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"code": data.get("code", "")})


@app.route("/api/models/builtin/<builtin_id>/code")
@login_required
def api_get_builtin_code(builtin_id: str):
    if builtin_id not in MODELS:
        return jsonify({"error": "Unknown builtin"}), 404
    import pathlib
    try:
        mod = importlib.import_module(MODELS[builtin_id])
        p = pathlib.Path(mod.__file__)
        code = p.read_text(encoding="utf-8")
        # ensure benchmark helper present for similarity — inject from TEMPLATE
        if "benchmark_vs_greedy" not in code:
            try:
                from user_models.runner import TEMPLATE as _TPL
                # extract benchmark helper block from TEMPLATE
                if "def _bench_progress" in _TPL:
                    helper = _TPL[_TPL.index("# def _bench_progress"):]
                    code = code.rstrip() + "\n\n" + helper
                else:
                    code += "\n\n# Benchmark helper — see TEMPLATE\n"
            except Exception:
                code += "\n\n# Benchmark helper — see TEMPLATE\n"
        return jsonify({"code": code})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/models/user/<model_id>", methods=["DELETE"])
@login_required
def api_delete_model(model_id: str):
    from db.user_models import delete_model
    delete_model(model_id, uid())
    return jsonify({"ok": True})


def _builtin_model_list() -> list[dict]:
    out = []
    for key, path in MODELS.items():
        try:
            mod = importlib.import_module(path)
            out.append({"id": key, "name": getattr(mod, "MODEL_NAME", key)})
        except Exception:
            pass
    return out


@app.route("/playground")
@login_required
def playground_page():
    from user_models.runner import TEMPLATE
    return render_template("playground.html", username=session.get("username", "Player"), template_code=TEMPLATE, builtin_models=_builtin_model_list())


@app.route("/playground/start", methods=["POST"])
@login_required
def pg_start():
    data     = request.get_json(silent=True) or {}
    pg_mode  = data.get("mode", "human_vs_code")
    opponent = data.get("opponent", "greedy")
    if pg_mode not in ("human_vs_code", "code_vs_ai"):
        return jsonify({"error": "Invalid mode"}), 400
    if opponent not in MODELS:
        return jsonify({"error": "Unknown opponent"}), 400
    state = new_pg_state(pg_mode, opponent)
    save_pg(uid(), state)
    return jsonify(_full_state(state))


@app.route("/playground/state")
@login_required
def pg_state():
    state = get_pg(uid())
    if state is None:
        state = new_pg_state()
        save_pg(uid(), state)
    return jsonify(_full_state(state))


@app.route("/playground/move", methods=["POST"])
@login_required
def pg_human_move():
    from user_models.runner import validate_code, execute_user_model
    state = get_pg(uid())
    if state is None:
        return jsonify({"error": "No game in progress. Click Start first."}), 400
    if state.get("game_over"):
        return jsonify(_full_state(state, {"error": "Game is already over."}))
    if not state["is_player_a"]:
        return jsonify({"error": "It is not the human turn."}), 400
    data       = request.get_json(silent=True) or {}
    player_idx = max(0, min(2, int(data.get("player_idx", 0))))
    angle      = float(data.get("angle", 0.0))
    power      = max(0.0, min(100.0, float(data.get("power", 80.0))))
    code       = data.get("code", "")
    human_result = _apply_move(state, player_idx, angle, power, True)
    extra = {"move_result": human_result, "code_error": None}
    if not state.get("game_over"):
        ok, msg = validate_code(code)
        if not ok:
            extra["code_error"] = f"Code error: {msg}"
        else:
            try:
                pidx, ang, pwr = execute_user_model(code, state, False, timeout_s=5.0)
                extra["code_result"] = _apply_move(state, pidx, ang, pwr, False)
            except RuntimeError as exc:
                extra["code_error"] = str(exc)
    save_pg(uid(), state)
    return jsonify(_full_state(state, extra))


@app.route("/playground/auto_move", methods=["POST"])
@login_required
def pg_auto_move():
    from user_models.runner import validate_code, execute_user_model
    state = get_pg(uid())
    if state is None:
        return jsonify({"error": "No game in progress. Click Start first."}), 400
    if state.get("game_over"):
        return jsonify(_full_state(state))
    is_player_a = state["is_player_a"]
    data = request.get_json(silent=True) or {}
    code = data.get("code", "")
    if is_player_a:
        ok, msg = validate_code(code)
        if not ok:
            return jsonify({"error": f"Code error: {msg}"}), 400
        try:
            pidx, ang, pwr = execute_user_model(code, state, True, timeout_s=5.0)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 400
        result = _apply_move(state, pidx, ang, pwr, True)
        extra  = {"code_result": result}
    else:
        result = _do_ai_move(state, state.get("pg_opponent", "greedy"), False)
        extra  = {"ai_result": result}
    save_pg(uid(), state)
    return jsonify(_full_state(state, extra))


@app.route("/playground/reset", methods=["POST"])
@login_required
def pg_reset():
    old = get_pg(uid())
    state = new_pg_state((old or {}).get("pg_mode", "human_vs_code"), (old or {}).get("pg_opponent", "greedy"))
    save_pg(uid(), state)
    return jsonify(_full_state(state))


def _run_pg_benchmark(user_id: str, code: str, opponent: str, games: int):
    from db.redis_client import r as redis
    import json as _json
    key = f"bench:pg:{user_id}"
    try:
        from user_models.runner import validate_code, execute_user_model
        from models.soccer_logic import new_soccer_state, apply_kick
        ok, msg = validate_code(code)
        if not ok:
            redis.setex(key, 600, _json.dumps({"status": "failed", "error": msg}))
            return
        opp_mod = _load_model(opponent)
        wins = 0
        lats: list[float] = []
        redis.setex(key, 600, _json.dumps({"status": "running", "done": 0, "total": games, "wins": 0}))
        for i in range(games):
            st = new_soccer_state()
            # alternate which side is the user's code for fairness
            code_is_a = (i % 2 == 0)
            for _ in range(60):
                if st.get("game_over"):
                    break
                is_a = st["is_player_a"]
                is_code_turn = (is_a == code_is_a)
                if is_code_turn:
                    t0 = time.time()
                    pidx, ang, pwr = execute_user_model(code, st, is_a, timeout_s=5.0)
                    lats.append((time.time() - t0) * 1000)
                else:
                    pidx, ang, pwr = opp_mod.get_ai_move(st, is_a)
                apply_kick(st, pidx, ang, pwr, is_a)
            winner = st.get("winner")
            # code wins if winner matches code side
            code_side = "A" if code_is_a else "B"
            if winner == code_side:
                wins += 1
            redis.setex(key, 600, _json.dumps({"status": "running", "done": i+1, "total": games, "wins": wins, "win_rate": round(wins/(i+1)*100,1), "avg_latency": round(sum(lats)/len(lats),1) if lats else 0}))
        avg_lat = round(sum(lats)/len(lats),1) if lats else 0
        redis.setex(key, 600, _json.dumps({"status": "done", "done": games, "total": games, "wins": wins, "win_rate": round(wins/games*100,1), "avg_latency": avg_lat, "games": games, "opponent": opponent}))
    except Exception as exc:
        import traceback; traceback.print_exc()
        try:
            redis.setex(key, 600, _json.dumps({"status": "failed", "error": str(exc)}))
        except Exception:
            pass


@app.route("/api/playground/benchmark", methods=["POST"])
@login_required
def api_pg_benchmark():
    from db.redis_client import r as redis
    import json as _json
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    opponent = (data.get("opponent") or "greedy").strip()
    try:
        games = max(1, min(20, int(data.get("games", 5))))
    except Exception:
        games = 5
    if opponent not in MODELS:
        return jsonify({"error": "Unknown opponent"}), 400
    from user_models.runner import validate_code
    ok, msg = validate_code(code)
    if not ok:
        return jsonify({"error": f"Code error: {msg}"}), 400
    key = f"bench:pg:{uid()}"
    cur = redis.get(key)
    if cur:
        try:
            curj = _json.loads(cur)
            if curj.get("status") == "running":
                return jsonify({"error": "Benchmark already running"}), 409
        except Exception:
            pass
    import threading as _th
    _th.Thread(target=_run_pg_benchmark, args=(uid(), code, opponent, games), daemon=True).start()
    return jsonify({"ok": True, "games": games, "opponent": opponent})


@app.route("/api/playground/benchmark/status", methods=["GET"])
@login_required
def api_pg_benchmark_status():
    from db.redis_client import r as redis
    import json as _json
    key = f"bench:pg:{uid()}"
    raw = redis.get(key)
    if not raw:
        return jsonify({"status": "idle"})
    try:
        return jsonify(_json.loads(raw))
    except Exception:
        return jsonify({"status": "idle"})


#  AI-builder tutorial (Learn page + machine-checked milestones) 

@app.route("/learn")
@login_required
def learn_page():
    from db.tutorial import get_progress
    from services.tutorial import LESSONS, unlock_state
    progress = get_progress(uid())
    meta = [{k: l.get(k) for k in (
        "id", "title", "kind", "games", "threshold", "target_choice",
        "opponent_label", "requires", "starter",
    )} for l in LESSONS]
    return render_template(
        "learn.html",
        username=session.get("username", "Player"),
        lessons=LESSONS,
        completed=progress,
        state=unlock_state(progress),
        lesson_meta=meta,
    )


def _run_tutorial_check(user_id: str, lesson_id: int, code: str,
                        target: str | None) -> None:
    """Background milestone check (daemon thread; user_id captured at request)."""
    from db.tutorial import set_status, mark_complete
    from services.tutorial import run_milestone_check, get_lesson
    lesson = get_lesson(lesson_id) or {}
    n = lesson.get("games", 1)
    set_status(user_id, lesson_id, "running", done=0, total=n)
    try:
        result = run_milestone_check(code, lesson_id, target)
        passed = bool(result.get("passed"))
        if passed:
            mark_complete(user_id, lesson_id)
        set_status(user_id, lesson_id, "done",
                   done=n, total=n, passed=passed, result=result)
        if passed:
            if lesson_id == 1:
                _ach_grant(user_id, "tut_first_lesson")
            if lesson_id == 7:
                _ach_grant(user_id, "tut_curriculum_done")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        set_status(user_id, lesson_id, "failed", error=str(exc))


@app.route("/api/tutorial/check", methods=["POST"])
@login_required
def api_tutorial_check():
    import threading as _th
    from db.tutorial import get_status, get_progress, mark_complete, is_complete
    from services.tutorial import get_lesson, is_unlocked
    from user_models.runner import validate_code

    data = request.get_json(silent=True) or {}
    try:
        lesson_id = int(data.get("lesson_id", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid lesson."}), 400
    lesson = get_lesson(lesson_id)
    if lesson is None:
        return jsonify({"error": "Unknown lesson."}), 404

    current = get_progress(uid())
    if not is_unlocked(lesson_id, current):
        return jsonify({"error": "Complete the previous lessons first."}), 403

    # Capstone: answered synchronously from the leaderboard store (no code).
    if lesson["kind"] == "leaderboard_submit":
        from db.leaderboard import list_user_submissions
        subs = list_user_submissions(uid())
        passed = bool(subs)
        if passed and not is_complete(uid(), lesson_id):
            mark_complete(uid(), lesson_id)
            _ach_grant(uid(), "tut_curriculum_done")
        return jsonify({
            "ok": True,
            "lesson_id": lesson_id,
            "passed": passed,
            "status": "done",
            "submitted": sorted(subs.keys()),
            "completed": sorted(get_progress(uid()).keys()),
            "achievements": _ach_toasts(),
        })

    code = data.get("code", "")
    ok, msg = validate_code(code)
    if not ok:
        return jsonify({"error": f"Code error: {msg}"}), 400

    if (get_status(uid(), lesson_id) or {}).get("status") == "running":
        return jsonify({"error": "A check is already running for this lesson."}), 409

    target = data.get("target") or None
    if lesson.get("target_choice") and target not in lesson["target_choice"]:
        return jsonify({"error": f"Pick a target: {' / '.join(lesson['target_choice'])}."}), 400

    _th.Thread(
        target=_run_tutorial_check,
        args=(uid(), lesson_id, code, target),
        daemon=True,
    ).start()
    return jsonify({"ok": True, "status": "running",
                    "games": lesson.get("games", 1), "lesson_id": lesson_id})


@app.route("/api/tutorial/check-status")
@login_required
def api_tutorial_check_status():
    from db.tutorial import get_status, get_progress
    try:
        lesson_id = int(request.args.get("lesson_id", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid lesson."}), 400
    status = get_status(uid(), lesson_id) or {}
    return jsonify({
        "lesson_id": lesson_id,
        "status": status.get("status", "idle"),
        "done": status.get("done", 0),
        "total": status.get("total", 0),
        "passed": status.get("passed"),
        "result": status.get("result"),
        "error": status.get("error"),
        "completed": sorted(get_progress(uid()).keys()),
        "achievements": _ach_toasts(),
    })


import uuid as _uuid, time as _time, json as _json

ROOM_TTL   = 3600 * 6   # 6 hours
INVITE_TTL = 3600 * 24  # 24 hours


def _get_room(room_id):
    from db.redis_client import r
    raw = r.get(f"room:{room_id}")
    return _json.loads(raw) if raw else None


def _save_room(room_id, room):
    from db.redis_client import r
    r.setex(f"room:{room_id}", ROOM_TTL, _json.dumps(room))


def _hash_room_pw(pw: str | None) -> str | None:
    if not pw:
        return None
    pw = str(pw).strip()
    if not pw or len(pw) > 32:
        return None
    import hashlib
    return hashlib.sha256(pw.encode()).hexdigest()[:16]


def _check_room_pw(room: dict, pw: str | None) -> bool:
    need = room.get("password_hash")
    if not need:
        return True
    if not pw:
        return False
    import hashlib
    return hashlib.sha256(str(pw).encode()).hexdigest()[:16] == need


#  Live-match index (spectator mode) 
# A Redis set tracks rooms whose status is "active", so /spectate can list
# open matches without scanning. Stale ids are pruned on read.

def _mark_room_active(room_id):
    from db.redis_client import r
    r.sadd("online:active", room_id)


def _mark_room_inactive(room_id):
    from db.redis_client import r
    r.srem("online:active", room_id)


def _active_room_list():
    from db.redis_client import r
    ids = {i.decode() if isinstance(i, bytes) else i for i in r.smembers("online:active")}
    rooms = []
    for rid in ids:
        room = _get_room(rid)
        if not room or room.get("status") != "active":
            _mark_room_inactive(rid)
            continue
        g = room["game"]
        rooms.append({
            "room_id":   rid,
            "name_a":    room.get("name_a", "Player A"),
            "name_b":    room.get("name_b", "Player B"),
            "score_a":   g.get("score_a", 0),
            "score_b":   g.get("score_b", 0),
            "kick_count": g.get("kick_count", 0),
            "started_at": room.get("started_at"),
        })
    rooms.sort(key=lambda x: x["started_at"] or 0)
    return rooms


#  Ranked matchmaking (ELO for human players) 
# A "Play Ranked" queue pairs human players by similar rating. ONLY matches
# created from this queue affect rating — casual create/link/invite rooms stay
# unranked (the hook only fires when room["ranked"] is set). The queue lives
# in Redis: a set of waiting user ids + per-user join timestamps; matched
# players are told their room id via a short-lived `ranked:match:{uid}` key.
# Ratings live in Supabase (db/ranked.py) and are written only by the
# server-side match-completion hook in `online_move`.

RANKED_QUEUE_TTL   = 3600  # how long a queue entry may linger
RANKED_MATCH_GRACE = 60    # seconds a matched-but-unstarted room survives
RANKED_QUEUE_KEY   = "ranked:queue"
RANKED_ROOMS_KEY   = "ranked:rooms"
RANKED_MATCH_KEY   = "ranked:match"


def _ranked_allowed_gap(wait_s: float) -> int:
    """Matching window widens the longer a player has waited.

    Small population, so don't leave anyone queued forever for a precise
    match: 0-10s ±50, 10-30s ±150, 30-60s ±300, 60s+ ±600.
    """
    if wait_s < 10:
        return 50
    if wait_s < 30:
        return 150
    if wait_s < 60:
        return 300
    return 600


def _ranked_members() -> set:
    from db.redis_client import r as redis
    members = redis.smembers(RANKED_QUEUE_KEY)
    return {m.decode() if isinstance(m, bytes) else m for m in members}


def _ranked_join_ts(uid_: str) -> float | None:
    from db.redis_client import r as redis
    raw = redis.get(f"ranked:join:{uid_}")
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _profile_names(user_ids) -> dict:
    names: dict[str, str] = {}
    from db.supabase_client import service
    if service:
        try:
            rows = (service.table("profiles").select("id,username")
                    .in_("id", list(user_ids)).execute().data or [])
            for row in rows:
                if row.get("username"):
                    names[row["id"]] = row["username"]
        except Exception:
            pass
    else:
        from db.profiles import _MEM_USERS
        for uid_ in user_ids:
            if _MEM_USERS.get(uid_):
                names[uid_] = _MEM_USERS[uid_]
    return names


def _create_ranked_room(a_uid: str, b_uid: str) -> str:
    from db.redis_client import r as redis
    from db.ranked import get_ratings
    ratings = get_ratings([a_uid, b_uid])
    # Lower-rated player is A (deterministic assignment).
    if ratings[a_uid]["rating"] > ratings[b_uid]["rating"]:
        a_uid, b_uid = b_uid, a_uid
    names = _profile_names([a_uid, b_uid])
    room_id = _uuid.uuid4().hex[:10]
    room = {
        "game":            new_game_state(mode="hvh"),
        "player_a":        a_uid,
        "player_b":        b_uid,
        "name_a":          names.get(a_uid) or "Player A",
        "name_b":          names.get(b_uid) or "Player B",
        "status":          "active",
        "last_move":       None,
        "move_log":        [],
        "started_at":      _time.time(),
        "ranked":          True,
        "ranked_processed": False,
        "ranked_pending":  False,
        "ranked_result":   None,
    }
    _save_room(room_id, room)
    _mark_room_active(room_id)
    redis.sadd(RANKED_ROOMS_KEY, room_id)
    redis.setex(f"{RANKED_MATCH_KEY}:{a_uid}", RANKED_MATCH_GRACE, room_id)
    redis.setex(f"{RANKED_MATCH_KEY}:{b_uid}", RANKED_MATCH_GRACE, room_id)
    _set_presence_in_match(a_uid, room_id)
    _set_presence_in_match(b_uid, room_id)
    return room_id


def _try_ranked_match() -> list[dict]:
    """Pair the closest-rated waiting players whose gap fits the window.

    Runs on every queue join/status poll. Greedy pass over the rating-sorted
    waiting list: adjacent pairs (the closest ratings) ship first; the window
    is the wider of the two players' wait-based thresholds.
    """
    from db.redis_client import r as redis
    from db.ranked import get_ratings
    members = _ranked_members()
    if len(members) < 2:
        return []
    ratings = get_ratings(sorted(members))
    ordered = sorted(members, key=lambda u: (ratings[u]["rating"], u))
    now = _time.time()
    matched: list[tuple[str, str]] = []
    used: set[str] = set()
    for i in range(len(ordered) - 1):
        a, b = ordered[i], ordered[i + 1]
        if a in used or b in used:
            continue
        wa = _ranked_join_ts(a) or now
        wb = _ranked_join_ts(b) or now
        gap = abs(ratings[a]["rating"] - ratings[b]["rating"])
        if gap <= _ranked_allowed_gap(min(now - wa, now - wb)):
            matched.append((a, b))
            used.add(a)
            used.add(b)
    rooms = []
    for a, b in matched:
        redis.srem(RANKED_QUEUE_KEY, a, b)
        redis.delete(f"ranked:join:{a}")
        redis.delete(f"ranked:join:{b}")
        room_id = _create_ranked_room(a, b)
        rooms.append({"room_id": room_id, "player_a": a, "player_b": b})
    return rooms


def _in_ranked_room(uid_: str) -> bool:
    """True if the user is a participant of an active ranked room."""
    from db.redis_client import r as redis
    ids = {i.decode() if isinstance(i, bytes) else i
           for i in redis.smembers(RANKED_ROOMS_KEY)}
    for rid in ids:
        room = _get_room(rid)
        if room and room.get("ranked") and room["status"] == "active" \
                and uid_ in (room["player_a"], room["player_b"]):
            return True
    return False


def _reclaim_stale_ranked() -> None:
    """Give up on ranked rooms where neither player started after the grace
    period and return both players to the queue."""
    from db.redis_client import r as redis
    ids = {i.decode() if isinstance(i, bytes) else i
           for i in redis.smembers(RANKED_ROOMS_KEY)}
    for rid in ids:
        room = _get_room(rid)
        if not room or not room.get("ranked") or room["status"] == "done":
            redis.srem(RANKED_ROOMS_KEY, rid)
            continue
        started = room.get("started_at") or _time.time()
        if room["game"].get("kick_count", 0) == 0 and _time.time() - started > RANKED_MATCH_GRACE:
            for p in (room["player_a"], room["player_b"]):
                if p:
                    redis.sadd(RANKED_QUEUE_KEY, p)
                    redis.setex(f"ranked:join:{p}", RANKED_QUEUE_TTL, _time.time())
                    redis.delete(f"{RANKED_MATCH_KEY}:{p}")
            redis.srem(RANKED_ROOMS_KEY, rid)
            redis.delete(f"room:{rid}")
            _mark_room_inactive(rid)


def _ranked_payload(uid_: str) -> dict:
    from db.redis_client import r as redis
    from db.ranked import get_rating
    season = _season_info()
    raw = redis.get(f"{RANKED_MATCH_KEY}:{uid_}")
    if raw:
        rid = raw.decode() if isinstance(raw, bytes) else raw
        room = _get_room(rid)
        if room and room.get("ranked"):
            other = room["name_b"] if uid_ == room["player_a"] else room["name_a"]
            other_id = room["player_b"] if uid_ == room["player_a"] else room["player_a"]
            return {
                "status":    "matched",
                "room_id":   rid,
                "my_side":   "a" if uid_ == room["player_a"] else "b",
                "name_a":    room["name_a"],
                "name_b":    room["name_b"],
                "opponent":  other,
                "opponent_rating": get_rating(other_id)["rating"],
                "season":    season,
            }
    if uid_ in _ranked_members():
        join_ts = _ranked_join_ts(uid_) or _time.time()
        return {"status": "waiting", "wait_s": int(max(0, _time.time() - join_ts)),
                "rating": get_rating(uid_)["rating"], "season": season}
    return {"status": "idle", "rating": get_rating(uid_)["rating"], "season": season}


def _season_info() -> dict:
    """Season indicator payload for ranked responses. Runs the transition
    lazily if a boundary has passed (cheap when nothing to do)."""
    try:
        from db.seasons import run_transition_if_due
        run_transition_if_due()
        from db.seasons import get_current_season
        s = get_current_season()
        from datetime import datetime, timezone
        ends_at = s.get("ends_at")
        if isinstance(ends_at, str):
            ends_at = datetime.fromisoformat(ends_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        ends_in_s = max(0, int((ends_at - now).total_seconds()))
        return {"number": int(s["number"]), "status": s.get("status"),
                "ends_at": s.get("ends_at"), "ends_in_s": ends_in_s}
    except Exception:
        return {}


def _process_ranked_result(room_id: str, room: dict) -> None:
    """Server-side rating update for a finished ranked match (idempotent).

    Called only from `online_move` after `apply_kick` marks the game over —
    never from a client-trusted request. `ranked_processed` guards against
    double-application; if the storage write fails we leave `ranked_pending`
    so the next state poll retries.
    """
    if room.get("ranked_processed"):
        return
    pa, pb = room.get("player_a"), room.get("player_b")
    game = room["game"]
    winner = game.get("winner")
    if not pa or not pb or pa.startswith("guest:") or pb.startswith("guest:") \
            or winner not in ("A", "B"):
        room["ranked_processed"] = True
        room["ranked_pending"] = False
        room["ranked_result"] = None
        _save_room(room_id, room)
        return
    try:
        from db.ranked import record_result
        res = record_result(room_id=room_id, player_a=pa, player_b=pb,
                            winner=winner, score_a=game.get("score_a", 0),
                            score_b=game.get("score_b", 0))
        _record_season_match(pa, pb, winner, res)
        room["ranked_processed"] = True
        room["ranked_pending"] = False
        room["ranked_result"] = res
        _check_ranked_achievements(pa, pb, res)
    except Exception as e:
        room["ranked_pending"] = True
        app.logger.warning("Ranked result for room %s failed: %s", room_id, e)
    _save_room(room_id, room)


def _record_season_match(pa: str, pb: str, winner: str, res: dict) -> None:
    """Per-season accounting alongside the career rating write. Purely
    additive — never changes the ELO math; failures are swallowed."""
    try:
        from db.seasons import get_current_season, apply_match
        season = get_current_season()
        for side, uid_ in (("a", pa), ("b", pb)):
            detail = res.get(f"player_{side}") or {}
            apply_match(season, uid_,
                        int(detail.get("rating_before", 1200)),
                        int(detail.get("rating_after", 1200)),
                        won=winner == side.upper())
    except Exception:
        app.logger.warning("Season match accounting failed", exc_info=True)


def _saved_lineup(user_id: str, side: str) -> list[dict] | None:
    """The player's saved point-buy lineup for `side` (a/b), or defaults."""
    try:
        from db.customization import get_customization, _DEFAULT_PLAYER_STATS
        stats = (get_customization(user_id) or {}).get("player_stats") or {}
        lineup = stats.get(side)
        if not lineup:
            return [dict(s) for s in _DEFAULT_PLAYER_STATS]
        return [dict(s) for s in lineup]
    except Exception:
        return None


def _save_match_summary(room_id: str, room: dict) -> None:
    """Snapshot a finished online match into the shareable summary store.

    Called from the `online_move` game-over branch (after the ranked
    result is applied so deltas are included). Purely an aggregate of
    existing room/customization/season data — failures are swallowed so
    nothing about match completion ever depends on it.
    """
    try:
        from db.summaries import save_summary
        from db.seasons import season_for_time
        game = room.get("game") or {}
        pa, pb = room.get("player_a"), room.get("player_b")
        started = room.get("started_at")
        season = season_for_time(started) if started else None
        moves = game.get("move_history") or []
        first_goal_kick = None
        for i, m in enumerate(moves, 1):
            if m.get("scored"):
                first_goal_kick = i
                break
        save_summary(room_id, {
            "room_id": room_id,
            "player_a": pa, "player_b": pb,
            "name_a": room.get("name_a") or "Player A",
            "name_b": room.get("name_b") or "Player B",
            "score_a": int(game.get("score_a", 0)),
            "score_b": int(game.get("score_b", 0)),
            "winner": game.get("winner"),
            "ranked": bool(room.get("ranked")),
            "ranked_result": room.get("ranked_result"),
            "build_a": _saved_lineup(pa, "a") if pa else None,
            "build_b": _saved_lineup(pb, "b") if pb else None,
            "season": int(season["number"]) if season else None,
            "started_at": started,
            "total_kicks": int(game.get("kick_count", 0)),
            "first_goal_kick": first_goal_kick,
        })
        _an_clear("an:data")
    except Exception:
        app.logger.warning("Match summary for room %s failed", room_id, exc_info=True)


@app.route("/ranked/join", methods=["POST"])
@login_required
def ranked_join():
    if uid().startswith("guest:"):
        return jsonify({"error": "Ranked play requires a registered account"}), 403
    _reclaim_stale_ranked()
    if _in_ranked_room(uid()):
        payload = _ranked_payload(uid())
        if payload.get("status") == "matched":
            return jsonify(payload)
        return jsonify({"error": "Finish your current ranked match first"}), 409
    from db.redis_client import r as redis
    if not redis.sismember(RANKED_QUEUE_KEY, uid()):
        redis.sadd(RANKED_QUEUE_KEY, uid())
        redis.setex(f"ranked:join:{uid()}", RANKED_QUEUE_TTL, _time.time())
    _try_ranked_match()
    return jsonify(_ranked_payload(uid()))


@app.route("/ranked/cancel", methods=["POST"])
@login_required
def ranked_cancel():
    from db.redis_client import r as redis
    redis.srem(RANKED_QUEUE_KEY, uid())
    redis.delete(f"ranked:join:{uid()}")
    redis.delete(f"{RANKED_MATCH_KEY}:{uid()}")
    return jsonify({"ok": True})


@app.route("/ranked/status")
@login_required
def ranked_status():
    if uid().startswith("guest:"):
        return jsonify({"error": "Ranked play requires a registered account"}), 403
    _reclaim_stale_ranked()
    _try_ranked_match()
    return jsonify(_ranked_payload(uid()))


#  Ranked leaderboard (human players, distinct from the AI-model board) 

@app.route("/api/leaderboard/ranked")
@login_required
def api_ranked_leaderboard():
    from db.ranked import list_leaderboard, PLACEMENT_GAMES
    from db.seasons import (season_standings, get_current_season, list_seasons,
                            run_transition_if_due)
    try:
        limit = max(1, min(100, int(request.args.get("limit", 20))))
        offset = max(0, int(request.args.get("offset", 0)))
    except (TypeError, ValueError):
        limit, offset = 20, 0
    run_transition_if_due()
    current = get_current_season()
    seasons = [{"number": int(s["number"]), "status": s.get("status"),
                "ends_at": s.get("ends_at")} for s in list_seasons()]
    viewing = request.args.get("season")
    if viewing in (None, "", "current"):
        season_id = int(current["id"])
        viewing_number = int(current["number"])
        entries, total = season_standings(season_id, limit=limit, offset=offset)
    elif viewing == "all":
        viewing_number = "all"
        entries, total = list_leaderboard(limit=limit, offset=offset)
    else:
        try:
            from db.seasons import get_season_by_number
            season = get_season_by_number(int(viewing))
        except (TypeError, ValueError):
            season = None
        if season is None:
            return jsonify({"error": "Unknown season"}), 404
        entries, total = season_standings(int(season["id"]), limit=limit, offset=offset)
        viewing_number = int(season["number"])
    return jsonify({"entries": entries, "total": total,
                    "limit": limit, "offset": offset,
                    "placement_games": PLACEMENT_GAMES,
                    "viewing": viewing_number,
                    "season": {"number": int(current["number"]),
                               "status": current.get("status"),
                               "ends_at": current.get("ends_at")},
                    "seasons": seasons})


@app.route("/leaderboard/ranked")
@login_required
def ranked_leaderboard_page():
    from db.ranked import PLACEMENT_GAMES
    from db.seasons import get_current_season, list_seasons, run_transition_if_due
    run_transition_if_due()
    current = get_current_season()
    return render_template("ranked_leaderboard.html",
                           username=session.get("username", ""),
                           placement_games=PLACEMENT_GAMES,
                           current_season={"number": int(current["number"]),
                                           "ends_at": current.get("ends_at"),
                                           "status": current.get("status")},
                           seasons=[{"number": int(s["number"]),
                                     "status": s.get("status"),
                                     "ends_at": s.get("ends_at")}
                                    for s in list_seasons()])


@app.route("/api/seasons/_transition", methods=["POST"])
@login_required
def seasons_transition_manual():
    """Dev/test-only season-boundary trigger. Default call behaves like the
    scheduled job (runs only when the end date has passed -> safe no-op
    otherwise); ?force=1 ignores the clock for manual verification."""
    if not __import__("config").DEV_MODE:
        return jsonify({"error": "Not available outside dev mode"}), 403
    from db.seasons import get_current_season, transition, run_transition_if_due
    if request.args.get("force") != "1":
        result = run_transition_if_due()
        if result is not None:
            return jsonify({"ok": True, "transition": result})
        return jsonify({"ok": True, "noop": True})
    season = get_current_season()
    result = transition(int(season["id"]))
    if result is None:
        return jsonify({"ok": True, "noop": True})
    return jsonify({"ok": True, "transition": result})


@app.route("/online")
@login_required
def online_page():
    return redirect(url_for("index_3d"))


@app.route("/join/<room_id>")
def join_room_page(room_id):
    return redirect(url_for("index_3d"))


@app.route("/online/create", methods=["POST"])
@login_required
def online_create():
    data = request.get_json(silent=True) or {}
    room_id = _uuid.uuid4().hex[:10]
    pw_hash = _hash_room_pw(data.get("password"))
    room = {
        "game":      new_game_state(mode="hvh"),
        "player_a":  uid(),
        "player_b":  None,
        "name_a":    session.get("username", "Player A"),
        "name_b":    None,
        "status":    "waiting",
        "last_move": None,
        "move_log":  [],
        "started_at": _time.time(),
    }
    if pw_hash:
        room["password_hash"] = pw_hash
        room["has_password"] = True
    _save_room(room_id, room)
    return jsonify({"room_id": room_id, "has_password": bool(pw_hash)})


@app.route("/online/<room_id>/join", methods=["POST"])
def online_join(room_id):
    if "user_id" not in session:
        import uuid
        session["user_id"]  = f"guest:{uuid.uuid4().hex[:12]}"
        session["username"] = "Guest"
    room = _get_room(room_id)
    if not room:
        return jsonify({"error": "Room not found"}), 404
    my_uid = uid()
    if room["player_a"] == my_uid:
        return jsonify({"my_side": "a", "status": room["status"],
                        "room_id": room_id,
                        "name_a": room["name_a"], "name_b": room["name_b"],
                        "ranked": room.get("ranked", False)})
    if room["player_b"] == my_uid:
        return jsonify({"my_side": "b", "status": room["status"],
                        "room_id": room_id,
                        "name_a": room["name_a"], "name_b": room["name_b"],
                        "ranked": room.get("ranked", False)})
    # Ranked rooms are pre-filled by the matchmaker — a listed participant
    # may claim their side without the generic "join as player B" path.
    if room.get("ranked") and my_uid in (room["player_a"], room["player_b"]):
        return jsonify({"my_side": "a" if my_uid == room["player_a"] else "b",
                        "status": room["status"],
                        "room_id": room_id,
                        "name_a": room["name_a"], "name_b": room["name_b"],
                        "ranked": True})
    if room["player_b"] is not None:
        return jsonify({"error": "Room is full"}), 400
    # password rooms
    pw = (request.get_json(silent=True) or {}).get("password") if request.is_json else request.form.get("password")
    if not _check_room_pw(room, pw):
        return jsonify({"error": "Incorrect room password", "has_password": True}), 403
    room["player_b"] = my_uid
    room["name_b"]   = session.get("username", "Guest")
    room["status"]   = "active"
    _save_room(room_id, room)
    _mark_room_active(room_id)
    _set_presence_in_match(room["player_a"], room_id)
    _set_presence_in_match(room["player_b"], room_id)
    return jsonify({"my_side": "b", "status": "active",
                    "room_id": room_id,
                    "name_a": room["name_a"], "name_b": room["name_b"],
                    "ranked": room.get("ranked", False)})


@app.route("/online/<room_id>/state")
def online_room_state(room_id):
    if "user_id" not in session:
        import uuid
        session["user_id"]  = f"guest:{uuid.uuid4().hex[:12]}"
        session["username"] = "Guest"
    room = _get_room(room_id)
    if not room:
        return jsonify({"error": "Room not found"}), 404
    since_kick = int(request.args.get("since_kick", -1))
    # A finished ranked match whose rating update failed previously is
    # retried lazily here (players poll every 1.5s; room TTL is 6h).
    if (room.get("ranked") and room.get("status") == "done"
            and room.get("ranked_pending") and not room.get("ranked_processed")):
        _process_ranked_result(room_id, room)
        room = _get_room(room_id) or room
    resp = {
        "game":    room["game"],
        "name_a":  room["name_a"],
        "name_b":  room["name_b"],
        "status":  room["status"],
        "room_id": room_id,
        "ranked":  room.get("ranked", False),
    }
    if room.get("ranked_result"):
        resp["ranked_result"] = room["ranked_result"]
    my_uid = uid()
    resp["my_side"] = ("a" if my_uid == room["player_a"]
                       else "b" if my_uid == room["player_b"] else None)
    if room["last_move"] and room["game"].get("kick_count", 0) > since_kick:
        resp["last_move"] = room["last_move"]
    move_log = room.get("move_log") or []
    if move_log:
        resp["moves"] = [
            item for item in move_log
            if item.get("kick_count", 0) > since_kick
        ]
    # Voice-chat signaling rides the same poll — only for the two participants.
    if request.args.get("voice_after") is not None and resp["my_side"]:
        from db.voice import get_voice_signals
        from db.chat import get_blocked
        try:
            after = int(request.args.get("voice_after", -1))
        except ValueError:
            after = -1
        blocked = get_blocked(my_uid)
        # A participant who blocked the other also stops receiving from them.
        other = (room["player_b"] if my_uid == room["player_a"] else room["player_a"])
        if other in blocked:
            blocked = {other}
        sigs, next_after = get_voice_signals(room_id, after, blocked)
        # skip messages we sent ourselves — only the peer's matter
        sigs = [s for s in sigs if s["from"] != my_uid]
        resp["voice_signals"] = sigs
        resp["voice_after"]   = next_after
    resp["achievements"] = _ach_toasts()
    return jsonify(resp)


@app.route("/online/<room_id>/voice", methods=["POST"])
def online_voice_signal(room_id):
    if "user_id" not in session:
        return jsonify({"error": "Not in session"}), 401
    room = _get_room(room_id)
    if not room:
        return jsonify({"error": "Room not found"}), 404
    my_uid  = uid()
    my_side = ("a" if my_uid == room["player_a"]
               else "b" if my_uid == room["player_b"] else None)
    if not my_side:
        return jsonify({"error": "Not a player in this room"}), 403
    data  = request.get_json(silent=True) or {}
    s_type = (data.get("type") or "").strip()
    if s_type not in ("offer", "answer", "ice", "mute"):
        return jsonify({"error": "Invalid signal type"}), 400
    # Mutual block check: a blocked user can't signal, and signals to a
    # blocker would never be delivered anyway — reject at the source.
    from db.chat import get_blocked
    other = (room["player_b"] if my_side == "a" else room["player_a"])
    try:
        blocked_me  = get_blocked(my_uid)
        blocked_them = get_blocked(other)
    except Exception:
        blocked_me = blocked_them = set()
    if other in blocked_me:
        return jsonify({"error": "You've blocked this user"}), 403
    if my_uid in blocked_them:
        return jsonify({"error": "This user has blocked you"}), 403
    from db.voice import send_voice_signal
    try:
        sig = send_voice_signal(room_id, my_uid, s_type, data.get("data") or {})
    except ValueError:
        return jsonify({"error": "Invalid signal type"}), 400
    return jsonify({"ok": True, "signal": sig})


@app.route("/online/<room_id>/move", methods=["POST"])
def online_move(room_id):
    if "user_id" not in session:
        return jsonify({"error": "Not in session"}), 401
    room = _get_room(room_id)
    if not room or room["status"] != "active":
        return jsonify({"error": "Game not active"}), 400
    my_uid  = uid()
    my_side = ("a" if my_uid == room["player_a"]
               else "b" if my_uid == room["player_b"] else None)
    if not my_side:
        return jsonify({"error": "Not a player in this room"}), 403
    game = room["game"]
    expected = "a" if game["is_player_a"] else "b"
    if my_side != expected:
        return jsonify({"error": "Not your turn"}), 400
    data       = request.get_json(silent=True) or {}
    player_idx = max(0, min(2, int(data.get("player_idx", 0))))
    angle      = float(data.get("angle", 0.0))
    pc         = int(game.get("power_cap", 100))
    power      = max(0.0, min(pc, float(data.get("power", 80.0))))
    push_snapshot(game)
    traj, scored, desc, kick_ep, push_res = apply_kick(game, player_idx, angle, power, game["is_player_a"])
    if scored:
        _goal_moment_achievements(traj)
    move_res = {
        "trajectory":    traj, "scored": scored, "desc": desc,
        "player_idx":    player_idx, "angle": round(angle, 1), "power": round(power, 1),
        "kick_endpoint": kick_ep, "push_result": push_res, "mover": my_side,
    }
    room["last_move"] = move_res
    room.setdefault("move_log", []).append({
        **move_res,
        "kick_count": game.get("kick_count", 0),
    })
    room["move_log"] = room["move_log"][-20:]
    try: _mem_short(my_uid, game, move_res)
    except: pass
    if game.get("game_over"):
        room["status"] = "done"
        _mark_room_inactive(room_id)
        _check_online_achievements(room, game)
        if room.get("ranked"):
            _process_ranked_result(room_id, room)
        _save_match_summary(room_id, room)
        _push_recent_pair(room.get("player_a") or "", room.get("name_a") or "Player A", room.get("player_b") or "", room.get("name_b") or "Player B")
        try:
            from db.friends import set_presence as _sp2
            if room.get("player_a"):
                _sp2(room["player_a"], "online")
            if room.get("player_b"):
                _sp2(room["player_b"], "online")
        except Exception:
            pass
        try:
            _mem_summ(my_uid, game)
            other = room["player_b"] if my_side=="a" else room["player_a"]
            if other: _mem_summ(other, game)
        except: pass
        _save_room(room_id, room)
    else:
        _save_room(room_id, room)
    return jsonify({"move_result": move_res, "game": game, "achievements": _ach_toasts()})


@app.route("/online/invite/search")
@login_required
def online_invite_search():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    try:
        from db.supabase_client import service
        if not service:
            from db.profiles import _MEM_USERS
            res = [{"id": _uid, "username": _name} for _uid, _name in _MEM_USERS.items() if q.lower() in _name.lower() and _uid != uid()]
            # exact first
            res.sort(key=lambda r: (r["username"].lower() != q.lower(), r["username"].lower()))
            return jsonify(res[:10])
        # exact match first (EA FC style), then ilike
        exact = service.table("profiles").select("id,username").eq("username", q).maybe_single().execute()
        rows = []
        if exact and exact.data and exact.data.get("id") != uid():
            rows.append(exact.data)
        ilike = (service.table("profiles").select("id,username").ilike("username", f"%{q}%").limit(10).execute().data or [])
        for r in ilike:
            if r["id"] != uid() and r["id"] not in {x["id"] for x in rows}:
                rows.append(r)
            if len(rows) >= 10:
                break
        return jsonify(rows)
    except Exception:
        return jsonify([])


@app.route("/online/invite/send", methods=["POST"])
@login_required
def online_invite_send():
    from db.redis_client import r as redis
    data   = request.get_json(silent=True) or {}
    to_uid = data.get("to_uid", "")
    if not to_uid or to_uid == uid():
        return jsonify({"error": "Invalid target"}), 400
    room_id = _uuid.uuid4().hex[:10]
    pw_hash = _hash_room_pw(data.get("password"))
    room = {
        "game": new_game_state(mode="hvh"), "player_a": uid(), "player_b": None,
        "name_a": session.get("username", "Player A"), "name_b": None,
        "status": "waiting", "last_move": None, "started_at": _time.time(),
    }
    if pw_hash:
        room["password_hash"] = pw_hash
        room["has_password"] = True
    _save_room(room_id, room)
    invite_id = _uuid.uuid4().hex[:12]
    invite = {
        "from_uid": uid(), "from_name": session.get("username", "Player"),
        "to_uid": to_uid, "room_id": room_id, "status": "pending",
    }
    if pw_hash:
        invite["has_password"] = True
    redis.setex(f"invite:{invite_id}", INVITE_TTL, _json.dumps(invite))
    redis.lpush(f"user_invites:{to_uid}", invite_id)
    redis.expire(f"user_invites:{to_uid}", INVITE_TTL)
    return jsonify({"ok": True, "invite_id": invite_id, "room_id": room_id})


@app.route("/online/invites")
@login_required
def online_get_invites():
    from db.redis_client import r as redis
    raw_ids = redis.lrange(f"user_invites:{uid()}", 0, 19)
    result  = []
    for raw_id in raw_ids:
        iid = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
        raw = redis.get(f"invite:{iid}")
        if raw:
            inv = _json.loads(raw)
            if inv.get("status") == "pending":
                result.append({**inv, "invite_id": iid})
    return jsonify(result)


@app.route("/online/invite/<invite_id>/accept", methods=["POST"])
@login_required
def online_accept_invite(invite_id):
    from db.redis_client import r as redis
    raw = redis.get(f"invite:{invite_id}")
    if not raw:
        return jsonify({"error": "Invite not found"}), 404
    inv = _json.loads(raw)
    if inv["to_uid"] != uid():
        return jsonify({"error": "Not your invite"}), 403
    if inv["status"] != "pending":
        return jsonify({"error": "Already used"}), 400
    room = _get_room(inv["room_id"])
    if not room:
        return jsonify({"error": "Room expired"}), 404
    room["player_b"] = uid()
    room["name_b"]   = session.get("username", "Guest")
    room["status"]   = "active"
    room.setdefault("started_at", _time.time())
    _save_room(inv["room_id"], room)
    _mark_room_active(inv["room_id"])
    _set_presence_in_match(room["player_a"], inv["room_id"])
    _set_presence_in_match(room["player_b"], inv["room_id"])
    inv["status"] = "accepted"
    redis.setex(f"invite:{invite_id}", INVITE_TTL, _json.dumps(inv))
    redis.lrem(f"user_invites:{uid()}", 0, invite_id)
    return jsonify({"ok": True, "room_id": inv["room_id"]})


@app.route("/online/invite/<invite_id>/decline", methods=["POST"])
@login_required
def online_decline_invite(invite_id):
    from db.redis_client import r as redis
    raw = redis.get(f"invite:{invite_id}")
    if not raw:
        return jsonify({"error": "Not found"}), 404
    inv = _json.loads(raw)
    if inv["to_uid"] != uid():
        return jsonify({"error": "Not your invite"}), 403
    inv["status"] = "declined"
    redis.setex(f"invite:{invite_id}", INVITE_TTL, _json.dumps(inv))
    redis.lrem(f"user_invites:{uid()}", 0, invite_id)
    return jsonify({"ok": True})


#  Live Spectator Mode (open: any user or logged-out visitor) 
# Spectators watch live online matches through the same HTTP-polled /state
# endpoint the players use. They get the full game + last_move, my_side=None,
# and every write path (move/voice/chat) is already rejected server-side for
# non-participants. No changes to the players' matching logic.

@app.route("/api/spectate/active")
def spectate_active():
    return jsonify({"matches": _active_room_list()})


@app.route("/spectate")
def spectate_page():
    return render_template("spectate.html", username=session.get("username"))


@app.route("/spectate/<room_id>")
def spectate_room(room_id):
    room = _get_room(room_id)
    if not room:
        flash("That match is no longer available.", "error")
        return redirect(url_for("spectate_page"))
    return render_template("replay_3d.html",
                           username=session.get("username"),
                           t=None, match={"replay_data": [], "replay_data_len": 0},
                           highlights=[], highlight=None, live_room=room_id,
                           loss_model=None, loss_model_name=None)



#  Teams (choose team, cannot pick same as opponent) 
@app.route("/api/teams")
def api_teams():
    from db.teams import list_teams
    return jsonify(list_teams())

@app.route("/api/formations")
def api_formations():
    from db.managers import FORMATIONS_7, list_formations, MANAGERS
    return jsonify({"formations": list_formations(), "map": FORMATIONS_7, "managers": MANAGERS})

@app.route("/api/referees")
def api_referees():
    from db.managers import REFEREES
    return jsonify(REFEREES)

@app.route("/online/<room_id>/team", methods=["POST"])
def online_choose_team(room_id):
    room = _get_room(room_id)
    if not room:
        return jsonify({"error": "Room not found"}), 404
    if "user_id" not in session:
        return jsonify({"error": "Not in session"}), 401
    my_uid = uid()
    my_side = ("a" if my_uid == room["player_a"] else "b" if my_uid == room["player_b"] else None)
    if not my_side:
        return jsonify({"error": "Not a player in this room"}), 403
    data = request.get_json(silent=True) or {}
    team_id = (data.get("team_id") or "").strip()
    from db.teams import TEAMS_BY_ID, team_for_players
    if team_id not in TEAMS_BY_ID:
        return jsonify({"error": "Unknown team"}), 400
    other_team = room.get("team_b") if my_side == "a" else room.get("team_a")
    if other_team == team_id:
        return jsonify({"error": "Team already taken by opponent — choose another"}), 409
    # assign
    if my_side == "a":
        room["team_a"] = team_id
    else:
        room["team_b"] = team_id
    # inject player names into game state for that side
    count = room["game"].get("player_count", 3)
    names = team_for_players(team_id, count)
    players = room["game"]["players_a"] if my_side == "a" else room["game"]["players_b"]
    for i, p in enumerate(players):
        if i < len(names):
            p["name"] = names[i]
    # also update room and game team fields
    team = TEAMS_BY_ID[team_id]
    from db.managers import get_manager
    mgr = get_manager(team_id)
    if my_side == "a":
        room["name_a"] = f"{team['crest']} {team['name']}"
        room["team_a"] = team_id
        room["game"]["team_a"] = team_id
        room["game"]["team_a_name"] = team["name"]
        room["game"]["manager_a"] = mgr["name"]
        room["game"]["formation_a"] = mgr["formation"]
        # update positions for new formation if 7 players
        if room["game"].get("player_count", 7) == 7:
            from models.soccer_logic import _home_positions
            ha = _home_positions(7, "a", mgr["formation"])
            for i, (x,y) in enumerate(ha):
                if i < len(room["game"]["players_a"]):
                    room["game"]["players_a"][i]["x"] = float(x); room["game"]["players_a"][i]["y"] = float(y)
    else:
        room["name_b"] = f"{team['crest']} {team['name']}"
        room["team_b"] = team_id
        room["game"]["team_b"] = team_id
        room["game"]["team_b_name"] = team["name"]
        room["game"]["manager_b"] = mgr["name"]
        room["game"]["formation_b"] = mgr["formation"]
        if room["game"].get("player_count", 7) == 7:
            from models.soccer_logic import _home_positions
            hb = _home_positions(7, "b", mgr["formation"])
            for i, (x,y) in enumerate(hb):
                if i < len(room["game"]["players_b"]):
                    room["game"]["players_b"][i]["x"] = float(x); room["game"]["players_b"][i]["y"] = float(y)
    _save_room(room_id, room)
    return jsonify({"ok": True, "team_id": team_id, "team": team, "room": {"team_a": room.get("team_a"), "team_b": room.get("team_b")}})

#  Friend system — persistent (Supabase) + presence (Redis) — EA FC / eFootball inspired

FRIEND_TTL = 3600 * 24 * 30   # legacy cache TTL (Supabase is source of truth now)


def _get_friends(user_id: str) -> list:
    try:
        from db.friends import list_friends as _lf
        return _lf(user_id)
    except Exception:
        from db.redis_client import r as redis
        raw = redis.get(f"friends:{user_id}")
        return _json.loads(raw) if raw else []


def _save_friends(user_id: str, friends: list) -> None:
    from db.redis_client import r as redis
    try:
        redis.setex(f"friends:{user_id}", FRIEND_TTL, _json.dumps(friends))
    except Exception:
        pass


def _get_friend_reqs(user_id: str) -> list:
    try:
        from db.friends import list_requests as _lr
        return _lr(user_id)
    except Exception:
        from db.redis_client import r as redis
        raw = redis.get(f"friend_reqs:{user_id}")
        return _json.loads(raw) if raw else []


def _save_friend_reqs(user_id: str, reqs: list) -> None:
    from db.redis_client import r as redis
    try:
        redis.setex(f"friend_reqs:{user_id}", FRIEND_TTL, _json.dumps(reqs))
    except Exception:
        pass


def _friend_heartbeat(uid_: str) -> None:
    try:
        from db.friends import heartbeat as _hb
        _hb(uid_)
    except Exception:
        pass


@app.route("/api/friends")
@login_required
def api_list_friends():
    _friend_heartbeat(uid())
    from db.friends import get_presence, get_recent
    friends = _get_friends(uid())
    # enrich with presence + avatar + stats head-to-head lightweight
    try:
        pres = get_presence([f["uid"] for f in friends]) if friends else {}
        for f in friends:
            p = pres.get(f["uid"], {"status": "offline", "last_seen": 0})
            f["presence"] = p.get("status", "offline")
            f["last_seen"] = p.get("last_seen", 0)
            f["in_match"] = p.get("status") == "in_match"
            f["room_id"] = p.get("room_id")
    except Exception:
        pass
    requests = _get_friend_reqs(uid())
    recent = []
    try:
        recent = get_recent(uid())
        # filter out existing friends
        fids = {f["uid"] for f in friends}
        recent = [r for r in recent if r.get("uid") not in fids][:30]
    except Exception:
        pass
    return jsonify({
        "friends":  friends,
        "requests": requests,
        "recent": recent,
        "cap": 32,
    })


@app.route("/api/friends/request", methods=["POST"])
@login_required
def api_send_friend_request():
    from db.supabase_client import service
    from db.friends import FRIEND_CAP
    data   = request.get_json(silent=True) or {}
    target = (data.get("username") or "").strip()
    if not target:
        return jsonify({"error": "Username required"}), 400
    my_uid      = uid()
    my_username = session.get("username", "")
    if target.lower() == my_username.lower():
        return jsonify({"error": "You can't add yourself"}), 400
    # cap 32
    if len(_get_friends(my_uid)) >= FRIEND_CAP:
        return jsonify({"error": f"Friend list full (max {FRIEND_CAP})"}), 400
    # exact match first, then ilike fallback — EA FC style
    target_uid = target_name = None
    try:
        if service:
            exact = service.table("profiles").select("id,username").eq("username", target).maybe_single().execute()
            if exact and exact.data:
                target_uid, target_name = exact.data["id"], exact.data["username"]
            else:
                res = (service.table("profiles").select("id,username").ilike("username", target).limit(1).execute())
                row = (res.data or [None])[0] if res.data else None
                if row:
                    target_uid, target_name = row["id"], row["username"]
        else:
            from db.profiles import _MEM_USERS
            for _uid, _name in _MEM_USERS.items():
                if _name.lower() == target.lower():
                    target_uid, target_name = _uid, _name
                    break
            if not target_uid:
                for _uid, _name in _MEM_USERS.items():
                    if target.lower() in _name.lower():
                        target_uid, target_name = _uid, _name
                        break
        if not target_uid:
            return jsonify({"error": "User not found"}), 404
    except Exception:
        return jsonify({"error": "User lookup failed"}), 500
    if any(f["uid"] == target_uid for f in _get_friends(my_uid)):
        return jsonify({"error": "Already friends"}), 400
    # use persistent check
    try:
        from db.friends import has_pending
        if has_pending(my_uid, target_uid):
            return jsonify({"error": "Request already sent"}), 400
    except Exception:
        if any(r_["from_uid"] == my_uid for r_ in _get_friend_reqs(target_uid)):
            return jsonify({"error": "Request already sent"}), 400
    try:
        from db.friends import create_request
        create_request(my_uid, my_username, target_uid)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        # fallback Redis
        reqs = _get_friend_reqs(target_uid)
        reqs.append({"id": _uuid.uuid4().hex[:12], "from_uid": my_uid, "from_username": my_username, "ts": _time.time()})
        _save_friend_reqs(target_uid, reqs)
    return jsonify({"ok": True, "to": target_name})


@app.route("/api/friends/accept/<req_id>", methods=["POST"])
@login_required
def api_accept_friend(req_id):
    my_uid = uid()
    # persistent path first
    try:
        from db.friends import delete_request, add_friend_pair, FRIEND_CAP
        # find request owner
        req = None
        # try persistent lookup
        for r in _get_friend_reqs(my_uid):
            if r.get("id") == req_id:
                req = r
                break
        if not req:
            return jsonify({"error": "Request not found"}), 404
        if len(_get_friends(my_uid)) >= FRIEND_CAP:
            return jsonify({"error": f"Friend list full (max {FRIEND_CAP})"}), 400
        # also check target cap
        if len(_get_friends(req["from_uid"])) >= FRIEND_CAP:
            return jsonify({"error": "Their friend list is full"}), 400
        add_friend_pair(my_uid, session.get("username", ""), req["from_uid"], req.get("from_username", "Player"))
        delete_request(my_uid, req_id)
        return jsonify({"ok": True, "friend": {"uid": req["from_uid"], "username": req.get("from_username", "Player")}} )
    except Exception:
        pass
    # legacy fallback
    reqs = _get_friend_reqs(my_uid)
    req = next((r_ for r_ in reqs if r_["id"] == req_id), None)
    if not req:
        return jsonify({"error": "Request not found"}), 404
    ts = _time.time()
    my_friends = _get_friends(my_uid)
    their_friends = _get_friends(req["from_uid"])
    my_friends.append({"uid": req["from_uid"], "username": req["from_username"], "since": ts})
    their_friends.append({"uid": my_uid, "username": session.get("username", ""), "since": ts})
    _save_friends(my_uid, my_friends)
    _save_friends(req["from_uid"], their_friends)
    _save_friend_reqs(my_uid, [r_ for r_ in reqs if r_["id"] != req_id])
    return jsonify({"ok": True, "friend": {"uid": req["from_uid"], "username": req["from_username"]}})


@app.route("/api/friends/decline/<req_id>", methods=["POST"])
@login_required
def api_decline_friend(req_id):
    try:
        from db.friends import delete_request
        delete_request(uid(), req_id)
        return jsonify({"ok": True})
    except Exception:
        pass
    _save_friend_reqs(uid(), [r_ for r_ in _get_friend_reqs(uid()) if r_["id"] != req_id])
    return jsonify({"ok": True})


@app.route("/api/friends/<friend_uid>", methods=["DELETE"])
@login_required
def api_remove_friend(friend_uid):
    try:
        from db.friends import remove_friend_pair
        remove_friend_pair(uid(), friend_uid)
        return jsonify({"ok": True})
    except Exception:
        pass
    my_uid = uid()
    my_friends = [f for f in _get_friends(my_uid) if f["uid"] != friend_uid]
    their_friends = [f for f in _get_friends(friend_uid) if f["uid"] != my_uid]
    _save_friends(my_uid, my_friends)
    _save_friends(friend_uid, their_friends)
    return jsonify({"ok": True})


@app.route("/api/friends/<friend_uid>", methods=["PATCH"])
@login_required
def api_update_friend(friend_uid):
    data = request.get_json(silent=True) or {}
    # allow nickname and favorite
    nickname_set = "nickname" in data
    favorite_set = "favorite" in data
    if not nickname_set and not favorite_set:
        return jsonify({"error": "No fields to update"}), 400
    nickname = data.get("nickname") if nickname_set else None
    favorite = data.get("favorite") if favorite_set else None
    if nickname is not None and len(str(nickname).strip()) > 24:
        return jsonify({"error": "Nickname max 24 chars"}), 400
    if favorite is not None:
        favorite = bool(favorite)
    try:
        from db.friends import update_friend
        row = update_friend(uid(), friend_uid, nickname=nickname if nickname_set else None, favorite=favorite if favorite_set else None)
        if not row:
            return jsonify({"error": "Not friends"}), 404
        return jsonify({"ok": True, "friend": row})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/friends/heartbeat", methods=["POST"])
@login_required
def api_friend_heartbeat():
    _friend_heartbeat(uid())
    # also sweep stale presence (covers in-memory fallback)
    try:
        from db.friends import sweep_presence
        sweep_presence()
    except Exception:
        pass
    return jsonify({"ok": True})


@app.route("/api/friends/recent")
@login_required
def api_friends_recent():
    try:
        from db.friends import get_recent
        recent = get_recent(uid())
        # filter friends out
        fids = {f["uid"] for f in _get_friends(uid())}
        recent = [r for r in recent if r.get("uid") not in fids]
        return jsonify({"recent": recent})
    except Exception:
        return jsonify({"recent": []})


@app.route("/api/friends/<friend_uid>/stats")
@login_required
def api_friend_stats(friend_uid):
    try:
        from db.friends import head_to_head, are_friends
        if not are_friends(uid(), friend_uid):
            return jsonify({"error": "Not friends"}), 403
        return jsonify(head_to_head(uid(), friend_uid))
    except Exception:
        return jsonify({"w": 0, "l": 0, "d": 0})


@app.route("/api/friends/invite-match", methods=["POST"])
@login_required
def api_friends_invite_match():
    """One-click invite a friend to a match lobby (EA FC style)."""
    data = request.get_json(silent=True) or {}
    friend_uid = (data.get("friend_uid") or data.get("to_uid") or "").strip()
    if not friend_uid:
        return jsonify({"error": "friend_uid required"}), 400
    try:
        from db.friends import are_friends
        if not are_friends(uid(), friend_uid):
            return jsonify({"error": "Not friends"}), 403
    except Exception:
        if not any(f["uid"] == friend_uid for f in _get_friends(uid())):
            return jsonify({"error": "Not friends"}), 403
    # reuse invite logic: create room + push invite
    from db.redis_client import r as redis
    pw_hash = _hash_room_pw(data.get("password"))
    room_id = _uuid.uuid4().hex[:10]
    room = {"game": new_game_state(mode="hvh"), "player_a": uid(), "player_b": None, "name_a": session.get("username", "Player A"), "name_b": None, "status": "waiting", "last_move": None, "started_at": _time.time()}
    if pw_hash:
        room["password_hash"] = pw_hash
        room["has_password"] = True
    _save_room(room_id, room)
    invite_id = _uuid.uuid4().hex[:12]
    invite = {"from_uid": uid(), "from_name": session.get("username", "Player"), "to_uid": friend_uid, "room_id": room_id, "status": "pending"}
    if pw_hash:
        invite["has_password"] = True
    try:
        redis.setex(f"invite:{invite_id}", 86400, _json.dumps(invite))
        redis.lpush(f"user_invites:{friend_uid}", invite_id)
        redis.expire(f"user_invites:{friend_uid}", 86400)
    except Exception:
        pass
    return jsonify({"ok": True, "room_id": room_id, "invite_id": invite_id})


#  Chat (match / tournament-lobby / friend DMs) 
# Delivery is polling (GET /chat/messages?after=<mid>) — same pattern as the
# room state endpoints. Match + lobby chat are ephemeral Redis lists with TTL;
# DMs persist in Supabase (migration_chat.sql). Rate limit, profanity filter,
# report + block are server-side.

def _chat_storage():
    from db.chat import ChatUnavailable
    return ChatUnavailable


def _can_lobby_chat(tid: str, user_id: str) -> bool:
    from db.tournaments import get_tournament
    t = get_tournament(tid)
    if not t:
        return False
    if t.get("creator_id") == user_id:
        return True
    return any(p.get("participant_id") == f"friend:{user_id}"
               for p in t.get("participants", []))


def _can_clan_chat(clan_id: str, user_id: str) -> bool:
    from db.clans import get_clan, list_members
    clan = get_clan(clan_id)
    if not clan:
        return False
    return any(m.get("user_id") == user_id for m in list_members(clan_id))


def _is_friends_with(a: str, b: str) -> bool:
    try:
        from db.friends import are_friends as _af
        return _af(a, b)
    except Exception:
        a_has_b = any(f["uid"] == b for f in _get_friends(a))
        b_has_a = any(f["uid"] == a for f in _get_friends(b))
        return a_has_b and b_has_a


def _push_recent_pair(a_uid: str, a_name: str, b_uid: str, b_name: str) -> None:
    try:
        from db.friends import push_recent
        push_recent(a_uid, b_uid, b_name)
        push_recent(b_uid, a_uid, a_name)
    except Exception:
        pass


def _set_presence_in_match(uid_: str, room_id: str) -> None:
    try:
        from db.friends import set_presence as _sp
        _sp(uid_, "in_match", room_id)
    except Exception:
        pass


@app.route("/messages")
@login_required
def messages_page():
    return render_template("messages.html", username=session.get("username", "Player"))


@app.route("/chat/send", methods=["POST"])
def chat_send():
    from db.chat import (contains_profanity, check_rate_limit,
                         send_ephemeral, send_dm, get_blocked, conv_id)
    from db.chat import ChatUnavailable
    data  = request.get_json(silent=True) or {}
    scope = (data.get("scope") or "").strip()
    body  = (data.get("body") or "").strip()
    if scope not in ("match", "tournament", "dm", "global", "clan"):
        return jsonify({"error": "Invalid scope"}), 400
    if not body:
        return jsonify({"error": "Message is empty"}), 400
    if len(body) > 280:
        return jsonify({"error": "Message too long (max 280 characters)"}), 400
    if scope in ("tournament", "dm", "clan") and "user_id" not in session:
        return jsonify({"error": "Not authenticated"}), 401
    if not uid():
        import uuid
        session["user_id"]  = f"guest:{uuid.uuid4().hex[:12]}"
        session["username"] = "Guest"
    my_uid  = uid()
    my_name = session.get("username", "Player")

    if contains_profanity(body):
        return jsonify({"error": "Message blocked by the content filter"}), 400

    allowed, retry_after = check_rate_limit(my_uid)
    if not allowed:
        return jsonify({"error": "You're sending messages too fast", "retry_after": retry_after}), 429

    try:
        if scope == "match":
            scope_id = (data.get("scope_id") or "").strip()
            room = _get_room(scope_id)
            if not room:
                return jsonify({"error": "Room not found"}), 404
            if my_uid not in (room["player_a"], room["player_b"]):
                return jsonify({"error": "You're not in this match"}), 403
            msg = send_ephemeral("match", scope_id, my_uid, my_name, body)
        elif scope == "tournament":
            scope_id = (data.get("scope_id") or "").strip()
            if not _can_lobby_chat(scope_id, my_uid):
                return jsonify({"error": "You're not in this tournament"}), 403
            msg = send_ephemeral("tournament", scope_id, my_uid, my_name, body)
        elif scope == "clan":
            scope_id = (data.get("scope_id") or "").strip()
            if not scope_id or not _can_clan_chat(scope_id, my_uid):
                return jsonify({"error": "You're not in this clan"}), 403
            if scope_id in get_blocked(my_uid):
                return jsonify({"error": "You've blocked this clan"}), 403
            msg = send_ephemeral("clan", scope_id, my_uid, my_name, body)
        elif scope == "global":
            # Server-wide chat — any logged-in user, no scope_id needed
            scope_id = "global"
            msg = send_ephemeral("global", scope_id, my_uid, my_name, body)
        else:
            to_uid = (data.get("to_uid") or "").strip()
            if not to_uid or to_uid == my_uid:
                return jsonify({"error": "Invalid recipient"}), 400
            if not _is_friends_with(my_uid, to_uid):
                return jsonify({"error": "You can only message friends"}), 403
            if to_uid in get_blocked(my_uid):
                return jsonify({"error": "You've blocked this user"}), 403
            if my_uid in get_blocked(to_uid):
                return jsonify({"error": "This user has blocked you"}), 403
            msg = send_dm(my_uid, my_name, conv_id(my_uid, to_uid), body)
    except ChatUnavailable:
        return jsonify({"error": "Chat storage is unavailable right now"}), 503
    try:
        _mem_short(uid(), [{"role": "user", "content": body}], category="short", metadata={"scope": scope, "scope_id": data.get("scope_id") or data.get("to_uid")})
    except: pass
    return jsonify({"ok": True, "message": msg})


@app.route("/chat/messages")
def chat_messages():
    from db.chat import (get_ephemeral, get_dm, get_blocked, mark_read,
                         conv_id, conv_parties)
    from db.chat import ChatUnavailable
    scope = (request.args.get("scope") or "").strip()
    if scope not in ("match", "tournament", "dm", "global", "clan"):
        return jsonify({"error": "Invalid scope"}), 400
    if scope in ("tournament", "dm", "clan") and "user_id" not in session:
        return jsonify({"error": "Not authenticated"}), 401
    my_uid = uid()
    raw_after = request.args.get("after")
    try:
        after = int(raw_after) if raw_after not in (None, "") else None
    except ValueError:
        after = None
    try:
        limit = max(1, min(100, int(request.args.get("limit", 60))))
    except ValueError:
        limit = 60
    blocked = get_blocked(my_uid) if my_uid else set()

    try:
        if scope in ("match", "tournament", "global", "clan"):
            scope_id = (request.args.get("scope_id") or "").strip()
            if scope == "global":
                scope_id = "global"
                # any logged-in user can read global
                if "user_id" not in session:
                    return jsonify({"error": "Not authenticated"}), 401
            else:
                if not scope_id:
                    return jsonify({"error": "scope_id required"}), 400
            if scope == "match":
                room = _get_room(scope_id)
                if not room:
                    return jsonify({"error": "Room not found"}), 404
                if my_uid not in (room["player_a"], room["player_b"]):
                    # Spectators may read a LIVE match's chat, but never post.
                    # Once the match ends (or the room is gone) read access closes.
                    if room.get("status") != "active":
                        return jsonify({"error": "You're not in this match"}), 403
            elif scope == "global":
                pass
            elif scope == "clan":
                if not _can_clan_chat(scope_id, my_uid):
                    return jsonify({"error": "You're not in this clan"}), 403
            else:
                if not _can_lobby_chat(scope_id, my_uid):
                    return jsonify({"error": "You're not in this tournament"}), 403
            msgs, next_after = get_ephemeral(scope, scope_id, after, limit, blocked)
        else:
            scope_id = (request.args.get("scope_id") or "").strip()
            with_uid = (request.args.get("with") or "").strip()
            if not scope_id and with_uid:
                if not _is_friends_with(my_uid, with_uid):
                    return jsonify({"error": "You can only message friends"}), 403
                scope_id = conv_id(my_uid, with_uid)
            if not scope_id:
                return jsonify({"error": "scope_id or with required"}), 400
            me, other = conv_parties(scope_id)
            if my_uid not in (me, other):
                return jsonify({"error": "You're not part of this conversation"}), 403
            msgs, next_after = get_dm(scope_id, after, limit, blocked)
            if request.args.get("mark_read") == "1" and my_uid:
                mark_read(my_uid, scope_id, next_after)
    except ChatUnavailable:
        return jsonify({"error": "Chat storage is unavailable right now"}), 503
    return jsonify({"messages": msgs, "next_after": str(next_after), "scope_id": scope_id, "me": my_uid})


@app.route("/chat/conversations")
@login_required
def chat_conversations():
    from db.chat import get_conversations
    from db.chat import ChatUnavailable
    try:
        convs = get_conversations(uid())
    except ChatUnavailable:
        return jsonify({"error": "Chat storage is unavailable right now"}), 503
    return jsonify({"conversations": convs})


@app.route("/chat/report", methods=["POST"])
@login_required
def chat_report():
    from db.chat import report_message
    from db.chat import ChatUnavailable
    data    = request.get_json(silent=True) or {}
    scope   = (data.get("scope") or "").strip()
    scope_id = (data.get("scope_id") or "").strip()
    mid     = (data.get("mid") or "").strip()
    reason  = (data.get("reason") or "").strip()[:200]
    if scope not in ("match", "tournament", "dm") or not scope_id or not mid:
        return jsonify({"error": "Missing fields"}), 400
    try:
        ok = report_message(uid(), scope, scope_id, mid, reason)
    except ChatUnavailable:
        return jsonify({"error": "Chat storage is unavailable right now"}), 503
    if not ok:
        return jsonify({"error": "Message not found"}), 404
    return jsonify({"ok": True})


@app.route("/chat/block", methods=["POST"])
@login_required
def chat_block():
    from db.chat import block_user
    from db.chat import ChatUnavailable
    data    = request.get_json(silent=True) or {}
    target  = (data.get("user_id") or "").strip()
    if not target or target == uid():
        return jsonify({"error": "Invalid user"}), 400
    try:
        block_user(uid(), target)
    except ChatUnavailable:
        return jsonify({"error": "Chat storage is unavailable right now"}), 503
    return jsonify({"ok": True})


@app.route("/chat/unblock", methods=["POST"])
@login_required
def chat_unblock():
    from db.chat import unblock_user
    from db.chat import ChatUnavailable
    data   = request.get_json(silent=True) or {}
    target = (data.get("user_id") or "").strip()
    if not target:
        return jsonify({"error": "Invalid user"}), 400
    try:
        unblock_user(uid(), target)
    except ChatUnavailable:
        return jsonify({"error": "Chat storage is unavailable right now"}), 503
    return jsonify({"ok": True})


@app.route("/chat/blocked")
@login_required
def chat_blocked():
    from db.chat import get_blocked
    return jsonify({"blocked": sorted(get_blocked(uid()))})


#  Tournaments 

@app.route("/tournaments")
@login_required
def tournaments_page():
    from db.tournaments import get_tournaments
    t_list = get_tournaments()
    return render_template("tournaments.html", username=session.get("username", "Player"), tournaments=t_list)

@app.route("/tournaments/create", methods=["POST"])
@login_required
def create_tournament_api():
    from db.tournaments import create_tournament
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "Unnamed Tournament").strip()
    t = create_tournament(uid(), name)
    _ach_grant(uid(), "tour_playmaker")
    return jsonify({"ok": True, "tournament": t, "achievements": _ach_toasts()})

@app.route("/tournaments/<tid>")
@login_required
def tournament_view(tid):
    from db.tournaments import get_tournament
    t = get_tournament(tid)
    if not t:
        flash("Tournament not found")
        return redirect(url_for("tournaments_page"))
    # build a list of all available models to pick from
    avail = _builtin_model_list()
    try:
        from db.user_models import get_user_models
        for m in get_user_models(uid()):
            avail.append({"id": USER_MODEL_PREFIX + m["id"], "name": m["name"]})
    except: pass
    
    # also add friends
    friends = _get_friends(uid())
    return render_template("tournament_view.html", username=session.get("username", "Player"), t=t, models=avail, friends=friends)

@app.route("/tournaments/<tid>/add", methods=["POST"])
@login_required
def tournament_add_participant(tid):
    from db.tournaments import add_participant, get_tournament
    t = get_tournament(tid)
    if not t or t["creator_id"] != uid(): return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json(silent=True) or {}
    pid = data.get("participant_id")
    name = data.get("name")
    if not pid or not name: return jsonify({"error": "Missing info"}), 400
    p = add_participant(tid, pid, name)
    return jsonify({"ok": True, "participant": p})

@app.route("/tournaments/<tid>/generate", methods=["POST"])
@login_required
def tournament_generate(tid):
    from db.tournaments import generate_bracket, get_tournament
    t = get_tournament(tid)
    if not t or t["creator_id"] != uid(): return jsonify({"error": "Unauthorized"}), 403
    if generate_bracket(tid):
        return jsonify({"ok": True})
    return jsonify({"error": "Could not generate bracket"}), 400

def _tournament_champion_award(tid: str) -> None:
    """Champion badge: when a tournament completes, award it to the owner of
    the human (`friend:<uid>`) participant who won the final — not a model."""
    from db.tournaments import get_tournament
    t = get_tournament(tid)
    if not t or t.get("status") != "completed":
        return
    rounds = [x.get("round_num", 0) for x in t.get("matches", [])]
    finals = [x for x in t.get("matches", [])
              if x.get("round_num") == (max(rounds) if rounds else 0)]
    final_winner = finals[0].get("winner") if finals else None
    if not final_winner:
        return
    for p in t.get("participants", []):
        if p.get("id") == final_winner:
            pid = p.get("participant_id") or ""
            if pid.startswith("friend:"):
                _ach_grant(pid[len("friend:"):], "tour_champion")
            break


@app.route("/tournaments/<tid>/simulate/<match_id>", methods=["POST"])
@login_required
def tournament_simulate(tid, match_id):
    from db.tournaments import get_tournament, get_match, save_match_result
    from models.soccer_logic import new_soccer_state, apply_kick as _kick
    t = get_tournament(tid)
    if not t or t["creator_id"] != uid(): return jsonify({"error": "Unauthorized"}), 403
    m = get_match(tid, match_id)
    if not m or m["status"] != "pending": return jsonify({"error": "Invalid match"}), 400
    
    parts = {p["id"]: p for p in t["participants"]}
    pa = parts.get(m["participant_a"])
    pb = parts.get(m["participant_b"])
    if not pa or not pb: return jsonify({"error": "Missing participants"}), 400
    
    # Load models
    def _run_model(participant_info, state, is_player_a):
        pid = participant_info["participant_id"]
        # If it's a friend (not a model), we fallback to greedy for now since we don't have async human-play built in for tournaments
        if pid.startswith("friend:"):
            mod = _load_model("greedy")
            return mod.get_ai_move(state, is_player_a)
        else:
            mod = _load_model(pid)
            return mod.get_ai_move(state, is_player_a)
            
    st = new_soccer_state()
    st["move_history"] = []

    pending_traces: list[dict] = []
    turn_n = 0  # monotonic kick counter (penalty kicks don't advance kick_count)
    for __ in range(40):
        if st.get("game_over"): break
        is_a = st["is_player_a"]
        try:
            pidx, ang, pwr = _run_model(pa if is_a else pb, st, is_a)
            from game.session import push_snapshot
            push_snapshot(st)
            # Loss-analysis capture: trace turns of the tournament creator's
            # own user models only (anonymous/guest runs are never traced).
            participant = pa if is_a else pb
            pid = (participant or {}).get("participant_id") or ""
            snap = None
            if pid.startswith(USER_MODEL_PREFIX):
                snap = dict(st)
                snap.pop("move_history", None)
            traj, scored, desc, kick_ep, push_res = _kick(st, pidx, ang, pwr, is_a)
            if snap is not None:
                pending_traces.append({
                    "is_a": is_a,
                    "model_id": pid,
                    "model_label": (participant.get("name") or pid),
                    "opponent": (pb if is_a else pa).get("name") or "Opponent",
                    "turn": turn_n,
                    "mover": "a" if is_a else "b",
                    "snapshot": snap,
                    "decision": {"player_idx": pidx, "angle": round(ang, 1), "power": round(pwr, 1)},
                    "scored": scored,
                    "trajectory": traj,
                })
            st["move_history"].append({
                "mover": "a" if is_a else "b",
                "player_idx": pidx, "angle": round(ang,1), "power": round(pwr,1),
                "trajectory": traj, "push_result": push_res, "scored": scored
            })
            turn_n += 1
        except Exception as e:
            # If a model crashes, other player wins
            st["winner"] = "B" if is_a else "A"
            st["game_over"] = True
            break

    winner_id = m["participant_a"] if st.get("winner") == "A" else m["participant_b"]
    save_match_result(tid, match_id, winner_id, st["move_history"])
    _tournament_champion_award(tid)
    if pending_traces:
        _persist_tournament_traces(uid(), tid, match_id, st, pending_traces)
    resp = {"ok": True, "winner": winner_id}
    resp["achievements"] = _ach_toasts()
    return jsonify(resp)

@app.route("/tournaments/<tid>/watch/<match_id>")
@login_required
def tournament_watch(tid, match_id):
    return redirect(url_for("tournament_watch_3d", tid=tid, match_id=match_id))


@app.route("/replay3d/<tid>/<match_id>")
@login_required
def tournament_watch_3d(tid, match_id):
    from db.tournaments import get_tournament, get_match
    from db.highlights import get_highlights
    t = get_tournament(tid)
    m = get_match(tid, match_id)
    if not t or not m or m["status"] != "completed":
        flash("Match not available for replay")
        return redirect(url_for("tournament_view", tid=tid))
    hls = get_highlights(tid, match_id) or []
    return render_template("replay_3d.html", username=session.get("username", "Player"),
                           t=t, match=m, highlights=hls, highlight=None, live_room=None,
                           loss_model=None, loss_model_name=None)


@app.route("/matches/<tid>/<match_id>/highlights")
@login_required
def match_highlights_api(tid, match_id):
    from db.tournaments import get_tournament, get_match
    from db.highlights import get_highlights
    t = get_tournament(tid)
    m = get_match(tid, match_id)
    if not t or not m or m["status"] != "completed":
        return jsonify({"error": "Not found"}), 404
    return jsonify({"highlights": get_highlights(tid, match_id) or []})


@app.route("/highlight/<hid>")
@login_required
def highlight_page(hid):
    from db.tournaments import get_tournament, get_match
    from db.highlights import resolve_highlight, get_highlights
    h = resolve_highlight(hid)
    if not h:
        flash("Highlight not found (replay may have expired)")
        return redirect(url_for("tournaments_page"))
    t = get_tournament(h["tid"])
    m = get_match(h["tid"], h["match_id"])
    if not t or not m or m["status"] != "completed":
        flash("Match not available for replay")
        return redirect(url_for("tournament_view", tid=h["tid"]))
    hls = get_highlights(h["tid"], h["match_id"]) or []
    return render_template("replay_3d.html", username=session.get("username", "Player"),
                           t=t, match=m, highlights=hls, highlight=h, live_room=None,
                           loss_model=None, loss_model_name=None)


#  Clan tournaments — leader creates, members join, scheduled with timezone, 10min DQ
@app.route("/api/clans/<clan_id>/tournaments", methods=["GET"])
@login_required
def api_clan_tournaments_list(clan_id):
    from db.clans import get_clan
    from db.tournaments import get_tournaments as _gt
    clan = get_clan(clan_id)
    if not clan:
        return jsonify({"error": "Clan not found"}), 404
    # must be member to list
    if not any(m["user_id"] == uid() for m in clan.get("members", [])):
        return jsonify({"error": "Not a clan member"}), 403
    from db.tournaments import get_tournament as _getT
    raw = _gt(clan_id=clan_id)
    ts = []
    for t in raw:
        full = _getT(t["id"])
        if full:
            ts.append(full)
    return jsonify({"tournaments": ts})

@app.route("/api/clans/<clan_id>/tournaments", methods=["POST"])
@login_required
def api_clan_tournaments_create(clan_id):
    from db.clans import get_clan
    from db.tournaments import create_tournament
    clan = get_clan(clan_id)
    if not clan or clan.get("leader_id") != uid():
        return jsonify({"error": "Only leader can create tournament"}), 403
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name or len(name) < 2:
        return jsonify({"error": "Name required (2+ chars)"}), 400
    scheduled_at = (data.get("scheduled_at") or "").strip() or None
    # validate ISO with timezone if provided
    if scheduled_at:
        try:
            from datetime import datetime
            iso = scheduled_at.replace("Z", "+00:00")
            datetime.fromisoformat(iso)
        except Exception:
            return jsonify({"error": "Invalid scheduled_at, use ISO like 2026-09-01T08:00:00+07:00"}), 400
    t = create_tournament(uid(), name, clan_id=clan_id, scheduled_at=scheduled_at)
    return jsonify({"ok": True, "tournament": t}), 201

@app.route("/api/clans/<clan_id>/tournaments/<tid>/join", methods=["POST"])
@login_required
def api_clan_tournament_join(clan_id, tid):
    from db.clans import get_clan
    from db.tournaments import get_tournament, add_participant
    clan = get_clan(clan_id)
    if not clan or not any(m["user_id"] == uid() for m in clan.get("members", [])):
        return jsonify({"error": "Not a clan member"}), 403
    t = get_tournament(tid)
    if not t or t.get("clan_id") != clan_id:
        return jsonify({"error": "Tournament not found"}), 404
    if t.get("status") != "pending":
        return jsonify({"error": "Tournament already started"}), 400
    # already joined?
    if any(p.get("participant_id") == f"clan_user:{uid()}" for p in t.get("participants", [])):
        return jsonify({"error": "Already joined"}), 400
    p = add_participant(tid, f"clan_user:{uid()}", session.get("username", "Player"))
    return jsonify({"ok": True, "participant": p})

@app.route("/api/clans/<clan_id>/tournaments/<tid>/generate", methods=["POST"])
@login_required
def api_clan_tournament_generate(clan_id, tid):
    from db.clans import get_clan
    from db.tournaments import get_tournament, generate_bracket
    clan = get_clan(clan_id)
    if not clan or clan.get("leader_id") != uid():
        return jsonify({"error": "Only leader can generate"}), 403
    t = get_tournament(tid)
    if not t or t.get("clan_id") != clan_id:
        return jsonify({"error": "Tournament not found"}), 404
    data = request.get_json(silent=True) or {}
    # match_scheduled_at is UTC ISO with offset, e.g. 2026-09-01T08:00:00+07:00
    # frontend sends full timezone, backend stores as-is and disqualifies +10min
    match_at = (data.get("match_scheduled_at") or data.get("scheduled_at") or "").strip() or None
    if match_at:
        try:
            from datetime import datetime
            iso = match_at.replace("Z", "+00:00")
            datetime.fromisoformat(iso)
        except Exception:
            return jsonify({"error": "Invalid match_scheduled_at, use ISO like 2026-09-01T08:00:00+07:00"}), 400
    ok = generate_bracket(tid, match_scheduled_at=match_at)
    if not ok:
        return jsonify({"error": "Could not generate bracket (need 2+ participants or already generated)"}), 400
    return jsonify({"ok": True})

@app.route("/api/clans/<clan_id>/tournaments/<tid>/matches/<mid>/join", methods=["POST"])
@login_required
def api_clan_match_join(clan_id, tid, mid):
    from db.clans import get_clan
    from db.tournaments import get_tournament, get_match, mark_joined
    clan = get_clan(clan_id)
    if not clan or not any(m["user_id"] == uid() for m in clan.get("members", [])):
        return jsonify({"error": "Not a clan member"}), 403
    t = get_tournament(tid)
    if not t or t.get("clan_id") != clan_id:
        return jsonify({"error": "Tournament not found"}), 404
    m = get_match(tid, mid)
    if not m or m.get("status") != "pending":
        return jsonify({"error": "Match not pending"}), 400
    # find participant id for this user in this tournament
    pid = None
    for p in t.get("participants", []):
        if p.get("participant_id") == f"clan_user:{uid()}":
            pid = p["id"]
            break
    if not pid or pid not in (m.get("participant_a"), m.get("participant_b")):
        return jsonify({"error": "Not a participant in this match"}), 403
    # check not already past deadline
    from db.tournaments import _parse_iso, _now_utc
    sched = _parse_iso(m.get("scheduled_at"))
    if sched and sched.tzinfo is None:
        from datetime import timezone
        sched = sched.replace(tzinfo=timezone.utc)
    if sched and _now_utc().timestamp() > sched.timestamp() + 600:
        return jsonify({"error": "Match already disqualified (10min past schedule)"}), 400
    mark_joined(tid, mid, pid)
    return jsonify({"ok": True})

@app.route("/match/<room_id>/summary")
def match_summary_page(room_id):
    """Public shareable post-match summary card.

    Deliberately no `@login_required`: the whole point of the card is
    that it can be opened outside the app (same public pattern as the
    live spectator pages). Data is a read-only aggregate of the snapshot
    stored at match end; names/avatars resolve live from profiles.
    """
    from db.summaries import get_summary
    from db.profiles import get_avatar_url
    summary = get_summary(room_id)
    if not summary:
        flash("Match summary not found (or the match never finished)")
        return redirect(url_for("index"))
    for side in ("a", "b"):
        uid_ = summary.get(f"player_{side}")
        summary[f"avatar_{side}"] = get_avatar_url(uid_) if uid_ else None
    return render_template("match_summary.html",
                           username=session.get("username", ""),
                           s=summary)


#  Clerk — verify session 
@app.route("/api/auth/clerk/verify", methods=["POST"])
def clerk_verify():
    data = request.get_json(silent=True) or {}
    token = data.get("session_token", "")
    if not token:
        return jsonify({"verified": False, "error": "No token"}), 400
    from services.clerk import verify_session as _clerk_verify
    result = _clerk_verify(token)
    if result:
        uid_ = result.get("user_id", "")
        session["user_id"] = f"clerk:{uid_}"
        session["username"] = result.get("username", "ClerkUser")
        _ph.track_pageview(uid_, "clerk_login")
        return jsonify({"verified": True, "user_id": uid_})
    return jsonify({"verified": False, "error": "Invalid token"}), 401


#  Feedback page 
@app.route("/feedback")
@login_required
def feedback_page():
    return render_template("feedback.html", username=session.get("username", "Player"))


#  About page feedback — server-side recipient, never leaks to client
@app.route("/api/feedback/about", methods=["POST"])
def api_feedback_about():
    # public, no login required — rate-limited via shared limiter
    if not _check_rate_limit("/api/feedback/about"):
        return jsonify({"error": "Too many requests. Try again later."}), 429
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()[:40]
    email = (data.get("email") or "").strip()[:80]
    message = (data.get("message") or "").strip()
    if not message or len(message) < 5:
        return jsonify({"error": "Message is too short"}), 400
    if len(message) > 2000:
        return jsonify({"error": "Message too long (max 2000)"}), 400
    # basic email check if provided
    if email and "@" not in email:
        return jsonify({"error": "Invalid email"}), 400
    from config import FEEDBACK_TO_EMAIL, RESEND_API_KEY
    # recipient is env-only; if not configured, log and return ok (no leak)
    to_addr = (FEEDBACK_TO_EMAIL or "").strip()
    if not to_addr:
        # In DEV_MODE or missing env, just log — still return ok to avoid probing
        app.logger.info("About feedback (no FEEDBACK_TO_EMAIL set): from %s <%s> — %s", name, email, message[:200])
        return jsonify({"ok": True})
    if not RESEND_API_KEY:
        app.logger.info("About feedback (Resend not configured): from %s <%s>", name, email)
        return jsonify({"ok": True, "queued": True})
    try:
        from services.resend import send_email
        html = f"<h3>About page feedback</h3><p><b>From:</b> {name} &lt;{email}&gt;</p><p><b>Message:</b></p><p>{message.replace(chr(10), '<br>')}</p><p><i>User: {session.get('username','guest')} / {session.get('user_id','-')}</i></p>"
        send_email(to_addr, f"[Agent Soccer] About feedback from {name or 'visitor'}", html)
        _ph.capture(session.get("user_id") or "guest", "about_feedback", {"has_email": bool(email)})
    except Exception as e:
        app.logger.warning("About feedback send failed: %s", e)
        return jsonify({"ok": True, "queued": True})
    return jsonify({"ok": True})


#  ProductBridge — feedback 
@app.route("/api/feedback", methods=["POST"])
@login_required
def api_submit_feedback():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    desc = (data.get("description") or "").strip()
    if not title:
        return jsonify({"error": "Title is required"}), 400
    from services.productbridge import submit_feedback
    result = submit_feedback(
        title=title,
        description=desc,
        user_email=session.get("username", ""),
    )
    if result:
        _ph.capture(uid(), "feedback_submitted", {"title": title})
        return jsonify({"ok": True, "result": result})
    return jsonify({"error": "Feedback service unavailable"}), 502


@app.route("/api/feedback/boards", methods=["GET"])
@login_required
def api_list_feedback_boards():
    from services.productbridge import list_boards
    boards = list_boards()
    return jsonify({"boards": boards})


#  Pinecone — AI move embedding 
@app.route("/api/embed/move", methods=["POST"])
@login_required
def api_embed_move():
    data = request.get_json(silent=True) or {}
    vector_id = data.get("id", "")
    values = data.get("values", [])
    metadata = data.get("metadata", {})
    if not vector_id or not values:
        return jsonify({"error": "id and values required"}), 400
    from services.pinecone import upsert_vector
    ok = upsert_vector(vector_id, values, metadata)
    return jsonify({"ok": ok})


@app.route("/api/embed/query", methods=["POST"])
@login_required
def api_query_embed():
    data = request.get_json(silent=True) or {}
    values = data.get("values", [])
    top_k = int(data.get("top_k", 5))
    if not values:
        return jsonify({"error": "values required"}), 400
    from services.pinecone import query_vector
    matches = query_vector(values, top_k=top_k)
    return jsonify({"matches": matches})


@app.route("/api/embed/stats", methods=["GET"])
@login_required
def api_embed_stats():
    from services.pinecone import describe_index_stats
    stats = describe_index_stats()
    return jsonify(stats)


#  Research Hub — paper search & library 

@app.route("/research")
@login_required
def research_page():
    from services.paper_search import SUGGESTED_QUERIES
    return render_template("research.html",
                           username=session.get("username", "Player"),
                           suggested_queries=SUGGESTED_QUERIES)


@app.route("/api/research/search")
@login_required
def api_research_search():
    from services.paper_search import search_papers, format_citation
    q = (request.args.get("q") or "").strip()
    limit = int(request.args.get("limit", 10))
    if not q or len(q) < 2:
        return jsonify({"error": "Query must be at least 2 characters"}), 400
    results = search_papers(q, limit)
    for r in results:
        r["_citation"] = format_citation(r)
    return jsonify({"results": results})


@app.route("/api/research/detail")
@login_required
def api_research_detail():
    from services.paper_search import get_paper_detail, get_recommended_papers, format_citation
    paper_id = (request.args.get("paper_id") or "").strip()
    if not paper_id:
        return jsonify({"error": "paper_id required"}), 400
    paper = get_paper_detail(paper_id)
    if not paper:
        return jsonify({"error": "Paper not found"}), 404
    paper["_citation"] = format_citation(paper)
    recs = get_recommended_papers(paper_id)
    for r in recs:
        r["_citation"] = format_citation(r)
    return jsonify({"paper": paper, "recommendations": recs})


@app.route("/api/research/saved")
@login_required
def api_research_saved():
    from db.research_papers import get_saved_papers
    papers = get_saved_papers(uid())
    return jsonify({"papers": papers})


@app.route("/api/research/save", methods=["POST"])
@login_required
def api_research_save():
    from db.research_papers import save_paper
    data = request.get_json(silent=True) or {}
    paper = data.get("paper", {})
    if not paper.get("paperId"):
        return jsonify({"error": "Invalid paper data"}), 400
    result = save_paper(uid(), paper)
    if result:
        return jsonify({"ok": True, "paper": result})
    return jsonify({"error": "Failed to save paper"}), 500


@app.route("/api/research/save/notes", methods=["POST"])
@login_required
def api_research_save_notes():
    from db.research_papers import update_paper_notes
    data = request.get_json(silent=True) or {}
    paper_id = data.get("paper_id", "")
    notes = data.get("notes", "")
    tags = data.get("tags")
    if not paper_id:
        return jsonify({"error": "paper_id required"}), 400
    ok = update_paper_notes(uid(), paper_id, notes, tags)
    return jsonify({"ok": ok})


@app.route("/api/research/delete", methods=["POST"])
@login_required
def api_research_delete():
    from db.research_papers import delete_paper
    data = request.get_json(silent=True) or {}
    paper_id = data.get("paper_id", "")
    if not paper_id:
        return jsonify({"error": "paper_id required"}), 400
    ok = delete_paper(uid(), paper_id)
    return jsonify({"ok": ok})


#  AI Arena — model benchmarking & analytics 
_LB_DEFAULT_GAMES = 5


def _persist_tournament_traces(owner_id: str, tid: str, match_id: str,
                               st: dict, pending: list[dict]) -> None:
    """Persist traced turns collected during a tournament sim.

    `st` is the final state (winner + scores). All failures are swallowed —
    loss analysis must never affect the match result.
    """
    from services.loss_analysis import save_traced_turn
    w = st.get("winner")
    for t in pending:
        side = "A" if t["is_a"] else "B"
        result = "win" if w == side else ("loss" if w in ("A", "B") else "draw")
        save_traced_turn(
            owner_id=owner_id,
            model_id=t["model_id"],
            model_label=t["model_label"],
            match_id=f"tournament:{tid}:{match_id}",
            opponent=t["opponent"],
            result=result,
            score_for=st["score_a"] if side == "A" else st["score_b"],
            score_against=st["score_b"] if side == "A" else st["score_a"],
            turn=t["turn"],
            mover=t["mover"],
            pre_state=t["snapshot"],
            decision=t["decision"],
            scored=t["scored"],
            trajectory=t["trajectory"],
        )


def _persist_battle_traces(owner_id: str, model_key: str, model_label: str,
                           opponent_label: str, side: str,
                           battle_result: dict, match_prefix: str) -> None:
    """Persist traced turns from a `run_model_battle` result.

    `battle_result` is the aggregate dict; its per-game entries carry
    `traced_turns` when a tracer was active. Match ids are deterministic
    per (match_prefix, game_idx) so re-runs overwrite idempotently.
    """
    from services.loss_analysis import save_traced_turn
    try:
        for gr in battle_result.get("games") or []:
            turns = gr.get("traced_turns") or []
            if not turns:
                continue
            w = gr.get("winner")
            result = "win" if w == side else ("loss" if w in ("A", "B") else "draw")
            score_for = gr["score_a"] if side == "A" else gr["score_b"]
            score_against = gr["score_b"] if side == "A" else gr["score_a"]
            match_id = f"{match_prefix}:{gr.get('game_idx', 0)}"
            for t in turns:
                save_traced_turn(
                    owner_id=owner_id,
                    model_id=model_key,
                    model_label=model_label,
                    match_id=match_id,
                    opponent=opponent_label,
                    result=result,
                    score_for=score_for,
                    score_against=score_against,
                    turn=t["turn"],
                    mover=t["mover"],
                    pre_state=t["snapshot"],
                    decision=t["decision"],
                    scored=t["scored"],
                    trajectory=t["trajectory"],
                )
    except Exception:
        pass


def _builtin_label(model_key: str) -> str:
    from services.game_analytics import MODEL_CATALOG
    return next((m["name"] for m in MODEL_CATALOG if m["id"] == model_key), model_key)


def _run_leaderboard_bench(model_id: str, user_id: str, model_name: str,
                           code: str, n_games: int) -> None:
    """Background benchmark for the model leaderboard (runs ~7 × n games)."""
    import datetime
    from services.game_analytics import benchmark_model_vs_builtins
    from db.leaderboard import (set_status, save_submission, get_submission)
    from db.user_models import update_model
    try:
        wrapper = _UserModelWrapper(model_id, model_name, code)
        total = 7 * n_games
        set_status(model_id, "running", done=0, total=total)

        def _progress(d, n):
            set_status(model_id, "running", done=d, total=n)

        result = benchmark_model_vs_builtins(
            wrapper, n_games=n_games, progress_callback=_progress,
            tracer={"side": "A"})
        save_submission(model_id, user_id, model_name, result["score"],
                        n_games, result["details"])
        bench_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        update_model(model_id, user_id,
                     submitted_to_leaderboard=True, last_benchmarked_at=bench_ts)
        sub = get_submission(model_id)
        set_status(model_id, "done", done=result["n_games"] * 7, total=result["n_games"] * 7,
                   score=result["score"], details=result["details"],
                   avg_stats=result["avg_stats"], model_name=model_name,
                   benchmarked_at=(sub or {}).get("benchmarked_at", bench_ts))
        _ach_grant(user_id, "ai_first_bench")
        details = result.get("details") or []
        if details and all(d.get("win_rate", 0) > 50 for d in details):
            _ach_grant(user_id, "ai_beat_all")
        rank = _model_rank(model_id)
        if rank is not None:
            if rank <= 5:
                _ach_grant(user_id, "ai_top_5")
            if rank == 1:
                _ach_grant(user_id, "ai_rank_one")
        # Loss-analysis capture (bench always runs the user model as team A).
        model_key = f"{USER_MODEL_PREFIX}{model_id}"
        for opp_chunk in (result.get("traced_games") or []):
            opp_id = opp_chunk.get("opponent", "")
            opp_label = opp_chunk.get("opponent_label", opp_id)
            chunk = dict(result)
            chunk["games"] = opp_chunk.get("games") or []
            _persist_battle_traces(
                owner_id=user_id,
                model_key=model_key,
                model_label=model_name,
                opponent_label=opp_label,
                side="A",
                battle_result=chunk,
                match_prefix=f"bench:{model_id}:{opp_id}",
            )
    except Exception as exc:
        import traceback
        traceback.print_exc()
        set_status(model_id, "failed", error=str(exc))


@app.route("/api/models/user/<model_id>/submit-leaderboard", methods=["POST"])
@login_required
def api_submit_leaderboard(model_id: str):
    from db.user_models import get_model_by_id
    from db.leaderboard import get_status
    import threading as _th
    data = request.get_json(silent=True) or {}
    n_games = min(max(int(data.get("games", _LB_DEFAULT_GAMES)), 1), 50)
    if get_status(model_id) and get_status(model_id).get("status") == "running":
        return jsonify({"error": "A benchmark is already running for this model."}), 409
    m = get_model_by_id(model_id, requesting_user_id=uid())
    if not m or m["user_id"] != uid():
        return jsonify({"error": "Model not found or access denied."}), 404
    _th.Thread(
        target=_run_leaderboard_bench,
        args=(model_id, uid(), m["name"], m["code"], n_games),
        daemon=True,
    ).start()
    return jsonify({"ok": True, "total_games": 7 * n_games, "games": n_games})


@app.route("/api/models/user/<model_id>/leaderboard-status")
@login_required
def api_leaderboard_status(model_id: str):
    from db.leaderboard import get_status, get_submission
    from db.user_models import get_model_by_id
    m = get_model_by_id(model_id, requesting_user_id=uid())
    if not m or m["user_id"] != uid():
        return jsonify({"error": "Model not found or access denied."}), 404
    status = get_status(model_id) or {}
    sub = get_submission(model_id)
    return jsonify({
        "status": status.get("status", "idle"),
        "done": status.get("done", 0),
        "total": status.get("total", 0),
        "score": status.get("score"),
        "details": status.get("details"),
        "avg_stats": status.get("avg_stats"),
        "benchmarked_at": (sub or status or {}).get("benchmarked_at"),
        "error": status.get("error"),
        "achievements": _ach_toasts(),
    })


@app.route("/api/leaderboard/models")
@login_required
def api_model_leaderboard():
    from db.leaderboard import list_leaderboard
    limit = min(max(int(request.args.get("limit", 20)), 1), 100)
    offset = max(int(request.args.get("offset", 0)), 0)
    sort = request.args.get("sort", "score")
    if sort not in ("score", "recent"):
        sort = "score"
    entries, total = list_leaderboard(limit=limit, offset=offset, sort=sort)
    return jsonify({"entries": entries, "total": total, "limit": limit, "offset": offset})


@app.route("/api/leaderboard/models/<model_id>")
@login_required
def api_model_leaderboard_detail(model_id: str):
    from db.leaderboard import get_entry_detail
    entry = get_entry_detail(model_id)
    if not entry:
        return jsonify({"error": "Not on the leaderboard."}), 404
    return jsonify(entry)

@app.route("/arena")
@login_required
def arena_page():
    from services.game_analytics import MODEL_CATALOG
    return render_template("arena.html", username=session.get("username", "Player"), models=MODEL_CATALOG)


@app.route("/api/arena/models")
@login_required
def arena_models():
    from services.game_analytics import MODEL_CATALOG
    return jsonify(MODEL_CATALOG)


@app.route("/api/arena/battle", methods=["POST"])
@login_required
def arena_battle():
    from services.game_analytics import run_model_battle
    data = request.get_json(silent=True) or {}
    model_a = data.get("model_a", "minimax")
    model_b = data.get("model_b", "greedy")
    games = min(int(data.get("games", 10)), 50)

    # Resolve via the app registry so `user_model:<id>` (the caller's own
    # model, enforced by _load_model -> owner-only lookup) loads too.
    try:
        ma = _load_model(model_a) if model_a else None
        mb = _load_model(model_b) if model_b else None
    except Exception:
        ma = mb = None

    # Loss-analysis capture: trace the caller's own model when one side is it.
    tracer = None
    own_key = own_side = None
    if isinstance(model_a, str) and model_a.startswith(USER_MODEL_PREFIX) and ma:
        own_key, own_side = model_a, "A"
    elif isinstance(model_b, str) and model_b.startswith(USER_MODEL_PREFIX) and mb:
        own_key, own_side = model_b, "B"
    if own_key:
        from db.user_models import get_model_by_id
        own_label = (get_model_by_id(own_key[len(USER_MODEL_PREFIX):],
                                     requesting_user_id=uid()) or {}).get("name") or own_key
        tracer = {"side": own_side}

    result = run_model_battle(ma, mb, games, tracer=tracer)
    if result is None:
        return jsonify({"error": f"Model not found or failed to load. Available: {list(MODELS.keys())}"}), 400
    if tracer:
        _opponent = model_a if own_side == "B" else model_b
        _opp_label = _builtin_label(_opponent) if _opponent in MODELS else \
            (getattr(mb if own_side == "A" else ma, "MODEL_NAME", None) or _opponent)
        _persist_battle_traces(
            owner_id=uid(),
            model_key=own_key,
            model_label=own_label,
            opponent_label=_opp_label,
            side=own_side,
            battle_result=result,
            match_prefix=f"arena:{uid()}:{own_side}",
        )
    return jsonify(result)


#  Loss analysis — "why did my model lose this match?" 
# Read side of the decision-trace feature (capture lives in the arena,
# leaderboard-bench and tournament-sim call sites above). Every route is
# owner-only: the model id must belong to the logged-in user.

def _resolve_loss_model(model_id: str):
    """Returns (raw_model_id, model_row) for an owner's model, else None."""
    from db.user_models import get_model_by_id
    if model_id.startswith(USER_MODEL_PREFIX):
        model_id = model_id[len(USER_MODEL_PREFIX):]
    m = get_model_by_id(model_id, requesting_user_id=uid())
    if not m or m["user_id"] != uid():
        return None
    return model_id, m


def _loss_display_state(snapshot: dict) -> dict:
    """Compact scene state for the viewer (keeps the full snapshot server-side)."""
    return {
        "ball": snapshot.get("ball"),
        "players_a": [{"x": p["x"], "y": p["y"]} for p in snapshot.get("players_a", [])],
        "players_b": [{"x": p["x"], "y": p["y"]} for p in snapshot.get("players_b", [])],
        "referee": snapshot.get("referee"),
        "score_a": snapshot.get("score_a", 0),
        "score_b": snapshot.get("score_b", 0),
        "kick_count": snapshot.get("kick_count", 0),
        "is_player_a": bool(snapshot.get("is_player_a")),
        "penalty_shootout": bool(snapshot.get("penalty_shootout")),
    }


@app.route("/loss-analysis")
@login_required
def loss_analysis_page():
    from db.user_models import get_model_by_id
    model_id = (request.args.get("model") or "").strip()
    if model_id.startswith(USER_MODEL_PREFIX):
        model_id = model_id[len(USER_MODEL_PREFIX):]
    m = get_model_by_id(model_id, requesting_user_id=uid())
    if not m or m["user_id"] != uid():
        flash("Model not found or access denied")
        return redirect(url_for("my_models_page"))
    return render_template(
        "replay_3d.html", username=session.get("username", "Player"),
        t=None, match=None, highlights=[], highlight=None, live_room=None,
        loss_model=USER_MODEL_PREFIX + model_id, loss_model_name=m["name"],
    )


@app.route("/api/loss/models/<model_id>/matches")
@login_required
def api_loss_matches(model_id):
    resolved = _resolve_loss_model(model_id)
    if not resolved:
        return jsonify({"error": "Model not found or access denied"}), 404
    mid, m = resolved
    from db.decision_traces import list_matches
    matches = list_matches(uid(), model_id=USER_MODEL_PREFIX + mid)
    return jsonify({"model_id": USER_MODEL_PREFIX + mid, "model_name": m["name"],
                    "matches": matches})


@app.route("/api/loss/models/<model_id>/matches/<match_id>")
@login_required
def api_loss_match_traces(model_id, match_id):
    resolved = _resolve_loss_model(model_id)
    if not resolved:
        return jsonify({"error": "Model not found or access denied"}), 404
    mid, m = resolved
    from db.decision_traces import get_match, get_match_meta
    rows = get_match(uid(), match_id)
    if not rows:
        return jsonify({"error": "Match not found"}), 404
    meta = get_match_meta(uid(), match_id)
    return jsonify({
        "model_id": USER_MODEL_PREFIX + mid,
        "model_name": m["name"],
        "match": meta,
        "traces": [{
            "turn": r["turn"],
            "mover": r.get("mover", ""),
            "decision": r.get("decision", {}),
            "outcome_tag": r.get("outcome_tag", "neutral"),
            "state": _loss_display_state(r.get("state_snapshot") or {}),
        } for r in rows],
    })


@app.route("/api/loss/models/<model_id>/matches/<match_id>/turns/<int:turn>/compare")
@login_required
def api_loss_compare(model_id, match_id, turn):
    resolved = _resolve_loss_model(model_id)
    if not resolved:
        return jsonify({"error": "Model not found or access denied"}), 404
    from db.decision_traces import get_match
    from services.loss_analysis import builtin_decision, default_comparison_model
    from services.game_analytics import _BUILTIN_MODEL_PATHS
    rows = get_match(uid(), match_id)
    row = next((r for r in rows if r["turn"] == turn), None)
    if not row:
        return jsonify({"error": "Turn not found"}), 404
    model_key = request.args.get("model", default_comparison_model())
    if model_key not in _BUILTIN_MODEL_PATHS:
        return jsonify({"error": f"Unknown model: {model_key}"}), 400
    theirs = builtin_decision(row.get("state_snapshot") or {}, model_key)
    if theirs is None:
        return jsonify({"error": "Comparison failed (model error)"}), 502
    yours = row.get("decision", {})
    return jsonify({
        "yours": yours,
        "theirs": theirs,
        "diff": {
            "same_player": theirs["player_idx"] == yours.get("player_idx"),
            "angle_delta": round(float(theirs["angle"]) - float(yours.get("angle", 0)), 1),
            "power_delta": round(float(theirs["power"]) - float(yours.get("power", 0)), 1),
        },
    })


@app.route("/api/loss/models/<model_id>/matches/<match_id>/turns/<int:turn>/playback",
           methods=["POST"])
@login_required
def api_loss_playback(model_id, match_id, turn):
    resolved = _resolve_loss_model(model_id)
    if not resolved:
        return jsonify({"error": "Model not found or access denied"}), 404
    from db.decision_traces import get_match
    from services.loss_analysis import playback_turn
    rows = get_match(uid(), match_id)
    row = next((r for r in rows if r["turn"] == turn), None)
    if not row:
        return jsonify({"error": "Turn not found"}), 404
    result = playback_turn(row.get("state_snapshot") or {}, row.get("decision", {}))
    return jsonify(result)


@app.route("/api/loss/models/<model_id>/patterns")
@login_required
def api_loss_patterns(model_id):
    resolved = _resolve_loss_model(model_id)
    if not resolved:
        return jsonify({"error": "Model not found or access denied"}), 404
    mid, _m = resolved
    from db.decision_traces import list_traces
    from services.loss_analysis import aggregate_patterns
    rows = [r for r in list_traces(uid(), limit=2000)
            if r.get("model_id") == USER_MODEL_PREFIX + mid]
    return jsonify(aggregate_patterns(rows))


@app.route("/api/arena/matrix", methods=["POST"])
@login_required
def arena_matrix():
    from services.game_analytics import compute_head_to_head_matrix
    result = compute_head_to_head_matrix()
    if result is None:
        return jsonify({"error": "Failed to compute matrix. Check server logs."}), 500
    return jsonify(result)


#  Analytics dashboard (public — aggregates only, portfolio piece) 
ANALYTICS_CACHE_TTL = 86400          # 24h: data only changes when matches finish
_MATRIX_DEFAULT_GAMES = 5            # 7 agents -> 21 head-to-head pairs
_MATRIX_STATUS_TTL = 7200


def _an_get(key: str):
    from db.redis_client import r as redis
    import json as _json
    raw = redis.get(key)
    if raw:
        try:
            return _json.loads(raw)
        except (TypeError, ValueError):
            return None
    return None


def _an_set(key: str, payload: dict, ttl: int = ANALYTICS_CACHE_TTL) -> None:
    from db.redis_client import r as redis
    import json as _json
    redis.setex(key, ttl, _json.dumps(payload, default=float))


def _an_clear(key: str) -> None:
    from db.redis_client import r as redis
    redis.delete(key)


def _compute_analytics_payload() -> dict:
    """Assemble the full dashboard payload from the cheap data reads.

    Everything except the 7-agent matrix is computed here (small tables,
    pandas reductions). The matrix is the one expensive computation
    (~9 min for 21 pairs at 5 games) and is always served from cache —
    it is produced by the background `/api/analytics/matrix/recompute`
    job, never on a page/JSON request.
    """
    from datetime import datetime, timezone
    from db.summaries import list_summaries
    from db.ranked import (get_all_rating_history, get_all_ranked_matches,
                           get_ratings)
    from db.seasons import list_seasons
    from db.leaderboard import list_leaderboard
    from db.tournaments import get_tournaments, get_tournament
    from services.analytics import (stat_build_analysis, agent_matrix_analysis,
                                    custom_models_vs_builtins,
                                    rating_progression, season_over_season,
                                    match_dynamics)

    summaries  = list_summaries()
    matches    = get_all_ranked_matches()
    history    = get_all_rating_history()
    seasons    = list_seasons()
    entries, _ = list_leaderboard(limit=100, offset=0, sort="score")
    replays = []
    for t in get_tournaments():
        full = get_tournament(t.get("id")) or {}
        for m in (full.get("matches") or []):
            if m.get("status") == "completed" and m.get("replay_data"):
                replays.append({"tid": t.get("id"), "match_id": m.get("id"),
                                "replay_data": m["replay_data"]})

    player_ids = {s.get("player_a") for s in summaries}
    player_ids |= {s.get("player_b") for s in summaries}
    ratings = get_ratings(list(player_ids))

    matrix = _an_get("an:matrix")
    matrix_status = _an_get("an:matrix:status") or {}
    agents = {
        "matrix": matrix,
        "status": matrix_status.get("status", "not_computed"),
        "status_detail": matrix_status,
        "customs": [],
    }
    if matrix:
        ma = agent_matrix_analysis(matrix)
        agents["analysis"] = ma
        agents["customs"] = custom_models_vs_builtins(entries, ma)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "meta": {
            "summaries": len(summaries),
            "ranked_matches": len(matches),
            "rating_history_rows": len(history),
            "seasons": len(seasons),
            "custom_models": len(entries),
            "tournament_replays": len(replays),
        },
        "stat_builds": stat_build_analysis(summaries, ratings=ratings),
        "rating": rating_progression(matches, history),
        "season_over_season": season_over_season(seasons),
        "dynamics": match_dynamics(summaries, replays),
        "agents": agents,
    }


def _run_matrix_job(n_games: int = _MATRIX_DEFAULT_GAMES) -> None:
    """Background job: build the 7-agent head-to-head matrix and cache it.
    Mirrors the model-leaderboard benchmark thread pattern (Redis status,
    guard against overlap)."""
    from db.redis_client import r as redis
    import json as _json
    from datetime import datetime, timezone
    from services.game_analytics import compute_head_to_head_matrix
    try:
        matrix = compute_head_to_head_matrix(n_games=n_games)
        redis.setex("an:matrix", ANALYTICS_CACHE_TTL, _json.dumps(matrix))
        redis.setex("an:matrix:status", _MATRIX_STATUS_TTL, _json.dumps({
            "status": "done", "n_pairs": len(matrix), "n_games": n_games,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }))
    except Exception as exc:
        redis.setex("an:matrix:status", _MATRIX_STATUS_TTL, _json.dumps({
            "status": "failed", "error": str(exc),
        }))


@app.route("/analytics")
def analytics_page():
    """Public analytics dashboard (aggregate data only; usernames shown
    are already public via the leaderboards)."""
    return render_template("analytics.html", dev_mode=_dev_mode)


@app.route("/api/analytics")
def api_analytics():
    """Cached dashboard JSON. First request computes the cheap reductions,
    then results are served from Redis for ANALYTICS_CACHE_TTL (invalidated
    on every finished online match). The agent matrix is never computed
    here — it comes from the cache or an honest 'not computed' state."""
    cached = _an_get("an:data")
    if cached:
        cached["cached"] = True
        return jsonify(cached)
    payload = _compute_analytics_payload()
    payload["cached"] = False
    _an_set("an:data", payload)
    return jsonify(payload)


@app.route("/api/analytics/matrix/recompute", methods=["POST"])
@rate_limited
def api_analytics_matrix_recompute():
    """Kick off the ~9-minute background matrix build. Guarded: a 409 if
    a run is already in progress. Public like the rest of the dashboard;
    the first response tells the client to poll `/api/analytics`."""
    status = _an_get("an:matrix:status")
    if status and status.get("status") == "running":
        return jsonify({"error": "Matrix computation already in progress."}), 409
    _an_set("an:matrix:status", {"status": "running", "done": 0,
                                 "total": 21}, ttl=_MATRIX_STATUS_TTL)
    threading.Thread(target=_run_matrix_job, args=(_MATRIX_DEFAULT_GAMES,),
                     daemon=True).start()
    return jsonify({"ok": True, "note": "Matrix builds in the background "
                                        "(~9 min for 21 pairs); poll "
                                        "GET /api/analytics for status."})


def _seed_test_account():
    if not __import__("config").DEV_MODE:
        return
    TEST_EMAIL = "edward@umass.edu"
    TEST_PASSWORD = "123456"
    TEST_USERNAME = "Edward"
    try:
        from db.supabase_client import anon, service
        if anon is None or service is None:
            return
        user = None
        try:
            existing = service.auth.admin.get_user_by_email(TEST_EMAIL)
            if existing:
                uid = existing.id if hasattr(existing, "id") else existing.user.id
                user = {"id": uid}
                try:
                    service.auth.admin.update_user_by_id(uid, {"email_confirm": True})
                except Exception:
                    pass
        except Exception:
            pass
        if not user:
            try:
                res = service.auth.admin.create_user({
                    "email": TEST_EMAIL,
                    "password": TEST_PASSWORD,
                    "email_confirm": True,
                })
                user = res.user
            except (AttributeError, NotImplementedError):
                res = anon.auth.sign_up({"email": TEST_EMAIL, "password": TEST_PASSWORD})
                user = res.user
                if user:
                    try:
                        service.auth.admin.update_user_by_id(user.id, {"email_confirm": True})
                    except Exception:
                        pass
        if user:
            uid = user.id if hasattr(user, "id") else user["id"]
            service.table("profiles").upsert({"id": uid, "username": TEST_USERNAME}).execute()
    except Exception:
        pass

_seed_test_account()


def _season_watcher() -> None:
    """Background daemon: runs the season transition when a boundary
    passes. Lightweight (one 60 s sleep + a cheap status check) — no
    scheduler dependency; ranked request paths also check lazily."""
    try:
        from db.seasons import initialize, run_transition_if_due
        initialize()
    except Exception:
        pass
    while True:
        _time.sleep(60)
        try:
            run_transition_if_due()
        except Exception:
            app.logger.warning("Season watcher tick failed", exc_info=True)


if __name__ == "__main__":
    try:
        from db.seasons import initialize
        initialize()
    except Exception:
        pass
    threading.Thread(target=_season_watcher, daemon=True).start()
    app.run(debug=__import__("config").DEV_MODE, port=5000)
