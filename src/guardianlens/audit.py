from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from .config import settings
from .db import connect, now_iso
from .images import validate_image_bytes
from .pii import residual_pii
from .states import TRANSITIONS, can_transition

STALE_REVIEW_SECONDS = 4 * 60 * 60
STALE_INTERMEDIATE_SECONDS = 60 * 60
INTERMEDIATE_STATES = {"processing", "ai_extracted", "validating"}
ACTIVE_RUN_STATES = {"running", "paused", "stopping"}
PII_FREE_TEXT_FIELDS = ("title", "description", "condition", "location_state", "location_city", "language")
KNOWN_LISTING_STATES = set(TRANSITIONS) | {state for targets in TRANSITIONS.values() for state in targets}


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _age_seconds(value: str | None, now: datetime) -> int | None:
    parsed = _parse_datetime(value)
    return max(0, int((now - parsed).total_seconds())) if parsed else None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError):
        return None

def data_integrity_report() -> dict:
    c = connect()
    findings = []
    checks: dict[str, object] = {}

    def add(issue: str, *, severity: str = "error", **details) -> None:
        findings.append({"severity": severity, "issue": issue, **details})

    try:
        c.execute("BEGIN")
        quick = [row[0] for row in c.execute("PRAGMA quick_check")]
        checks["quick_check"] = quick
        if quick != ["ok"]:
            add("database_quick_check_failed", results=quick)

        foreign_keys = [tuple(row) for row in c.execute("PRAGMA foreign_key_check")]
        checks["foreign_key_violations"] = len(foreign_keys)
        if foreign_keys:
            add("foreign_key_violations", count=len(foreign_keys))

        listings = c.execute("SELECT * FROM listings ORDER BY created_at,listing_id").fetchall()
        by_id = {row["listing_id"]: row for row in listings}
        approved = [row for row in listings if row["status"] == "approved"]
        now = datetime.now(timezone.utc)

        histories: dict[str, list] = defaultdict(list)
        for history in c.execute("SELECT * FROM status_history ORDER BY listing_id,timestamp,rowid"):
            histories[history["listing_id"]].append(history)
        for listing in listings:
            listing_id = listing["listing_id"]
            history = histories.get(listing_id, [])
            if not history:
                add("missing_status_history", listing_id=listing_id)
                continue
            first = history[0]
            if first["from_status"] is not None or first["to_status"] != "captured":
                add(
                    "invalid_initial_status_history",
                    listing_id=listing_id,
                    from_status=first["from_status"],
                    to_status=first["to_status"],
                )
            previous = first["to_status"]
            for entry in history[1:]:
                if entry["from_status"] != previous:
                    add(
                        "broken_status_history_chain",
                        listing_id=listing_id,
                        expected_from=previous,
                        actual_from=entry["from_status"],
                    )
                if entry["from_status"] and not can_transition(entry["from_status"], entry["to_status"]):
                    add(
                        "illegal_status_history_transition",
                        listing_id=listing_id,
                        from_status=entry["from_status"],
                        to_status=entry["to_status"],
                    )
                previous = entry["to_status"]
            if history[-1]["to_status"] != listing["status"]:
                add(
                    "current_status_history_mismatch",
                    listing_id=listing_id,
                    listing_status=listing["status"],
                    history_status=history[-1]["to_status"],
                )

        required_approved = ("title", "category", "price", "currency", "approved_at")
        configured_categories = {item["name"] for item in settings.categories()}
        for listing in approved:
            listing_id = listing["listing_id"]
            if residual_pii("\n".join(str(listing[key] or "") for key in PII_FREE_TEXT_FIELDS)):
                add("residual_pii", listing_id=listing_id)
            missing = [key for key in required_approved if listing[key] in {None, ""}]
            if missing:
                add("approved_missing_required_fields", listing_id=listing_id, fields=missing)
            if listing["category"] not in configured_categories:
                add("approved_invalid_category", listing_id=listing_id, category=listing["category"])
            provenance_missing = [
                field for field in ("ai_provider", "ai_model", "ai_prompt_version")
                if listing[field] in {None, ""}
            ]
            if not listing["ai_extracted"] or provenance_missing:
                add(
                    "approved_missing_ai_provenance",
                    listing_id=listing_id,
                    fields=provenance_missing,
                )
            successful_extraction = c.execute(
                "SELECT 1 FROM ai_extractions WHERE listing_id=? AND status='success' LIMIT 1",
                (listing_id,),
            ).fetchone()
            if not successful_extraction:
                add("approved_without_successful_ai_extraction", listing_id=listing_id)
            approved_action = c.execute(
                "SELECT 1 FROM review_actions WHERE listing_id=? AND action='approve' LIMIT 1",
                (listing_id,),
            ).fetchone()
            if not approved_action:
                add("approved_without_review_action", listing_id=listing_id)
            image_count = c.execute(
                "SELECT COUNT(*) n FROM images WHERE listing_id=?",
                (listing_id,),
            ).fetchone()["n"]
            if image_count < 1:
                add("approved_without_image", listing_id=listing_id)

        for listing in listings:
            if listing["status"] not in KNOWN_LISTING_STATES:
                add("unknown_listing_status", listing_id=listing["listing_id"], status=listing["status"])
            if listing["status"] != "approved" and listing["approved_at"] is not None:
                add("approval_timestamp_status_mismatch", listing_id=listing["listing_id"])
            if listing["status"] != "rejected" and listing["rejected_at"] is not None:
                add("rejection_timestamp_status_mismatch", listing_id=listing["listing_id"])

        duplicate_queries = {
            "duplicate_approved_urls": """
                SELECT source_url_hash,COUNT(*) n FROM listings
                WHERE status='approved' GROUP BY source_url_hash HAVING COUNT(*)>1
            """,
            "duplicate_approved_marketplace_ids": """
                SELECT platform,marketplace_listing_id,COUNT(*) n FROM listings
                WHERE status='approved' AND marketplace_listing_id IS NOT NULL AND marketplace_listing_id<>''
                GROUP BY platform,marketplace_listing_id HAVING COUNT(*)>1
            """,
            "duplicate_approved_metadata": """
                SELECT metadata_signature,COUNT(*) n FROM listings
                WHERE status='approved' AND metadata_signature IS NOT NULL
                GROUP BY metadata_signature HAVING COUNT(*)>1
            """,
            "duplicate_approved_image_sha256": """
                SELECT i.sha256,COUNT(DISTINCT i.listing_id) n
                FROM images i JOIN listings l ON l.listing_id=i.listing_id
                WHERE l.status='approved' GROUP BY i.sha256 HAVING COUNT(DISTINCT i.listing_id)>1
            """,
        }
        for issue, query in duplicate_queries.items():
            groups = c.execute(query).fetchall()
            if groups:
                add(issue, count=len(groups))

        orphan_images = c.execute(
            "SELECT COUNT(*) n FROM images i LEFT JOIN listings l ON l.listing_id=i.listing_id WHERE l.listing_id IS NULL"
        ).fetchone()["n"]
        if orphan_images:
            add("orphan_image_rows", count=orphan_images)

        image_root = (settings.data_dir / "images").resolve()
        db_image_paths: set[Path] = set()
        image_rows = c.execute("SELECT * FROM images ORDER BY image_id").fetchall()
        for image in image_rows:
            path = (settings.root / image["storage_path"]).resolve()
            if not _is_within(path, image_root):
                add("image_path_outside_runtime", image_id=image["image_id"], path=str(path))
                continue
            listing = by_id.get(image["listing_id"])
            expected_directory = (
                image_root / listing["platform"] / image["listing_id"]
            ).resolve() if listing else None
            if (
                not listing
                or image["platform"] != listing["platform"]
                or path.parent != expected_directory
            ):
                add(
                    "image_path_listing_mismatch",
                    image_id=image["image_id"],
                    listing_id=image["listing_id"],
                    path=str(path),
                )
            db_image_paths.add(path)
            if not path.is_file():
                add("missing_product_image_file", image_id=image["image_id"], path=str(path))
                continue
            try:
                data = path.read_bytes()
                _fmt, width, height, actual_sha, actual_phash = validate_image_bytes(data)
            except (OSError, ValueError) as exc:
                add("unreadable_product_image_file", image_id=image["image_id"], reason=str(exc))
                continue
            if len(data) != image["file_size_bytes"]:
                add("product_image_size_mismatch", image_id=image["image_id"])
            if actual_sha != image["sha256"]:
                add("product_image_sha256_mismatch", image_id=image["image_id"])
            if width != image["width"] or height != image["height"]:
                add("product_image_dimension_mismatch", image_id=image["image_id"])
            if f"{actual_phash:016x}" != image["perceptual_hash"]:
                add("product_image_perceptual_hash_mismatch", image_id=image["image_id"])

        if image_root.exists():
            disk_images = {
                path.resolve()
                for path in image_root.rglob("*")
                if path.is_file()
            }
            for path in sorted(disk_images - db_image_paths):
                add("orphan_product_image_path", path=str(path))
            referenced_directories = {path.parent for path in db_image_paths}
            for platform_directory in (path for path in image_root.iterdir() if path.is_dir()):
                for listing_directory in (path for path in platform_directory.iterdir() if path.is_dir()):
                    resolved = listing_directory.resolve()
                    if resolved not in referenced_directories:
                        add("orphan_product_image_directory", path=str(resolved))

        screenshot_root = (settings.data_dir / "private" / "screenshots").resolve()
        staging_root = (settings.data_dir / "private" / "staging").resolve()
        referenced_screenshots: set[Path] = set()
        referenced_staging: set[Path] = set()
        for listing in listings:
            status = listing["status"]
            if listing["screenshot_path"]:
                path = (settings.root / listing["screenshot_path"]).resolve()
                if not _is_within(path, screenshot_root):
                    add("screenshot_path_outside_runtime", listing_id=listing["listing_id"], path=str(path))
                else:
                    referenced_screenshots.add(path)
                    expected_directory = (
                        screenshot_root / listing["platform"] / listing["listing_id"]
                    ).resolve()
                    if path.parent != expected_directory:
                        add(
                            "screenshot_path_listing_mismatch",
                            listing_id=listing["listing_id"],
                            path=str(path),
                        )
                    deleted_by_policy = (
                        settings.screenshot_retention == "review_only" and status in {"approved", "rejected"}
                    ) or (
                        settings.screenshot_retention == "delete_after_approval" and status == "approved"
                    )
                    if deleted_by_policy and path.exists():
                        add("private_screenshot_retained_after_outcome", listing_id=listing["listing_id"])
                    elif not deleted_by_policy and not path.is_file():
                        add("missing_private_screenshot", listing_id=listing["listing_id"])
            if listing["staging_text_path"]:
                path = (settings.root / listing["staging_text_path"]).resolve()
                if not _is_within(path, staging_root):
                    add("staging_path_outside_runtime", listing_id=listing["listing_id"], path=str(path))
                else:
                    referenced_staging.add(path)
                    expected = (staging_root / f"{listing['listing_id']}.json").resolve()
                    if path != expected:
                        add(
                            "staging_path_listing_mismatch",
                            listing_id=listing["listing_id"],
                            path=str(path),
                        )
                    if status in {"approved", "rejected"}:
                        if path.exists():
                            add("private_staging_retained_after_outcome", listing_id=listing["listing_id"])
                    elif not path.is_file():
                        add("missing_private_staging", listing_id=listing["listing_id"])

        if screenshot_root.exists():
            for path in sorted(
                item.resolve() for item in screenshot_root.rglob("*") if item.is_file()
            ):
                if path not in referenced_screenshots:
                    add("orphan_private_screenshot_path", path=str(path))
        if staging_root.exists():
            for path in sorted(
                item.resolve() for item in staging_root.rglob("*") if item.is_file()
            ):
                if path not in referenced_staging:
                    add("orphan_private_staging_path", path=str(path))

        open_sessions: dict[str, list] = defaultdict(list)
        for session in c.execute(
            """
            SELECT review_action_id,listing_id,started_at,finished_at,duration_seconds
            FROM review_actions WHERE action='review_session'
            """
        ):
            started = _parse_datetime(session["started_at"])
            finished = _parse_datetime(session["finished_at"])
            if started is None:
                add("invalid_review_session_start_timestamp", listing_id=session["listing_id"])
            if session["finished_at"] is None:
                open_sessions[session["listing_id"]].append(session)
                listing = by_id.get(session["listing_id"])
                if not listing or listing["status"] != "under_review":
                    add("open_review_session_status_mismatch", listing_id=session["listing_id"])
                if session["duration_seconds"] is not None:
                    add("open_review_session_has_duration", listing_id=session["listing_id"])
                age = _age_seconds(session["started_at"], now)
                if age is not None and age > STALE_REVIEW_SECONDS:
                    add("stale_review_session", listing_id=session["listing_id"], age_seconds=age)
            else:
                if finished is None:
                    add("invalid_review_session_finish_timestamp", listing_id=session["listing_id"])
                try:
                    duration = int(session["duration_seconds"])
                except (TypeError, ValueError):
                    add("invalid_review_session_duration", listing_id=session["listing_id"])
                else:
                    if duration < 0:
                        add("negative_review_session_duration", listing_id=session["listing_id"])
                    if started and finished:
                        elapsed = int((finished - started).total_seconds())
                        if elapsed < 0:
                            add("review_session_finished_before_start", listing_id=session["listing_id"])
                        elif abs(duration - elapsed) > 2:
                            add("review_session_duration_mismatch", listing_id=session["listing_id"])
        for session in c.execute(
            """
            SELECT listing_id,started_at,finished_at,duration_seconds
            FROM review_actions WHERE action='review_session_interrupted'
            """
        ):
            started = _parse_datetime(session["started_at"])
            finished = _parse_datetime(session["finished_at"])
            if started is None:
                add("invalid_interrupted_review_start_timestamp", listing_id=session["listing_id"])
            if finished is None:
                add("invalid_interrupted_review_finish_timestamp", listing_id=session["listing_id"])
            if session["duration_seconds"] is not None:
                add("interrupted_review_has_duration", listing_id=session["listing_id"])
            if started and finished and finished < started:
                add("interrupted_review_finished_before_start", listing_id=session["listing_id"])
        for listing in listings:
            if listing["status"] == "under_review" and len(open_sessions.get(listing["listing_id"], [])) != 1:
                add(
                    "under_review_session_count_mismatch",
                    listing_id=listing["listing_id"],
                    open_sessions=len(open_sessions.get(listing["listing_id"], [])),
                )
            if listing["status"] in INTERMEDIATE_STATES:
                age = _age_seconds(listing["updated_at"], now)
                if age is None:
                    add("invalid_intermediate_state_timestamp", listing_id=listing["listing_id"])
                elif age > STALE_INTERMEDIATE_SECONDS:
                    add(
                        "stale_intermediate_state",
                        listing_id=listing["listing_id"],
                        status=listing["status"],
                        age_seconds=age,
                    )

        unresolved_cleanup = c.execute(
            "SELECT listing_id,message FROM warnings WHERE warning_type='private_asset_cleanup' AND resolved=0"
        ).fetchall()
        for warning in unresolved_cleanup:
            add(
                "unresolved_private_asset_cleanup",
                listing_id=warning["listing_id"],
                message=warning["message"],
            )

        counter_names = (
            "attempted", "processed", "successful", "duplicates", "ai_failures",
            "image_failures", "privacy_warnings", "needs_attention", "other_failures", "failed",
        )
        run_rows = c.execute("SELECT * FROM processing_runs ORDER BY started_at").fetchall()
        run_ids = {run["run_id"] for run in run_rows}
        for listing in listings:
            if listing["processing_run_id"] and listing["processing_run_id"] not in run_ids:
                add(
                    "listing_processing_run_missing",
                    listing_id=listing["listing_id"],
                    run_id=listing["processing_run_id"],
                )
        for run in run_rows:
            counters: dict[str, int] = {}
            invalid_counters = []
            for name in counter_names:
                try:
                    counters[name] = int(run[name])
                except (TypeError, ValueError):
                    invalid_counters.append(name)
            if invalid_counters:
                add(
                    "invalid_processing_run_counter",
                    run_id=run["run_id"],
                    fields=invalid_counters,
                )
            else:
                if any(value < 0 for value in counters.values()):
                    add("negative_processing_run_counter", run_id=run["run_id"])
                if counters["processed"] != counters["attempted"]:
                    add("processing_run_processed_mismatch", run_id=run["run_id"])
                classified = sum(counters[name] for name in ("successful", "duplicates", "ai_failures", "image_failures", "other_failures"))
                if classified != counters["attempted"]:
                    add(
                        "processing_run_classification_mismatch",
                        run_id=run["run_id"],
                        attempted=counters["attempted"],
                        classified=classified,
                    )
                expected_failed = sum(counters[name] for name in ("ai_failures", "image_failures", "other_failures"))
                if counters["failed"] != expected_failed:
                    add("processing_run_failed_counter_mismatch", run_id=run["run_id"])
                if counters["needs_attention"] > counters["successful"]:
                    add("processing_run_attention_counter_mismatch", run_id=run["run_id"])
                if counters["privacy_warnings"] > counters["needs_attention"]:
                    add("processing_run_privacy_counter_mismatch", run_id=run["run_id"])
            if run["status"] not in ACTIVE_RUN_STATES and (run["finished_at"] is None or run["duration_seconds"] is None):
                add("finished_processing_run_missing_timing", run_id=run["run_id"], status=run["status"])
            if run["status"] in ACTIVE_RUN_STATES and (run["finished_at"] is not None or run["duration_seconds"] is not None):
                add("active_processing_run_has_finished_timing", run_id=run["run_id"], status=run["status"])
            started = _parse_datetime(run["started_at"])
            finished = _parse_datetime(run["finished_at"])
            if started is None:
                add("invalid_processing_run_start_timestamp", run_id=run["run_id"])
            if run["finished_at"] is not None and finished is None:
                add("invalid_processing_run_finish_timestamp", run_id=run["run_id"])
            duration: int | None = None
            if run["duration_seconds"] is not None:
                try:
                    duration = int(run["duration_seconds"])
                except (TypeError, ValueError):
                    add("invalid_processing_run_duration", run_id=run["run_id"])
                else:
                    if duration < 0:
                        add("negative_processing_run_duration", run_id=run["run_id"])
            if started and finished:
                elapsed = int((finished - started).total_seconds())
                if elapsed < 0:
                    add("processing_run_finished_before_start", run_id=run["run_id"])
                elif duration is not None and abs(duration - elapsed) > 2:
                    add(
                        "processing_run_duration_mismatch",
                        run_id=run["run_id"],
                        stored=duration,
                        elapsed=elapsed,
                    )

        soft_root = (settings.base_data_dir / "soft_test").resolve()
        db_path = settings.db_path.resolve()
        data_path = settings.data_dir.resolve()
        if settings.mode == "soft_test":
            if not _is_within(db_path, soft_root):
                add("soft_test_database_outside_soft_test_root", path=str(db_path))
            if not _is_within(data_path, soft_root):
                add("soft_test_runtime_outside_soft_test_root", path=str(data_path))
        else:
            if _is_within(db_path, soft_root):
                add("production_database_inside_soft_test_root", path=str(db_path))
            if _is_within(data_path, soft_root):
                add("production_runtime_inside_soft_test_root", path=str(data_path))

        checks["listing_count"] = len(listings)
        checks["approved_count"] = len(approved)
        checks["image_row_count"] = len(image_rows)
        return {
            "generated_at": now_iso(),
            "approved": len(approved),
            "checks": checks,
            "findings": findings,
            "pass": not any(item["severity"] == "error" for item in findings),
        }
    finally:
        c.close()

