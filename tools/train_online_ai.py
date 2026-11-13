"""Inspect or explicitly train the bounded online experience policy.

Run with the same Redis/configuration environment as the game server. Default
mode only prints aggregate status. Automatic training needs no scheduled job.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--train", action="store_true", help="Train and validate the current completed-match corpus")
    action.add_argument("--reset", action="store_true", help="Reset the deployed policy, retaining match experience")
    arguments = parser.parse_args()
    from services import online_learning
    if arguments.train:
        online_learning.train_now()
    if arguments.reset:
        online_learning.reset_policy()
    print(json.dumps(online_learning.status(), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
