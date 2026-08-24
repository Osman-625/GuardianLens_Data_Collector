"""Non-destructive structural verification for a source tree or fresh release."""
from __future__ import annotations

import json
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import yaml
from dotenv import dotenv_values

from guardianlens.config import AI_PROVIDERS, RUN_MODES, SCREENSHOT_RETENTION_POLICIES, settings
from guardianlens.db import connect, init_db
from guardianlens.release import REQUIRED_DIRECTORIES
from guardianlens.security import assert_safe_bind


def run_self_test() -> list[str]:
    errors: list[str] = []
    try:
        environment = yaml.safe_load((ROOT / "environment.yml").read_text(encoding="utf-8"))
        if environment.get("name") != "guardianlens-collector":
            errors.append("Conda environment name must be guardianlens-collector")
        dependencies = environment.get("dependencies", [])
        if not any(str(item).startswith("python=3.12") for item in dependencies):
            errors.append("Conda environment must pin Python 3.12")
    except Exception as exc:
        errors.append(f"environment.yml invalid: {type(exc).__name__}")

    required = ["extension", "config", "tests", "src/guardianlens", *REQUIRED_DIRECTORIES]
    for value in required:
        if not (ROOT / value).exists():
            errors.append(f"missing path: {value}")

    try:
        example = dotenv_values(ROOT / ".env.example")
        if (example.get("AI_PROVIDER") or "").lower() not in AI_PROVIDERS:
            errors.append(".env.example AI_PROVIDER is invalid")
        if (example.get("GUARDIANLENS_MODE") or "").lower() not in RUN_MODES:
            errors.append(".env.example GUARDIANLENS_MODE is invalid")
        if (example.get("SCREENSHOT_RETENTION") or "").lower() not in SCREENSHOT_RETENTION_POLICIES:
            errors.append(".env.example SCREENSHOT_RETENTION is invalid")
        attempts = int(example.get("AI_MAX_ATTEMPTS") or "0")
        retry_base = float(example.get("AI_RETRY_BASE_SECONDS") or "-1")
        retry_max = float(example.get("AI_RETRY_MAX_SECONDS") or "-1")
        if not 1 <= attempts <= 5 or not 0 <= retry_base <= retry_max <= 60:
            errors.append(".env.example retry policy is invalid")
    except Exception as exc:
        errors.append(f".env.example invalid: {type(exc).__name__}")

    try:
        categories = yaml.safe_load((ROOT / "config/categories.yaml").read_text(encoding="utf-8"))["categories"]
        if round(sum(float(item["target_pct"]) for item in categories), 5) != 100:
            errors.append("category target percentages do not sum to 100")
    except Exception as exc:
        errors.append(f"category config invalid: {type(exc).__name__}")
    try:
        targets = yaml.safe_load((ROOT / "config/platform_targets.yaml").read_text(encoding="utf-8"))
        if (targets["total_target"], targets["platforms"]["carousell"]["target"],
                targets["platforms"]["mudah"]["target"]) != (2500, 1500, 1000):
            errors.append("platform targets do not match 2500 / 1500 / 1000 design")
    except Exception as exc:
        errors.append(f"platform config invalid: {type(exc).__name__}")
    try:
        manifest = json.loads((ROOT / "extension/manifest.json").read_text(encoding="utf-8"))
        if manifest["manifest_version"] != 3:
            errors.append("extension is not Manifest V3")
        if "<all_urls>" in manifest.get("host_permissions", []):
            errors.append("extension requests <all_urls>")
    except Exception as exc:
        errors.append(f"extension manifest invalid: {type(exc).__name__}")

    for script in ("extension/background.js", "extension/popup.js", "src/guardianlens/static/app.js"):
        try:
            result = subprocess.run(
                ["node", "--check", str(ROOT / script)], capture_output=True, text=True, timeout=30
            )
            if result.returncode:
                errors.append(f"javascript syntax failed: {script}")
        except FileNotFoundError:
            break
        except subprocess.TimeoutExpired:
            errors.append(f"javascript syntax check timed out: {script}")

    try:
        assert_safe_bind(settings.host)
    except Exception as exc:
        errors.append(str(exc))
    try:
        with tempfile.TemporaryDirectory(prefix="guardianlens-self-test-") as temporary:
            database = Path(temporary) / "collector.sqlite3"
            init_db(database)
            connection = connect(database)
            try:
                connection.execute("SELECT 1 FROM listings LIMIT 1")
            finally:
                connection.close()
    except Exception as exc:
        errors.append(f"temporary database/schema check failed: {type(exc).__name__}")
    if len(secrets.token_urlsafe(32)) < 30:
        errors.append("local token generator produced insufficient entropy")
    return errors


def main() -> int:
    errors = run_self_test()
    if errors:
        print("SELF TEST: FAIL")
        for error in errors:
            print(" -", error)
        return 1
    print("SELF TEST: PASS (non-destructive temporary database check)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
