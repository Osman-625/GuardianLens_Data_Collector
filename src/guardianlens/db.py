from __future__ import annotations
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .config import settings

LATEST_SCHEMA_VERSION = 1
MIGRATION_NAMES = {1: "baseline_versioned_schema"}

REQUIRED_TABLE_COLUMNS = {
    "listings": {
        "listing_id", "platform", "marketplace_listing_id", "source_url", "source_url_hash",
        "page_title", "title", "description", "category", "condition", "price", "currency",
        "location_state", "location_city", "account_age_days", "seller_rating", "review_count",
        "active_listing_count", "language", "status", "metadata_signature", "screenshot_path",
        "staging_text_path", "ai_extracted", "ai_provider", "ai_model", "ai_prompt_version",
        "captured_at", "processed_at", "approved_at", "rejected_at", "processing_run_id",
        "created_at", "updated_at",
    },
    "images": {
        "image_id", "listing_id", "platform", "storage_path", "sha256", "perceptual_hash",
        "file_size_bytes", "width", "height", "source_position", "is_primary", "source_url",
        "created_at",
    },
    "ai_extractions": {
        "extraction_id", "listing_id", "provider", "model", "prompt_version",
        "structured_output", "created_at", "status", "error",
    },
    "review_actions": {
        "review_action_id", "listing_id", "action", "previous_data", "updated_data",
        "started_at", "finished_at", "duration_seconds", "created_at",
    },
    "warnings": {"warning_id", "listing_id", "warning_type", "message", "resolved", "created_at"},
    "status_history": {"history_id", "listing_id", "from_status", "to_status", "timestamp", "reason"},
    "processing_runs": {
        "run_id", "started_at", "finished_at", "duration_seconds", "duration_limit_minutes",
        "item_limit", "status", "attempted", "processed", "successful", "duplicates",
        "ai_failures", "image_failures", "privacy_warnings", "needs_attention",
        "other_failures", "failed",
    },
    "processor_control": {"control_id", "command", "updated_at"},
    "schema_migrations": {"version", "name", "applied_at"},
}

REQUIRED_INDEX_COLUMNS = {
    "ux_listings_platform_marketplace_id": ("platform", "marketplace_listing_id"),
    "ix_listings_url_hash": ("source_url_hash",),
    "ix_listings_status": ("status",),
    "ix_listings_platform_category": ("platform", "category"),
    "ix_listings_metadata_signature": ("metadata_signature",),
    "ix_images_listing": ("listing_id",),
    "ix_images_sha256": ("sha256",),
    "ix_images_phash": ("perceptual_hash",),
    "ix_warnings_listing": ("listing_id",),
}

REQUIRED_FOREIGN_KEYS = {
    "images": {("listing_id", "listings", "listing_id", "CASCADE")},
    "ai_extractions": {("listing_id", "listings", "listing_id", "CASCADE")},
    "review_actions": {("listing_id", "listings", "listing_id", "CASCADE")},
    "warnings": {("listing_id", "listings", "listing_id", "CASCADE")},
    "status_history": {("listing_id", "listings", "listing_id", "CASCADE")},
}


class MigrationError(RuntimeError):
    """A database migration failed; the pre-migration backup remains available."""

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def _schema_text() -> str:
    return (settings.root / "src" / "guardianlens" / "schema.sql").read_text(encoding="utf-8")


def _execute_schema_statements(con: sqlite3.Connection, schema: str) -> None:
    """Execute schema statements without sqlite3.executescript's implicit commit."""
    pending = ""
    for line in schema.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statement = pending.strip()
            pending = ""
            if statement:
                con.execute(statement)
    if pending.strip():
        raise MigrationError("schema.sql ends with an incomplete SQL statement")


def _backup_database(source: sqlite3.Connection, path: Path, from_version: int) -> Path:
    backup_dir = path.parent / "backups" / "schema_migrations"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_dir / f"{path.stem}_v{from_version}_{stamp}_{uuid.uuid4().hex[:8]}.sqlite3"
    destination = sqlite3.connect(backup_path)
    try:
        source.backup(destination)
        quick = [row[0] for row in destination.execute("PRAGMA quick_check")]
        if quick != ["ok"]:
            raise MigrationError(f"pre-migration backup failed SQLite quick_check: {quick}")
    finally:
        destination.close()
    return backup_path


