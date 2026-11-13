import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

TRAINING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TRAINING))
from controller_evaluation import controller_evaluation, emitted_metrics, LearnedControlReplay
from evaluation import RuleBaseline
from synthetic import BASELINE, landmarks


class ControllerEvaluationTests(unittest.TestCase):
    def test_python_replay_matches_browser_control_actions(self):
        frames = []
        for index in range(65):
            label = 'neutral' if index < 12 or index >= 50 else 'jump' if index < 22 else 'left' if index < 32 else 'duck' if index < 42 else 'right'
            points = landmarks(label)
            if index == 45:
                points = []
            frames.append({'t': index * 70, 'landmarks': points})
        runtime = (TRAINING.parent / 'web/js/learned-controls.mjs').as_uri()
        rules_runtime = (TRAINING.parent / 'web/js/pose-controls.mjs').as_uri()
        code = ('import {readFileSync} from "node:fs";'
                f'import {{LearnedController}} from {json.dumps(runtime)};'
                f'import {{PoseController}} from {json.dumps(rules_runtime)};'
                'const {model,frames,baseline}=JSON.parse(readFileSync(0,"utf8"));'
                'const learned=new LearnedController(); learned.load(model);learned.setMode("control");'
                'const rules=new PoseController();rules.baseline=baseline;'
                'process.stdout.write(JSON.stringify(frames.map(f=>{const p=learned.update(f.landmarks,f.t,baseline,rules.update(f.landmarks,f.t));'
                'return {lane:p.lane,jump:p.jump,duck:p.duck,tracked:p.tracked,source:p.source,reason:p.reason};})));')
        for target in range(5):
            model = dict(format='metro-motion-motion-model', version=1, featureVersion='mm-body-v1',
                         labels=['neutral', 'left', 'right', 'jump', 'duck'], windowSize=8, sampleHz=15,
                         inputSize=64, hiddenSize=1, mean=[0]*64, scale=[1]*64,
                         weights1=[[0]*64], bias1=[0], weights2=[[0] for _ in range(5)],
                         bias2=[100 if i == target else 0 for i in range(5)], threshold=.8,
                         metrics={'controlEligible': True})
            actual = subprocess.run(['node', '--input-type=module', '-e', code],
                                    input=json.dumps(dict(model=model, frames=frames, baseline=BASELINE)),
                                    text=True, capture_output=True, check=True)
            replay, rules = LearnedControlReplay(), RuleBaseline(BASELINE)
            expected = []
            for frame in frames:
                prediction = dict(label=model['labels'][target], confidence=1.)
                pose = replay.update(frame['landmarks'], frame['t'], BASELINE,
                                     rules.update_pose(frame['landmarks'], frame['t']), prediction)
                expected.append({key: pose[key] for key in ('lane', 'jump', 'duck', 'tracked', 'source', 'reason')})
            self.assertEqual(json.loads(actual.stdout), expected, model['labels'][target])

    def test_jump_is_one_event_and_duplicate_pulses_are_errors(self):
        clip = dict(label='jump', range=[500, 1000], frames=[{'t': t} for t in range(0, 1100, 100)])
        trace = [dict(tracked=True, calibrated=True, lane=0, duck=False, jump=t == 600) for t in range(0, 1100, 100)]
        result = emitted_metrics([clip], [trace])['actionDetection']['jump']
        self.assertEqual(result['recall'], 1)
        self.assertEqual(result['precision'], 1)
        self.assertEqual(result['medianLatencyMs'], 100)
        self.assertIsNone(result['heldTargetCoverage'])
        trace[9]['jump'] = True
        result = emitted_metrics([clip], [trace])['actionDetection']['jump']
        self.assertEqual(result['repeatedEvents'], 1)
        self.assertEqual(result['precision'], .5)

    def test_full_context_is_inferred_but_never_assumed_neutral(self):
        frames = [dict(t=t, landmarks=landmarks()) for t in range(0, 2100, 70)]
        clip = dict(label='left', range=[1000, 1800], baseline=BASELINE, frames=frames)
        seen = []
        def probabilities(values):
            seen.extend(values)
            return [[0, 1, 0, 0, 0] for _ in values]
        report = controller_evaluation([clip], probabilities, .8)
        self.assertGreater(len(seen), sum(1000 <= f['t'] <= 1800 for f in frames))
        self.assertEqual(report['learned']['neutralSeconds'], 0)
        self.assertEqual(report['learned']['actionDetection']['left']['recall'], 1)


if __name__ == '__main__':
    unittest.main()
