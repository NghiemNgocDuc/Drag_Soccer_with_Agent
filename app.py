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

from config import SECRET_KEY
from game.session import (
    get_game, save_game, new_game_state, push_snapshot, pop_snapshot,
    new_pg_state, get_pg, save_pg,
)
from models.soccer_logic import apply_kick, apply_penalty_kick, _setup_penalty_positions

# ── External service initialisation ───────────────────────────────────────
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
    return resp

MODELS: dict[str, str] = {
    "minimax":          "models.minimax",
    "monte_carlo":      "models.monte_carlo",
    "greedy":           "models.greedy_model",
    "bayesian":         "models.bayes",
    "value_iteration":  "models.value_iteration",
    "policy_iteration": "models.policy_iteration",
    "q_learning":       "models.q_learning",
}

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


# ── Rate limiter (Redis-backed) ─────────────────────────────────────────

_RATE_LIMITS: dict[str, tuple[int, int]] = {
    "/auth/login":    (5, 60),     # 5 requests per 60 seconds
    "/auth/register": (3, 300),    # 3 registrations per 5 minutes
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
    }
    if extra:
        result.update(extra)
    if state.get("game_over") and state.get("game_mode") == "hvai" and not state.get("_finalized"):
        _persist_result(state)
        state["_finalized"] = True
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
        _auto_clear_state(uid())
    except Exception as e:
        app.logger.warning("Failed to save game result: %s", e)


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


def _do_ai_move(state: dict, model_name: str, is_player_a: bool) -> dict:
    model = _load_model(model_name)
    t0    = time.time()
    player_idx, angle, power = model.get_ai_move(state, is_player_a)
    elapsed = round((time.time() - t0) * 1000)
    result  = _apply_move(state, player_idx, angle, power, is_player_a)
    result["think_ms"] = elapsed
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


@app.route("/")
@login_required
def index():
    return redirect(url_for("index_3d"))


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
        extra = {"move_result": result}
    else:
        result = _apply_move(state, player_idx, angle, power, True)
        extra = {"move_result": result}

    save_game(user_id, state)
    _auto_save_state(user_id, state)
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
    save_game(user_id, state)
    _auto_save_state(user_id, state)
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
        _setup_penalty_positions(state, True)
    else:
        pc = int(data.get("player_count", old_state.get("player_count", 3)))
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


# ── Auto-save / load game progress ──────────────────────────────────────────

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


@app.route("/profile")
@login_required
def profile():
    from db.games import get_user_stats
    stats = get_user_stats(uid())
    username = session.get("username", "Player")
    joined_days = session.get("joined_at")
    return render_template("profile.html", username=username, stats=stats, joined_days=joined_days)


@app.route("/leaderboard")
@login_required
def leaderboard():
    from db.games import get_leaderboard
    entries = get_leaderboard()
    return render_template("leaderboard.html", entries=entries, username=session.get("username", ""))


@app.route("/customize")
@login_required
def customize_page():
    from db.customization import get_customization
    cust = get_customization(uid())
    return render_template("customize.html", username=session.get("username", "Player"), cust=cust)


@app.route("/customize/save", methods=["POST"])
@login_required
def customize_save():
    from db.customization import save_customization
    data = request.get_json(silent=True) or {}
    from db.customization import _ALLOWED
    cleaned = {k: v for k, v in data.items() if k in _ALLOWED}
    ok = save_customization(uid(), cleaned)
    return jsonify({"ok": ok})


@app.route("/api/customization")
@login_required
def api_customization():
    from db.customization import get_customization
    cust = get_customization(uid())
    return jsonify(cust)


@app.route("/my-models")
@login_required
def my_models_page():
    from db.user_models import get_user_models
    from user_models.runner import TEMPLATE
    models = get_user_models(uid())
    return render_template("my_models.html", username=session.get("username", "Player"), models=models, template_code=TEMPLATE)


