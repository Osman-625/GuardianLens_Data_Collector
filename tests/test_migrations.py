from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
import time

import pytest

from guardianlens.config import settings
from guardianlens.db import LATEST_SCHEMA_VERSION, MigrationError, init_db


def test_new_database_is_versioned_without_backup(isolated):
    path = isolated / "data" / "new.sqlite3"

    backup = init_db(path)

    assert backup is None
    con = sqlite3.connect(path)
    try:
        assert con.execute("PRAGMA user_version").fetchone()[0] == LATEST_SCHEMA_VERSION
        assert con.execute("SELECT name FROM schema_migrations WHERE version=1").fetchone()[0] == "baseline_versioned_schema"
        assert con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        con.close()


def test_unversioned_database_is_backed_up_and_preserved(isolated):
    path = isolated / "data" / "legacy.sqlite3"
    con = sqlite3.connect(path)
    try:
        con.executescript((settings.root / "src" / "guardianlens" / "schema.sql").read_text(encoding="utf-8"))
        con.execute(
            """
            INSERT INTO listings(
                listing_id,platform,source_url,source_url_hash,status,
                captured_at,created_at,updated_at
            ) VALUES('legacy','carousell','https://example.test/legacy','legacy-hash',
                     'captured','2026-01-01','2026-01-01','2026-01-01')
            """
        )
        con.commit()
        assert con.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        con.close()

    backup = init_db(path)

    assert backup is not None and backup.is_file()
    migrated = sqlite3.connect(path)
    preserved = sqlite3.connect(backup)
    try:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == LATEST_SCHEMA_VERSION
        assert migrated.execute("SELECT COUNT(*) FROM listings WHERE listing_id='legacy'").fetchone()[0] == 1
        assert preserved.execute("PRAGMA user_version").fetchone()[0] == 0
        assert preserved.execute("SELECT COUNT(*) FROM listings WHERE listing_id='legacy'").fetchone()[0] == 1
        assert preserved.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        migrated.close()
        preserved.close()


def test_failed_migration_rolls_back_and_keeps_valid_backup(isolated):
    path = isolated / "data" / "incompatible.sqlite3"
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE listings(listing_id TEXT PRIMARY KEY)")
        con.execute("INSERT INTO listings(listing_id) VALUES('keep-me')")
        con.commit()
    finally:
        con.close()

    with pytest.raises(MigrationError, match="Pre-migration backup"):
        init_db(path)

    backups = list((path.parent / "backups" / "schema_migrations").glob("incompatible_v0_*.sqlite3"))
    assert len(backups) == 1
    original = sqlite3.connect(path)
    backup = sqlite3.connect(backups[0])
    try:
        assert [row[1] for row in original.execute("PRAGMA table_info(listings)")] == ["listing_id"]
        assert original.execute("SELECT listing_id FROM listings").fetchone()[0] == "keep-me"
        assert backup.execute("SELECT listing_id FROM listings").fetchone()[0] == "keep-me"
        assert original.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        original.close()
        backup.close()


def test_migration_rejects_wrong_index_definition_and_preserves_original(isolated):
    path = isolated / "data" / "wrong-index.sqlite3"
    con = sqlite3.connect(path)
    try:
        con.executescript((settings.root / "src" / "guardianlens" / "schema.sql").read_text(encoding="utf-8"))
        con.execute("DROP INDEX ix_listings_status")
        con.execute("CREATE INDEX ix_listings_status ON listings(category)")
        con.commit()
    finally:
        con.close()

    with pytest.raises(MigrationError, match="ix_listings_status"):
        init_db(path)

    backups = list((path.parent / "backups" / "schema_migrations").glob("wrong-index_v0_*.sqlite3"))
    assert len(backups) == 1
    original = sqlite3.connect(path)
    try:
        columns = tuple(row[2] for row in original.execute("PRAGMA index_info(ix_listings_status)"))
        assert columns == ("category",)
        assert original.execute("PRAGMA user_version").fetchone()[0] == 0
        assert original.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
    finally:
        original.close()


def test_newer_database_is_rejected_without_runtime_mutation_or_backup(isolated):
    path = isolated / "data" / "future.sqlite3"
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE future_data(value TEXT)")
        con.execute("INSERT INTO future_data(value) VALUES('preserve-me')")
        con.execute(f"PRAGMA user_version={LATEST_SCHEMA_VERSION + 1}")
        con.execute("PRAGMA journal_mode=DELETE")
        con.commit()
    finally:
        con.close()

    with pytest.raises(MigrationError, match="newer than supported"):
        init_db(path)

    untouched = sqlite3.connect(path)
    try:
        assert untouched.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert untouched.execute("SELECT value FROM future_data").fetchone()[0] == "preserve-me"
        assert untouched.execute("PRAGMA user_version").fetchone()[0] == LATEST_SCHEMA_VERSION + 1
    finally:
        untouched.close()
    backup_root = path.parent / "backups" / "schema_migrations"
    assert not backup_root.exists()


def test_concurrent_migration_callers_converge_on_one_schema(isolated, monkeypatch):
    from guardianlens import db as db_module

    path = isolated / "data" / "concurrent.sqlite3"
    con = sqlite3.connect(path)
    try:
        con.executescript((settings.root / "src" / "guardianlens" / "schema.sql").read_text(encoding="utf-8"))
        con.commit()
    finally:
        con.close()

    first_migration_entered = threading.Event()
    allow_first_migration = threading.Event()
    original_apply = db_module._apply_migration

    def delayed_apply(connection, version, schema):
        if not first_migration_entered.is_set():
            first_migration_entered.set()
            assert allow_first_migration.wait(timeout=5)
        return original_apply(connection, version, schema)

    monkeypatch.setattr(db_module, "_apply_migration", delayed_apply)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(init_db, path)
        assert first_migration_entered.wait(timeout=5)
        second = pool.submit(init_db, path)
        time.sleep(0.2)  # Let the second caller observe v0 and wait for the write lock.
        allow_first_migration.set()
        assert first.result(timeout=10) is not None
        assert second.result(timeout=10) is not None

    migrated = sqlite3.connect(path)
    try:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == LATEST_SCHEMA_VERSION
        assert migrated.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=1").fetchone()[0] == 1
        assert migrated.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        migrated.close()
