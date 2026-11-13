"""Explicit held-out metric scope and a conservative, non-regression export gate."""
import math
from collections import Counter, defaultdict

from features import LABELS, pose_features


class RuleBaseline:
    """Standing-calibrated PoseController state machine, reset once per clip.

    No calibration is fitted from held-out actions. The recording's baseline is
    provided just as in live play. Jump is an event, not a held airborne label.
    """
    def __init__(self, baseline):
        self.baseline = baseline
        self.filtered = None
        self.last_valid = -math.inf
        self.last_update = -math.inf
        self.lane = self.pending_lane = 0
        self.lane_since = 0
        self.duck = False
        self.duck_since = None
        self.jump_armed = True
        self.last_jump = -math.inf
        self.grounded_since = None

    def _result(self, tracked, jump=False):
        return dict(tracked=tracked, calibrated=True, progress=1, lane=self.lane,
                    jump=jump, duck=tracked and self.duck)

    def update(self, points, timestamp):
        result = self.update_pose(points, timestamp)
        return (0 if not result["tracked"] else 3 if result["jump"] else 4 if result["duck"]
                else 1 if result["lane"] < 0 else 2 if result["lane"] > 0 else 0)

    def update_pose(self, points, timestamp):
        if (not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp)
                or timestamp < self.last_valid or self.last_update - timestamp > 450):
            return self._result(False)
        self.last_update = max(self.last_update, timestamp)
        pose = pose_features(points)
        if pose is None:
            if timestamp - self.last_valid > 450:
                self.filtered = None
                self.duck = False
                self.duck_since = self.grounded_since = None
                self.jump_armed = False
                return self._result(False)
            return self._result(True)
        elapsed = timestamp - self.last_valid
        self.last_valid = timestamp
        if self.filtered is None or elapsed > 450:
            self.filtered = dict(pose)
            self.jump_armed = False
            self.grounded_since = None
        else:
            alpha = 1 - math.exp(-max(0, min(200, elapsed)) / 65)
            for key in pose:
                self.filtered[key] += (pose[key] - self.filtered[key]) * alpha
        p, b = self.filtered, self.baseline
        horizontal = -(p["hipX"] - b["hipX"]) / b["width"]
        desired = self.lane
        if horizontal > .58:
            desired = 1
        elif horizontal < -.58:
            desired = -1
        elif abs(horizontal) < .32:
            desired = 0
        if desired != self.pending_lane:
            self.pending_lane, self.lane_since = desired, timestamp
        elif timestamp - self.lane_since >= 90:
            self.lane = desired
        hip_up = (b["hipY"] - p["hipY"]) / b["torso"]
        head_up = (b["headY"] - p["headY"]) / b["torso"]
        feet_up = min(b["leftFootY"] - p["leftFootY"], b["rightFootY"] - p["rightFootY"]) / b["torso"]
        grounded = hip_up < .07 and feet_up < .055
        if grounded:
            if self.grounded_since is None:
                self.grounded_since = timestamp
            if timestamp - self.grounded_since >= 120 and timestamp - self.last_jump >= 650:
                self.jump_armed = True
        else:
            self.grounded_since = None
        jump = (self.jump_armed and not self.duck and hip_up > .17 and head_up > .13
                and feet_up > .10 and timestamp - self.last_jump >= 650)
        if jump:
            self.jump_armed, self.last_jump = False, timestamp
        wants_duck = (head_up < -.16 and hip_up < -.065) if self.duck else (head_up < -.33 and hip_up < -.14)
        if wants_duck and not jump:
            if self.duck_since is None:
                self.duck_since = timestamp
            if timestamp - self.duck_since >= 80:
                self.duck = True
        else:
            self.duck_since, self.duck = None, False
        return self._result(True, jump)


def baseline_predictions(clips, windows):
    results = {}
    for clip in clips:
        controller = RuleBaseline(clip["baseline"])
        for index, frame in enumerate(clip["frames"]):
            results[(clip["id"], index)] = controller.update(frame["landmarks"], frame["t"])
    return [results[(window["clip"], window["frame"])] for window in windows]


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))]


