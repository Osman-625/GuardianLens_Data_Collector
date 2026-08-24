from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from guardianlens.audit import data_integrity_report
from guardianlens.capture import create_capture
from guardianlens.config import settings
from guardianlens.db import init_db, now_iso, transaction
from guardianlens.models import CaptureRequest
from guardianlens.openai_extractor import MockExtractor
from guardianlens.processor import process_listing
from guardianlens.review import approve, begin_review
from guardianlens.images import validate_image_bytes
from conftest import data_url, image_bytes


def issue_names(report: dict) -> set[str]:
    return {finding["issue"] for finding in report["findings"]}


def insert_listing(con, listing_id: str, status: str, updated_at: str) -> None:
    con.execute(
        """
        INSERT INTO listings(
            listing_id,platform,source_url,source_url_hash,status,
            captured_at,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            listing_id, "carousell", f"https://www.carousell.com.my/p/{listing_id}",
            f"hash-{listing_id}", status, updated_at, updated_at, updated_at,
        ),
    )
    con.execute(
        "INSERT INTO status_history(history_id,listing_id,from_status,to_status,timestamp,reason) VALUES(?,?,?,?,?,?)",
        (str(uuid.uuid4()), listing_id, None, "captured", updated_at, "fixture capture"),
    )


def test_populated_valid_dataset_passes_expanded_integrity_checks(isolated, monkeypatch):
    monkeypatch.setattr(
        "guardianlens.processor.download_image",
        lambda _url: image_bytes((400, 300), value=133),
    )
    captured = create_capture(CaptureRequest(
        platform="carousell",
        source_url="https://www.carousell.com.my/p/integrity-valid-100001",
        page_title="Integrity valid",
        screenshot_data_url=data_url(image_bytes()),
        image_urls=["https://fixture.test/integrity.jpg"],
        listing_evidence={"h1_text": "Integrity valid fixture", "has_price_signal": True},
    ))
    process_listing(captured["listing_id"], MockExtractor())
    begin_review(captured["listing_id"])
    approve(captured["listing_id"])

    report = data_integrity_report()

    assert report["pass"]
    assert report["checks"]["quick_check"] == ["ok"]
    assert report["checks"]["foreign_key_violations"] == 0
    assert report["approved"] == 1


def test_integrity_audit_reports_populated_database_and_filesystem_failures(isolated):
    old = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    with transaction() as con:
        insert_listing(con, "hash-mismatch", "captured", now_iso())
        insert_listing(con, "stale-review", "under_review", old)
        insert_listing(con, "stale-validating", "validating", old)
        insert_listing(con, "retained-private", "rejected", now_iso())
        con.execute(
            "INSERT INTO review_actions(review_action_id,listing_id,action,started_at,created_at) VALUES(?,?,?,?,?)",
            (str(uuid.uuid4()), "stale-review", "review_session", old, old),
        )
        con.execute(
            """
            INSERT INTO review_actions(
                review_action_id,listing_id,action,started_at,finished_at,duration_seconds,created_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (str(uuid.uuid4()), "hash-mismatch", "review_session", old, now_iso(), 1, old),
        )
        con.execute(
            "UPDATE listings SET processing_run_id='missing-run' WHERE listing_id='hash-mismatch'"
        )
        con.execute(
            "UPDATE status_history SET from_status='captured',to_status='processing' WHERE listing_id='hash-mismatch'"
        )
        con.execute(
            "INSERT INTO warnings(warning_id,listing_id,warning_type,message,created_at) VALUES(?,?,?,?,?)",
            (str(uuid.uuid4()), "hash-mismatch", "private_asset_cleanup", "fixture cleanup failed", now_iso()),
        )
        screenshot_path = isolated / "data" / "private" / "screenshots" / "carousell" / "retained-private" / "listing.jpg"
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        screenshot_path.write_bytes(image_bytes())
        staging_path = isolated / "data" / "private" / "staging" / "retained-private.json"
        staging_path.write_text("{}", encoding="utf-8")
        con.execute(
            "UPDATE listings SET screenshot_path=?,staging_text_path=? WHERE listing_id='retained-private'",
            (str(screenshot_path.relative_to(isolated)), str(staging_path.relative_to(isolated))),
        )
        con.execute(
            """
            INSERT INTO processing_runs(
                run_id,started_at,finished_at,duration_seconds,status,
                attempted,processed,successful,ai_failures
            ) VALUES('bad-run',?,?,1,'completed',3,2,1,1)
            """,
            (old, now_iso()),
        )

        image_path = isolated / "data" / "images" / "carousell" / "hash-mismatch" / "image_001.jpg"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        data = image_bytes((320, 240), value=77)
        image_path.write_bytes(data)
        con.execute(
            """
            INSERT INTO images(
                image_id,listing_id,platform,storage_path,sha256,perceptual_hash,
                file_size_bytes,width,height,source_position,is_primary,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "bad-image", "hash-mismatch", "carousell",
                str(image_path.relative_to(isolated)), "0" * 64, "f" * 16,
                len(data), 320, 240, 1, 1, now_iso(),
            ),
        )

    orphan_path = isolated / "data" / "images" / "mudah" / "orphan" / "image_001.jpg"
    orphan_path.parent.mkdir(parents=True, exist_ok=True)
    orphan_path.write_bytes(image_bytes())
    orphan_screenshot = isolated / "data" / "private" / "screenshots" / "mudah" / "orphan" / "listing.jpg"
    orphan_screenshot.parent.mkdir(parents=True, exist_ok=True)
    orphan_screenshot.write_bytes(image_bytes())
    orphan_staging = isolated / "data" / "private" / "staging" / "orphan.json"
    orphan_staging.write_text("{}", encoding="utf-8")

    raw = sqlite3.connect(settings.db_path)
    try:
        raw.execute("PRAGMA foreign_keys=OFF")
        raw.execute(
            "INSERT INTO warnings(warning_id,listing_id,warning_type,message,created_at) VALUES(?,?,?,?,?)",
            (str(uuid.uuid4()), "missing-listing", "fixture", "orphan warning", now_iso()),
        )
        raw.commit()
    finally:
        raw.close()

    report = data_integrity_report()
    issues = issue_names(report)

    assert not report["pass"]
    assert "foreign_key_violations" in issues
    assert "product_image_sha256_mismatch" in issues
    assert "product_image_perceptual_hash_mismatch" in issues
    assert "orphan_product_image_path" in issues
    assert "orphan_product_image_directory" in issues
    assert "orphan_private_screenshot_path" in issues
    assert "orphan_private_staging_path" in issues
    assert "invalid_initial_status_history" in issues
    assert "current_status_history_mismatch" in issues
    assert "stale_review_session" in issues
    assert "review_session_duration_mismatch" in issues
    assert "stale_intermediate_state" in issues
    assert "processing_run_processed_mismatch" in issues
    assert "processing_run_classification_mismatch" in issues
    assert "processing_run_failed_counter_mismatch" in issues
    assert "processing_run_duration_mismatch" in issues
    assert "listing_processing_run_missing" in issues
    assert "unresolved_private_asset_cleanup" in issues
    assert "private_screenshot_retained_after_outcome" in issues
    assert "private_staging_retained_after_outcome" in issues


def test_integrity_audit_reports_all_approved_duplicate_dimensions(isolated):
    data = image_bytes((320, 240), value=91)
    _fmt, width, height, sha256, phash = validate_image_bytes(data)
    timestamp = now_iso()
    with transaction() as con:
        for index, listing_id in enumerate(("approved-duplicate-a", "approved-duplicate-b"), start=1):
            con.execute(
                """
                INSERT INTO listings(
                    listing_id,platform,source_url,source_url_hash,title,category,price,currency,
                    status,metadata_signature,captured_at,approved_at,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    listing_id, "carousell", f"https://example.test/{index}", "same-url-hash",
                    "Same title", "Phones", 10, "MYR", "approved", "same-metadata",
                    timestamp, timestamp, timestamp, timestamp,
                ),
            )
            con.execute(
                "INSERT INTO status_history(history_id,listing_id,from_status,to_status,timestamp,reason) VALUES(?,?,?,?,?,?)",
                (str(uuid.uuid4()), listing_id, None, "captured", timestamp, "fixture capture"),
            )
            path = isolated / "data" / "images" / "carousell" / listing_id / "image_001.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            con.execute(
                """
                INSERT INTO images(
                    image_id,listing_id,platform,storage_path,sha256,perceptual_hash,
                    file_size_bytes,width,height,source_position,is_primary,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f"image-{index}", listing_id, "carousell", str(path.relative_to(isolated)),
                    sha256, f"{phash:016x}", len(data), width, height, 1, 1, timestamp,
                ),
            )

    issues = issue_names(data_integrity_report())

    assert "duplicate_approved_urls" in issues
    assert "duplicate_approved_metadata" in issues
    assert "duplicate_approved_image_sha256" in issues


def test_soft_test_audit_rejects_production_runtime_paths(isolated):
    object.__setattr__(settings, "mode", "soft_test")
    try:
        init_db()
        with transaction() as con:
            timestamp = now_iso()
            con.execute(
                """
                INSERT INTO listings(
                    listing_id,platform,source_url,source_url_hash,status,screenshot_path,
                    captured_at,created_at,updated_at
                ) VALUES('soft-path','mudah','https://www.mudah.my/soft-path','soft-path',
                         'captured','data/private/screenshots/soft-path/listing.jpg',?,?,?)
                """,
                (timestamp, timestamp, timestamp),
            )
            con.execute(
                "INSERT INTO status_history(history_id,listing_id,from_status,to_status,timestamp,reason) VALUES(?,?,?,?,?,?)",
                (str(uuid.uuid4()), "soft-path", None, "captured", timestamp, "fixture capture"),
            )

        report = data_integrity_report()
        assert "screenshot_path_outside_runtime" in issue_names(report)
    finally:
        object.__setattr__(settings, "mode", "production")
