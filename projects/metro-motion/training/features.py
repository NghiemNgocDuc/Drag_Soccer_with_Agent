"""mm-body-v1: standard-library twin of web/js/motion-features.mjs."""
import math

FEATURE_VERSION = "mm-body-v1"
LABELS = ["neutral", "left", "right", "jump", "duck"]
FEATURE_COUNT, WINDOW_SIZE, SAMPLE_HZ, INPUT_SIZE = 8, 8, 15, 64
BASELINE_FIELDS = ("shoulderX", "shoulderY", "hipX", "hipY", "width", "torso",
                   "headY", "leftFootY", "rightFootY")
REQUIRED = (0, 11, 12, 23, 24, 25, 26, 27, 28)
EPSILON = 1e-7


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def valid_baseline(baseline):
    return (isinstance(baseline, dict)
            and all(finite(baseline.get(key)) for key in BASELINE_FIELDS)
            and baseline["width"] >= .055 and baseline["torso"] >= .075)


def pose_features(points):
    if not isinstance(points, list) or len(points) < 29:
        return None
    for index in REQUIRED:
        p = points[index]
        if not isinstance(p, dict) or any(not finite(p.get(k)) for k in ("x", "y", "visibility")):
            return None
        if (p["visibility"] < .55 or not finite(p.get("presence", 1))
                or p.get("presence", 1) < .55 or not .015 <= p["x"] <= .985
                or not .015 <= p["y"] <= .985):
            return None
    shoulder_x = (points[11]["x"] + points[12]["x"]) / 2
    shoulder_y = (points[11]["y"] + points[12]["y"]) / 2
    hip_x = (points[23]["x"] + points[24]["x"]) / 2
    hip_y = (points[23]["y"] + points[24]["y"]) / 2
    width, torso = abs(points[11]["x"] - points[12]["x"]), hip_y - shoulder_y
    if width < .055 or torso < .075 or points[27]["y"] - points[0]["y"] < .32:
        return None
    return dict(shoulderX=shoulder_x, shoulderY=shoulder_y, hipX=hip_x, hipY=hip_y,
                width=width, torso=torso, headY=points[0]["y"],
                leftFootY=points[27]["y"], rightFootY=points[28]["y"])


def extract_features(points, baseline):
    if (not valid_baseline(baseline) or not isinstance(points, list) or len(points) != 33
            or any(not isinstance(p, dict) or any(not finite(p.get(k)) for k in ("x", "y", "visibility"))
                   or any(k in p and not finite(p[k]) for k in ("z", "presence")) for p in points)):
        return None
    p = pose_features(points)
    if p is None:
        return None
    b = baseline
    values = [-(p["hipX"] - b["hipX"]) / b["width"],
              -(p["shoulderX"] - b["shoulderX"]) / b["width"],
              (b["hipY"] - p["hipY"]) / b["torso"],
              (b["headY"] - p["headY"]) / b["torso"],
              (b["leftFootY"] - p["leftFootY"]) / b["torso"],
              (b["rightFootY"] - p["rightFootY"]) / b["torso"],
              p["torso"] / b["torso"], p["width"] / b["width"]]
    return [max(-6., min(6., value)) for value in values]


class TemporalWindow:
    def __init__(self):
        self.reset()

    def reset(self):
        self.history = []

    def push(self, values, timestamp):
        if (not finite(timestamp) or timestamp < 0 or not isinstance(values, list) or len(values) != FEATURE_COUNT
                or any(not finite(value) or abs(value) > 6 for value in values)):
            self.reset()
            return None
        if self.history and timestamp <= self.history[-1][0]:
            self.reset()
            return None
        if self.history and timestamp - self.history[-1][0] > 250:
            self.reset()
        self.history.append((timestamp, list(values)))
        if len(self.history) > 1024:
            self.history = self.history[-1024:]
        start = timestamp - (WINDOW_SIZE - 1) * 1000 / SAMPLE_HZ
        # Preserve the sample immediately before the interpolation boundary.
        while len(self.history) > 2 and self.history[1][0] <= start:
            self.history.pop(0)
        if self.history[0][0] > start + EPSILON:
            return None
        result, cursor = [], 0
        for index in range(WINDOW_SIZE):
            target = start + index * 1000 / SAMPLE_HZ
            while cursor + 1 < len(self.history) and self.history[cursor + 1][0] < target:
                cursor += 1
            a = self.history[cursor]
            b = self.history[min(cursor + 1, len(self.history) - 1)]
            fraction = 0 if b[0] == a[0] else max(0., min(1., (target - a[0]) / (b[0] - a[0])))
            result.extend(x + (y - x) * fraction for x, y in zip(a[1], b[1]))
        return result
