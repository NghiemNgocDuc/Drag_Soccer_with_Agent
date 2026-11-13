"""Objective/augmentation invariants, not claims of real-person accuracy."""
import importlib.util
from pathlib import Path
import sys
import unittest

TRAINING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TRAINING))
from optimization import augment_batch, balanced_window_weights, calibrate_temperature, mirror_windows, normalization


class BalanceTests(unittest.TestCase):
    def test_long_clips_and_prolific_people_cannot_dominate(self):
        clips, windows = [], []
        def add(name, person, session, label, count):
            clips.append(dict(id=name, participantId=person, sessionId=session))
            windows.extend(dict(clip=name, y=label) for _ in range(count))
        add("short", "person1", "a", 0, 1)
        add("long", "person1", "a", 0, 100)
        add("another-session", "person1", "b", 0, 3)
        add("another-person", "person2", "c", 0, 10)
        add("rare-class", "person1", "a", 1, 2)
        weights = balanced_window_weights(clips, windows)
        mass = lambda names: sum(w for row, w in zip(windows, weights) if row["clip"] in names)
        self.assertAlmostEqual(sum(weights), 1.)
        self.assertAlmostEqual(mass({"rare-class"}), .5)
        self.assertAlmostEqual(mass({"another-person"}), .25)
        self.assertAlmostEqual(mass({"short", "long", "another-session"}), .25)
        self.assertAlmostEqual(mass({"short"}), mass({"long"}))
        self.assertAlmostEqual(mass({"short", "long"}), mass({"another-session"}))


@unittest.skipUnless(importlib.util.find_spec("torch"), "PyTorch optional")
class OptimizationTests(unittest.TestCase):
    def setUp(self):
        import torch
        self.torch = torch
        torch.set_num_threads(1)

    def test_mirror_is_exact_involution_and_preserves_inputs(self):
        torch = self.torch
        features = torch.arange(5 * 64, dtype=torch.float32).reshape(5, 64) / 100.
        labels = torch.arange(5)
        original = features.clone()
        mirrored, targets = mirror_windows(torch, features, labels)
        self.assertEqual(targets.tolist(), [0, 2, 1, 3, 4])
        self.assertTrue(torch.equal(mirrored[:, 0::8], -features[:, 0::8]))
        self.assertTrue(torch.equal(mirrored[:, 1::8], -features[:, 1::8]))
        self.assertTrue(torch.equal(mirrored[:, 4::8], features[:, 5::8]))
        self.assertTrue(torch.equal(mirrored[:, 5::8], features[:, 4::8]))
        for index in (2, 3, 6, 7):
            self.assertTrue(torch.equal(mirrored[:, index::8], features[:, index::8]))
        twice, twice_labels = mirror_windows(torch, mirrored, targets)
        self.assertTrue(torch.equal(twice, original))
        self.assertTrue(torch.equal(twice_labels, labels))
        self.assertTrue(torch.equal(features, original))

    def test_augmentation_is_deterministic_bounded_and_train_only(self):
        torch = self.torch
        inputs = torch.full((20, 64), .1)
        labels = torch.arange(20) % 5
        torch.manual_seed(9)
        first, first_labels = augment_batch(torch, inputs, labels)
        torch.manual_seed(9)
        second, second_labels = augment_batch(torch, inputs, labels)
        self.assertTrue(torch.equal(first, second))
        self.assertTrue(torch.equal(first_labels, second_labels))
        self.assertTrue(torch.equal(inputs, torch.full((20, 64), .1)))
        self.assertTrue(torch.equal(labels, torch.arange(20) % 5))
        self.assertFalse(torch.equal(inputs, first))
        unchanged, targets = augment_batch(torch, inputs, labels, 0., 0.)
        self.assertTrue(torch.equal(inputs, unchanged))
        self.assertTrue(torch.equal(labels, targets))
        extremes, _ = augment_batch(torch, torch.full((20, 64), 6.), labels, .1)
        self.assertLessEqual(float(extremes.abs().max()), 6.)

    def test_noise_floor_weighted_stats_and_mirror_expected_distribution(self):
        torch = self.torch
        train = torch.stack([torch.ones(64), torch.ones(64) * 3])
        mean, scale = normalization(torch, train, [.9, .1])
        self.assertTrue(torch.allclose(mean, torch.full((64,), 1.2)))
        self.assertTrue(torch.allclose(scale, torch.full((64,), .6)))
        mean, scale = normalization(torch, torch.ones((3, 64)))
        self.assertTrue(torch.equal(mean, torch.ones(64)))
        self.assertTrue(torch.allclose(scale, torch.full((64,), .05)))
        mean, scale = normalization(torch, train, [.9, .1], mirror=True)
        self.assertEqual(float(mean[0]), 0.)
        self.assertEqual(float(mean[1]), 0.)
        self.assertAlmostEqual(float(mean[4]), float(mean[5]))
        # A new independent held-out tensor has no route into fitted statistics.
        held_out = torch.full((100, 64), -6.)
        before = normalization(torch, train)
        held_out.mul_(10)
        after = normalization(torch, train)
        self.assertTrue(all(torch.equal(a, b) for a, b in zip(before, after)))

    def test_validation_temperature_improves_nll_without_changing_argmax(self):
        torch = self.torch
        logits = torch.tensor([[8., 0., -2., -3., -4.]] * 10)
        targets = torch.tensor([0] * 7 + [1] * 3)
        temperature, report = calibrate_temperature(torch, logits, targets)
        self.assertGreater(temperature, 1.)
        self.assertLessEqual(temperature, 5.)
        self.assertLess(report["nllAfter"], report["nllBefore"])
        self.assertTrue(torch.equal(logits.argmax(1), (logits / temperature).argmax(1)))
        no_calibration, disabled = calibrate_temperature(torch, logits, targets, enabled=False)
        self.assertEqual(no_calibration, 1.)
        self.assertEqual(disabled["nllAfter"], disabled["nllBefore"])
        flat, flat_report = calibrate_temperature(torch, torch.zeros((10, 5)), targets)
        self.assertEqual(flat, 1.)
        self.assertEqual(flat_report["nllAfter"], flat_report["nllBefore"])


if __name__ == "__main__":
    unittest.main()
