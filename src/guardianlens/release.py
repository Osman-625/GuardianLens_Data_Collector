from __future__ import annotations

import hashlib
import io
import os
import re
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from .config import settings


class ReleaseValidationError(RuntimeError):
    """The proposed release is incomplete, stale, unsafe, or non-reproducible."""


ROOT_FILES = frozenset(
    {
        ".env.example", ".gitignore", "environment.yml", "main.py",
        "MANIFEST.sha256", "package.json", "package-lock.json", "pyproject.toml",
        "README.md", "requirements.txt", "run.bat", "setup.bat", "VERSION",
    }
)
TREE_SUFFIXES = {
    "config": frozenset({".yaml", ".yml"}),
    "docs": frozenset({".md"}),
    "extension": frozenset({".html", ".js", ".json", ".png", ".svg"}),
    "scripts": frozenset({".py"}),
    "src": frozenset({".css", ".html", ".js", ".py", ".sql"}),
    "tests": frozenset({".json", ".py", ".yaml", ".yml"}),
}
REQUIRED_DIRECTORIES = (
    "data/backups/", "data/exports/", "data/images/carousell/", "data/images/mudah/",
    "data/private/screenshots/", "data/private/staging/", "data/reports/", "data/temporary/",
    "data/soft_test/runtime/exports/", "data/soft_test/runtime/images/carousell/",
    "data/soft_test/runtime/images/mudah/", "data/soft_test/runtime/private/screenshots/",
    "data/soft_test/runtime/private/staging/", "data/soft_test/runtime/reports/",
    "data/soft_test/runtime/temporary/", "logs/", "tests/fixtures/",
)
RUNTIME_MARKERS = frozenset(
    PurePosixPath(directory).joinpath(".gitkeep").as_posix()
    for directory in REQUIRED_DIRECTORIES
)
REQUIRED_PACKAGE_FILES = frozenset(
    {
        ".env.example", "README.md", "VERSION", "config/categories.yaml",
        "config/platform_targets.yaml", "environment.yml", "extension/background.js",
        "extension/manifest.json", "extension/popup.html", "extension/popup.js", "main.py",
        "requirements.txt", "scripts/mock_integration_check.py", "scripts/self_test.py",
        "scripts/build_release_zip.py", "scripts/manifest.py", "scripts/verify_fresh_release.py",
        "setup.bat", "run.bat", "src/guardianlens/__init__.py", "src/guardianlens/schema.sql",
    }
)

_DATABASE_SUFFIXES = (".db", ".sqlite", ".sqlite3", "-journal", "-shm", "-wal")
_SECRET_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("github_token", re.compile(r"\bgh[oprsu]_[A-Za-z0-9]{30,}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
)
_KEY_ASSIGNMENT = re.compile(
    r"(?m)^[ \t]*(?:OPENAI|ANTHROPIC|OPENROUTER|GEMINI|GOOGLE|AWS|GITHUB)?_?"
    r"(?:API_)?(?:SECRET_)?KEY[ \t]*=[ \t]*([^\s#]+)"
)
_PLACEHOLDER_HINTS = ("example", "placeholder", "replace", "your_", "dummy", "changeme", "...")


def _normalized_relative(value: Path | str) -> PurePosixPath | None:
    text = value.as_posix() if isinstance(value, Path) else str(value).replace("\\", "/")
    if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return None
    path = PurePosixPath(text)
    if any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path


def is_release_source(value: Path | str) -> bool:
    """Return whether a relative file is in the explicit source allowlist."""
    rel = _normalized_relative(value)
    if rel is None:
        return False
    name = rel.as_posix()
    if len(rel.parts) == 1:
        return name in ROOT_FILES
    if name in RUNTIME_MARKERS:
        return True
    allowed_suffixes = TREE_SUFFIXES.get(rel.parts[0])
    if not allowed_suffixes or rel.suffix.lower() not in allowed_suffixes:
        return False
    if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
        return False
    return True


def iter_release_files(root: Path | None = None) -> list[Path]:
    root = Path(root or settings.root).resolve()
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if not is_release_source(rel):
            continue
        if path.is_symlink():
            raise ReleaseValidationError(f"release source must not be a symlink: {rel.as_posix()}")
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix().casefold())


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest_expected(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _sha256_bytes(path.read_bytes())
        for path in iter_release_files(root)
        if path.relative_to(root).as_posix() != "MANIFEST.sha256"
    }


def _parse_manifest_text(text: str) -> tuple[dict[str, str], list[str]]:
    entries: dict[str, str] = {}
    errors: list[str] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        match = re.fullmatch(r"([0-9a-f]{64})  \./(.+)", raw)
        if not match:
            errors.append(f"line {line_number} has invalid syntax")
            continue
        name = match.group(2)
        rel = _normalized_relative(name)
        if rel is None or rel.as_posix() != name or not is_release_source(name):
            errors.append(f"line {line_number} has an unsafe or non-release path")
            continue
        if name == "MANIFEST.sha256":
            errors.append(f"line {line_number} must not hash the manifest itself")
            continue
        if name in entries:
            errors.append(f"line {line_number} duplicates {name}")
            continue
        entries[name] = match.group(1)
    return entries, errors


