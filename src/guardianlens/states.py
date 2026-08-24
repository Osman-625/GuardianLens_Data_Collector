from __future__ import annotations
from .db import transaction, now_iso
import uuid

CAPTURED = "captured"
PROCESSING = "processing"
AI_EXTRACTED = "ai_extracted"
VALIDATING = "validating"
READY = "ready_for_review"
ATTENTION = "needs_attention"
UNDER_REVIEW = "under_review"
SKIPPED = "skipped"
FLAGGED = "flagged"
REJECTED = "rejected"
APPROVED = "approved"
FAILURES = {"capture_failed", "processing_failed", "ai_failed", "image_failed", "duplicate_blocked"}

TRANSITIONS = {
    CAPTURED: {PROCESSING, "capture_failed", "duplicate_blocked"},
    PROCESSING: {AI_EXTRACTED, "ai_failed", "image_failed", "processing_failed", CAPTURED},
    AI_EXTRACTED: {VALIDATING, "image_failed", "processing_failed"},
    VALIDATING: {READY, ATTENTION, "duplicate_blocked", "processing_failed"},
    READY: {UNDER_REVIEW},
    ATTENTION: {UNDER_REVIEW, READY},
    UNDER_REVIEW: {READY, ATTENTION, SKIPPED, FLAGGED, REJECTED, APPROVED},
    SKIPPED: {UNDER_REVIEW, READY},
    FLAGGED: {UNDER_REVIEW, READY, ATTENTION},
    "ai_failed": {CAPTURED, PROCESSING, REJECTED},
    "image_failed": {CAPTURED, PROCESSING, REJECTED},
    "processing_failed": {CAPTURED, PROCESSING, REJECTED},
    "duplicate_blocked": {REJECTED},
    REJECTED: set(),
    APPROVED: set(),
}

def can_transition(old: str, new: str) -> bool:
    return new in TRANSITIONS.get(old, set())

def _transition(con, listing_id: str, new_status: str, reason: str | None) -> None:
    row = con.execute("SELECT status FROM listings WHERE listing_id=?", (listing_id,)).fetchone()
    if not row:
        raise KeyError(f"listing not found: {listing_id}")
    old = row["status"]
    if old == new_status:
        return
    if not can_transition(old, new_status):
        raise ValueError(f"invalid state transition: {old} -> {new_status}")
    changed_at = now_iso()
    con.execute(
        "UPDATE listings SET status=?, updated_at=? WHERE listing_id=?",
        (new_status, changed_at, listing_id),
    )
    con.execute(
        "INSERT INTO status_history(history_id,listing_id,from_status,to_status,timestamp,reason) VALUES(?,?,?,?,?,?)",
        (str(uuid.uuid4()), listing_id, old, new_status, changed_at, reason),
    )


def transition(listing_id: str, new_status: str, reason: str | None = None, con=None) -> None:
    if con is not None:
        _transition(con, listing_id, new_status, reason)
        return
    with transaction() as owned:
        _transition(owned, listing_id, new_status, reason)