def _validate_schema(con: sqlite3.Connection, expected_version: int = LATEST_SCHEMA_VERSION) -> None:
    quick = [row[0] for row in con.execute("PRAGMA quick_check")]
    if quick != ["ok"]:
        raise MigrationError(f"SQLite quick_check failed: {quick}")
    foreign_keys = list(con.execute("PRAGMA foreign_key_check"))
    if foreign_keys:
        raise MigrationError(f"foreign-key validation failed with {len(foreign_keys)} violation(s)")

    for table, required in REQUIRED_TABLE_COLUMNS.items():
        columns = {row[1] for row in con.execute(f'PRAGMA table_info("{table}")')}
        missing = required - columns
        if missing:
            raise MigrationError(f"table {table!r} is missing required columns: {sorted(missing)}")

    indexes = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    missing_indexes = set(REQUIRED_INDEX_COLUMNS) - indexes
    if missing_indexes:
        raise MigrationError(f"database is missing required indexes: {sorted(missing_indexes)}")
    for index_name, expected_columns in REQUIRED_INDEX_COLUMNS.items():
        actual_columns = tuple(
            row[2] for row in con.execute(f'PRAGMA index_info("{index_name}")')
        )
        if actual_columns != expected_columns:
            raise MigrationError(
                f"index {index_name!r} has columns {actual_columns}; expected {expected_columns}"
            )
    listing_indexes = {
        row[1]: (bool(row[2]), bool(row[4]))
        for row in con.execute('PRAGMA index_list("listings")')
    }
    if listing_indexes.get("ux_listings_platform_marketplace_id") != (True, True):
        raise MigrationError("approved marketplace identifier index must be unique and partial")

    for table, expected in REQUIRED_FOREIGN_KEYS.items():
        actual = {
            (row[3], row[2], row[4], row[6].upper())
            for row in con.execute(f'PRAGMA foreign_key_list("{table}")')
        }
        if not expected.issubset(actual):
            raise MigrationError(f"table {table!r} is missing required foreign keys")

    version = int(con.execute("PRAGMA user_version").fetchone()[0])
    if version != expected_version:
        raise MigrationError(f"schema version is {version}; expected {expected_version}")
    applied_rows = {
        row["version"]: row["name"]
        for row in con.execute("SELECT version,name FROM schema_migrations")
    }
    applied = set(applied_rows)
    missing_versions = set(range(1, expected_version + 1)) - applied
    if missing_versions:
        raise MigrationError(f"schema migration history is incomplete: {sorted(missing_versions)}")
    unexpected_versions = applied - set(range(1, expected_version + 1))
    if unexpected_versions:
        raise MigrationError(f"schema migration history has unsupported versions: {sorted(unexpected_versions)}")
    wrong_names = {
        version: name
        for version, name in applied_rows.items()
        if version in MIGRATION_NAMES and name != MIGRATION_NAMES[version]
    }
    if wrong_names:
        raise MigrationError(f"schema migration history has unexpected names: {wrong_names}")


def _apply_migration(con: sqlite3.Connection, version: int, schema: str) -> None:
    if version == 1:
        # Version 1 adopts the existing v2.0.0 schema without rewriting user rows.
        # CREATE IF NOT EXISTS is intentional: validation below rejects an older,
        # incompatible table shape and leaves the original transaction untouched.
        _execute_schema_statements(con, schema)
    else:
        raise MigrationError(f"no migration implementation for schema version {version}")
    con.execute(
        "INSERT INTO schema_migrations(version,name,applied_at) VALUES(?,?,?)",
        (version, MIGRATION_NAMES[version], now_iso()),
    )
    con.execute(f"PRAGMA user_version={version}")


def init_db(db_path: Path | None = None) -> Path | None:
    """Initialize or migrate a database, returning a backup path when migrated.

    Existing databases are backed up through SQLite's online backup API before
    the first migration statement. Every migration runs in one immediate
    transaction and is validated before commit.
    """
    path = Path(db_path or settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.exists() and path.stat().st_size > 0
    backup_path: Path | None = None

    if existing:
        inspection = sqlite3.connect(path, timeout=30)
        try:
            inspected_version = int(inspection.execute("PRAGMA user_version").fetchone()[0])
            if inspected_version > LATEST_SCHEMA_VERSION:
                raise MigrationError(
                    f"database schema version {inspected_version} is newer than supported version {LATEST_SCHEMA_VERSION}"
                )
            if inspected_version < LATEST_SCHEMA_VERSION:
                backup_path = _backup_database(inspection, path, inspected_version)
        finally:
            inspection.close()

    con = connect(path)
    current_version = int(con.execute("PRAGMA user_version").fetchone()[0])
    try:
        if current_version > LATEST_SCHEMA_VERSION:
            raise MigrationError(
                f"database schema version {current_version} is newer than supported version {LATEST_SCHEMA_VERSION}"
            )
        if current_version < LATEST_SCHEMA_VERSION:
            con.execute("BEGIN IMMEDIATE")
            try:
                # Another process may have migrated while this connection waited
                # for the write lock. Re-read the durable version under that lock.
                locked_version = int(con.execute("PRAGMA user_version").fetchone()[0])
                if locked_version > LATEST_SCHEMA_VERSION:
                    raise MigrationError(
                        f"database schema version {locked_version} is newer than supported version {LATEST_SCHEMA_VERSION}"
                    )
                if locked_version < LATEST_SCHEMA_VERSION:
                    schema = _schema_text()
                    for version in range(locked_version + 1, LATEST_SCHEMA_VERSION + 1):
                        _apply_migration(con, version, schema)
                _validate_schema(con)
                con.commit()
            except Exception as exc:
                con.rollback()
                suffix = f" Pre-migration backup: {backup_path}" if backup_path else ""
                if isinstance(exc, MigrationError):
                    raise MigrationError(f"database migration failed: {exc}.{suffix}") from exc
                raise MigrationError(f"database migration failed: {exc}.{suffix}") from exc
        else:
            _validate_schema(con)
    finally:
        con.close()
    return backup_path

@contextmanager
def transaction(db_path: Path | None = None):
    con = connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
