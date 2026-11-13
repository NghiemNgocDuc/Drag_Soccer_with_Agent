"""Bounded JSON validation, grouped holdouts and labeled temporal windows. No torch."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import re

from features import (BASELINE_FIELDS, FEATURE_VERSION, LABELS, TemporalWindow,
                      extract_features, finite, valid_baseline)

MAX_BYTES = 100 * 1024 * 1024
MAX_FRAMES = 200_000
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class DataError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise DataError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def read_json(path, limit=MAX_BYTES):
    path = Path(path)
    require(path.stat().st_size <= limit, f"File too large (limit {limit} bytes): {path.name}")
    def invalid(value):
        raise DataError(f"Non-finite JSON number: {value}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=invalid,
                          object_pairs_hook=_object)
    except (json.JSONDecodeError, UnicodeError, RecursionError) as error:
        raise DataError(f"Invalid JSON in {path.name}: {error}") from error


def validate_recording(data):
    require(isinstance(data, dict), "Recording must be an object")
    require(set(data) == {"format", "version", "featureVersion", "source", "clips"},
            "Unexpected recording fields; only landmark datasets are supported")
    require(data.get("format") == "metro-motion-motion-data" and data.get("version") == 1
            and not isinstance(data.get("version"), bool), "Unsupported recording format/version")
    require(data.get("featureVersion") == FEATURE_VERSION, "Incompatible feature version")
    require(data.get("source") in ("human", "synthetic"), "source must be human or synthetic")
    require(isinstance(data.get("clips"), list) and 0 < len(data["clips"]) <= 10000,
            "Expected 1..10000 clips")
    total = 0
    for clip in data["clips"]:
        require(isinstance(clip, dict), "Each clip must be an object")
        require(set(clip) == {"id", "sessionId", "participantId", "label", "range", "baseline", "frames"},
                "Unexpected clip fields")
        for field in ("id", "sessionId", "participantId"):
            require(isinstance(clip.get(field), str) and ID.fullmatch(clip[field]),
                    f"Invalid opaque clip {field}")
        name = clip["id"]
        require(clip.get("label") in LABELS, f"{name}: unknown label")
        baseline = clip.get("baseline")
        require(valid_baseline(baseline), f"{name}: invalid standing baseline")
        require(set(baseline) == set(BASELINE_FIELDS), f"{name}: unexpected baseline fields")
        require(all(0 <= value <= 2 for value in baseline.values()), f"{name}: baseline out of range")
        frames = clip.get("frames")
        require(isinstance(frames, list) and 8 <= len(frames) <= 10000,
                f"{name}: expected 8..10000 frames")
        total += len(frames)
        require(total <= MAX_FRAMES, "Too many frames in recording")
        previous = -1
        for frame in frames:
            require(isinstance(frame, dict) and finite(frame.get("t")), f"{name}: invalid frame time")
            require(set(frame) == {"t", "landmarks"}, f"{name}: unexpected frame fields")
            t = frame["t"]
            require(previous < t <= 600000 and t >= 0, f"{name}: timestamps must increase within 10 minutes")
            previous = t
            points = frame.get("landmarks")
            require(isinstance(points, list) and len(points) == 33, f"{name}: expected 33 landmarks")
            for point in points:
                require(isinstance(point, dict), f"{name}: malformed landmark")
                require(set(point).issubset({"x", "y", "z", "visibility", "presence"}),
                        f"{name}: unsupported landmark field")
                require(all(finite(point.get(key)) for key in ("x", "y", "visibility"))
                        and all(finite(value) for value in point.values()), f"{name}: non-finite landmark")
                require(0 <= point["visibility"] <= 1 and 0 <= point.get("presence", 1) <= 1,
                        f"{name}: visibility/presence outside [0,1]")
                require(abs(point["x"]) <= 10 and abs(point["y"]) <= 10
                        and abs(point.get("z", 0)) <= 100, f"{name}: unreasonable landmark coordinate")
        interval = clip.get("range")
        require(isinstance(interval, list) and len(interval) == 2 and all(finite(t) for t in interval),
                f"{name}: invalid label range")
        require(frames[0]["t"] <= interval[0] < interval[1] <= frames[-1]["t"],
                f"{name}: label range must be inside captured times")
    return data


def load_dataset(paths):
    clips, sources, ids, payloads, sessions = [], set(), set(), {}, {}
    total_bytes = 0
    for path in paths:
        total_bytes += Path(path).stat().st_size
        require(total_bytes <= 250 * 1024 * 1024, "Combined dataset exceeds 250 MB")
        data = validate_recording(read_json(path))
        sources.add(data["source"])
        for clip in data["clips"]:
            require(clip["id"] not in ids, f"Duplicate/conflicting clip ID: {clip['id']}")
            ids.add(clip["id"])
            # Changing an ID, label, trim or session must not evade duplicate detection.
            payload = digest({"baseline": clip["baseline"], "frames": clip["frames"]})
            require(payload not in payloads, f"Duplicate/conflicting recording content: {clip['id']}")
            payloads[payload] = clip["id"]
            signature = (clip["participantId"], canonical(clip["baseline"]))
            require(clip["sessionId"] not in sessions or sessions[clip["sessionId"]] == signature,
                    f"Session {clip['sessionId']} changes participant or standing calibration")
            sessions[clip["sessionId"]] = signature
            clips.append(clip)
    require(clips, "Dataset is empty")
    require(sum(len(c["frames"]) for c in clips) <= MAX_FRAMES, "Combined dataset exceeds 200000 frames")
    require(set(c["label"] for c in clips) == set(LABELS), "Dataset must contain all five classes")
    clips.sort(key=lambda clip: clip["id"])
    return {"clips": clips, "synthetic": "synthetic" in sources,
            "identity": digest({"sources": sorted(sources), "clips": clips})}


def split_groups(clips, group_by="session", seed=42):
    require(group_by in ("session", "participant"), "Unsupported grouping")
    field = "sessionId" if group_by == "session" else "participantId"
    groups = sorted({clip[field] for clip in clips})
    require(len(groups) >= 3, f"Need at least three independent {group_by}s, each with all classes")
    count = max(1, round(len(groups) * .2))
    require(len(groups) - 2 * count >= 1, "Insufficient training groups")
    generator = random.Random(seed)
    for _ in range(1000):
        shuffled = list(groups)
        generator.shuffle(shuffled)
        assignments = dict(train=shuffled[2 * count:], validation=shuffled[:count], test=shuffled[count:2 * count])
        split = {name: [c for c in clips if c[field] in selected] for name, selected in assignments.items()}
        if all({c["label"] for c in part} == set(LABELS) for part in split.values()):
            return split, {"groupBy": group_by, "seed": seed,
                           "groups": {name: sorted(value) for name, value in assignments.items()},
                           "clipIds": {name: [c["id"] for c in value] for name, value in split.items()}}
    raise DataError("Cannot produce three group-separated splits containing every class. Record all five classes in more sessions.")


def windows_for_clip(clip):
    history = TemporalWindow()
    windows = []
    for index, frame in enumerate(clip["frames"]):
        vector = history.push(extract_features(frame["landmarks"], clip["baseline"]), frame["t"])
        if vector is not None and clip["range"][0] <= frame["t"] <= clip["range"][1]:
            windows.append(dict(x=vector, y=LABELS.index(clip["label"]), t=frame["t"],
                                frame=index, clip=clip["id"]))
    return windows


def prepare(paths, group_by="session", seed=42):
    data = load_dataset(paths)
    split, manifest = split_groups(data["clips"], group_by, seed)
    windows = {}
    for name, clips in split.items():
        windows[name] = []
        for clip in clips:
            selected = windows_for_clip(clip)
            require(selected, f"{clip['id']}: no valid 466.7 ms full-body windows in labeled range")
            windows[name].extend(selected)
        require({w["y"] for w in windows[name]} == set(range(5)), f"{name}: missing valid class windows")
    data.update(split=split, manifest=manifest, windows=windows)
    return data


def summary(data):
    return {"datasetIdentity": data["identity"], "synthetic": data["synthetic"],
            "groupBy": data["manifest"]["groupBy"], "splits": {
                name: {"groups": len(data["manifest"]["groups"][name]), "clips": len(clips),
                       "windows": len(data["windows"][name]),
                       "classClips": dict(Counter(c["label"] for c in clips)),
                       "classWindows": dict(Counter(LABELS[w["y"]] for w in data["windows"][name]))}
                for name, clips in data["split"].items()}}
