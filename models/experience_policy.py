"""Bounded soccer value learning from completed, detached match experience.

This module does no storage, network access, or physics. Fitting runs outside a
move request; inference is two ten-feature dot products. Promotion measures
prediction error on *whole held-out matches*, never claimed playing win rate.
The ordinary tactical/goal scores remain authoritative.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import math

SCHEMA_VERSION = 1
RULES_VERSION = "soccer-v1"
VALIDATION_VERSION = "whole-match-holdout-v1"
FEATURE_COUNT = 10
MIN_MATCHES = 20
MIN_SAMPLES = 100
MIN_HOLDOUT_MATCHES = 4
MAX_MATCHES = 256
MAX_SAMPLES_PER_MATCH = 256
MAX_ADJUSTMENT = 40.0
_FIELD_W, _FIELD_H = 1400.0, 875.0
_DIAGONAL = math.hypot(_FIELD_W, _FIELD_H)
_MIN_IMPROVEMENT = 0.005
_MAX_GAME_REGRET = 0.05


def _clip(value, low=-1.0, high=1.0):
    return max(low, min(high, value))


def _number(value, default=0.0):
    if isinstance(value, bool):
        return default
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return value if math.isfinite(value) else default


def _side_a(side):
    if side is True or side == "A":
        return True
    if side is False or side == "B":
        return False
    raise ValueError("Expected team A/B or a boolean side")


def _point(point, fallback=None):
    point = point if isinstance(point, Mapping) else {}
    fallback = fallback if isinstance(fallback, Mapping) else {}
    return (_clip(_number(point.get("x"), _number(fallback.get("x"), _FIELD_W / 2)), 0, _FIELD_W),
            _clip(_number(point.get("y"), _number(fallback.get("y"), _FIELD_H / 2)), 0, _FIELD_H))


def _roster(state, endpoint, key, side):
    original = state.get("players_" + side, ())
    original = original if isinstance(original, (list, tuple)) else ()
    selected = endpoint.get(key, original)
    if not isinstance(selected, (list, tuple)):
        selected = original
    result = []
    for index, player in enumerate(selected[:11]):
        if not isinstance(player, Mapping):
            continue
        source = original[index] if index < len(original) and isinstance(original[index], Mapping) else {}
        stats = source.get("stats") or player.get("stats") or {}
        stats = stats if isinstance(stats, Mapping) else {}
        # Same launch/braking equations as soccer_logic: greater agility stops
        # sooner; high power travels farther until the 480-unit free-run cap.
        # Endpoint roster tracks need not repeat the original player stats.
        power = _clip(_number(stats.get("power"), 50), 0, 100)
        agility = _clip(_number(stats.get("agility"), 50), 0, 100)
        braking = 700.0 + agility * 4.0
        velocity = min(5.0 + power * .04, math.sqrt(2 * braking * 480.0) / 100.0)
        reach = (100.0 * velocity) ** 2 / (2 * braking) / (700.0 ** 2 / 1800.0)
        result.append((*_point(player, source), reach))
    return result


def compact_features(state, side, endpoint=None):
    """Return ten finite [-1, 1] board features in the attacking team's frame.

    Endpoint x/y and a/b tracks override the initial ball/rosters. No move,
    player identity, final winner, or future match information enters features.
    Mirroring x and swapping teams leaves the selected side's features equal.
    """
    if not isinstance(state, Mapping):
        raise ValueError("Expected a game state")
    is_a = _side_a(side)
    endpoint = endpoint if isinstance(endpoint, Mapping) else {}
    ball_x, ball_y = _point(endpoint, state.get("ball"))
    own = _roster(state, endpoint, "a" if is_a else "b", "a" if is_a else "b")
    opponents = _roster(state, endpoint, "b" if is_a else "a", "b" if is_a else "a")
    progress = ball_x if is_a else _FIELD_W - ball_x
    own_gap = min((math.hypot(x - ball_x, y - ball_y) / reach for x, y, reach in own), default=_DIAGONAL)
    opponent_gap = min((math.hypot(x - ball_x, y - ball_y) / reach for x, y, reach in opponents), default=_DIAGONAL)
    center = 1.0 - abs(ball_y - _FIELD_H / 2) / (_FIELD_H / 2)
    outfield = own[1:] if len(own) > 1 else own
    advance = sum((x if is_a else _FIELD_W - x) / _FIELD_W * 2 - 1 for x, _, _ in outfield) / max(1, len(outfield))
    cover = sum((x if is_a else _FIELD_W - x) < progress for x, _, _ in own) / max(1, len(own))
    threat = max(0.0, 1.0 - math.hypot(_FIELD_W - progress, ball_y - _FIELD_H / 2) / 500.0)
    danger = max(0.0, 1.0 - math.hypot(progress, ball_y - _FIELD_H / 2) / 500.0)
    score_a, score_b = _number(state.get("score_a")), _number(state.get("score_b"))
    lead = (score_a - score_b) * (1 if is_a else -1) / 5.0
    return tuple(_clip(value) for value in (
        progress / _FIELD_W * 2 - 1, center,
        (opponent_gap - own_gap) / 350.0,
        1.0 - min(1.0, own_gap / 350.0),
        1.0 - min(1.0, opponent_gap / 350.0),
        advance, cover, threat, danger, lead,
    ))


def _features(values):
    if not isinstance(values, (list, tuple)) or len(values) != FEATURE_COUNT:
        raise ValueError("Invalid experience feature count")
    result = tuple(float(value) for value in values)
    if any(isinstance(value, bool) for value in values) or any(not math.isfinite(value) or abs(value) > 1.000001 for value in result):
        raise ValueError("Experience features must be finite and bounded")
    return result


def baseline_value(features):
    """Fixed cheap positional baseline, independent of any trained model."""
    f = _features(features)
    return _clip(.30 * f[0] + .28 * f[2] + .10 * (f[3] - f[4])
                 + .16 * (f[7] - f[8]) + .16 * f[9])


@dataclass(frozen=True)
class _ActiveModel:
    weights: tuple
    bias: float
    identifier: str


_active_model = None
_UNSET = object()
_model_override = ContextVar("experience_value_model", default=_UNSET)


def _effective_model():
    override = _model_override.get()
    return _active_model if override is _UNSET else override


def predict(model, features):
    """Predict a bounded position value; raw models are never activated here."""
    f = _features(features)
    if isinstance(model, _ActiveModel):
        weights, bias = model.weights, model.bias
    elif isinstance(model, Mapping):
        weights, bias = _parameters(model)
    else:
        raise ValueError("Invalid experience model")
    return _clip(bias + sum(weight * value for weight, value in zip(weights, f)))


def _parameters(model):
    weights = model.get("weights")
    if not isinstance(weights, (list, tuple)) or len(weights) != FEATURE_COUNT:
        raise ValueError("Invalid experience weights")
    if any(isinstance(value, bool) for value in weights) or isinstance(model.get("bias"), bool):
        raise ValueError("Invalid experience parameters")
    weights = tuple(float(value) for value in weights)
    bias = float(model.get("bias"))
    if (not math.isfinite(bias) or abs(bias) > 2 or
            any(not math.isfinite(value) or abs(value) > 4 for value in weights) or
            sum(abs(value) for value in weights) > 12):
        raise ValueError("Unbounded experience model")
    return weights, bias


def _identifier(model):
    payload = {key: model.get(key) for key in (
        "schema_version", "rules_version", "validation_version", "sport",
        "weights", "bias", "metrics", "validated",
    )}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _structural_safety(model):
    """Basic monotonic safety plus hard clipping; scoring bypasses inference."""
    try:
        weights, _ = _parameters(model)
        # Clipped neutral probes alone can hide a backwards coefficient behind
        # a saturated intercept. Enforce signs on the underlying linear model.
        for index, direction in ((0, 1), (2, 1), (7, 1), (8, -1), (9, 1)):
            if weights[index] * direction < -1e-9:
                return False
        neutral = [0.0] * FEATURE_COUNT
        for index, direction in ((0, 1), (2, 1), (7, 1), (8, -1), (9, 1)):
            low, high = neutral.copy(), neutral.copy()
            low[index], high[index] = -1.0, 1.0
            if direction * (predict(model, high) - predict(model, low)) < -0.02:
                return False
        for fixture in (neutral, [1.0] * FEATURE_COUNT, [-1.0] * FEATURE_COUNT):
            if not -1.0 <= predict(model, fixture) <= 1.0:
                return False
        return True
    except (TypeError, ValueError, OverflowError):
        return False


def _validated(model):
    if not isinstance(model, Mapping):
        return False
    try:
        if (model.get("schema_version") != SCHEMA_VERSION or model.get("rules_version") != RULES_VERSION
                or model.get("validation_version") != VALIDATION_VERSION or model.get("sport") != "soccer"
                or model.get("validated") is not True or model.get("id") != _identifier(model)):
            return False
        metrics = model.get("metrics")
        if not isinstance(metrics, Mapping) or metrics.get("evaluation") != "whole_match_prediction_mse":
            return False
        for key in ("matches", "samples", "train_matches", "holdout_matches", "train_samples", "holdout_samples"):
            if type(metrics.get(key)) is not int or metrics[key] < 0:
                return False
        train_ids, holdout_ids = metrics.get("train_ids"), metrics.get("holdout_ids")
        if (not isinstance(train_ids, list) or not isinstance(holdout_ids, list)
                or len(train_ids) != metrics["train_matches"] or len(holdout_ids) != metrics["holdout_matches"]
                or metrics["train_matches"] + metrics["holdout_matches"] != metrics["matches"]
                or metrics["train_samples"] + metrics["holdout_samples"] != metrics["samples"]
                or any(not isinstance(identifier, str) for identifier in train_ids + holdout_ids)
                or len(set(train_ids + holdout_ids)) != metrics["matches"]
                or any(_holdout_id(identifier) for identifier in train_ids)
                or any(not _holdout_id(identifier) for identifier in holdout_ids)):
            return False
        if (metrics.get("matches", 0) < MIN_MATCHES or metrics.get("samples", 0) < MIN_SAMPLES
                or metrics.get("train_matches", 0) < 12 or metrics.get("holdout_matches", 0) < MIN_HOLDOUT_MATCHES
                or metrics.get("holdout_samples", 0) < 20 or metrics.get("max_game_regret", 1) > _MAX_GAME_REGRET
                or metrics.get("improved_match_fraction", 0) < .6):
            return False
        candidate = float(metrics["candidate_mse"])
        reference = min(float(metrics["baseline_mse"]), float(metrics["incumbent_mse"]))
        if not all(math.isfinite(float(value)) for value in (
                candidate, reference, metrics["max_game_regret"], metrics["improved_match_fraction"])):
            return False
        if candidate < 0 or reference - candidate < max(_MIN_IMPROVEMENT, reference * .02):
            return False
        return _structural_safety(model)
    except (TypeError, ValueError, KeyError, OverflowError):
        return False


def set_active(model):
    """Atomically replace process-local immutable inference state.

    None clears the active model. A model needs the current evidence marker,
    successful holdout metrics, bounded coefficients, safety fixtures, and its
    canonical content digest. The digest detects corrupt persisted content; it
    is not authentication of untrusted uploads (which callers must not use).
    """
    global _active_model
    if model is None:
        _active_model = None
        return True
    if not _validated(model):
        return False
    weights, bias = _parameters(model)
    _active_model = _ActiveModel(weights, bias, model["id"])
    return True


def active_model_id():
    model = _effective_model()
    return model.identifier if model is not None else None


@contextmanager
def using_model(model=_UNSET):
    """Snapshot a search's model, or isolate an offline candidate comparison.

    Omitted model captures the current effective model once. Explicit None
    disables learning for this context. A candidate must pass the same evidence
    validation as set_active. ContextVar isolation avoids changing live workers.
    """
    if model is _UNSET:
        selected = _effective_model()
    elif model is None:
        selected = None
    elif _validated(model):
        weights, bias = _parameters(model)
        selected = _ActiveModel(weights, bias, model["id"])
    else:
        raise ValueError("Unvalidated experience model")
    token = _model_override.set(selected)
    try:
        yield selected.identifier if selected is not None else None
    finally:
        _model_override.reset(token)


def compatible_state(state):
    """Learned evidence currently covers normal-material five-a-side only."""
    if (not isinstance(state, Mapping) or state.get("sport", "soccer") != "soccer"
            or state.get("penalty_shootout") or state.get("period") == "penalties"
            or state.get("rules_version", RULES_VERSION) != RULES_VERSION
            or state.get("player_count", 5) != 5 or _number(state.get("power_cap", 100), -1) != 100
            or any(state.get(key, "normal") not in (None, "normal")
                   for key in ("ball_type", "ball_size", "ball_bounciness"))):
        return False
    field = state.get("field") or {}
    if not isinstance(field, Mapping) or _number(field.get("width", _FIELD_W), -1) != _FIELD_W or _number(field.get("height", _FIELD_H), -1) != _FIELD_H:
        return False
    for key in ("players_a", "players_b"):
        roster = state.get(key)
        if not isinstance(roster, (list, tuple)) or len(roster) != 5:
            return False
        if any(not isinstance(player, Mapping) or
               any(not math.isfinite(_number(player.get(axis), float("nan"))) for axis in ("x", "y"))
               for player in roster):
            return False
    ball = state.get("ball")
    return (isinstance(ball, Mapping) and _number(ball.get("z", 0), -1) == 0
            and all(math.isfinite(_number(ball.get(axis), float("nan"))) for axis in ("x", "y")))


def outcome_adjustment(state, side, move, trajectory, scored):
    """Small learned residual-potential change, with no additional prediction.

    A single correction is at most 40 tactical points. Goals, own goals,
    penalties, basketball, and absent/invalid evidence always bypass it.
    """
    model = _effective_model()
    if (model is None or not compatible_state(state) or scored
            or not isinstance(trajectory, (list, tuple)) or not trajectory
            or not isinstance(trajectory[-1], Mapping)
            or _number(trajectory[-1].get("z", 0), -1) != 0
            or any(not math.isfinite(_number(trajectory[-1].get(axis), float("nan"))) for axis in ("x", "y"))):
        return 0.0
    try:
        before = compact_features(state, side)
        after = compact_features(state, side, trajectory[-1])
        residual_change = ((predict(model, after) - baseline_value(after))
                           - (predict(model, before) - baseline_value(before)))
        return _clip(residual_change * 40.0, -MAX_ADJUSTMENT, MAX_ADJUSTMENT)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _holdout_id(identifier):
    # Permanent assignment: adding later matches cannot move an earlier game
    # between training and evaluation. Never split actions from the same game.
    return int(hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:8], 16) % 4 == 0


def _detached_matches(matches):
    """Validate/copy only opaque IDs, outcomes, sides and compact vectors."""
    detached, blocked = {}, set()
    if not isinstance(matches, (list, tuple)):
        return []
    for match in matches:
        if not isinstance(match, Mapping):
            continue
        identifier, winner = match.get("id"), match.get("winner")
        if (not isinstance(identifier, str) or not identifier or len(identifier) > 128
                or winner not in ("A", "B", "Draw") or match.get("sport") != "soccer"
                or match.get("rules_version") != RULES_VERSION or identifier in blocked):
            continue
        samples = match.get("samples")
        if not isinstance(samples, (list, tuple)):
            continue
        rows = []
        for sample in samples:
            if not isinstance(sample, Mapping) or sample.get("side") not in ("A", "B"):
                continue
            try:
                before, after = _features(sample.get("before")), _features(sample.get("after"))
            except (TypeError, ValueError, OverflowError):
                continue
            scored = sample.get("scored")
            if scored not in (None, "A", "B"):
                continue
            rows.append((sample["side"], before, after, scored))
        if len(rows) > MAX_SAMPLES_PER_MATCH:
            rows = [rows[round(index * (len(rows) - 1) / (MAX_SAMPLES_PER_MATCH - 1))]
                    for index in range(MAX_SAMPLES_PER_MATCH)]
        record = (identifier, winner, tuple(rows))
        if identifier in detached and detached[identifier] != record:
            detached.pop(identifier)
            blocked.add(identifier)
        elif rows:
            detached[identifier] = record
    return sorted(detached.values(), key=lambda record: hashlib.sha256(record[0].encode()).hexdigest())[:MAX_MATCHES]


def _observations(match):
    identifier, winner, samples = match
    counts = {side: sum(row[0] == side for row in samples) for side in ("A", "B")}
    present = sum(count > 0 for count in counts.values())
    rows = []
    for side, before, after, scored in samples:
        result = 0.0 if winner == "Draw" else (1.0 if winner == side else -1.0)
        reward = (1.0 if scored == side else -1.0) if scored else _clip(after[0] - before[0])
        # Final result supplies the learning signal. A modest measured progress
        # and immediate goal signal makes early actions less blindly imitative.
        target = _clip(.70 * result + .20 * baseline_value(after) + .10 * reward)
        weight = 1.0 / (present * counts[side])
        rows.append((after, target, weight))
    return rows


def _game_error(model, observations):
    return sum(weight * ((baseline_value(features) if model is None else predict(model, features)) - target) ** 2
               for features, target, weight in observations)


def fit_candidate(matches, incumbent=None):
    """Fit deterministic ridge value regression and conservatively promote.

    Equal weight per match and per observed team prevents long games or one
    side's repeated actions dominating. All records are detached before fitting.
    Reported improvement is holdout prediction MSE, not measured playing skill.
    """
    records = _detached_matches(matches)
    train = [record for record in records if not _holdout_id(record[0])]
    holdout = [record for record in records if _holdout_id(record[0])]
    sample_count = sum(len(record[2]) for record in records)
    metrics = {
        "evaluation": "whole_match_prediction_mse", "matches": len(records), "samples": sample_count,
        "train_matches": len(train), "holdout_matches": len(holdout),
        "train_samples": sum(len(record[2]) for record in train),
        "holdout_samples": sum(len(record[2]) for record in holdout),
        "train_ids": [record[0] for record in train], "holdout_ids": [record[0] for record in holdout],
    }

    def report(reason, candidate=None):
        return {"candidate": candidate, "promoted": candidate is not None, "reason": reason, "metrics": metrics}

    if (len(records) < MIN_MATCHES or sample_count < MIN_SAMPLES or len(train) < 12
            or len(holdout) < MIN_HOLDOUT_MATCHES or metrics["holdout_samples"] < 20):
        return report("insufficient_completed_match_evidence")
    if incumbent is not None and not _validated(incumbent):
        return report("invalid_incumbent")
    # NumPy is already a project dependency. Import only in background fitting,
    # keeping ordinary gameplay inference independent of its startup cost.
    import numpy as np

    rows = [row for record in train for row in _observations(record)]
    design = np.asarray([(*features, 1.0) for features, _, _ in rows], dtype=float)
    targets = np.asarray([target for _, target, _ in rows], dtype=float)
    weights = np.asarray([weight for _, _, weight in rows], dtype=float)
    regularizer = np.eye(FEATURE_COUNT + 1) * .5
    regularizer[-1, -1] = .05
    try:
        coefficients = np.linalg.solve(design.T @ (design * weights[:, None]) + regularizer,
                                       design.T @ (targets * weights))
    except (ValueError, np.linalg.LinAlgError):
        return report("fit_failed")
    candidate = {"schema_version": SCHEMA_VERSION, "rules_version": RULES_VERSION,
                 "validation_version": VALIDATION_VERSION, "sport": "soccer",
                 "weights": [round(float(value), 12) for value in coefficients[:-1]],
                 "bias": round(float(coefficients[-1]), 12), "validated": True}
    if not _structural_safety(candidate):
        return report("structural_safety_failed")
    observations = [_observations(record) for record in holdout]
    baseline_errors = [_game_error(None, rows) for rows in observations]
    incumbent_errors = [_game_error(incumbent, rows) for rows in observations] if incumbent is not None else baseline_errors
    candidate_errors = [_game_error(candidate, rows) for rows in observations]
    reference_errors = [min(base, active) for base, active in zip(baseline_errors, incumbent_errors)]
    n = len(observations)
    metrics.update({
        "baseline_mse": sum(baseline_errors) / n, "incumbent_mse": sum(incumbent_errors) / n,
        "candidate_mse": sum(candidate_errors) / n,
        "max_game_regret": max(new - old for new, old in zip(candidate_errors, reference_errors)),
        "improved_match_fraction": sum(new < old - 1e-9 for new, old in zip(candidate_errors, reference_errors)) / n,
    })
    reference = min(metrics["baseline_mse"], metrics["incumbent_mse"])
    metrics["absolute_mse_improvement"] = reference - metrics["candidate_mse"]
    metrics["relative_mse_improvement"] = (reference - metrics["candidate_mse"]) / max(reference, 1e-12)
    if reference - metrics["candidate_mse"] < max(_MIN_IMPROVEMENT, reference * .02):
        return report("no_holdout_improvement")
    if metrics["max_game_regret"] > _MAX_GAME_REGRET or metrics["improved_match_fraction"] < .6:
        return report("inconsistent_holdout_improvement")
    candidate["metrics"] = metrics.copy()
    candidate["id"] = _identifier(candidate)
    if not _validated(candidate):
        return report("invalid_candidate")
    return report("holdout_prediction_improved", candidate)