@app.route("/api/models/user/validate", methods=["POST"])
@login_required
def api_validate_model():
    from user_models.runner import validate_code, execute_user_model
    from models.soccer_logic import new_soccer_state
    code = (request.get_json(silent=True) or {}).get("code", "")
    ok, msg = validate_code(code)
    if not ok:
        return jsonify({"ok": False, "error": msg})
    try:
        st = new_soccer_state()
        pidx, ang, pwr = execute_user_model(code, st, True, timeout_s=5.0)
        return jsonify({"ok": True, "test_move": f"player {pidx}, angle {round(ang)}, power {round(pwr)}"})
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/api/models/user", methods=["POST"])
@login_required
def api_create_model():
    from user_models.runner import validate_code
    from db.user_models import create_model
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    desc = (data.get("description") or "").strip()
    code = (data.get("code") or "").strip()
    if not name:
        return jsonify({"error": "Name is required."}), 400
    ok, msg = validate_code(code)
    if not ok:
        return jsonify({"error": f"Code error: {msg}"}), 400
    model = create_model(uid(), name, desc, code)
    return jsonify({"ok": True, "model": model}), 201


@app.route("/api/models/user/<model_id>", methods=["PUT"])
@login_required
def api_update_model(model_id: str):
    from user_models.runner import validate_code
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
    updated = update_model(model_id, uid(), **fields)
    if not updated:
        return jsonify({"error": "Model not found or access denied."}), 404
    return jsonify({"ok": True})


@app.route("/api/models/user/<model_id>/code")
@login_required
def api_get_model_code(model_id: str):
    from db.user_models import get_model_by_id
    data = get_model_by_id(model_id, requesting_user_id=uid())
    if not data:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"code": data.get("code", "")})


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
    room_id = _uuid.uuid4().hex[:10]
    room = {
        "game":      new_game_state(mode="hvh"),
        "player_a":  uid(),
        "player_b":  None,
        "name_a":    session.get("username", "Player A"),
        "name_b":    None,
        "status":    "waiting",
        "last_move": None,
    }
    _save_room(room_id, room)
    return jsonify({"room_id": room_id})


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
                        "name_a": room["name_a"], "name_b": room["name_b"]})
    if room["player_b"] == my_uid:
        return jsonify({"my_side": "b", "status": room["status"],
                        "name_a": room["name_a"], "name_b": room["name_b"]})
    if room["player_b"] is not None:
        return jsonify({"error": "Room is full"}), 400
    room["player_b"] = my_uid
    room["name_b"]   = session.get("username", "Guest")
    room["status"]   = "active"
    _save_room(room_id, room)
    return jsonify({"my_side": "b", "status": "active",
                    "name_a": room["name_a"], "name_b": room["name_b"]})


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
    resp = {
        "game":    room["game"],
        "name_a":  room["name_a"],
        "name_b":  room["name_b"],
        "status":  room["status"],
    }
    my_uid = uid()
    resp["my_side"] = ("a" if my_uid == room["player_a"]
                       else "b" if my_uid == room["player_b"] else None)
    if room["last_move"] and room["game"].get("kick_count", 0) > since_kick:
        resp["last_move"] = room["last_move"]
    return jsonify(resp)


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
    move_res = {
        "trajectory":    traj, "scored": scored, "desc": desc,
        "player_idx":    player_idx, "angle": round(angle, 1), "power": round(power, 1),
        "kick_endpoint": kick_ep, "push_result": push_res, "mover": my_side,
    }
    room["last_move"] = move_res
    if game.get("game_over"):
        room["status"] = "done"
    _save_room(room_id, room)
    return jsonify({"move_result": move_res, "game": game})


