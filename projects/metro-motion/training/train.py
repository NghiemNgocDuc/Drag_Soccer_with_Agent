#!/usr/bin/env python3
"""Motion Lab CLI. Validation/synthetic generation deliberately import no torch."""
import argparse
import copy
import glob
import json
import math
import os
from pathlib import Path
import random
import signal
import sys
import tempfile

from dataset import DataError, canonical, prepare, require, summary
from evaluation import METRIC_SCOPE, baseline_predictions, eligibility, metrics
from controller_evaluation import controller_evaluation
from features import FEATURE_VERSION, INPUT_SIZE, LABELS, SAMPLE_HZ, WINDOW_SIZE, finite
from optimization import (augment_batch, balanced_window_weights,
                          calibrate_temperature, normalization)

CHECKPOINT_VERSION = 2


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, allow_nan=False) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_checkpoint(torch, path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            torch.save(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def import_torch():
    try:
        import torch
    except ImportError as error:
        raise DataError("Training needs PyTorch. See training/README.md; validation needs only Python.") from error
    require(tuple(int(x) for x in torch.__version__.split("+")[0].split(".")[:2]) >= (2, 6),
            "Use PyTorch >=2.6 for weights_only checkpoint loading")
    return torch


def build_model(torch, hidden):
    return torch.nn.Sequential(torch.nn.Linear(INPUT_SIZE, hidden), torch.nn.ReLU(), torch.nn.Linear(hidden, 5))


def _finite_tree(torch, value, depth=0):
    require(depth < 20, "Checkpoint nesting too deep")
    if isinstance(value, torch.Tensor):
        require(value.numel() < 1_000_000 and bool(torch.isfinite(value).all()), "Invalid checkpoint tensor")
    elif isinstance(value, dict):
        require(len(value) < 100_000, "Checkpoint mapping too large")
        for key, child in value.items():
            require(isinstance(key, (str, int)), "Invalid checkpoint key")
            _finite_tree(torch, child, depth + 1)
    elif isinstance(value, (list, tuple)):
        require(len(value) < 100_000, "Checkpoint sequence too large")
        for child in value:
            _finite_tree(torch, child, depth + 1)
    else:
        require(value is None or isinstance(value, (str, bool, int)) or finite(value), "Invalid checkpoint value")


def load_checkpoint(torch, path):
    path = Path(path)
    require(path.stat().st_size <= 32 * 1024 * 1024, "Checkpoint exceeds 32 MB")
    try:
        checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    except Exception as error:
        raise DataError(f"Cannot safely load checkpoint: {error}") from error
    _finite_tree(torch, checkpoint)
    require(not isinstance(checkpoint, dict) or checkpoint.get("version") != 1,
            "Checkpoint v1 used different normalization and sampling. Start a fresh v2 run; keep the v1 model for comparison.")
    require(isinstance(checkpoint, dict) and checkpoint.get("format") == "metro-motion-training-checkpoint"
            and checkpoint.get("version") == CHECKPOINT_VERSION
            and checkpoint.get("featureVersion") == FEATURE_VERSION, "Incompatible checkpoint")
    config = checkpoint.get("config")
    require(isinstance(config, dict) and set(config) == {"hidden", "seed", "groupBy", "learningRate", "batchSize", "threshold",
                                                       "normalizationFloor", "featureNoise", "mirrorAugmentation", "calibrate", "balance"},
            "Invalid checkpoint configuration")
    require(isinstance(config["hidden"], int) and not isinstance(config["hidden"], bool) and 1 <= config["hidden"] <= 128,
            "Invalid checkpoint hidden size")
    require(isinstance(config["seed"], int) and 0 <= config["seed"] <= 2**32 - 1
            and config["groupBy"] in ("session", "participant")
            and finite(config["learningRate"]) and 0 < config["learningRate"] <= .1
            and isinstance(config["batchSize"], int) and 1 <= config["batchSize"] <= 4096
            and finite(config["threshold"]) and .5 <= config["threshold"] <= .99
            and finite(config["normalizationFloor"]) and .005 <= config["normalizationFloor"] <= .5
            and finite(config["featureNoise"]) and 0 <= config["featureNoise"] <= .1
            and isinstance(config["mirrorAugmentation"], bool) and isinstance(config["calibrate"], bool)
            and config["balance"] == "class-participant-session-clip", "Invalid checkpoint hyperparameters")
    require(isinstance(checkpoint.get("epoch"), int) and 1 <= checkpoint["epoch"] <= 10000
            and isinstance(checkpoint.get("bestEpoch"), int) and 1 <= checkpoint["bestEpoch"] <= checkpoint["epoch"]
            and finite(checkpoint.get("bestLoss")) and checkpoint["bestLoss"] >= 0, "Invalid checkpoint epoch/loss")
    reference = build_model(torch, config["hidden"]).state_dict()
    for name in ("model", "bestModel"):
        values = checkpoint.get(name)
        require(isinstance(values, dict) and set(values) == set(reference), f"Invalid {name} state")
        for key, tensor in values.items():
            require(isinstance(tensor, torch.Tensor) and tensor.dtype == torch.float32
                    and tensor.shape == reference[key].shape, f"Invalid {name} tensor {key}")
    for name in ("mean", "scale"):
        tensor = checkpoint.get(name)
        require(isinstance(tensor, torch.Tensor) and tensor.dtype == torch.float32
                and tuple(tensor.shape) == (INPUT_SIZE,), f"Invalid checkpoint {name}")
    require(bool((checkpoint["scale"] >= 1e-6).all()), "Invalid checkpoint normalization")
    require(isinstance(checkpoint.get("optimizer"), dict) and isinstance(checkpoint.get("random"), dict),
            "Checkpoint lacks optimizer/random state")
    return checkpoint


def tensors(torch, data):
    return {name: (torch.tensor([w["x"] for w in values], dtype=torch.float32),
                   torch.tensor([w["y"] for w in values], dtype=torch.long))
            for name, values in data["windows"].items()}


def model_logits(torch, model, x, mean, scale, device):
    model.eval()
    results = []
    with torch.inference_mode():
        for chunk in x.split(1024):
            results.append(model(((chunk - mean) / scale).to(device)).cpu())
    return torch.cat(results)


def model_probabilities(torch, model, x, mean, scale, device, temperature=1.):
    require(finite(temperature) and .25 <= temperature <= 5., "Invalid confidence temperature")
    return torch.softmax(model_logits(torch, model, x, mean, scale, device) / temperature, dim=1).tolist()


def evaluate_model(torch, model, data, arrays, mean, scale, config, device, temperature=1.):
    learned, raw, rules, confidences, emitted = {}, {}, {}, {}, {}
    for name in ("validation", "test"):
        probabilities = model_probabilities(torch, model, arrays[name][0], mean, scale, device, temperature)
        predicted = [max(range(5), key=p.__getitem__) for p in probabilities]
        confident = [max(p) >= config["threshold"] for p in probabilities]
        baseline = baseline_predictions(data["split"][name], data["windows"][name])
        effective = [p if confident[i] else baseline[i] for i, p in enumerate(predicted)]
        raw[name] = metrics(data["split"][name], data["windows"][name], predicted)
        learned[name] = metrics(data["split"][name], data["windows"][name], effective, confident)
        rules[name] = metrics(data["split"][name], data["windows"][name], baseline)
        confidences[name] = sum(confident) / len(confident)
        emitted[name] = controller_evaluation(data["split"][name],
            lambda values: model_probabilities(torch, model, torch.tensor(values, dtype=torch.float32),
                                               mean, scale, device, temperature), config["threshold"])
    eligible, reasons = eligibility(summary(data), learned, rules, data["synthetic"], emitted)
    return {"controlEligible": eligible, "synthetic": data["synthetic"], "gateReasons": reasons,
            "scope": METRIC_SCOPE, "groupBy": config["groupBy"],
            "validation": learned["validation"], "test": learned["test"],
            "rawModel": raw, "ruleBaseline": rules, "confidenceCoverage": confidences,
            "evidence": summary(data), "emittedControls": emitted}


def export_artifact(model, mean, scale, config, report, temperature=1.):
    state = model.state_dict()
    artifact = {"format": "metro-motion-motion-model", "version": 1,
                "featureVersion": FEATURE_VERSION, "labels": LABELS,
                "windowSize": WINDOW_SIZE, "sampleHz": SAMPLE_HZ, "inputSize": INPUT_SIZE,
                "hiddenSize": config["hidden"], "mean": mean.tolist(), "scale": scale.tolist(),
                "weights1": state["0.weight"].cpu().tolist(), "bias1": state["0.bias"].cpu().tolist(),
                "weights2": state["2.weight"].cpu().tolist(), "bias2": state["2.bias"].cpu().tolist(),
                "threshold": config["threshold"], "temperature": temperature, "metrics": report}
    require(len(json.dumps(artifact, indent=2, allow_nan=False).encode("utf-8")) < 2 * 1024 * 1024,
            "Export exceeds browser 2 MB limit")
    return artifact


def choose_device(torch, requested):
    if requested == "cuda":
        require(torch.cuda.is_available(), "CUDA requested but unavailable. Check your allocated GPU and PyTorch wheel.")
    return torch.device("cuda" if requested == "cuda" or requested == "auto" and torch.cuda.is_available() else "cpu")


def validate_optimizer(torch, optimizer):
    require(len(optimizer.param_groups) == 1, "Unexpected optimizer parameter groups")
    for parameter, state in optimizer.state.items():
        require(set(state).issubset({"step", "exp_avg", "exp_avg_sq"}), "Unexpected optimizer state")
        for key, value in state.items():
            require(isinstance(value, torch.Tensor) and bool(torch.isfinite(value).all())
                    and (value.numel() == 1 if key == "step" else value.shape == parameter.shape),
                    "Invalid optimizer tensor shape")


def train(args):
    # Validate even before importing torch / allocating CUDA memory.
    data = prepare(args.data, args.group_by, args.seed)
    require(not data["synthetic"] or args.allow_synthetic, "Synthetic data requires --allow-synthetic and can never control play")
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    torch = import_torch()
    torch.set_num_threads(args.threads)
    torch.use_deterministic_algorithms(True)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
    device = choose_device(torch, args.device)
    config = dict(hidden=args.hidden, seed=args.seed, groupBy=args.group_by,
                  learningRate=args.learning_rate, batchSize=args.batch_size, threshold=args.threshold,
                  normalizationFloor=args.normalization_floor, featureNoise=args.feature_noise,
                  mirrorAugmentation=not args.no_mirror, calibrate=not args.no_calibrate,
                  balance="class-participant-session-clip")
    arrays = tensors(torch, data)
    weights = {name: torch.tensor(balanced_window_weights(data["split"][name], data["windows"][name]), dtype=torch.float64)
               for name in ("train", "validation")}
    mean, scale = normalization(torch, arrays["train"][0], weights["train"],
                                config["normalizationFloor"], config["mirrorAugmentation"])
    model = build_model(torch, args.hidden).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=1e-4)
    first, best_loss, best_state, best_epoch = 1, math.inf, None, 0
    if args.resume:
        previous = load_checkpoint(torch, args.resume)
        require(previous.get("datasetIdentity") == data["identity"] and previous.get("manifest") == data["manifest"],
                "Resume dataset or split identity changed")
        require(previous["config"] == config, "Resume hyperparameters changed; use the original settings")
        require(torch.equal(previous["mean"], mean) and torch.equal(previous["scale"], scale),
                "Resume training normalization differs")
        model.load_state_dict(previous["model"], strict=True)
        try:
            optimizer.load_state_dict(previous["optimizer"])
            validate_optimizer(torch, optimizer)
            random.setstate(previous["random"]["python"])
            torch.set_rng_state(previous["random"]["torch"])
            if device.type == "cuda" and previous["random"].get("cuda"):
                torch.cuda.set_rng_state_all(previous["random"]["cuda"])
        except (KeyError, ValueError, RuntimeError, TypeError) as error:
            raise DataError(f"Invalid resume state: {error}") from error
        first, best_loss = previous["epoch"] + 1, previous["bestLoss"]
        best_state, best_epoch = previous["bestModel"], previous["bestEpoch"]
        require(args.epochs >= previous["epoch"], "Requested epoch total is below the checkpoint epoch")
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    atomic_json(out / "split.json", {"datasetIdentity": data["identity"], **data["manifest"]})
    stop = {"requested": False}
    def request_stop(_signal, _frame):
        stop["requested"] = True
    for name in ("SIGINT", "SIGTERM", "SIGUSR1"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), request_stop)
    train_x, train_y = arrays["train"]
    # Every original window is visited once per epoch. A weighted objective
    # prevents repeated adjacent windows/long clips from dominating the loss.
    training_weights = (weights["train"] * len(train_x)).float()
    print(json.dumps({"device": str(device), "data": summary(data), "startEpoch": first}), flush=True)
    for epoch in range(first, args.epochs + 1):
        model.train()
        permutation = torch.randperm(len(train_x))
        training_loss = 0.
        for indexes in permutation.split(args.batch_size):
            optimizer.zero_grad(set_to_none=True)
            batch_x, batch_y = augment_batch(torch, train_x[indexes], train_y[indexes], config["featureNoise"],
                                              .5 if config["mirrorAugmentation"] else 0.)
            logits = model(((batch_x - mean) / scale).to(device))
            per_window = torch.nn.functional.cross_entropy(logits, batch_y.to(device), reduction="none")
            loss = (per_window * training_weights[indexes].to(device)).mean()
            require(bool(torch.isfinite(loss)), "Non-finite training loss; no model exported")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.)
            optimizer.step()
            training_loss += loss.item() * len(indexes)
        # Only validation chooses the best checkpoint; test remains untouched here.
        model.eval()
        with torch.inference_mode():
            validation_loss = 0.
            vx, vy = arrays["validation"]
            for start in range(0, len(vx), 1024):
                logits = model(((vx[start:start + 1024] - mean) / scale).to(device))
                per_window = torch.nn.functional.cross_entropy(logits, vy[start:start + 1024].to(device), reduction="none")
                validation_loss += (per_window.cpu().double() * weights["validation"][start:start + 1024]).sum().item()
        require(math.isfinite(validation_loss), "Non-finite validation loss")
        if validation_loss < best_loss:
            best_loss, best_epoch = validation_loss, epoch
            best_state = {name: value.detach().cpu().clone() for name, value in model.state_dict().items()}
        checkpoint = {"format": "metro-motion-training-checkpoint", "version": CHECKPOINT_VERSION,
                      "featureVersion": FEATURE_VERSION, "datasetIdentity": data["identity"],
                      "manifest": data["manifest"], "config": config, "epoch": epoch,
                      "model": {name: value.detach().cpu() for name, value in model.state_dict().items()},
                      "optimizer": optimizer.state_dict(), "mean": mean, "scale": scale,
                      "bestLoss": best_loss, "bestEpoch": best_epoch, "bestModel": best_state,
                      "random": {"python": random.getstate(), "torch": torch.get_rng_state(),
                                 "cuda": torch.cuda.get_rng_state_all() if device.type == "cuda" else []}}
        atomic_checkpoint(torch, out / "last.pt", checkpoint)
        print(json.dumps({"epoch": epoch, "trainLoss": training_loss / len(train_x),
                          "validationLoss": validation_loss, "bestEpoch": best_epoch}), flush=True)
        if stop["requested"]:
            print("Stopped after atomic epoch checkpoint; resume from last.pt. No new model export.", flush=True)
            return 130
    model.load_state_dict(best_state, strict=True)
    # Test is never passed to the calibration optimizer or checkpoint selector.
    temperature, calibration = calibrate_temperature(torch,
        model_logits(torch, model, arrays["validation"][0], mean, scale, device), arrays["validation"][1],
        weights["validation"], config["calibrate"])
    report = evaluate_model(torch, model, data, arrays, mean, scale, config, device, temperature)
    report.update(bestEpoch=best_epoch, validationLoss=best_loss, trainingEpochs=args.epochs,
                  calibration=calibration, trainingConfig=config)
    atomic_json(out / "metrics.json", report)
    atomic_json(out / "model.json", export_artifact(model, mean, scale, config, report, temperature))
    print(json.dumps({"model": str(out / "model.json"), "controlEligible": report["controlEligible"],
                      "gateReasons": report["gateReasons"]}), flush=True)
    return 0