def generate_manifest(root: Path | None = None, output_path: Path | None = None) -> Path:
    """Generate an atomic manifest for allowlisted files (excluding the manifest itself)."""
    root = Path(root or settings.root).resolve()
    output = Path(output_path or root / "MANIFEST.sha256")
    expected = _manifest_expected(root)
    text = "".join(f"{digest}  ./{name}\n" for name, digest in sorted(expected.items()))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def verify_manifest(root: Path | None = None, manifest_path: Path | None = None) -> dict:
    root = Path(root or settings.root).resolve()
    manifest = Path(manifest_path or root / "MANIFEST.sha256")
    if not manifest.is_file():
        return {"pass": False, "path": str(manifest), "missing": ["MANIFEST.sha256"],
                "unexpected": [], "mismatched": [], "errors": ["manifest is missing"]}
    declared, errors = _parse_manifest_text(manifest.read_text(encoding="utf-8"))
    expected = _manifest_expected(root)
    missing = sorted(set(expected) - set(declared))
    unexpected = sorted(set(declared) - set(expected))
    mismatched = sorted(name for name in set(expected) & set(declared) if expected[name] != declared[name])
    return {"pass": not (missing or unexpected or mismatched or errors), "path": str(manifest),
            "file_count": len(declared), "missing": missing, "unexpected": unexpected,
            "mismatched": mismatched, "errors": errors}


def _secret_labels(text: str) -> list[str]:
    labels = [label for label, pattern in _SECRET_PATTERNS if pattern.search(text)]
    for match in _KEY_ASSIGNMENT.finditer(text):
        value = match.group(1).strip().lower()
        if value and not any(hint in value for hint in _PLACEHOLDER_HINTS):
            labels.append("configured_secret_assignment")
            break
    return sorted(set(labels))


def scan_release_tree_for_secrets(root: Path | None = None) -> list[dict[str, str]]:
    root = Path(root or settings.root).resolve()
    hits: list[dict[str, str]] = []
    for path in iter_release_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label in _secret_labels(text):
            hits.append({"path": path.relative_to(root).as_posix(), "kind": label})
    return hits


def _forbidden_archive_name(name: str) -> bool:
    rel = _normalized_relative(name)
    if rel is None:
        return True
    lowered = rel.as_posix().lower()
    basename = rel.name.lower()
    if basename == ".env" or (basename.startswith(".env.") and basename != ".env.example"):
        return True
    if basename in {"local_token.txt", "server.out"} or basename.endswith(_DATABASE_SUFFIXES):
        return True
    if basename.endswith((".log", ".out", ".pyc")):
        return True
    if any(part.lower() in {".git", ".agents", ".codex", ".cursor", "node_modules", "__pycache__"} for part in rel.parts):
        return True
    if lowered.startswith("data/") and lowered not in RUNTIME_MARKERS:
        return True
    return False


def _missing_required_files(root: Path) -> list[str]:
    return sorted(name for name in REQUIRED_PACKAGE_FILES if not (root / name).is_file())


