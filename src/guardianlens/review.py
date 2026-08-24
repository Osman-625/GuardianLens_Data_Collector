from __future__ import annotations
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .config import settings
from .db import connect, transaction, now_iso
from .states import transition
from .dedupe import metadata_signature
from .pii import scrub_text, residual_pii
from .images import validate_image_bytes

EDITABLE = ["title","description","category","condition","price","currency","location_state","location_city","account_age_days","seller_rating","review_count","active_listing_count","language"]
PII_FREE_TEXT_FIELDS = ("title", "description", "condition", "location_state", "location_city", "language")
REVIEWABLE = {"ready_for_review","needs_attention","under_review","skipped","flagged"}


class PrivateAssetCleanupError(RuntimeError):
    """The review outcome committed, but retention-policy cleanup needs attention."""


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _image_file_available(relative_path: str | None, allowed_root: Path) -> bool:
    if not relative_path:
        return False
    path = (settings.root / relative_path).resolve()
    if not _is_within(path, allowed_root.resolve()) or not path.is_file():
        return False
    try:
        validate_image_bytes(path.read_bytes())
    except (OSError, ValueError):
        return False
    return True

def get_listing(listing_id: str) -> dict:
    c = connect()
    try:
        c.execute("BEGIN")
        row = c.execute("SELECT * FROM listings WHERE listing_id=?", (listing_id,)).fetchone()
        if not row: raise KeyError(listing_id)
        d = dict(row)
        d["screenshot_storage_path"] = d["screenshot_path"]
        d["screenshot_available"] = _image_file_available(
            d["screenshot_path"],
            settings.data_dir / "private" / "screenshots" / d["platform"] / listing_id,
        )
        if not d["screenshot_available"]:
            # Templates use screenshot_path as the display gate. Preserve the
            # recorded value separately while avoiding a broken asset request.
            d["screenshot_path"] = None

        image_records = []
        for image in c.execute(
            "SELECT * FROM images WHERE listing_id=? ORDER BY source_position,image_id",
            (listing_id,),
        ):
            record = dict(image)
            record["available"] = record["platform"] == d["platform"] and _image_file_available(
                record["storage_path"],
                settings.data_dir / "images" / d["platform"] / listing_id,
            )
            image_records.append(record)
        d["image_records"] = image_records
        d["images"] = [image for image in image_records if image["available"]]
        d["unavailable_images"] = [image for image in image_records if not image["available"]]
        d["warnings"] = [dict(x) for x in c.execute("SELECT * FROM warnings WHERE listing_id=? ORDER BY created_at,rowid", (listing_id,))]
        d["history"] = [dict(x) for x in c.execute("SELECT * FROM status_history WHERE listing_id=? ORDER BY timestamp,rowid", (listing_id,))]
        d["ai_extractions"] = [dict(x) for x in c.execute(
            "SELECT * FROM ai_extractions WHERE listing_id=? ORDER BY created_at,rowid",
            (listing_id,),
        )]
        d["review_actions"] = [dict(x) for x in c.execute(
            "SELECT * FROM review_actions WHERE listing_id=? ORDER BY created_at,rowid",
            (listing_id,),
        )]
        return d
    finally:
        c.close()

def begin_review(listing_id: str) -> None:
    with transaction() as con:
        row = con.execute("SELECT status FROM listings WHERE listing_id=?", (listing_id,)).fetchone()
        if not row or row["status"] not in REVIEWABLE:
            raise ValueError("listing is not reviewable")
        if row["status"] != "under_review":
            transition(listing_id, "under_review", "human review opened", con=con)
        existing = con.execute("SELECT review_action_id FROM review_actions WHERE listing_id=? AND action='review_session' AND finished_at IS NULL ORDER BY created_at DESC LIMIT 1", (listing_id,)).fetchone()
        if not existing:
            con.execute("INSERT INTO review_actions(review_action_id,listing_id,action,started_at,created_at) VALUES(?,?,?,?,?)", (str(uuid.uuid4()),listing_id,"review_session",now_iso(),now_iso()))

def _finish_review_session(con, listing_id: str) -> int | None:
    row = con.execute("SELECT review_action_id,started_at FROM review_actions WHERE listing_id=? AND action='review_session' AND finished_at IS NULL ORDER BY created_at DESC LIMIT 1", (listing_id,)).fetchone()
    if not row or not row["started_at"]: return None
    started = datetime.fromisoformat(row["started_at"])
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    duration = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
    con.execute("UPDATE review_actions SET finished_at=?,duration_seconds=? WHERE review_action_id=?", (now_iso(),duration,row["review_action_id"]))
    return duration


def _require_active_review(con, listing_id: str):
    row = con.execute("SELECT * FROM listings WHERE listing_id=?", (listing_id,)).fetchone()
    if not row or row["status"] != "under_review":
        raise ValueError("listing must be under_review before a human review outcome")
    sessions = con.execute(
        """
        SELECT review_action_id,started_at
        FROM review_actions
        WHERE listing_id=? AND action='review_session' AND finished_at IS NULL
        ORDER BY created_at DESC,rowid DESC
        """,
        (listing_id,),
    ).fetchall()
    if len(sessions) != 1 or not sessions[0]["started_at"]:
        if len(sessions) > 1:
            raise ValueError("listing must have exactly one active human review session")
        raise ValueError("listing has no active human review session")
    return row