@app.route("/online/invite/search")
@login_required
def online_invite_search():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    try:
        from db.supabase_client import service
        if not service:
            return jsonify([])
        rows = (service.table("profiles").select("id,username")
                .ilike("username", f"%{q}%").limit(8).execute().data or [])
        return jsonify([r for r in rows if r["id"] != uid()])
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
    room = {
        "game": new_game_state(mode="hvh"), "player_a": uid(), "player_b": None,
        "name_a": session.get("username", "Player A"), "name_b": None,
        "status": "waiting", "last_move": None,
    }
    _save_room(room_id, room)
    invite_id = _uuid.uuid4().hex[:12]
    invite = {
        "from_uid": uid(), "from_name": session.get("username", "Player"),
        "to_uid": to_uid, "room_id": room_id, "status": "pending",
    }
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
    _save_room(inv["room_id"], room)
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


# ── Friend system ─────────────────────────────────────────────────────────────

FRIEND_TTL = 3600 * 24 * 30   # 30 days


def _get_friends(user_id: str) -> list:
    from db.redis_client import r as redis
    raw = redis.get(f"friends:{user_id}")
    return _json.loads(raw) if raw else []


def _save_friends(user_id: str, friends: list) -> None:
    from db.redis_client import r as redis
    redis.setex(f"friends:{user_id}", FRIEND_TTL, _json.dumps(friends))


def _get_friend_reqs(user_id: str) -> list:
    from db.redis_client import r as redis
    raw = redis.get(f"friend_reqs:{user_id}")
    return _json.loads(raw) if raw else []


def _save_friend_reqs(user_id: str, reqs: list) -> None:
    from db.redis_client import r as redis
    redis.setex(f"friend_reqs:{user_id}", FRIEND_TTL, _json.dumps(reqs))


@app.route("/api/friends")
@login_required
def api_list_friends():
    return jsonify({
        "friends":  _get_friends(uid()),
        "requests": _get_friend_reqs(uid()),
    })


@app.route("/api/friends/request", methods=["POST"])
@login_required
def api_send_friend_request():
    from db.supabase_client import service
    data   = request.get_json(silent=True) or {}
    target = (data.get("username") or "").strip()
    if not target:
        return jsonify({"error": "Username required"}), 400
    my_uid      = uid()
    my_username = session.get("username", "")
    if target.lower() == my_username.lower():
        return jsonify({"error": "You can't add yourself"}), 400
    try:
        res = (service.table("profiles").select("id,username")
               .ilike("username", target).limit(1).execute())
        row = (res.data or [None])[0] if res.data else None
        if not row:
            return jsonify({"error": "User not found"}), 404
        target_uid  = row["id"]
        target_name = row["username"]
    except Exception:
        return jsonify({"error": "User lookup failed"}), 500
    if any(f["uid"] == target_uid for f in _get_friends(my_uid)):
        return jsonify({"error": "Already friends"}), 400
    reqs = _get_friend_reqs(target_uid)
    if any(r_["from_uid"] == my_uid for r_ in reqs):
        return jsonify({"error": "Request already sent"}), 400
    req_id = _uuid.uuid4().hex[:12]
    reqs.append({
        "id":            req_id,
        "from_uid":      my_uid,
        "from_username": my_username,
        "ts":            _time.time(),
    })
    _save_friend_reqs(target_uid, reqs)
    return jsonify({"ok": True, "to": target_name})


@app.route("/api/friends/accept/<req_id>", methods=["POST"])
@login_required
def api_accept_friend(req_id):
    my_uid        = uid()
    reqs          = _get_friend_reqs(my_uid)
    req           = next((r_ for r_ in reqs if r_["id"] == req_id), None)
    if not req:
        return jsonify({"error": "Request not found"}), 404
    ts             = _time.time()
    my_friends     = _get_friends(my_uid)
    their_friends  = _get_friends(req["from_uid"])
    my_friends.append({"uid": req["from_uid"], "username": req["from_username"], "since": ts})
    their_friends.append({"uid": my_uid, "username": session.get("username", ""), "since": ts})
    _save_friends(my_uid, my_friends)
    _save_friends(req["from_uid"], their_friends)
    _save_friend_reqs(my_uid, [r_ for r_ in reqs if r_["id"] != req_id])
    return jsonify({"ok": True, "friend": {"uid": req["from_uid"], "username": req["from_username"]}})


