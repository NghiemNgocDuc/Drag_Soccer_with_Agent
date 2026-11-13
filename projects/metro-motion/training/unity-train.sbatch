#!/usr/bin/env bash
# Submit from the repository root on Unity. See README.md for environment setup.
#SBATCH --job-name=metro-motion
#SBATCH --partition=gpu-preempt
#SBATCH --gpus=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=4G
#SBATCH --time=00:20:00
#SBATCH --signal=B:USR1@60
#SBATCH --output=motion-%j.log

set -euo pipefail
if [[ -z "${SLURM_JOB_ID:-}" ]]; then
  echo "Submit this script with sbatch; do not train on the login node." >&2
  exit 2
fi
if [[ $# -lt 2 ]]; then
  echo "Usage: sbatch projects/metro-motion/training/unity-train.sbatch DATA_DIR OUTPUT_DIR [training options]" >&2
  exit 2
fi
data_dir="$1"
output_dir="$2"
shift 2
repo_dir="${SLURM_SUBMIT_DIR:?Submit from the repository root}"
python_bin="${MOTION_PYTHON:-$repo_dir/.venv-motion/bin/python}"
trainer="$repo_dir/projects/metro-motion/training/train.py"
if [[ ! -x "$python_bin" || ! -f "$trainer" || ! -d "$data_dir" ]]; then
  echo "Check the Python environment, repository submission directory, and dataset directory." >&2
  exit 2
fi
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-2}"
export PYTHONUNBUFFERED=1
# exec makes the batch process the trainer, so B:USR1 reaches its checkpoint handler.
exec "$python_bin" -u "$trainer" train "$data_dir/*.json" \
  --output "$output_dir" --device cuda --threads "$OMP_NUM_THREADS" "$@"