def _clean_updates(updates: dict) -> dict:
    updates = {k:v for k,v in updates.items() if k in EDITABLE}
    for field in PII_FREE_TEXT_FIELDS:
        if field in updates:
            updates[field] = scrub_text(updates[field]).text or None
    return updates


def _edit_listing_in_transaction(con, listing_id: str, updates: dict) -> dict:
    row = _require_active_review(con, listing_id)
    updates = _clean_updates(updates)
    old = {k: row[k] for k in EDITABLE}
    assignments = ",".join(f"{k}=?" for k in updates)
    if updates:
        con.execute(
            f"UPDATE listings SET {assignments},updated_at=? WHERE listing_id=?",
            (*updates.values(), now_iso(), listing_id),
        )
    newrow = con.execute("SELECT * FROM listings WHERE listing_id=?", (listing_id,)).fetchone()
    new = {k:newrow[k] for k in EDITABLE}
    changes = {k:{"old":old[k],"new":new[k]} for k in EDITABLE if old[k] != new[k]}
    if changes:
        con.execute(
            "INSERT INTO review_actions(review_action_id,listing_id,action,previous_data,updated_data,created_at) VALUES(?,?,?,?,?,?)",
            (
                str(uuid.uuid4()), listing_id, "edit",
                json.dumps({k:v["old"] for k,v in changes.items()}, ensure_ascii=False),
                json.dumps({k:v["new"] for k,v in changes.items()}, ensure_ascii=False),
                now_iso(),
            ),
        )
    return changes


def edit_listing(listing_id: str, updates: dict) -> dict:
    with transaction() as con:
        return _edit_listing_in_transaction(con, listing_id, updates)


def _cleanup_private_assets(listing_id: str, status: str) -> list[str]:
    c = connect()
    try:
        row = c.execute(
            "SELECT platform,screenshot_path,staging_text_path FROM listings WHERE listing_id=?",
            (listing_id,),
        ).fetchone()
    finally:
        c.close()
    if not row:
        return ["listing disappeared before private-asset cleanup"]

    failures: list[str] = []
    delete_screenshot = settings.screenshot_retention == "review_only" or (settings.screenshot_retention == "delete_after_approval" and status == "approved")
    if delete_screenshot and row["screenshot_path"]:
        p = (settings.root / row["screenshot_path"]).resolve()
        allowed = (settings.data_dir / "private" / "screenshots").resolve()
        try:
            cleanup_dir = p.parent
            expected_dir = (allowed / row["platform"] / listing_id).resolve()
            if cleanup_dir != expected_dir:
                raise OSError(f"screenshot path is not owned by listing {listing_id}: {p}")
            if cleanup_dir.exists():
                shutil.rmtree(cleanup_dir)
        except OSError as exc:
            failures.append(f"screenshot cleanup failed: {exc}")
    if row["staging_text_path"]:
        p = (settings.root / row["staging_text_path"]).resolve()
        allowed = (settings.data_dir / "private" / "staging").resolve()
        try:
            expected = (allowed / f"{listing_id}.json").resolve()
            if p != expected:
                raise OSError(f"staging path is not owned by listing {listing_id}: {p}")
            p.unlink(missing_ok=True)
        except OSError as exc:
            failures.append(f"staging cleanup failed: {exc}")
    return failures


def _raise_cleanup_failures(listing_id: str, status: str, failures: list[str]) -> None:
    if not failures:
        return
    message = "; ".join(failures)
    with transaction() as con:
        con.execute(
            "INSERT INTO warnings(warning_id,listing_id,warning_type,message,created_at) VALUES(?,?,?,?,?)",
            (str(uuid.uuid4()), listing_id, "private_asset_cleanup", message[:2000], now_iso()),
        )
    raise PrivateAssetCleanupError(f"{status} committed, but private asset cleanup failed: {message}")


def _verify_product_images(con, listing_id: str, platform: str) -> None:
    images = con.execute(
        "SELECT * FROM images WHERE listing_id=? ORDER BY source_position",
        (listing_id,),
    ).fetchall()
    if not images:
        raise ValueError("at least one product image is required for approval")
    allowed = (settings.data_dir / "images").resolve()
    expected_directory = (allowed / platform / listing_id).resolve()
    for image in images:
        path = (settings.root / image["storage_path"]).resolve()
        label = image["image_id"]
        if image["platform"] != platform or path.parent != expected_directory:
            raise ValueError(f"product image {label} is not stored under this listing's dataset directory")
        if not path.is_file():
            raise ValueError(f"product image {label} is missing from storage")
        try:
            data = path.read_bytes()
            _fmt, width, height, sha256, phash_int = validate_image_bytes(data)
        except (OSError, ValueError) as exc:
            raise ValueError(f"product image {label} is unreadable or invalid: {exc}") from exc
        if len(data) != image["file_size_bytes"]:
            raise ValueError(f"product image {label} file size does not match its database metadata")
        if sha256 != image["sha256"]:
            raise ValueError(f"product image {label} SHA-256 does not match its database metadata")
        if width != image["width"] or height != image["height"]:
            raise ValueError(f"product image {label} dimensions do not match its database metadata")
        if f"{phash_int:016x}" != image["perceptual_hash"]:
            raise ValueError(f"product image {label} perceptual hash does not match its database metadata")


