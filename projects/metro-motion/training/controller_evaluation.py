"""Replay browser controls on held-out clips, without Node or camera access.

The Python controller is parity tested against the shipped JavaScript. Recording
context warms the real state machines, but only the reviewed interval is labeled.
Jump is evaluated as a pulse; sustained labels do not demand repeated jumps.
"""
import math

from evaluation import RuleBaseline, percentile
from features import BASELINE_FIELDS, LABELS, TemporalWindow, extract_features, finite


def _events(current, previous):
    """Action onsets, including a wrong held state on entering a label interval."""
    events = []
    if current["jump"]:
        events.append("jump")
    if current["duck"] and (previous is None or not previous["duck"]):
        events.append("duck")
    if current["lane"] and (previous is None or current["lane"] != previous["lane"]):
        events.append("left" if current["lane"] < 0 else "right")
    return events


def _matches(label, pose):
    if label == "jump":
        return pose["jump"]
    if label == "duck":
        return pose["duck"]
    if label == "neutral":
        return not pose["jump"] and not pose["duck"] and pose["lane"] == 0
    return pose["lane"] == (-1 if label == "left" else 1) and not pose["jump"] and not pose["duck"]


def emitted_metrics(clips, traces):
    """One labeled action per clip; invalid/gapped time never dilutes rates."""
    detection = {label: dict(clips=0, detected=0, emittedEvents=0, matchedEvents=0,
                             repeatedEvents=0, wrongEvents=0, latencies=[],
                             coveredMs=0., matchedMs=0.) for label in LABELS[1:]}
    neutral_ms, neutral_false, total_events, correct_events = 0., 0, 0, 0
    wrong, repeated, labeled_ms, latencies = 0, 0, 0., []
    for clip, trace in zip(clips, traces):
        if len(trace) != len(clip["frames"]):
            raise ValueError("Controller trace length mismatch")
        label, start, end = clip["label"], *clip["range"]
        target = detection.get(label)
        if target is not None:
            target["clips"] += 1
        previous, previous_time, found, event_matched = None, None, False, False
        for frame, pose in zip(clip["frames"], trace):
            t = frame["t"]
            if t < start or t > end:
                continue
            if not pose["tracked"] or not pose["calibrated"]:
                previous, previous_time = None, None
                continue
            if previous_time is not None and t - previous_time > 250:
                previous, previous_time = None, None
            duration = t - previous_time if previous_time is not None else 0.
            labeled_ms += duration
            matched = _matches(label, pose)
            if target is not None:
                target["coveredMs"] += duration
                if matched and previous is not None and _matches(label, previous):
                    target["matchedMs"] += duration
                if matched and not found:
                    found = True
                    delay = max(0., t - start)
                    target["detected"] += 1
                    target["latencies"].append(delay)
                    latencies.append(delay)
            else:
                neutral_ms += duration
            for event in _events(pose, previous):
                total_events += 1
                if target is not None:
                    target["emittedEvents"] += 1
                if event == label and not event_matched:
                    event_matched = True
                    correct_events += 1
                    target["matchedEvents"] += 1
                elif event == label:
                    repeated += 1
                    target["repeatedEvents"] += 1
                else:
                    wrong += 1
                    if target is not None:
                        target["wrongEvents"] += 1
                    else:
                        neutral_false += 1
            previous, previous_time = pose, t
    for label, entry in detection.items():
        entry["recall"] = entry["detected"] / entry["clips"] if entry["clips"] else 0.
        entry["precision"] = entry["matchedEvents"] / entry["emittedEvents"] if entry["emittedEvents"] else 0.
        values = entry.pop("latencies")
        entry["medianLatencyMs"], entry["p95LatencyMs"] = percentile(values, .5), percentile(values, .95)
        covered, matched = entry.pop("coveredMs"), entry.pop("matchedMs")
        entry["coveredSeconds"] = covered / 1000
        # An airborne label is not a request to emit jump on every camera frame.
        entry["heldTargetCoverage"] = None if label == "jump" else matched / covered if covered else 0.
    return dict(clips=len(clips), labeledSeconds=labeled_ms / 1000,
                neutralSeconds=neutral_ms / 1000, neutralFalseActions=neutral_false,
                neutralFalseActionsPerMinute=neutral_false * 60000 / neutral_ms if neutral_ms else None,
                emittedEvents=total_events, matchedEvents=correct_events, wrongEvents=wrong,
                repeatedEvents=repeated, actionPrecision=correct_events / total_events if total_events else 0.,
                actionDetection=detection, detectedActionLatencyMs=dict(
                    median=percentile(latencies, .5), p95=percentile(latencies, .95), detected=len(latencies)))


def _prediction(probabilities):
    if (not isinstance(probabilities, (list, tuple)) or len(probabilities) != len(LABELS)
            or any(not finite(value) or not 0 <= value <= 1 for value in probabilities)
            or abs(sum(probabilities) - 1) > 1e-4):
        raise ValueError("Controller evaluation requires five finite normalized probabilities")
    index = max(range(5), key=probabilities.__getitem__)
    return dict(label=LABELS[index], confidence=probabilities[index], probabilities=list(probabilities))


