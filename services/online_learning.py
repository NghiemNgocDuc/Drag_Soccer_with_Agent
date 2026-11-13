"""Bounded online experience capture and background candidate promotion.

Only compact, identity-free board features leave a room. Redis is the shared
source of truth; live candidate scoring reads an immutable in-memory policy.
No training, Redis read or additional physics runs happen inside AI scoring.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import logging
import threading
import time

from models import experience_policy as policy

LOG = logging.getLogger(__name__)
PREFIX = "ai:experience:v1:"
INDEX_KEY = PREFIX + "index"
ACTIVE_KEY = PREFIX + "active"
REPORT_KEY = PREFIX + "report"
TTL = 30 * 86400
MAX_MATCHES = 200
MAX_SAMPLES = 240  # Both perspectives, at most 120 moves per match.
MIN_MOVES = 8
AUTO_EVERY = 10

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ai-learning")
_slots = threading.BoundedSemaphore(4)
_local_store_lock = threading.RLock()
_local_train_lock = threading.Lock()
_refresh_lock = threading.Lock()
_last_refresh = float("-inf")
_manual_pending = threading.Lock()


def _redis():
    from db.redis_client import r
    return r


def _decode(raw, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


@contextmanager
def _lease(redis, name, *, training=False):
    """Use cross-worker leases in production and real thread locks in dev."""
    if hasattr(redis, "lock"):
        lock = redis.lock(PREFIX + name, timeout=120 if training else 15,
                          blocking_timeout=0.25)
        acquired = lock.acquire(blocking=False if training else True)
    else:
        lock = _local_train_lock if training else _local_store_lock
        acquired = lock.acquire(blocking=False) if training else lock.acquire(timeout=.25)
    if not acquired:
        raise RuntimeError("Learning update already running")
    try:
        yield lock
    finally:
        try:
            lock.release()
        except Exception:
            LOG.warning("Learning lease expired: %s", name)


def _submit(function, *args):
    if not _slots.acquire(blocking=False):
        return False
    try:
        future = _executor.submit(function, *args)
    except Exception:
        _slots.release()
        return False
    future.add_done_callback(lambda _future: _slots.release())
    return True


def prepare_transition(game):
    """Capture both views before authoritative physics; no state mutation/I/O."""
    if (game.get("sport", "soccer") != "soccer" or game.get("penalty_shootout")
            or game.get("game_over") or len(game.get("players_a", [])) != 5
            or len(game.get("players_b", [])) != 5 or game.get("power_cap", 100) != 100
            or any(game.get(key, "normal") != "normal" for key in ("ball_type", "ball_size", "ball_bounciness"))):
        return None
    try:
        return {side: list(policy.compact_features(game, side == "A")) for side in ("A", "B")}
    except (ValueError, TypeError, KeyError, IndexError, OverflowError):
        return None


def record_transition(room, before, trajectory, scored):
    """Append at most two small feature vectors to the existing room write."""
    if not before or not trajectory:
        return
    end = trajectory[-1]
    if not isinstance(end, dict) or not all(isinstance(end.get(side), list) for side in ("a", "b")):
        return  # Legacy/fake trajectories must never become invented experience.
    try:
        rows = [{"side": side, "before": before[side],
                 "after": list(policy.compact_features(room["game"], side == "A", endpoint=end)),
                 "scored": scored if scored in ("A", "B") else None}
                for side in ("A", "B")]
        # Physics candidates are scored before score/reset mutations. Preserve
        # that same convention even on an actual scoring transition.
        for row in rows:
            row["after"][9] = row["before"][9]
    except (ValueError, TypeError, KeyError, IndexError, OverflowError):
        return
    capture = room.setdefault("_learning", {"rules_version": policy.RULES_VERSION, "samples": [], "moves": 0})
    if capture.get("rules_version") != policy.RULES_VERSION:
        return
    capture["moves"] += 1
    capture["samples"].extend(rows)
    capture["samples"] = capture["samples"][-MAX_SAMPLES:]


def completed_match(room_id, room):
    """Queue only naturally completed, non-self human matches, once per room."""
    game, capture = room.get("game", {}), room.get("_learning", {})
    if (room.get("learning_queued") or not game.get("game_over")
            or game.get("termination") in ("forfeit", "cancelled")
            or game.get("sport", "soccer") != "soccer"
            or game.get("penalty_shootout")
            or not room.get("player_a") or not room.get("player_b")
            or room.get("player_a") == room.get("player_b")
            or capture.get("moves", 0) < MIN_MOVES
            or capture.get("rules_version") != policy.RULES_VERSION
            or game.get("winner") not in ("A", "B", "Draw")):
        return False
    # An opaque grouping key deduplicates retries without storing invitation IDs.
    identifier = hashlib.sha256((policy.RULES_VERSION + ":" + room_id).encode()).hexdigest()[:24]
    match = {"id": identifier, "sport": "soccer", "rules_version": policy.RULES_VERSION,
             "winner": game["winner"], "samples": capture.get("samples", [])[-MAX_SAMPLES:]}
    # Detach from a room that will be serialized/mutated by another request.
    packet = json.loads(json.dumps(match, allow_nan=False))
    accepted = _submit(_store_and_maybe_train, packet)
    if accepted:
        room["learning_queued"] = True
    return accepted


def _index(redis):
    value = _decode(redis.get(INDEX_KEY), {})
    if not isinstance(value, dict) or not isinstance(value.get("ids", []), list):
        return {"ids": [], "revision": 0}
    identifiers = list(dict.fromkeys(item for item in value.get("ids", [])
                                    if isinstance(item, str) and 0 < len(item) <= 128))[:MAX_MATCHES]
    try:
        revision = max(0, int(value.get("revision", 0)))
    except (ValueError, TypeError, OverflowError):
        revision = 0
    return {"ids": identifiers, "revision": revision}


def _report(redis):
    value = _decode(redis.get(REPORT_KEY), {})
    return value if isinstance(value, dict) else {}


def _store_match(match):
    redis = _redis()
    with _lease(redis, "store-lock"):
        index = _index(redis)
        if match["id"] in index["ids"]:
            return False
        identifiers = [match["id"], *index["ids"]]
        expired = identifiers[MAX_MATCHES:]
        index = {"ids": identifiers[:MAX_MATCHES], "revision": index["revision"] + 1}
        operations = redis.pipeline(transaction=True) if hasattr(redis, "pipeline") else redis
        operations.setex(PREFIX + "match:" + match["id"], TTL, json.dumps(match, separators=(",", ":"), allow_nan=False))
        operations.setex(INDEX_KEY, TTL, json.dumps(index))
        for identifier in expired:
            operations.delete(PREFIX + "match:" + identifier)
        if operations is not redis:
            operations.execute()
        return True


def _load_matches(redis, index):
    # Bounded by 200 records / 48k samples. Only the background trainer reads it.
    keys = [PREFIX + "match:" + identifier for identifier in index["ids"]]
    if hasattr(redis, "mget") and keys:
        rows = redis.mget(keys)
    else:
        rows = [redis.get(key) for key in keys]
    return [value for raw in rows if isinstance(value := _decode(raw, None), dict)]


def _store_and_maybe_train(match):
    try:
        if not _store_match(match):
            return
        redis = _redis()
        index = _index(redis)
        report = _report(redis)
        try:
            attempted = max(0, int(report.get("revision", 0)))
        except (ValueError, TypeError, OverflowError):
            attempted = 0
        if (len(index["ids"]) >= 20 and
                index["revision"] - attempted >= AUTO_EVERY):
            train_now()
    except Exception:
        LOG.warning("Could not store/train online AI experience", exc_info=True)


def _refresh():
    try:
        redis = _redis()
        with _lease(redis, "train-lock", training=True):
            encoded = redis.get(ACTIVE_KEY)
            if encoded is None:
                policy.set_active(None)
                return
            raw = _decode(encoded, None)
            if not isinstance(raw, dict):
                return  # Corruption is not a legitimate operator reset.
            fitness = raw.get("fitness", {})
            from services.learning_fitness import FITNESS_VERSION
            if isinstance(fitness, dict) and fitness.get("passed") and fitness.get("version") == FITNESS_VERSION:
                policy.set_active(raw)
    except Exception:
        LOG.debug("Keeping cached AI policy while storage is unavailable", exc_info=True)


def schedule_refresh():
    """Refresh across workers without adding network latency to a live move."""
    global _last_refresh
    now = time.monotonic()
    with _refresh_lock:
        if now - _last_refresh < 30:
            return
        if _submit(_refresh):
            _last_refresh = now


def train_now():
    """Train a detached candidate; persist only a validated promotion."""
    redis = _redis()
    with _lease(redis, "train-lock", training=True) as lease:
        index = _index(redis)
        matches = _load_matches(redis, index)
        incumbent = _decode(redis.get(ACTIVE_KEY), None)
        result = policy.fit_candidate(matches, incumbent=incumbent)
        report = {key: value for key, value in result.items() if key != "candidate"}
        report.update(revision=index["revision"], completed_at=time.time(), matches=len(matches))
        candidate = result.get("candidate")
        if result.get("promoted") and candidate:
            from services.learning_fitness import evaluate_candidate
            fitness = evaluate_candidate(candidate, incumbent)
            report["fitness"] = fitness
            if fitness["passed"]:
                candidate = {**candidate, "fitness": fitness}
                report["active_id"] = candidate["id"]
            else:
                report.update(promoted=False, reason="physical_fitness_regression")
                candidate = None
        _commit(redis, lease, report, candidate if report.get("promoted") else None)
        if report.get("promoted") and candidate:
            policy.set_active(candidate)
        return report


def _commit(redis, lease, report, candidate=None, *, reset=False):
    """Fence commits against expired leases, including cross-worker resets."""
    encoded_report = json.dumps(report, allow_nan=False)
    encoded_model = json.dumps(candidate, allow_nan=False) if candidate is not None else ""
    if hasattr(redis, "eval") and hasattr(lease, "local"):
        # Redis checks ownership and writes the model/report as one operation.
        # An expired trainer cannot overwrite a newer promotion or reset.
        script = """
        if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
        redis.call('setex', KEYS[2], ARGV[2], ARGV[3])
        if ARGV[5] == 'reset' then redis.call('del', KEYS[3])
        elseif ARGV[4] ~= '' then redis.call('setex', KEYS[3], ARGV[2], ARGV[4]) end
        return 1
        """
        if not redis.eval(script, 3, PREFIX + "train-lock", REPORT_KEY, ACTIVE_KEY,
                          lease.local.token, TTL, encoded_report, encoded_model, "reset" if reset else "publish"):
            raise RuntimeError("Training lease expired; candidate was not published")
    else:
        # The in-memory fallback holds the same local lock throughout this call.
        if reset:
            redis.delete(ACTIVE_KEY)
        elif candidate is not None:
            redis.setex(ACTIVE_KEY, TTL, encoded_model)
        redis.setex(REPORT_KEY, TTL, encoded_report)


def request_training():
    if not _manual_pending.acquire(blocking=False):
        return False
    if not _submit(_manual_train):
        _manual_pending.release()
        return False
    return True


def _manual_train():
    try:
        train_now()
    except Exception:
        LOG.warning("Online AI training attempt failed; active policy retained", exc_info=True)
    finally:
        _manual_pending.release()


def status():
    """Read-only aggregate status: no match IDs, features or player information."""
    redis = _redis()
    index = _index(redis)
    report = _report(redis)
    if isinstance(report.get("metrics"), dict):
        report["metrics"] = {key: value for key, value in report["metrics"].items()
                             if key not in ("train_ids", "holdout_ids")}
    active = _decode(redis.get(ACTIVE_KEY), None)
    return {"source": "completed_online_matches", "sport": "soccer", "rules_version": policy.RULES_VERSION,
            "stored_matches": len(index["ids"]), "minimum_matches": 20,
            "status": "active" if active else "collecting" if len(index["ids"]) < 20 else "awaiting_validation",
            "active_id": active.get("id") if isinstance(active, dict) else None,
            "last_training": report,
            "basketball": "separate_local_policy_no_online_data"}


def reset_policy():
    """Reset deployment, retaining collected games; serialize with promotion."""
    redis = _redis()
    with _lease(redis, "train-lock", training=True) as lease:
        index = _index(redis)
        _commit(redis, lease, {"reason": "reset", "promoted": False,
                              "revision": index["revision"], "completed_at": time.time()}, reset=True)
        policy.set_active(None)
    return status()
