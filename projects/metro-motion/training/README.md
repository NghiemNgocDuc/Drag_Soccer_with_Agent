# Train your Metro Motion controls

Motion Lab trains a small personal movement classifier from **your reviewed
landmark recordings**. No human-trained model ships with the game. The existing
MediaPipe model still locates body joints; this new model learns how sequences
of those joints correspond to neutral, left, right, jump and duck.

## 1. Collect and review

Accuracy-focused training now balances classes, participants, sessions and clips;
long recordings cannot dominate simply by producing more overlapping windows.
Training adds small feature jitter and exact left/right reflections. A .05
normalization floor limits amplification of tiny landmark noise. The validation
set selects the checkpoint and calibrates confidence with a bounded temperature;
the test set remains excluded from these choices. Temperature changes confidence,
not the highest-scoring class. [Confidence calibration research](https://proceedings.mlr.press/v70/guo17a.html)

The defaults enable these improvements. For controlled comparisons, use
`--feature-noise 0`, `--no-mirror`, or `--no-calibrate`; keep the same dataset and
split. New checkpoints use version 2 and capture these settings. Restart training
from your recordings when upgrading a version-1 checkpoint. Existing browser
models remain compatible and default to temperature 1.

Evaluation now also replays the complete browser control state machines, including
context, filtering, confidence fallback, jump rearming and lane/duck dwell.
`emittedControls` reports actual action pulses, wrong/repeated actions, sustained
control coverage and timing. Eligibility requires this evidence in addition to
the classifier checks below. This still requires real held-out recordings to
measure personal accuracy; synthetic fixtures only validate the pipeline.

Open `/metro-motion/motion-lab.html`, or choose **Open Motion Lab** in the runner.
Enable the camera and stand upright for calibration. Choose a movement, press
**Record**, wait for the countdown, and make that movement during the three-second
capture. Review the skeleton, trim the labeled interval, confirm it, and save.
For a jump, select takeoff/airborne motion and exclude standing and landing.
Record neutral examples separately. Version 1 uses one action at a time.

Only coordinates and random participant/session identifiers are stored, locally
in IndexedDB after Save. Video and audio are never saved. If storage fails, the
clips remain in the tab and can still be downloaded. Changing tabs stops the
camera and cancels an unfinished capture. Keep exports private unless you choose
to share them: body coordinates still describe a person's movement.

Start with at least **three separately calibrated sessions containing all five
movements** to exercise training. Collect several examples of every movement in
each session, with natural variation. Use **Change participant** when someone
else takes over; use **New calibration** after moving the camera. Several clips
from one calibration are still one session, not independent sessions.

The first small dataset will normally produce a **comparison-only model**. The
control gate needs substantially more evidence (below). Download batches before
the 200-clip local limit. The trainer accepts multiple exports. When combining
batches, exclude overlapping exports: duplicate clips or recordings are rejected.
After verifying a batch download, you can delete its local clips to make room.
Do not delete the sole copy of recordings you still need.

## 2. Set up Python on Unity

These commands target the **Unity research computing cluster**, not the Unity
game engine. Use your existing account and copy this repository plus your chosen
dataset exports to your own cluster workspace. No account credentials belong in
this repository. From the repository root, create a separate environment:

```bash
python3 -m venv .venv-motion
source .venv-motion/bin/activate
python -m pip install --upgrade pip
python -m pip install 'torch>=2.6,<3' --index-url https://download.pytorch.org/whl/cu126
python -m pip freeze > motion-environment.txt
```

Use a supported Python 3.10+ module if `python3` is unavailable. Unity currently
recommends a virtual environment and CUDA 12.6 wheels; consult its
[PyTorch guide](https://docs.unity.rc.umass.edu/documentation/tools/pytorch/)
if your allocated GPU or environment differs. This model uses ordinary PyTorch
operations and does not need torchvision or multiple GPUs.

Keep exports outside the served `web/` directory. Example directory:

```text
projects/metro-motion/training/data/
  session-batch-01.json
  session-batch-02.json
```

Validate before allocating a GPU. This command uses the Python standard library:

```bash
python projects/metro-motion/training/train.py validate 'projects/metro-motion/training/data/*.json'
```

The report shows train/validation/test groups and class counts. It refuses
missing classes, duplicate/conflicting recordings, changed calibration within a
session, incompatible data, and splits without all five valid classes. It never
fills missing human examples with synthetic data.

## 3. Submit a training job

Submit **from the repository root**:

```bash
sbatch projects/metro-motion/training/unity-train.sbatch \
  "$PWD/projects/metro-motion/training/data" \
  "$PWD/projects/metro-motion/training/runs/personal-v1"
```

The script requests one ordinary GPU, two CPU cores, 4 GB RAM and twenty minutes
on `gpu-preempt`. Override an allocation with `sbatch --account=YOUR_ACCOUNT ...`
only if your Unity group requires it. `MOTION_PYTHON` can point to another
existing environment's Python. Use `squeue --me` to inspect the queue and read
`motion-JOBID.log` in the submission directory. Do not run training on the login
node; Unity schedules GPU work through Slurm.
[Unity GPU jobs](https://docs.unity.rc.umass.edu/documentation/tools/gpus/)

The network is small enough that CPU training can be faster for small datasets;
one GPU is sufficient for this experiment. It does not need a high-VRAM queue.
Unity separated GPUs with at least 40 GB into `gpu-highvram` and
`gpu-preempt-highvram` in October 2026, and reduced maximum job durations to five
days. The provided short job avoids depending on the retired limits.
[Unity partition changes](https://docs.unity.rc.umass.edu/news/2026/10/partition-announcement/)

Each completed epoch atomically writes `last.pt`, including optimizer and random
state. A pre-timeout signal requests a clean stop after an epoch. Preemption or
abrupt cancellation can lose the current epoch; resume the most recent complete
checkpoint manually with the **same data and hyperparameters**:

```bash
sbatch projects/metro-motion/training/unity-train.sbatch \
  "$PWD/projects/metro-motion/training/data" \
  "$PWD/projects/metro-motion/training/runs/personal-v1" \
  --resume "$PWD/projects/metro-motion/training/runs/personal-v1/last.pt"
```

Use a new output directory for a different dataset or experiment. Successful
completion writes `model.json`, `metrics.json`, `split.json`, and `last.pt`.
Only `model.json` goes into the browser. Checkpoints are loaded with PyTorch's
restricted `weights_only=True` and validated shapes; load only your own training
artifacts. [PyTorch checkpoint documentation](https://docs.pytorch.org/tutorials/beginner/saving_loading_models.html)

For local CPU training, install CPU PyTorch into a separate environment using
`--index-url https://download.pytorch.org/whl/cpu`, then run:

```bash
python projects/metro-motion/training/train.py train 'path/to/data/*.json' \
  --device cpu --output projects/metro-motion/training/runs/local-v1
```

## 4. Read the evaluation, then compare in the browser

Copy `model.json` back to your computer. In Metro Motion, expand **Try a trained
model**, import it, enable the camera, and calibrate. It always starts in
**Compare predictions**. Watch whether the predicted action matches your actual
movement while the standard controls continue running the game. A model remains
in this tab only; it is not uploaded or remembered after reload.

Training uses 64 inputs (eight body features at eight points over 0.47 seconds),
a 32-unit ReLU hidden layer and five outputs. The exact learned weights export
as a bounded JSON file; a tiny browser evaluator avoids an extra ONNX runtime for
this small network. Python/JavaScript feature and prediction parity are tested.
The [format contract](../docs/motion-format.md) lists every feature and limit.

Whole sessions go into separate train, validation and test sets. Only training
data determines normalization; only validation loss selects the checkpoint. Test
data is evaluated after training. Use `--group-by participant` with at least three
participants to assess unseen people; default session splits assess new sessions
of familiar participants. Do not repeatedly tune on the test report and still
call it an untouched holdout—collect a new final evaluation set for that purpose.

`metrics.json` includes confusion matrices, per-class precision/recall/F1, class
and group counts, raw model predictions, confidence-fallback results, the
existing rule baseline, neutral false-action rates, and onset latency estimates.
Overlapping windows are correlated. Jump is a pulse in the rules but an airborne
label in the classifier, so inspect action detection as well as frame metrics.
Classifier estimates exclude camera/rendering delay and controller dwell/rearm.
The separate emitted-control replay includes dwell/rearm, but excludes actual
camera/rendering delay and does not establish continuous human gameplay accuracy.

Experimental control requires **all** of these checks in both held-out splits:

- At least two independent groups, ten clips and 100 valid windows per class,
  and 60 seconds of valid neutral recording.
- Macro F1 at least .85 and no lower than the rule baseline; no class loses more
  than .03 F1; confidence coverage at least 75%.
- Neutral false actions at most two per minute and at most .1/min above rules.
- Action detection recall at least .90 and no worse than rules; median estimated
  detection latency no more than 100 ms slower than rules.
- No synthetic data anywhere in the dataset.

These are project acceptance thresholds, not proof of general performance. With
three-second clips, the neutral-time requirement alone needs many recordings.
Failed gates still export a comparison model with specific `gateReasons`. Do not
edit the eligibility flag to bypass evaluation; imported metadata is a report,
not independently verified evidence. Only after comparing a qualifying model in
your own space should you select **Use trained controls · experimental**. Changing
models or mode pauses the run. Uncertain predictions fall back to standard
controls; lost tracking still pauses. **Remove model** restores standard controls.

## Reproduce the pipeline checks

```bash
node --test projects/metro-motion/tests/*.test.mjs
python -m unittest discover -s projects/metro-motion/training/tests -v
python tools/browser/verify_motion_lab.py --channel chrome
```

The PyTorch test trains on CPU, resumes a checkpoint, compares with uninterrupted
training, evaluates the export, and checks its probabilities with the shipped
JavaScript evaluator. It requires Node and PyTorch; otherwise the optional torch
test is explicitly skipped. A generated fixture can exercise the CLI manually:

```bash
python projects/metro-motion/training/train.py synthetic --output /tmp/motion-fixture.json
python projects/metro-motion/training/train.py train /tmp/motion-fixture.json \
  --device cpu --epochs 4 --allow-synthetic --output /tmp/motion-smoke
```

Synthetic fixtures test plumbing and can never qualify for trained controls.
No Unity job or GPU training was executed from this development session. Real
improvement remains to be measured with your recordings and held-out sessions.
