from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from guardianlens.config import settings
from guardianlens.release import (
    REQUIRED_DIRECTORIES,
    ReleaseValidationError,
    build_release_zip,
    generate_manifest,
    iter_release_files,
    run_fresh_release_checks,
    scan_release_tree_for_secrets,
    verify_manifest,
    verify_release_zip,
)


@pytest.fixture()
def release_tree(tmp_path):
    source = settings.root
    root = tmp_path / "release-source"
    for path in iter_release_files(source):
        relative = path.relative_to(source)
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    generate_manifest(root)
    return root


def test_manifest_detects_changed_and_unlisted_allowlisted_files(release_tree):
    assert verify_manifest(release_tree)["pass"]
    (release_tree / "README.md").write_text("changed", encoding="utf-8")
    (release_tree / "scripts/new_source.py").write_text("VALUE = 1\n", encoding="utf-8")
    report = verify_manifest(release_tree)
    assert not report["pass"]
    assert "README.md" in report["mismatched"]
    assert "scripts/new_source.py" in report["missing"]


def test_release_uses_allowlist_excludes_runtime_and_preserves_empty_structure(release_tree):
    assert scan_release_tree_for_secrets(release_tree) == []
    runtime_files = {
        ".env": "OPENAI_API_KEY=not-for-release",
        "data/collector.sqlite3": "database",
        "data/collector.sqlite3-wal": "sidecar",
        "data/images/carousell/private.jpg": "captured image",
        "data/private/local_token.txt": "local token",
        "logs/server.out": "runtime log",
        ".codex/session.json": "tool state",
    }
    for name, content in runtime_files.items():
        path = release_tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    generate_manifest(release_tree)
    archive = build_release_zip(release_tree / "dist/release.zip", root=release_tree)
    report = verify_release_zip(archive, root=release_tree)
    assert report["pass"], report
    with zipfile.ZipFile(archive) as package:
        names = set(package.namelist())
    assert not (set(runtime_files) & names)
    assert set(REQUIRED_DIRECTORIES) <= names


def test_secret_scan_blocks_allowlisted_source_credentials(release_tree):
    credential = "sk-" + "proj-" + ("A" * 40)
    (release_tree / "README.md").write_text(f"credential={credential}\n", encoding="utf-8")
    generate_manifest(release_tree)
    hits = scan_release_tree_for_secrets(release_tree)
    assert {item["kind"] for item in hits} == {"openai_key"}
    with pytest.raises(ReleaseValidationError, match="secret-like"):
        build_release_zip(release_tree / "dist/release.zip", root=release_tree)


def test_independent_zip_verifier_rejects_runtime_injection(release_tree):
    archive = build_release_zip(release_tree / "dist/release.zip", root=release_tree)
    with zipfile.ZipFile(archive, "a") as package:
        package.writestr("data/collector.sqlite3", b"injected")
    report = verify_release_zip(archive, root=release_tree)
    assert not report["pass"]
    assert "data/collector.sqlite3" in report["offenders"]


def test_fresh_release_runs_structural_and_synthetic_checks_in_temp(release_tree):
    archive = build_release_zip(release_tree / "dist/release.zip", root=release_tree)
    report = run_fresh_release_checks(
        archive, source_root=release_tree, include_tests=False
    )
    assert report["pass"], report
    assert [item["returncode"] for item in report["checks"]] == [0, 0]


def test_source_tree_has_no_automatic_production_reset_utility():
    """Production deletion must never be a one-command, unaudited package feature."""
    assert not (settings.root / "scripts/reset_production.py").exists()


def test_release_requires_the_documented_windows_entry_points(release_tree):
    archive = build_release_zip(release_tree / "dist/release.zip", root=release_tree)
    with zipfile.ZipFile(archive) as package:
        names = set(package.namelist())
    assert {"setup.bat", "run.bat"} <= names
    assert not {"setup_conda.bat", "setup_conda.ps1", "run.ps1"} & names