def metrics(clips, windows, predictions, confidences=None):
    if len(windows) != len(predictions):
        raise ValueError("Prediction length mismatch")
    matrix = [[0] * 5 for _ in LABELS]
    by_clip = defaultdict(list)
    for window, prediction in zip(windows, predictions):
        matrix[window["y"]][prediction] += 1
        by_clip[window["clip"]].append((window, prediction))
    per_class = {}
    for index, label in enumerate(LABELS):
        tp = matrix[index][index]
        support = sum(matrix[index])
        positive = sum(row[index] for row in matrix)
        precision, recall = tp / positive if positive else 0, tp / support if support else 0
        per_class[label] = {"precision": precision, "recall": recall,
                            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0,
                            "windows": support}
    neutral_ms, false_events = 0., 0
    latencies, detection = [], {label: {"clips": 0, "detected": 0, "latencies": []} for label in LABELS[1:]}
    for clip in clips:
        selected = by_clip[clip["id"]]
        if clip["label"] == "neutral":
            # Only time with valid model history contributes to the denominator;
            # gaps over 250 ms and startup/context cannot dilute false actions.
            previous = None
            for window, predicted in selected:
                contiguous = previous is not None and window["t"] - previous[0]["t"] <= 250
                if contiguous:
                    neutral_ms += window["t"] - previous[0]["t"]
                if predicted != 0 and (not contiguous or predicted != previous[1]):
                    false_events += 1
                previous = (window, predicted)
        else:
            entry = detection[clip["label"]]
            entry["clips"] += 1
            first = next((window["t"] for window, pred in selected if pred == LABELS.index(clip["label"])), None)
            if first is not None:
                delay = max(0, first - clip["range"][0])
                entry["detected"] += 1
                entry["latencies"].append(delay)
                latencies.append(delay)
    for entry in detection.values():
        entry["recall"] = entry["detected"] / entry["clips"] if entry["clips"] else 0
        entry["medianLatencyMs"] = percentile(entry.pop("latencies"), .5)
    result = {"windows": len(windows), "clips": len(clips),
              "classClips": dict(Counter(clip["label"] for clip in clips)),
              "accuracy": sum(matrix[i][i] for i in range(5)) / len(windows),
              "macroF1": sum(item["f1"] for item in per_class.values()) / 5,
              "perClass": per_class, "confusionMatrix": matrix,
              "neutralSeconds": neutral_ms / 1000, "neutralFalseActions": false_events,
              "neutralFalseActionsPerMinute": false_events * 60000 / neutral_ms if neutral_ms else None,
              "actionDetection": detection, "detectedActionLatencyMs": {
                  "median": percentile(latencies, .5), "p95": percentile(latencies, .95), "detected": len(latencies)}}
    if confidences is not None:
        result["confidenceCoverage"] = sum(confidences) / len(confidences)
    return result


