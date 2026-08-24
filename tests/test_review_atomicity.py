from __future__ import annotations

import json
import threading
import pytest
import guardianlens.review as review_module

from guardianlens.capture import create_capture
from guardianlens.config import settings
from guardianlens.db import connect, now_iso, transaction
from guardianlens.models import CaptureRequest
from guardianlens.openai_extractor import MockExtractor
from guardianlens.processor import process_listing
from guardianlens.review import (
    PrivateAssetCleanupError,
    action,
    approve,
    begin_review,
    edit_and_approve,
    get_listing,
)
from conftest import data_url, image_bytes


def processed_listing(monkeypatch, *, suffix: str, with_image: bool = True, image_value: int = 117) -> str:
    request = CaptureRequest(
        platform="carousell",
        source_url=f"https://www.carousell.com.my/p/review-atomic-{suffix}",
        page_title="Review atomic fixture",
        screenshot_data_url=data_url(image_bytes()),
        image_urls=[f"https://fixture.test/{suffix}.jpg"],
        listing_evidence={"h1_text": "Review atomic fixture", "has_price_signal": True},
    )
    result = create_capture(request)
    if not with_image:
        staged = get_listing(result["listing_id"])["staging_text_path"]
        (settings.root / staged).write_text(json.dumps({"image_urls": []}), encoding="utf-8")
    if with_image:
        monkeypatch.setattr(
            "guardianlens.processor.download_image",
            lambda _url: image_bytes((400, 300), value=image_value),
        )
    process_listing(result["listing_id"], MockExtractor())
    return result["listing_id"]


def test_review_outcomes_require_active_under_review_session(isolated, monkeypatch):
    listing_id = processed_listing(monkeypatch, suffix="100001")

    with pytest.raises(ValueError, match="under_review"):
        approve(listing_id)
    with pytest.raises(ValueError, match="under_review"):
        action(listing_id, "skipped")

    assert get_listing(listing_id)["status"] == "ready_for_review"


def test_review_outcome_rejects_multiple_open_sessions(isolated, monkeypatch):
    listing_id = processed_listing(monkeypatch, suffix="100013")
    begin_review(listing_id)
    with transaction() as con:
        timestamp = now_iso()
        con.execute(
            "INSERT INTO review_actions(review_action_id,listing_id,action,started_at,created_at) VALUES(?,?,?,?,?)",
            ("second-open-session", listing_id, "review_session", timestamp, timestamp),
        )

    with pytest.raises(ValueError, match="exactly one active"):
        approve(listing_id)

    assert get_listing(listing_id)["status"] == "under_review"


def test_edit_and_approve_rolls_back_corrections_when_approval_fails(isolated, monkeypatch):
    listing_id = processed_listing(monkeypatch, suffix="100002", with_image=False)
    original_title = get_listing(listing_id)["title"]
    begin_review(listing_id)

    with pytest.raises(ValueError, match="product image"):
        edit_and_approve(listing_id, {"title": "Must roll back"})

    item = get_listing(listing_id)
    assert item["status"] == "under_review"
    assert item["title"] == original_title
    con = connect()
    try:
        assert con.execute(
            "SELECT COUNT(*) n FROM review_actions WHERE listing_id=? AND action='edit'",
            (listing_id,),
        ).fetchone()["n"] == 0
        assert con.execute(
            "SELECT COUNT(*) n FROM review_actions WHERE listing_id=? AND action='review_session' AND finished_at IS NULL",
            (listing_id,),
        ).fetchone()["n"] == 1
    finally:
        con.close()


def test_edit_and_approve_commits_correction_history_and_status_together(isolated, monkeypatch):
    listing_id = processed_listing(monkeypatch, suffix="100003")
    begin_review(listing_id)

    changes = edit_and_approve(listing_id, {"title": "Human verified title"})

    item = get_listing(listing_id)
    assert item["status"] == "approved"
    assert item["title"] == "Human verified title"
    assert changes["title"]["new"] == "Human verified title"
    con = connect()
    try:
        actions = [
            row["action"]
            for row in con.execute(
                "SELECT action FROM review_actions WHERE listing_id=? ORDER BY created_at,rowid",
                (listing_id,),
            )
        ]
        assert "edit" in actions and "approve" in actions
        assert con.execute(
            "SELECT COUNT(*) n FROM review_actions WHERE listing_id=? AND action='review_session' AND finished_at IS NULL",
            (listing_id,),
        ).fetchone()["n"] == 0
    finally:
        con.close()


