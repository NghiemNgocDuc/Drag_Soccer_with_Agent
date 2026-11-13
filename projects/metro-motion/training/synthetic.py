"""Obviously synthetic landmarks for parser/trainer/browser plumbing checks only."""
import math
import random

from features import FEATURE_VERSION, LABELS

BASELINE = dict(shoulderX=.5, shoulderY=.32, hipX=.5, hipY=.55, width=.2, torso=.23,
                headY=.14, leftFootY=.9, rightFootY=.9)


def landmarks(label="neutral", amount=1., jitter=0.):
    points = [dict(x=.5 + jitter, y=.45, z=0., visibility=.99, presence=.99) for _ in range(33)]
    for index, x, y in ((0, .5, .14), (11, .4, .32), (12, .6, .32),
                        (23, .44, .55), (24, .56, .55), (25, .44, .72),
                        (26, .56, .72), (27, .44, .9), (28, .56, .9)):
        points[index].update(x=x + jitter, y=y)
    dx = (.17 if label == "left" else -.17 if label == "right" else 0.) * amount
    for point in points:
        point["x"] += dx
        if label == "jump":
            point["y"] -= .09 * amount
    if label == "duck":
        for index in (0, 11, 12):
            points[index]["y"] += .10 * amount
        for index in (23, 24):
            points[index]["y"] += .06 * amount
    return points


def make_dataset(sessions=6, seed=3):
    rng = random.Random(seed)
    clips = []
    for session in range(sessions):
        for label in LABELS:
            frames = []
            for index in range(46):
                t = index * 1000 / 15
                # Standing context; review range covers the labeled plateau only.
                amount = max(0, min(1, (t - 600) / 200))
                if t > 1800:
                    amount = max(0, 1 - (t - 1800) / 200)
                points = landmarks(label, amount, rng.uniform(-.001, .001))
                frames.append(dict(t=t, landmarks=points))
            clips.append(dict(id=f"synthetic-{seed}-{session}-{label}", sessionId=f"synthetic-session-{seed}-{session}",
                              participantId=f"synthetic-participant-{seed}-{session}", label=label,
                              range=[800., 1750.] if label != "neutral" else [600., 2900.],
                              baseline=dict(BASELINE), frames=frames))
    return dict(format="metro-motion-motion-data", version=1, featureVersion=FEATURE_VERSION,
                source="synthetic", clips=clips)
