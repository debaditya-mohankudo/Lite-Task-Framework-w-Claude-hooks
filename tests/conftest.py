"""Shared pytest configuration — ensures all test files have the project root
and hooks/ directory on sys.path, matching the runtime environment."""
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
for _p in [str(_PROJECT_ROOT), str(_PROJECT_ROOT / "hooks")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)
