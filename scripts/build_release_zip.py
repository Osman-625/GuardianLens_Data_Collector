from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from guardianlens.release import build_release_zip, verify_manifest, verify_release_zip


def main() -> int:
    manifest = verify_manifest(ROOT)
    if not manifest["pass"]:
        print(json.dumps({"pass": False, "stage": "manifest", "manifest": manifest}, indent=2))
        return 1
    zip_path = build_release_zip(root=ROOT)
    report = verify_release_zip(zip_path, root=ROOT)
    print(json.dumps(report, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
