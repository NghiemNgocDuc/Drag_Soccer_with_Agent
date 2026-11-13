"""Train-only balancing/augmentation and validation-only confidence calibration.

Functions take torch explicitly so dataset validation still needs only stdlib.
No function reads the test split or mutates its inputs.
"""
from collections import Counter, defaultdict
import math

from dataset import require
from features import FEATURE_COUNT, INPUT_SIZE, WINDOW_SIZE


def balanced_window_weights(clips, windows):
    """Equal classes, then people, sessions, clips, and windows within each clip.

    Adjacent sliding windows are correlated: one long recording must not outweigh
    several independently recorded clips or one prolific participant dominate.
    Missing classes in an individual session are permitted; split validation
    separately requires every class in each complete split.
    """
    by_id = {clip["id"]: clip for clip in clips}
    counts = Counter(window["clip"] for window in windows)
    people = defaultdict(set)
    sessions = defaultdict(set)
    recordings = defaultdict(set)
    for window in windows:
        clip = by_id[window["clip"]]
        label, participant, session = window["y"], clip["participantId"], clip["sessionId"]
        people[label].add(participant)
        sessions[label, participant].add(session)
        recordings[label, participant, session].add(clip["id"])
    require(windows and len(people) > 0, "Cannot balance an empty split")
    result = []
    for window in windows:
        clip = by_id[window["clip"]]
        label, participant, session = window["y"], clip["participantId"], clip["sessionId"]
        result.append(1. / (len(people) * len(people[label]) * len(sessions[label, participant])
                            * len(recordings[label, participant, session]) * counts[clip["id"]]))
    return result


def mirror_windows(torch, inputs, labels):
    """Exact mm-body-v1 reflection: negate lateral features and exchange feet."""
    values = inputs.reshape(-1, WINDOW_SIZE, FEATURE_COUNT).clone()
    values[:, :, :2].neg_()
    feet = values[:, :, 4].clone()
    values[:, :, 4] = values[:, :, 5]
    values[:, :, 5] = feet
    targets = labels.clone()
    targets[labels == 1] = 2
    targets[labels == 2] = 1
    return values.reshape(-1, INPUT_SIZE), targets


def augment_batch(torch, inputs, labels, noise_std=.015, mirror_probability=.5):
    """Use only inside the training loop, before normalization; inputs stay intact."""
    values, targets = inputs.clone(), labels.clone()
    if mirror_probability:
        mask = torch.rand(len(values), device=values.device) < mirror_probability
        reflected, reflected_labels = mirror_windows(torch, values[mask], targets[mask])
        values[mask], targets[mask] = reflected, reflected_labels
    if noise_std:
        # Feature-space jitter, not noise multiplied by a tiny measured std.
        values += torch.randn_like(values) * noise_std
    return values.clamp_(-6., 6.), targets


def normalization(torch, train_x, weights=None, scale_floor=.05, mirror=False):
    """Weighted train-only moments with a physical feature-unit noise floor.

    When reflection augmentation is enabled, include its exact expected moments
    instead of allowing arbitrary left/right collection imbalance into the mean.
    No random augmentation or held-out data contributes to normalization.
    """
    require(math.isfinite(scale_floor) and .005 <= scale_floor <= .5, "Invalid normalization floor")
    values = train_x.to(dtype=torch.float64)
    weight = (torch.ones(len(values), dtype=torch.float64) if weights is None
              else torch.as_tensor(weights, dtype=torch.float64).clone())
    require(tuple(weight.shape) == (len(values),) and bool(torch.isfinite(weight).all())
            and bool((weight > 0).all()), "Invalid normalization weights")
    weight /= weight.sum()
    mean = (values * weight[:, None]).sum(dim=0)
    if mirror:
        reflected, _ = mirror_windows(torch, values, torch.zeros(len(values), dtype=torch.long))
        mirror_mean = (reflected * weight[:, None]).sum(dim=0)
        mean = (mean + mirror_mean) / 2
        variance = ((((values - mean) ** 2 + (reflected - mean) ** 2) / 2) * weight[:, None]).sum(dim=0)
    else:
        variance = (((values - mean) ** 2) * weight[:, None]).sum(dim=0)
    return mean.float(), variance.sqrt().clamp(min=scale_floor).float()


def calibrate_temperature(torch, logits, labels, weights=None, enabled=True):
    """Minimize validation NLL over temperature [.25, 5]; never change argmax.

    NLL is convex in inverse temperature. Deterministic bisection of its gradient
    avoids optimizer state/extra randomness and retains T=1 unless NLL improves.
    """
    values = logits.detach().cpu().double()
    targets = labels.detach().cpu().long()
    weight = (torch.ones(len(values), dtype=torch.float64) if weights is None
              else torch.as_tensor(weights, dtype=torch.float64).cpu().clone())
    require(tuple(values.shape) == (len(targets), 5) and len(targets) > 0
            and bool(torch.isfinite(values).all()), "Invalid calibration logits")
    require(tuple(weight.shape) == (len(values),) and bool(torch.isfinite(weight).all())
            and bool((weight > 0).all()), "Invalid calibration weights")
    weight /= weight.sum()
    truth = values.gather(1, targets[:, None]).squeeze(1)

    def loss(inverse):
        return float(((torch.logsumexp(values * inverse, dim=1) - truth * inverse) * weight).sum())

    def gradient(inverse):
        expected = (torch.softmax(values * inverse, dim=1) * values).sum(dim=1)
        return float(((expected - truth) * weight).sum())

    before = loss(1.)
    temperature = 1.
    if enabled:
        lower, upper = .2, 4.
        if gradient(lower) >= 0:
            inverse = lower
        elif gradient(upper) <= 0:
            inverse = upper
        else:
            for _ in range(48):
                middle = (lower + upper) / 2
                if gradient(middle) > 0:
                    upper = middle
                else:
                    lower = middle
            inverse = (lower + upper) / 2
        if loss(inverse) < before - 1e-12:
            temperature = min(5., max(.25, 1. / inverse))
    return temperature, {"temperature": temperature, "source": "validation",
                         "objective": "class-participant-session-clip-balanced NLL",
                         "enabled": enabled, "nllBefore": before, "nllAfter": loss(1. / temperature)}