def controller_evaluation(clips, probability_fn, threshold):
    """probability_fn batches 64-input vectors and includes temperature scaling.

    Replaying control mode is deliberate even for an ineligible candidate: the
    measured behavior is evidence for the eligibility decision, never activation.
    """
    references, vectors, locations = [], [], []
    for ci, clip in enumerate(clips):
        rules, history, trace = RuleBaseline(clip["baseline"]), TemporalWindow(), []
        for fi, frame in enumerate(clip["frames"]):
            trace.append(rules.update_pose(frame["landmarks"], frame["t"]))
            flat = history.push(extract_features(frame["landmarks"], clip["baseline"]), frame["t"])
            if flat is not None:
                vectors.append(flat)
                locations.append((ci, fi))
        references.append(trace)
    predictions = {}
    for start in range(0, len(vectors), 4096):
        batch = vectors[start:start + 4096]
        probabilities = probability_fn(batch)
        if len(probabilities) != len(batch):
            raise ValueError("Controller probability batch length mismatch")
        for key, values in zip(locations[start:start + 4096], probabilities):
            predictions[key] = _prediction(values)
    traces = []
    for ci, clip in enumerate(clips):
        controller, trace = LearnedControlReplay(threshold), []
        for fi, frame in enumerate(clip["frames"]):
            trace.append(controller.update(frame["landmarks"], frame["t"], clip["baseline"],
                                           references[ci][fi], predictions.get((ci, fi))))
        traces.append(trace)
    return dict(learned=emitted_metrics(clips, traces), rules=emitted_metrics(clips, references))


class LearnedControlReplay:
    """State machine twin of LearnedController; predictions supplied externally."""
    def __init__(self, threshold=.8):
        self.threshold = threshold
        self.reset()

    def reset(self):
        self.window = TemporalWindow()
        self.baseline_key = None
        self.last_time = self.last_jump = -math.inf
        self._clear_gestures()

    def _clear_gestures(self):
        self.jump_armed = False
        self.grounded_since = None
        self._clear_debounce()

    def _clear_debounce(self):
        self.duck_since = self.pending_lane = self.lane_since = self.lane = None

    def _fallback(self, rules, reason, prediction=None, timestamp=None):
        self._clear_debounce()
        jump = rules["jump"]
        if jump and finite(timestamp):
            jump = timestamp - self.last_jump >= 650
            if jump:
                self.last_jump = timestamp
                self.jump_armed = False
                self.grounded_since = None
        return dict(rules, jump=jump, prediction=prediction, source="rules", reason=reason)

    def update(self, points, timestamp, baseline, rule_pose, prediction=None):
        rules = dict(tracked=False, calibrated=False, progress=0, lane=0, jump=False, duck=False)
        rules.update(rule_pose)
        features = extract_features(points, baseline)
        if (not rules["tracked"] or not rules["calibrated"] or features is None or not finite(timestamp)
                or timestamp < 0 or timestamp <= self.last_time):
            self.window.reset()
            self._clear_gestures()
            safe_rules = rules if rules["tracked"] and rules["calibrated"] else dict(rules, jump=False, duck=False)
            return self._fallback(safe_rules, "invalid-tracking", timestamp=timestamp)
        key = tuple(baseline[field] for field in BASELINE_FIELDS)
        if key != self.baseline_key or timestamp - self.last_time > 250:
            self.window.reset()
            self._clear_gestures()
            self.baseline_key = key
        self.last_time = timestamp
        flat = self.window.push(features, timestamp)
        predicted_jump = flat is not None and prediction is not None and prediction["label"] == "jump"
        grounded = features[2] < .07 and max(features[4], features[5]) < .055
        if grounded and not predicted_jump:
            if self.grounded_since is None:
                self.grounded_since = timestamp
            if timestamp - self.grounded_since >= 120 and timestamp - self.last_jump >= 650:
                self.jump_armed = True
        else:
            self.grounded_since = None
        if flat is None:
            return self._fallback(rules, "warming-up", timestamp=timestamp)
        if prediction is None:
            return self._fallback(rules, "prediction-failed", timestamp=timestamp)
        if prediction["confidence"] < self.threshold:
            return self._fallback(rules, "low-confidence", prediction, timestamp)
        label = prediction["label"]
        jump = label == "jump" and not grounded and self.jump_armed and timestamp - self.last_jump >= 650
        if jump:
            self.jump_armed, self.last_jump = False, timestamp
        if label == "duck":
            if self.duck_since is None:
                self.duck_since = timestamp
        else:
            self.duck_since = None
        duck = self.duck_since is not None and timestamp - self.duck_since >= 80
        lane = rules["lane"]
        if label in ("neutral", "left", "right"):
            desired = -1 if label == "left" else 1 if label == "right" else 0
            if self.lane is None:
                self.lane = rules["lane"]
            if desired != self.pending_lane:
                self.pending_lane, self.lane_since = desired, timestamp
            elif timestamp - self.lane_since >= 90:
                self.lane = desired
            lane = self.lane
        else:
            self.pending_lane = None
            self.lane = lane
        return dict(rules, lane=lane, jump=jump, duck=duck, prediction=prediction, source="learned", reason="confident")
