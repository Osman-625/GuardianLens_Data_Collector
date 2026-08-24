from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from guardianlens.release import generate_manifest, verify_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or verify GuardianLens MANIFEST.sha256")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--generate", action="store_true", help="replace the manifest atomically")
    action.add_argument("--verify", action="store_true", help="verify only (default)")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.generate:
        generate_manifest(root)
    report = verify_manifest(root)
    print(json.dumps(report, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
