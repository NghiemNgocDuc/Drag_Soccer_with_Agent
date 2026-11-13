import copy
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TRAINING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TRAINING))
from dataset import DataError, load_dataset, prepare, read_json, split_groups, validate_recording, windows_for_clip
from evaluation import baseline_predictions, eligibility, metrics
from features import TemporalWindow, extract_features
from synthetic import BASELINE, landmarks, make_dataset
from train import atomic_json


class FeatureTests(unittest.TestCase):
    def test_feature_units_mirror_and_invalid_quality(self):
        for actual, expected in zip(extract_features(landmarks(), BASELINE), [0., 0., 0., 0., 0., 0., 1., 1.]):
            self.assertAlmostEqual(actual, expected)
        self.assertLess(extract_features(landmarks("left"), BASELINE)[0], -.8)
        self.assertGreater(extract_features(landmarks("right"), BASELINE)[0], .8)
        invalid = landmarks()
        invalid[26]["visibility"] = .4
        self.assertIsNone(extract_features(invalid, BASELINE))
        invalid = landmarks()
        invalid[7]["z"] = math.nan
        self.assertIsNone(extract_features(invalid, BASELINE))
        self.assertIsNone(extract_features(landmarks()[:29], BASELINE))

    def test_time_interpolation_and_gap_reset(self):
        history = TemporalWindow()
        self.assertIsNone(history.push([0.] * 8, 0))
        history.push([1.] * 8, 200)
        history.push([2.] * 8, 400)
        vector = history.push([3.] * 8, 600)
        self.assertEqual(len(vector), 64)
        self.assertAlmostEqual(vector[0], 2 / 3)
        self.assertEqual(vector[-1], 3.)
        self.assertIsNone(history.push([3.] * 8, 900))
        self.assertIsNone(history.push([3.] * 8, 899))
        self.assertEqual(history.history, [])
        self.assertIsNone(history.push([7.] * 8, 1000))

    def test_fixture_parity(self):
        fixture = read_json(TRAINING / "tests" / "parity.json")
        history = TemporalWindow()
        for frame in fixture["frames"]:
            features = extract_features(frame["landmarks"], fixture["baseline"])
            self.assertEqual(features, frame["features"])
            self.assertEqual(history.push(features, frame["t"]), frame["window"])


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "data.json"
        self.data = make_dataset()
        atomic_json(self.path, self.data)

    def tearDown(self):
        self.temp.cleanup()

    def test_three_disjoint_whole_group_holdouts(self):
        prepared = prepare([self.path])
        groups = prepared["manifest"]["groups"]
        self.assertFalse(set(groups["train"]) & set(groups["validation"]))
        self.assertFalse(set(groups["test"]) & set(groups["validation"]))
        self.assertFalse(set(groups["test"]) & set(groups["train"]))
        self.assertEqual(prepared["manifest"], prepare([self.path])["manifest"])
        self.assertTrue(prepared["synthetic"])
        participant = prepare([self.path], "participant")
        self.assertEqual(participant["manifest"]["groupBy"], "participant")

    def test_insufficient_groups_and_missing_classes(self):
        clips = self.data["clips"][:5]
        with self.assertRaisesRegex(DataError, "three"):
            split_groups(clips)
        self.data["clips"] = [c for c in self.data["clips"] if c["label"] != "jump"]
        atomic_json(self.path, self.data)
        with self.assertRaisesRegex(DataError, "five classes"):
            load_dataset([self.path])

    def test_duplicate_and_conflicting_content_rejected(self):
        duplicate = copy.deepcopy(self.data["clips"][0])
        duplicate.update(id="different", sessionId="different", label="duck")
        self.data["clips"].append(duplicate)
        atomic_json(self.path, self.data)
        with self.assertRaisesRegex(DataError, "content"):
            load_dataset([self.path])
        with self.assertRaisesRegex(DataError, "Duplicate"):
            load_dataset([self.path, self.path])

    def test_session_cannot_change_calibration(self):
        self.data["clips"][0]["baseline"]["hipX"] += .01
        atomic_json(self.path, self.data)
        with self.assertRaisesRegex(DataError, "calibration"):
            load_dataset([self.path])

    def test_bad_schema_and_frame_times(self):
        for mutate in (
            lambda d: d.update(featureVersion="future"),
            lambda d: d["clips"][0]["frames"][1].update(t=0),
            lambda d: d["clips"][0]["frames"][0]["landmarks"][0].update(x=True),
            lambda d: d["clips"][0].update(range=[100., 99999.]),
        ):
            invalid = copy.deepcopy(self.data)
            mutate(invalid)
            with self.assertRaises(DataError):
                validate_recording(invalid)

    def test_nonfinite_and_duplicate_json_keys_rejected(self):
        self.path.write_text('{"version": 1, "version": 1}')
        with self.assertRaises(DataError):
            read_json(self.path)
        self.path.write_text('{"x": NaN}')
        with self.assertRaises(DataError):
            read_json(self.path)

    def test_invalid_frames_reset_history_and_context_is_unlabeled(self):
        clip = copy.deepcopy(self.data["clips"][0])
        windows = windows_for_clip(clip)
        self.assertTrue(all(clip["range"][0] <= w["t"] <= clip["range"][1] for w in windows))
        clip["frames"][20]["landmarks"][0]["visibility"] = 0
        windows = windows_for_clip(clip)
        invalid_time = clip["frames"][20]["t"]
        self.assertFalse(any(invalid_time <= w["t"] < invalid_time + 466.6666 for w in windows))

    def test_validation_cli_needs_no_torch(self):
        result = subprocess.run([sys.executable, str(TRAINING / "train.py"), "validate", str(self.path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["synthetic"])

    def test_metrics_and_synthetic_gate(self):
        from dataset import summary
        prepared = prepare([self.path])
        values = {}
        for name in ("validation", "test"):
            clips, windows = prepared["split"][name], prepared["windows"][name]
            predicted = baseline_predictions(clips, windows)
            values[name] = metrics(clips, windows, predicted, [True] * len(windows))
        eligible, reasons = eligibility(summary(prepared), values, values, True)
        self.assertFalse(eligible)
        self.assertTrue(any("Synthetic" in reason for reason in reasons))
        self.assertEqual(values["test"]["neutralFalseActionsPerMinute"], 0)

    def test_rule_baseline_matches_shipped_pose_controller(self):
        from evaluation import RuleBaseline
        runtime = (TRAINING.parent / 'web/js/pose-controls.mjs').as_uri()
        code = ('import {readFileSync} from "node:fs";'
                f'import {{PoseController}} from {json.dumps(runtime)};'
                'const clips=JSON.parse(readFileSync(0,"utf8"));'
                'process.stdout.write(JSON.stringify(clips.map(clip=>{'
                'const controller=new PoseController();controller.baseline=clip.baseline;'
                'return clip.frames.map(frame=>{const p=controller.update(frame.landmarks,frame.t);'
                'return !p.tracked?0:p.jump?3:p.duck?4:p.lane<0?1:p.lane>0?2:0;});})));')
        result = subprocess.run(['node', '--input-type=module', '-e', code],
                                input=json.dumps(self.data['clips']), capture_output=True,
                                text=True, check=True)
        for clip, actual in zip(self.data['clips'], json.loads(result.stdout)):
            controller = RuleBaseline(clip['baseline'])
            self.assertEqual(actual, [controller.update(frame['landmarks'], frame['t']) for frame in clip['frames']])


@unittest.skipUnless(importlib.util.find_spec("torch"), "PyTorch optional; install to run real CPU training smoke")
class TorchSmokeTests(unittest.TestCase):
    def test_real_training_resume_export_and_safe_checkpoint_validation(self):
        import torch
        from train import load_checkpoint, main, normalization
        torch.set_num_threads(1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "synthetic.json"
            atomic_json(data, make_dataset())
            common = [str(data), "--device", "cpu", "--allow-synthetic", "--threads", "1"]
            self.assertEqual(main(["train", *common, "--epochs", "2", "--output", str(root / "resume")]), 0)
            self.assertEqual(main(["train", *common, "--epochs", "4", "--output", str(root / "resume"),
                                   "--resume", str(root / "resume" / "last.pt")]), 0)
            self.assertEqual(main(["train", *common, "--epochs", "4", "--output", str(root / "full")]), 0)
            resumed = load_checkpoint(torch, root / "resume" / "last.pt")
            full = load_checkpoint(torch, root / "full" / "last.pt")
            for key in full["model"]:
                self.assertTrue(torch.equal(full["model"][key], resumed["model"][key]), key)
            artifact = read_json(root / "resume" / "model.json")
            self.assertFalse(artifact["metrics"]["controlEligible"])
            self.assertEqual(set(artifact['metrics']['emittedControls']), {'validation', 'test'})
            self.assertGreater(artifact['metrics']['emittedControls']['test']['learned']['labeledSeconds'], 0)
            self.assertEqual(len(artifact["mean"]), 64)
            self.assertEqual(len(artifact["weights1"]), 32)
            self.assertGreaterEqual(min(artifact['scale']), 1e-6)
            # Run the actual exported weights through the shipped JavaScript,
            # not a second hand-written inference implementation.
            from train import build_model, model_probabilities, tensors
            prepared = prepare([data])
            inputs = [row['x'] for row in prepared['windows']['test']][::7]
            reference = build_model(torch, 32)
            reference.load_state_dict(resumed['bestModel'])
            expected = model_probabilities(torch, reference, torch.tensor(inputs), resumed['mean'], resumed['scale'],
                                           'cpu', artifact.get('temperature', 1.))
            runtime = (TRAINING.parent / 'web/js/learned-controls.mjs').as_uri()
            code = ('import {readFileSync} from "node:fs";'
                    f'import {{validateModel,inferModel}} from {json.dumps(runtime)};'
                    'const {model,inputs}=JSON.parse(readFileSync(0,"utf8"));'
                    'const validated=validateModel(model);'
                    'process.stdout.write(JSON.stringify(inputs.map(x=>inferModel(validated,x).probabilities)));')
            inferred = subprocess.run(['node', '--input-type=module', '-e', code],
                                     input=json.dumps({'model': artifact, 'inputs': inputs}),
                                     capture_output=True, text=True, check=True)
            for actual, reference in zip(json.loads(inferred.stdout), expected):
                for left, right in zip(actual, reference):
                    self.assertAlmostEqual(left, right, delta=2e-6)
            self.assertEqual(main(["evaluate", str(data), "--checkpoint", str(root / "resume" / "last.pt"),
                                   "--device", "cpu", "--output", str(root / "evaluation.json")]), 0)
            self.assertEqual(read_json(root / "evaluation.json")["test"], artifact["metrics"]["test"])
            self.assertEqual(read_json(root / "evaluation.json")["calibration"], artifact["metrics"]["calibration"])
            self.assertLessEqual(artifact["metrics"]["calibration"]["nllAfter"],
                                 artifact["metrics"]["calibration"]["nllBefore"])
            self.assertGreaterEqual(min(artifact['scale']), .04999999)
            legacy = copy.deepcopy(full)
            legacy["version"] = 1
            torch.save(legacy, root / "legacy.pt")
            with self.assertRaisesRegex(DataError, "Start a fresh v2 run"):
                load_checkpoint(torch, root / "legacy.pt")
            invalid = copy.deepcopy(full)
            invalid["model"]["0.bias"][0] = math.nan
            torch.save(invalid, root / "invalid.pt")
            with self.assertRaises(DataError):
                load_checkpoint(torch, root / "invalid.pt")
            altered = make_dataset(seed=99)
            atomic_json(data, altered)
            self.assertEqual(main(["train", *common, "--epochs", "5", "--output", str(root / "resume"),
                                   "--resume", str(root / "resume" / "last.pt")]), 2)
            x = torch.tensor([[1.] * 64, [3.] * 64])
            mean, scale = normalization(torch, x)
            self.assertEqual(mean.tolist(), [2.] * 64)
            self.assertEqual(scale.tolist(), [1.] * 64)


if __name__ == "__main__":
    unittest.main()