def eligibility(summary, model_metrics, baseline_metrics, synthetic, controller_metrics=None):
    """A deliberate evidence floor, not proof of safety or population accuracy."""
    reasons = []
    if synthetic:
        reasons.append("Synthetic fixtures never qualify for experimental control.")
    for name in ("validation", "test"):
        own, baseline = model_metrics[name], baseline_metrics[name]
        if summary["splits"][name]["groups"] < 2:
            reasons.append(f"{name}: need at least two independent held-out groups.")
        if any(own["classClips"].get(label, 0) < 10 or own["perClass"][label]["windows"] < 100 for label in LABELS):
            reasons.append(f"{name}: need 10 clips and 100 valid windows per class.")
        if own["neutralSeconds"] < 60:
            reasons.append(f"{name}: need at least 60 valid seconds of neutral recording.")
        if own["macroF1"] < .85 or own["macroF1"] + 1e-9 < baseline["macroF1"]:
            reasons.append(f"{name}: macro F1 must reach .85 and match or exceed the rules.")
        if any(own["perClass"][label]["f1"] < baseline["perClass"][label]["f1"] - .03 for label in LABELS):
            reasons.append(f"{name}: a class regresses by more than .03 F1.")
        if own.get("confidenceCoverage", 0) < .75:
            reasons.append(f"{name}: model confidence coverage is below 75%.")
        rate, base_rate = own["neutralFalseActionsPerMinute"], baseline["neutralFalseActionsPerMinute"]
        if rate is None or base_rate is None or rate > min(2, base_rate + .1):
            reasons.append(f"{name}: neutral false-action rate must be <=2/min and no more than rules +.1/min.")
        for label in LABELS[1:]:
            action, original = own["actionDetection"][label], baseline["actionDetection"][label]
            if action["recall"] < max(.9, original["recall"]):
                reasons.append(f"{name}/{label}: detection recall must reach .90 and match the rules.")
            latency, original_latency = action["medianLatencyMs"], original["medianLatencyMs"]
            if latency is None or (original_latency is not None and latency > original_latency + 100):
                reasons.append(f"{name}/{label}: detected-action median latency exceeds rules +100 ms.")
        emitted = controller_metrics.get(name) if isinstance(controller_metrics, dict) else None
        if not emitted:
            reasons.append(f"{name}: emitted-control replay evidence is required.")
            continue
        actual, original = emitted["learned"], emitted["rules"]
        rate, base_rate = actual["neutralFalseActionsPerMinute"], original["neutralFalseActionsPerMinute"]
        if actual["neutralSeconds"] < 60:
            reasons.append(f"{name}: emitted controls need 60 tracked labeled neutral seconds.")
        if rate is None or base_rate is None or rate > min(2, base_rate + .1):
            reasons.append(f"{name}: emitted neutral false actions must be <=2/min and no more than rules +.1/min.")
        for label in LABELS[1:]:
            action, base = actual["actionDetection"][label], original["actionDetection"][label]
            if action["recall"] < max(.9, base["recall"]):
                reasons.append(f"{name}/{label}: emitted action recall must reach .90 and match the rules.")
            if action["precision"] < max(.9, base["precision"] - .03):
                reasons.append(f"{name}/{label}: emitted action precision must reach .90 without wrong or repeated actions.")
            latency, base_latency = action["medianLatencyMs"], base["medianLatencyMs"]
            if latency is None or (base_latency is not None and latency > base_latency + 100):
                reasons.append(f"{name}/{label}: emitted action median latency exceeds rules +100 ms.")
            p95, base_p95 = action["p95LatencyMs"], base["p95LatencyMs"]
            if p95 is None or (base_p95 is not None and p95 > base_p95 + 150):
                reasons.append(f"{name}/{label}: emitted action p95 latency exceeds rules +150 ms.")
            if label != "jump" and action["heldTargetCoverage"] < max(.85, base["heldTargetCoverage"] - .03):
                reasons.append(f"{name}/{label}: emitted held-control coverage must reach .85 and stay within .03 of rules.")
    return not reasons, reasons


METRIC_SCOPE = (
    "Confusion and per-class metrics use valid temporal-window endpoints in manually labeled ranges; "
    "overlapping windows are correlated, not independent trials. Existing standing-calibrated rules "
    "reset per clip; jump is a single event. Low-confidence learned predictions fall back to the "
    "rules. Neutral false actions count changes into a non-neutral class during contiguous valid "
    "neutral windows, divided by that covered time; this is not a measured live-play action rate. "
    "Detection latency is first correct window/event after manually marked onset, excluding misses "
    "(reported separately as detection recall); it excludes camera, inference and rendering delay. "
    "These classifier summaries are separate from emittedControls, "
    "which replay the shipped rule and learned-controller state machines on every captured frame, "
    "including unlabeled context for warming, filtering and rearming. Only reviewed ranges are scored. "
    "Emitted jump recall/precision count one intended pulse per action clip, so an airborne hold does "
    "not require repeated jumps. Extra same-action pulses and wrong lane/duck/jump onsets are errors; "
    "held lane/duck target coverage measures sustained correct control. Neutral rates use all tracked "
    "labeled time, excluding gaps over 250 ms. Emitted latency includes smoothing and dwell/rearm, "
    "but excludes camera/inference/rendering delay and misses (separately reported). Clips reset state "
    "and do not establish continuous mixed-action live-game performance. "
    "Session holdouts estimate familiar-participant performance; participant holdouts assess unseen "
    "participants. Neither establishes performance across unrecorded lighting, cameras or users."
)