def _approve_in_transaction(con, listing_id: str) -> None:
    row = _require_active_review(con, listing_id)
    if residual_pii("\n".join(str(row[field] or "") for field in PII_FREE_TEXT_FIELDS)):
        raise ValueError("residual PII check failed")
    for key in ("title", "category", "price", "currency"):
        if row[key] in {None, ""}:
            raise ValueError(f"required field missing: {key}")
    categories = {item["name"] for item in settings.categories()}
    if row["category"] not in categories:
        raise ValueError("category is not in configured category list")
    _verify_product_images(con, listing_id, row["platform"])
    duplicate = con.execute(
        """
        SELECT listing_id FROM listings
        WHERE listing_id<>? AND status='approved'
          AND (
            source_url_hash=? OR
            (marketplace_listing_id IS NOT NULL AND marketplace_listing_id<>''
             AND platform=? AND marketplace_listing_id=?)
          )
        LIMIT 1
        """,
        (listing_id, row["source_url_hash"], row["platform"], row["marketplace_listing_id"]),
    ).fetchone()
    if duplicate:
        raise ValueError(f"source duplicate of approved listing {duplicate['listing_id']}")
    signature = metadata_signature(row["platform"], row["title"], row["description"], row["price"])
    duplicate_metadata = con.execute(
        "SELECT listing_id FROM listings WHERE listing_id<>? AND status='approved' AND metadata_signature=? LIMIT 1",
        (listing_id, signature),
    ).fetchone()
    if duplicate_metadata:
        raise ValueError(f"metadata duplicate of approved listing {duplicate_metadata['listing_id']}")
    duplicate_image = con.execute(
        """
        SELECT approved_images.listing_id
        FROM images current_images
        JOIN images approved_images
          ON approved_images.sha256=current_images.sha256
         AND approved_images.listing_id<>current_images.listing_id
        JOIN listings approved_listings
          ON approved_listings.listing_id=approved_images.listing_id
         AND approved_listings.status='approved'
        WHERE current_images.listing_id=?
        LIMIT 1
        """,
        (listing_id,),
    ).fetchone()
    if duplicate_image:
        raise ValueError(f"exact product image duplicate of approved listing {duplicate_image['listing_id']}")
    approved_at = now_iso()
    con.execute(
        "UPDATE listings SET metadata_signature=?,approved_at=?,updated_at=? WHERE listing_id=?",
        (signature, approved_at, approved_at, listing_id),
    )
    transition(listing_id, "approved", "human approved", con=con)
    duration = _finish_review_session(con, listing_id)
    con.execute(
        "INSERT INTO review_actions(review_action_id,listing_id,action,finished_at,duration_seconds,created_at) VALUES(?,?,?,?,?,?)",
        (str(uuid.uuid4()), listing_id, "approve", approved_at, duration, approved_at),
    )

def approve(listing_id: str) -> None:
    with transaction() as con:
        _approve_in_transaction(con, listing_id)
    _raise_cleanup_failures(listing_id, "approved", _cleanup_private_assets(listing_id, "approved"))


def edit_and_approve(listing_id: str, updates: dict) -> dict:
    """Apply human corrections and approval in one SQLite transaction."""
    with transaction() as con:
        changes = _edit_listing_in_transaction(con, listing_id, updates)
        _approve_in_transaction(con, listing_id)
    _raise_cleanup_failures(listing_id, "approved", _cleanup_private_assets(listing_id, "approved"))
    return changes

def action(listing_id: str, target: str, reason: str | None = None) -> None:
    if target == "approved": return approve(listing_id)
    if target not in {"rejected","skipped","flagged","ready_for_review"}: raise ValueError("unsupported review action")
    with transaction() as con:
        _require_active_review(con, listing_id)
        transition(listing_id, target, reason or f"human {target}", con=con)
        duration = _finish_review_session(con, listing_id)
        if target == "rejected": con.execute("UPDATE listings SET rejected_at=?,updated_at=? WHERE listing_id=?", (now_iso(),now_iso(),listing_id))
        con.execute("INSERT INTO review_actions(review_action_id,listing_id,action,finished_at,duration_seconds,created_at) VALUES(?,?,?,?,?,?)", (str(uuid.uuid4()),listing_id,target,now_iso(),duration,now_iso()))
    if target == "rejected":
        _raise_cleanup_failures(listing_id, "rejected", _cleanup_private_assets(listing_id, "rejected"))
