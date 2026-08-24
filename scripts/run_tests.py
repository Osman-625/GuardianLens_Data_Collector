from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
TEMP_ROOT = ROOT / ".tmp"
TEMP_ROOT.mkdir(parents=True, exist_ok=True)
base_temp = TEMP_ROOT / f"pytest-run-{uuid.uuid4().hex}"
result = 1
try:
    result = subprocess.call(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--basetemp={base_temp}",
        ],
        cwd=ROOT,
    )
finally:
    shutil.rmtree(base_temp, ignore_errors=True)
raise SystemExit(result)
