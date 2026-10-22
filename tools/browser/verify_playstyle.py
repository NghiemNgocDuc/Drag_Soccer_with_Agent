"""Compatibility entry point for pre-match formation and mentality checks.

Playstyle is now chosen before kickoff. Both this command and
``python tools/browser/verify_match_setup.py`` exercise the same flow.
"""
from __future__ import annotations

import json

from verify_match_setup import run


if __name__ == '__main__':
    results = run()
    print(json.dumps({'passed': len(results['checks']), 'artifacts': results['artifacts']}, indent=2))