def security_report() -> dict:
    findings = []
    if settings.host not in {"127.0.0.1","localhost","::1"}: findings.append({"severity":"error","issue":"non_local_bind"})
    provider_keys = {
        "openai": settings.openai_api_key,
        "anthropic": settings.anthropic_api_key,
        "openrouter": settings.openrouter_api_key,
        "gemini": settings.gemini_api_key,
    }
    for provider_name, needle in provider_keys.items():
        if not needle:
            continue
        for base in [settings.root / "extension", settings.data_dir / "exports", settings.root / "logs"]:
            if not base.exists(): continue
            for p in base.rglob("*"):
                if p.is_file() and p.stat().st_size < 5_000_000:
                    try:
                        if needle in p.read_text(encoding="utf-8", errors="ignore"):
                            findings.append({"severity":"error","issue":"api_key_leak","provider":provider_name,"path":str(p.relative_to(settings.root))})
                    except OSError: pass
    manifest = json.loads((settings.root / "extension" / "manifest.json").read_text(encoding="utf-8"))
    if "<all_urls>" in manifest.get("host_permissions", []): findings.append({"severity":"error","issue":"extension_all_urls_permission"})
    return {"generated_at":now_iso(),"findings":findings,"pass":not any(x["severity"]=="error" for x in findings)}