def evaluate(args):
    # Schema validation still occurs before torch import; checkpoint supplies split settings.
    from dataset import load_dataset
    load_dataset(args.data)
    torch = import_torch()
    torch.set_num_threads(args.threads)
    checkpoint = load_checkpoint(torch, args.checkpoint)
    config = checkpoint["config"]
    data = prepare(args.data, config["groupBy"], config["seed"])
    require(data["identity"] == checkpoint.get("datasetIdentity") and data["manifest"] == checkpoint.get("manifest"),
            "Evaluation dataset/split differs from this checkpoint. Do not silently redefine the holdout.")
    arrays = tensors(torch, data)
    train_weights = balanced_window_weights(data["split"]["train"], data["windows"]["train"])
    mean, scale = normalization(torch, arrays["train"][0], train_weights,
                                config["normalizationFloor"], config["mirrorAugmentation"])
    require(torch.equal(mean, checkpoint["mean"]) and torch.equal(scale, checkpoint["scale"]), "Normalization mismatch")
    device = choose_device(torch, args.device)
    model = build_model(torch, config["hidden"]).to(device)
    model.load_state_dict(checkpoint["bestModel"], strict=True)
    temperature, calibration = calibrate_temperature(torch,
        model_logits(torch, model, arrays["validation"][0], mean, scale, device), arrays["validation"][1],
        balanced_window_weights(data["split"]["validation"], data["windows"]["validation"]), config["calibrate"])
    report = evaluate_model(torch, model, data, arrays, mean, scale, config, device, temperature)
    report.update(bestEpoch=checkpoint["bestEpoch"], validationLoss=checkpoint["bestLoss"], trainingEpochs=checkpoint["epoch"],
                  calibration=calibration, trainingConfig=config)
    atomic_json(args.output, report)
    print(json.dumps({"report": args.output, "controlEligible": report["controlEligible"]}), flush=True)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "train", "evaluate"):
        sub = commands.add_parser(name)
        sub.add_argument("data", nargs="+", help="Local Motion Lab JSON exports; globs accepted")
        if name != "evaluate":
            sub.add_argument("--group-by", choices=("session", "participant"), default="session")
            sub.add_argument("--seed", type=int, default=42)
        if name != "validate":
            sub.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
            sub.add_argument("--threads", type=int, default=2)
            sub.add_argument("--output", required=True)
        if name == "train":
            sub.add_argument("--epochs", type=int, default=60)
            sub.add_argument("--hidden", type=int, default=32)
            sub.add_argument("--batch-size", type=int, default=128)
            sub.add_argument("--learning-rate", type=float, default=.001)
            sub.add_argument("--threshold", type=float, default=.8)
            sub.add_argument("--normalization-floor", type=float, default=.05, help="Minimum std in raw feature units")
            sub.add_argument("--feature-noise", type=float, default=.015, help="Train-only feature jitter std; 0 disables")
            sub.add_argument("--no-mirror", action="store_true", help="Disable train-only left/right reflection")
            sub.add_argument("--no-calibrate", action="store_true", help="Keep confidence temperature 1")
            sub.add_argument("--resume")
            sub.add_argument("--allow-synthetic", action="store_true")
        if name == "evaluate":
            sub.add_argument("--checkpoint", required=True)
    synthetic = commands.add_parser("synthetic", help="Generate plumbing fixtures, never human training evidence")
    synthetic.add_argument("--output", required=True)
    synthetic.add_argument("--sessions", type=int, default=6)
    synthetic.add_argument("--seed", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        if args.command == "synthetic":
            from synthetic import make_dataset
            require(3 <= args.sessions <= 100, "Synthetic sessions must be 3..100")
            atomic_json(args.output, make_dataset(args.sessions, args.seed))
            print("Wrote SYNTHETIC plumbing fixture. It cannot qualify a control model.")
            return 0
        paths = []
        for pattern in args.data:
            matched = sorted(glob.glob(pattern))
            require(matched, f"No dataset files match: {pattern}")
            paths.extend(matched)
        args.data = paths
        if hasattr(args, "seed"):
            require(0 <= args.seed <= 2**32 - 1, "Seed must be a nonnegative 32-bit integer")
        if args.command == "validate":
            print(json.dumps(summary(prepare(paths, args.group_by, args.seed)), indent=2))
            return 0
        require(1 <= args.threads <= 32, "Threads must be 1..32")
        if args.command == "evaluate":
            return evaluate(args)
        require(1 <= args.epochs <= 10000 and 1 <= args.hidden <= 128 and 1 <= args.batch_size <= 4096,
                "Invalid epochs, hidden size or batch size")
        require(finite(args.learning_rate) and 0 < args.learning_rate <= .1
                and finite(args.threshold) and .5 <= args.threshold <= .99, "Invalid learning rate or threshold")
        require(finite(args.normalization_floor) and .005 <= args.normalization_floor <= .5
                and finite(args.feature_noise) and 0 <= args.feature_noise <= .1,
                "Normalization floor must be .005..5e-1 and feature noise must be 0..1e-1")
        return train(args)
    except (DataError, OSError) as error:
        print(f"Motion Lab: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
