from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from guardianlens.release import run_fresh_release_checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify GuardianLens from a fresh temporary extraction")
    parser.add_argument("zip_path", nargs="?", type=Path, default=ROOT / "dist/guardianlens_release.zip")
    parser.add_argument("--skip-tests", action="store_true", help="run structural + synthetic checks only")
    args = parser.parse_args()
    report = run_fresh_release_checks(
        args.zip_path.resolve(), source_root=ROOT, include_tests=not args.skip_tests
    )
    print(json.dumps(report, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
