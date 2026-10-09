"""Run repository pytest suites without external integrations.

Usage: python tools/run_tests.py [pytest arguments]
The standalone HTTP integration script runs separately from the pytest suites.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.browser.verify_game_performance import isolated_environment


def main() -> int:
    isolated_environment()
    import pytest

    return int(pytest.main([
        "-q", f"--ignore={ROOT / 'tests/backend/test_integration.py'}",
        *sys.argv[1:],
    ]))


if __name__ == "__main__":
    raise SystemExit(main())