def build_release_zip(output_path: Path | None = None, *, root: Path | None = None,
                      require_manifest: bool = True) -> Path:
    root = Path(root or settings.root).resolve()
    output = Path(output_path or root / "dist" / "guardianlens_release.zip")
    missing = _missing_required_files(root)
    if missing:
        raise ReleaseValidationError("required release files are missing: " + ", ".join(missing))
    if require_manifest and not verify_manifest(root)["pass"]:
        raise ReleaseValidationError("MANIFEST.sha256 is missing or stale")
    if scan_release_tree_for_secrets(root):
        raise ReleaseValidationError("allowlisted source files contain secret-like material")
    files = iter_release_files(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + f".{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for directory in REQUIRED_DIRECTORIES:
                info = zipfile.ZipInfo(directory, date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o40755 << 16
                archive.writestr(info, b"")
            for path in files:
                name = path.relative_to(root).as_posix()
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def verify_release_zip(zip_path: Path, *, root: Path | None = None) -> dict:
    """Independently validate names, contents, manifest, and secrets inside one ZIP."""
    root = Path(root or settings.root).resolve()
    expected_files = {path.relative_to(root).as_posix() for path in iter_release_files(root)}
    errors: list[str] = []
    offenders: list[str] = []
    secret_hits: list[dict[str, str]] = []
    archive_files: dict[str, bytes] = {}
    directory_names: set[str] = set()
    seen_casefold: set[str] = set()
    try:
        with zipfile.ZipFile(zip_path) as archive:
            if len(archive.infolist()) > 5000:
                errors.append("archive has too many entries")
            total_uncompressed = sum(info.file_size for info in archive.infolist())
            if total_uncompressed > 100 * 1024 * 1024:
                errors.append("archive uncompressed size exceeds the release limit")
            for info in archive.infolist():
                name = info.filename
                folded = name.casefold()
                if folded in seen_casefold:
                    errors.append(f"duplicate archive name: {name}")
                    continue
                seen_casefold.add(folded)
                if "\\" in name or _normalized_relative(name.rstrip("/")) is None:
                    offenders.append(name)
                    continue
                unix_type = (info.external_attr >> 16) & 0o170000
                if unix_type == 0o120000:
                    offenders.append(name)
                    continue
                if info.is_dir():
                    if name not in REQUIRED_DIRECTORIES:
                        offenders.append(name)
                    else:
                        directory_names.add(name)
                    continue
                if info.file_size > 20 * 1024 * 1024:
                    offenders.append(name)
                    continue
                if _forbidden_archive_name(name) or not is_release_source(name):
                    offenders.append(name)
                    continue
                data = archive.read(info)
                archive_files[name] = data
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                for label in _secret_labels(text):
                    secret_hits.append({"path": name, "kind": label})
    except (OSError, zipfile.BadZipFile) as exc:
        errors.append(f"archive is unreadable: {type(exc).__name__}")
    actual_files = set(archive_files)
    missing = sorted(expected_files - actual_files)
    unexpected = sorted(actual_files - expected_files)
    missing_directories = sorted(set(REQUIRED_DIRECTORIES) - directory_names)
    manifest_errors: list[str] = []
    manifest_bytes = archive_files.get("MANIFEST.sha256")
    if manifest_bytes is None:
        manifest_errors.append("manifest missing from archive")
    else:
        try:
            declared, parse_errors = _parse_manifest_text(manifest_bytes.decode("utf-8"))
        except UnicodeDecodeError:
            declared, parse_errors = {}, ["manifest is not UTF-8"]
        manifest_errors.extend(parse_errors)
        expected_hashes = {name: _sha256_bytes(data) for name, data in archive_files.items()
                           if name != "MANIFEST.sha256"}
        for name in sorted(set(expected_hashes) - set(declared)):
            manifest_errors.append(f"manifest omits {name}")
        for name in sorted(set(declared) - set(expected_hashes)):
            manifest_errors.append(f"manifest has unexpected {name}")
        for name in sorted(set(expected_hashes) & set(declared)):
            if expected_hashes[name] != declared[name]:
                manifest_errors.append(f"manifest hash mismatch: {name}")
    passed = not (errors or offenders or secret_hits or missing or unexpected or missing_directories or manifest_errors)
    return {"pass": passed, "path": str(zip_path), "file_count": len(actual_files),
            "errors": errors, "offenders": sorted(set(offenders)), "secret_hits": secret_hits,
            "missing": missing, "unexpected": unexpected, "missing_directories": missing_directories,
            "manifest_errors": manifest_errors}


def extract_verified_release(zip_path: Path, destination: Path, *,
                             source_root: Path | None = None) -> dict:
    zip_path = Path(zip_path)
    if not zip_path.is_file() or zip_path.stat().st_size > 100 * 1024 * 1024:
        raise ReleaseValidationError("release ZIP is missing or exceeds the compressed size limit")
    verified_bytes = zip_path.read_bytes()
    report = verify_release_zip(zip_path, root=source_root)
    if not report["pass"]:
        raise ReleaseValidationError("release ZIP verification failed")
    if zip_path.read_bytes() != verified_bytes:
        raise ReleaseValidationError("release ZIP changed during verification")
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ReleaseValidationError("fresh extraction destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(verified_bytes)) as archive:
        archive.extractall(destination)
    return report


def run_fresh_release_checks(zip_path: Path, *, source_root: Path | None = None,
                             python_executable: str | None = None,
                             include_tests: bool = True) -> dict:
    """Extract to an OS temp directory and run checks without touching source runtime data."""
    executable = python_executable or sys.executable
    checks: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="guardianlens-fresh-release-") as temporary:
        extracted = Path(temporary) / "package"
        verification = extract_verified_release(zip_path, extracted, source_root=source_root)
        commands = [[executable, "scripts/self_test.py"]]
        if include_tests:
            commands.append([executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                             f"--basetemp={Path(temporary) / 'pytest'}"])
        commands.append([executable, "scripts/mock_integration_check.py"])
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        for command in commands:
            result = subprocess.run(command, cwd=extracted, env=environment, capture_output=True,
                                    text=True, timeout=600)
            checks.append({"command": command, "returncode": result.returncode,
                           "stdout": result.stdout[-4000:], "stderr": result.stderr[-4000:]})
            if result.returncode:
                break
    return {"pass": verification["pass"] and all(item["returncode"] == 0 for item in checks),
            "zip_verification": verification, "checks": checks}