@app.route("/api/friends/decline/<req_id>", methods=["POST"])
@login_required
def api_decline_friend(req_id):
    my_uid = uid()
    _save_friend_reqs(my_uid, [r_ for r_ in _get_friend_reqs(my_uid) if r_["id"] != req_id])
    return jsonify({"ok": True})


@app.route("/api/friends/<friend_uid>", methods=["DELETE"])
@login_required
def api_remove_friend(friend_uid):
    my_uid        = uid()
    my_friends    = [f for f in _get_friends(my_uid)      if f["uid"] != friend_uid]
    their_friends = [f for f in _get_friends(friend_uid)  if f["uid"] != my_uid]
    _save_friends(my_uid, my_friends)
    _save_friends(friend_uid, their_friends)
    return jsonify({"ok": True})


# ── Tournaments ─────────────────────────────────────────────────────────────

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
    return jsonify({"ok": True, "tournament": t})

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
    
    for __ in range(40):
        if st.get("game_over"): break
        is_a = st["is_player_a"]
        try:
            pidx, ang, pwr = _run_model(pa if is_a else pb, st, is_a)
            from game.session import push_snapshot
            push_snapshot(st)
            traj, scored, desc, kick_ep, push_res = _kick(st, pidx, ang, pwr, is_a)
            st["move_history"].append({
                "mover": "a" if is_a else "b",
                "player_idx": pidx, "angle": round(ang,1), "power": round(pwr,1),
                "trajectory": traj, "push_result": push_res, "scored": scored
            })
        except Exception as e:
            # If a model crashes, other player wins
            st["winner"] = "B" if is_a else "A"
            st["game_over"] = True
            break
            
    winner_id = m["participant_a"] if st.get("winner") == "A" else m["participant_b"]
    save_match_result(tid, match_id, winner_id, st["move_history"])
    return jsonify({"ok": True, "winner": winner_id})

@app.route("/tournaments/<tid>/watch/<match_id>")
@login_required
def tournament_watch(tid, match_id):
    return redirect(url_for("tournament_watch_3d", tid=tid, match_id=match_id))


@app.route("/replay3d/<tid>/<match_id>")
@login_required
def tournament_watch_3d(tid, match_id):
    from db.tournaments import get_tournament, get_match
    t = get_tournament(tid)
    m = get_match(tid, match_id)
    if not t or not m or m["status"] != "completed":
        flash("Match not available for replay")
        return redirect(url_for("tournament_view", tid=tid))
    return render_template("replay_3d.html", username=session.get("username", "Player"), t=t, match=m)

# ── Clerk — verify session ────────────────────────────────────────────────
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


# ── Feedback page ────────────────────────────────────────────────────────
@app.route("/feedback")
@login_required
def feedback_page():
    return render_template("feedback.html", username=session.get("username", "Player"))


# ── ProductBridge — feedback ─────────────────────────────────────────────
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


# ── Pinecone — AI move embedding ─────────────────────────────────────────
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


# ── Research Hub — paper search & library ──────────────────────────────────────

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


# ── AI Arena — model benchmarking & analytics ──────────────────────────────

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
    result = run_model_battle(model_a, model_b, games)
    if result is None:
        return jsonify({"error": f"Model not found or failed to load. Available: {list(MODELS.keys())}"}), 400
    return jsonify(result)


@app.route("/api/arena/matrix", methods=["POST"])
@login_required
def arena_matrix():
    from services.game_analytics import compute_head_to_head_matrix
    result = compute_head_to_head_matrix()
    if result is None:
        return jsonify({"error": "Failed to compute matrix. Check server logs."}), 500
    return jsonify(result)


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

if __name__ == "__main__":
    app.run(debug=__import__("config").DEV_MODE, port=5000)
