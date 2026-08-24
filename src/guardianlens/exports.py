from __future__ import annotations
import csv
import json
import os
import uuid
from pathlib import Path
from .config import settings
from .db import connect, now_iso

PUBLIC_FIELDS = [
    "listing_id", "platform", "marketplace_listing_id", "source_url", "title", "description",
    "category", "condition", "price", "currency", "location_state", "location_city",
    "account_age_days", "seller_rating", "review_count", "active_listing_count", "language",
    "captured_at", "processed_at", "approved_at", "ai_extracted", "ai_provider", "ai_model",
    "ai_prompt_version", "ai_extraction_timestamp", "human_corrections_json",
    "review_audit_json", "status_history_json",
]


def _csv_safe(value):
    if isinstance(value, str):
        formula_candidate = value.lstrip(" \t\r\n")
        if value.startswith(("\t", "\r", "\n")) or formula_candidate.startswith(("=", "+", "-", "@")):
            return "'" + value
    return value


def _json_value(value: str | None):
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _write_export_pair(csv_path: Path, jsonl_path: Path, rows: list[dict], images: list[dict]) -> None:
    nonce = uuid.uuid4().hex
    csv_tmp = csv_path.with_name(f".{csv_path.name}.{nonce}.tmp")
    jsonl_tmp = jsonl_path.with_name(f".{jsonl_path.name}.{nonce}.tmp")
    replaced: list[Path] = []
    try:
        with csv_tmp.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=PUBLIC_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: _csv_safe(row.get(key)) for key in PUBLIC_FIELDS})
            handle.flush()
            os.fsync(handle.fileno())
        with jsonl_tmp.open("w", encoding="utf-8") as handle:
            for image in images:
                handle.write(json.dumps(image, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(jsonl_tmp, jsonl_path)
        replaced.append(jsonl_path)
        # Publish the primary CSV last. A crash can at worst leave an orphaned
        # companion manifest, never a CSV that points at an unpublished manifest.
        os.replace(csv_tmp, csv_path)
        replaced.append(csv_path)
    except Exception:
        csv_tmp.unlink(missing_ok=True)
        jsonl_tmp.unlink(missing_ok=True)
        for path in replaced:
            path.unlink(missing_ok=True)
        raise

def export_approved() -> tuple[Path, Path]:
    out = settings.data_dir / "exports"
    out.mkdir(parents=True, exist_ok=True)
    stamp = now_iso().replace(":", "-").replace("+", "_") + f"_{uuid.uuid4().hex[:8]}"
    csv_path = out / f"approved_listings_{stamp}.csv"
    jsonl_path = out / f"approved_images_{stamp}.jsonl"
    c = connect()
    try:
        c.execute("BEGIN")
        listing_rows = c.execute(
            """
            SELECT l.*,
                   (SELECT created_at FROM ai_extractions a
                    WHERE a.listing_id=l.listing_id AND a.status='success'
                    ORDER BY a.created_at DESC,a.rowid DESC LIMIT 1) AS ai_extraction_timestamp
            FROM listings l
            WHERE l.status='approved'
            ORDER BY l.approved_at,l.listing_id
            """
        ).fetchall()
        rows: list[dict] = []
        images: list[dict] = []
        for listing in listing_rows:
            listing_id = listing["listing_id"]
            corrections = []
            review_audit = []
            for action in c.execute(
                "SELECT * FROM review_actions WHERE listing_id=? ORDER BY created_at,rowid",
                (listing_id,),
            ):
                review_audit.append({
                    "review_action_id": action["review_action_id"],
                    "action": action["action"],
                    "started_at": action["started_at"],
                    "finished_at": action["finished_at"],
                    "duration_seconds": action["duration_seconds"],
                    "created_at": action["created_at"],
                })
                if action["action"] == "edit":
                    corrections.append({
                        "review_action_id": action["review_action_id"],
                        "previous": _json_value(action["previous_data"]),
                        "updated": _json_value(action["updated_data"]),
                        "changed_at": action["created_at"],
                    })
            history = [
                {
                    "from_status": item["from_status"],
                    "to_status": item["to_status"],
                    "timestamp": item["timestamp"],
                    "reason": item["reason"],
                }
                for item in c.execute(
                    "SELECT * FROM status_history WHERE listing_id=? ORDER BY timestamp,rowid",
                    (listing_id,),
                )
            ]
            exported = {key: listing[key] for key in PUBLIC_FIELDS if key in listing.keys()}
            exported["human_corrections_json"] = json.dumps(corrections, ensure_ascii=False, separators=(",", ":"))
            exported["review_audit_json"] = json.dumps(review_audit, ensure_ascii=False, separators=(",", ":"))
            exported["status_history_json"] = json.dumps(history, ensure_ascii=False, separators=(",", ":"))
            rows.append(exported)
            images.extend(
                dict(item) for item in c.execute(
                    """
                    SELECT image_id,listing_id,platform,storage_path,sha256,perceptual_hash,
                           file_size_bytes,width,height,source_position,is_primary,created_at
                    FROM images WHERE listing_id=? ORDER BY source_position,image_id
                    """,
                    (listing_id,),
                )
            )
        c.rollback()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    _write_export_pair(csv_path, jsonl_path, rows, images)
    return csv_path, jsonl_path