def test_human_edits_scrub_all_free_text_fields_before_approval(isolated, monkeypatch):
    listing_id = processed_listing(monkeypatch, suffix="100006")
    begin_review(listing_id)

    edit_and_approve(listing_id, {
        "condition": "Like new, WhatsApp 012-3456789",
        "location_state": "Selangor contact me on Telegram",
        "language": "English seller@example.test",
    })

    item = get_listing(listing_id)
    assert item["status"] == "approved"
    assert "012-3456789" not in item["condition"]
    assert "Telegram" not in item["location_state"]
    assert "seller@example.test" not in item["language"]
    assert "[REDACTED]" in item["condition"]


def test_approval_checks_residual_pii_in_every_free_text_field(isolated, monkeypatch):
    listing_id = processed_listing(monkeypatch, suffix="100007")
    begin_review(listing_id)
    con = connect()
    try:
        con.execute("UPDATE listings SET condition='Call +60123456789' WHERE listing_id=?", (listing_id,))
    finally:
        con.close()

    with pytest.raises(ValueError, match="residual PII"):
        approve(listing_id)

    assert get_listing(listing_id)["status"] == "under_review"


def test_approval_verifies_product_image_file_contents(isolated, monkeypatch):
    listing_id = processed_listing(monkeypatch, suffix="100004")
    begin_review(listing_id)
    item = get_listing(listing_id)
    path = settings.root / item["images"][0]["storage_path"]
    path.write_bytes(b"not a readable product image")

    with pytest.raises(ValueError, match="unreadable or invalid"):
        approve(listing_id)

    assert get_listing(listing_id)["status"] == "under_review"


def test_approval_verifies_product_image_sha256(isolated, monkeypatch):
    listing_id = processed_listing(monkeypatch, suffix="100008")
    begin_review(listing_id)
    with transaction() as con:
        con.execute("UPDATE images SET sha256=? WHERE listing_id=?", ("0" * 64, listing_id))

    with pytest.raises(ValueError, match="SHA-256"):
        approve(listing_id)

    assert get_listing(listing_id)["status"] == "under_review"


def test_approval_blocks_legacy_exact_image_duplicate_of_approved_record(isolated, monkeypatch):
    approved_id = processed_listing(monkeypatch, suffix="100011", image_value=101)
    candidate_id = processed_listing(monkeypatch, suffix="100012", image_value=202)
    begin_review(approved_id)
    approve(approved_id)

    approved_image = get_listing(approved_id)["images"][0]
    candidate_image = get_listing(candidate_id)["images"][0]
    approved_path = settings.root / approved_image["storage_path"]
    candidate_path = settings.root / candidate_image["storage_path"]
    candidate_path.write_bytes(approved_path.read_bytes())
    with transaction() as con:
        con.execute(
            """
            UPDATE images
            SET sha256=?,perceptual_hash=?,file_size_bytes=?,width=?,height=?
            WHERE image_id=?
            """,
            (
                approved_image["sha256"], approved_image["perceptual_hash"],
                approved_image["file_size_bytes"], approved_image["width"],
                approved_image["height"], candidate_image["image_id"],
            ),
        )
    original_title = get_listing(candidate_id)["title"]
    begin_review(candidate_id)

    with pytest.raises(ValueError, match="exact product image duplicate"):
        edit_and_approve(candidate_id, {"title": "Distinct human title"})

    candidate = get_listing(candidate_id)
    assert candidate["status"] == "under_review"
    assert candidate["title"] == original_title


def test_private_cleanup_failure_is_persisted_and_raised_after_commit(isolated, monkeypatch):
    listing_id = processed_listing(monkeypatch, suffix="100005")
    begin_review(listing_id)

    def fail_cleanup(_path):
        raise OSError("simulated retention cleanup failure")

    monkeypatch.setattr("guardianlens.review.shutil.rmtree", fail_cleanup)
    with pytest.raises(PrivateAssetCleanupError, match="approved committed"):
        approve(listing_id)

    assert get_listing(listing_id)["status"] == "approved"
    con = connect()
    try:
        warning = con.execute(
            "SELECT message FROM warnings WHERE listing_id=? AND warning_type='private_asset_cleanup'",
            (listing_id,),
        ).fetchone()
        assert warning is not None
        assert "simulated retention cleanup failure" in warning["message"]
    finally:
        con.close()


def test_private_cleanup_refuses_to_delete_screenshot_root(isolated, monkeypatch):
    listing_id = processed_listing(monkeypatch, suffix="100009")
    begin_review(listing_id)
    screenshot_root = settings.data_dir / "private" / "screenshots"
    sentinel = screenshot_root / "must-survive.txt"
    sentinel.write_text("sentinel", encoding="utf-8")
    with transaction() as con:
        con.execute(
            "UPDATE listings SET screenshot_path=? WHERE listing_id=?",
            (str(screenshot_root.relative_to(settings.root)), listing_id),
        )

    with pytest.raises(PrivateAssetCleanupError, match="cleanup failed"):
        approve(listing_id)

    assert get_listing(listing_id)["status"] == "approved"
    assert sentinel.read_text(encoding="utf-8") == "sentinel"


def test_get_listing_includes_ordered_provenance_and_filters_unavailable_evidence(isolated, monkeypatch):
    listing_id = processed_listing(monkeypatch, suffix="100010")
    begin_review(listing_id)
    edit_and_approve(listing_id, {"title": "Verified lifecycle record"})

    item = get_listing(listing_id)

    assert [entry["status"] for entry in item["ai_extractions"]] == ["success"]
    actions = [entry["action"] for entry in item["review_actions"]]
    assert actions == ["review_session", "edit", "approve"]
    assert item["screenshot_storage_path"]
    assert item["screenshot_path"] is None
    assert not item["screenshot_available"]
    assert len(item["images"]) == 1
    assert item["images"][0]["available"]

    image_path = settings.root / item["images"][0]["storage_path"]
    image_path.unlink()
    refreshed = get_listing(listing_id)
    assert refreshed["images"] == []
    assert len(refreshed["unavailable_images"]) == 1
    assert not refreshed["unavailable_images"][0]["available"]


def test_get_listing_reads_one_lifecycle_snapshot_during_concurrent_update(isolated, monkeypatch):
    listing_id = processed_listing(monkeypatch, suffix="100014")
    original_title = get_listing(listing_id)["title"]
    snapshot_open = threading.Event()
    writer_finished = threading.Event()
    original_availability = review_module._image_file_available
    calls = {"count": 0}

    def pause_after_listing_select(relative_path, allowed_root):
        calls["count"] += 1
        if calls["count"] == 1:
            snapshot_open.set()
            assert writer_finished.wait(timeout=5)
        return original_availability(relative_path, allowed_root)

    monkeypatch.setattr(review_module, "_image_file_available", pause_after_listing_select)
    outcome = {}

    def read_record():
        try:
            outcome["item"] = get_listing(listing_id)
        except Exception as exc:  # surfaced by the assertion in the main test thread
            outcome["error"] = exc

    reader = threading.Thread(target=read_record)
    reader.start()
    assert snapshot_open.wait(timeout=5)
    try:
        with transaction() as con:
            timestamp = now_iso()
            con.execute(
                "UPDATE listings SET title=?,updated_at=? WHERE listing_id=?",
                ("Concurrent new title", timestamp, listing_id),
            )
            con.execute(
                """
                INSERT INTO review_actions(review_action_id,listing_id,action,created_at)
                VALUES('concurrent-record-action',?,'concurrent_fixture',?)
                """,
                (listing_id, timestamp),
            )
    finally:
        writer_finished.set()
    reader.join(timeout=5)

    assert not reader.is_alive()
    assert "error" not in outcome
    assert outcome["item"]["title"] == original_title
    assert all(
        action["review_action_id"] != "concurrent-record-action"
        for action in outcome["item"]["review_actions"]
    )
    refreshed = get_listing(listing_id)
    assert refreshed["title"] == "Concurrent new title"
    assert any(
        action["review_action_id"] == "concurrent-record-action"
        for action in refreshed["review_actions"]
    )
