from __future__ import annotations

import math
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import settings
from .db import connect
from .planner import planner_snapshot


LISTING_STATUSES = (
    "captured",
    "processing",
    "ai_extracted",
    "validating",
    "needs_attention",
    "ready_for_review",
    "under_review",
    "approved",
    "rejected",
    "skipped",
    "flagged",
    "capture_failed",
    "processing_failed",
    "ai_failed",
    "image_failed",
    "duplicate_blocked",
)

REVIEW_QUEUE_STATUSES = (
    "ready_for_review",
    "needs_attention",
    "under_review",
    "skipped",
    "flagged",
)

OPERATIONAL_QUEUE_STATUSES = (
    "captured",
    "processing",
    "ai_extracted",
    "validating",
    "capture_failed",
    "processing_failed",
    "ai_failed",
    "image_failed",
    "duplicate_blocked",
)

RETRYABLE_FAILURE_STATUSES = ("processing_failed", "ai_failed", "image_failed")


def _placeholders(values: tuple[str, ...]) -> str:
    return ",".join("?" for _ in values)


def _operator_day_utc_bounds(now: datetime | None = None) -> tuple[str, str]:
    zone = ZoneInfo(settings.operator_timezone)
    local_now = now.astimezone(zone) if now is not None else datetime.now(zone)
    start_local = datetime.combine(local_now.date(), time.min, tzinfo=zone)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc).isoformat(),
        end_local.astimezone(timezone.utc).isoformat(),
    )


def dashboard_snapshot() -> dict:
    """Return one transactionally consistent, SQL-backed dashboard snapshot."""
    con = connect()
    try:
        con.execute("BEGIN")
        observed = {
            row["status"]: int(row["n"])
            for row in con.execute("SELECT status, COUNT(*) AS n FROM listings GROUP BY status")
        }
        counts = {status: observed.get(status, 0) for status in LISTING_STATUSES}
        for status, value in observed.items():
            counts.setdefault(status, value)

        activity = [
            dict(row)
            for row in con.execute(
                """
                SELECT h.history_id, h.listing_id, l.platform, l.title, l.page_title,
                       h.from_status, h.to_status, h.timestamp, h.reason
                FROM status_history AS h
                JOIN listings AS l ON l.listing_id = h.listing_id
                ORDER BY h.timestamp DESC
                LIMIT 20
                """
            )
        ]
        queue = [
            dict(row)
            for row in con.execute(
                f"""
                SELECT l.listing_id, l.platform, l.title, l.page_title, l.status,
                       l.captured_at, l.updated_at,
                       (SELECT h.reason FROM status_history AS h
                        WHERE h.listing_id=l.listing_id
                        ORDER BY h.timestamp DESC LIMIT 1) AS status_reason,
                       (SELECT a.provider FROM ai_extractions AS a
                        WHERE a.listing_id=l.listing_id
                        ORDER BY a.created_at DESC LIMIT 1) AS ai_provider,
                       (SELECT a.error FROM ai_extractions AS a
                        WHERE a.listing_id=l.listing_id AND a.status='failed'
                        ORDER BY a.created_at DESC LIMIT 1) AS ai_error
                FROM listings AS l
                WHERE l.status IN ({_placeholders(OPERATIONAL_QUEUE_STATUSES)})
                ORDER BY CASE l.status
                    WHEN 'processing' THEN 0
                    WHEN 'ai_extracted' THEN 1
                    WHEN 'validating' THEN 2
                    WHEN 'captured' THEN 3
                    WHEN 'needs_attention' THEN 4
                    ELSE 5 END,
                    l.captured_at
                LIMIT 100
                """,
                OPERATIONAL_QUEUE_STATUSES,
            )
        ]
        review_stats_row = con.execute(
            """
            SELECT COUNT(*) AS completed_sessions,
                   COALESCE(AVG(duration_seconds), 0) AS avg_sec
            FROM review_actions
            WHERE action='review_session' AND finished_at IS NOT NULL
              AND duration_seconds IS NOT NULL
            """
        ).fetchone()
        reviewed_day_start, reviewed_day_end = _operator_day_utc_bounds()
        reviewed_today = con.execute(
            """
            SELECT COUNT(DISTINCT listing_id) AS n
            FROM review_actions
            WHERE action IN ('approve','approved','rejected','skipped','flagged')
              AND finished_at IS NOT NULL
              AND finished_at>=? AND finished_at<?
            """,
            (reviewed_day_start, reviewed_day_end),
        ).fetchone()["n"]
        open_review_count = sum(counts.get(status, 0) for status in REVIEW_QUEUE_STATUSES)
        average_seconds = float(review_stats_row["avg_sec"] or 0)
        review_stats = {
            "completed_sessions": int(review_stats_row["completed_sessions"]),
            "reviewed_today": int(reviewed_today),
            "average_seconds": average_seconds,
            "open_count": int(open_review_count),
            "estimated_remaining_seconds": int(open_review_count * average_seconds),
            "operator_timezone": settings.operator_timezone,
        }
        plan = planner_snapshot(con)
        con.commit()
        return {
            "counts": counts,
            "activity": activity,
            "operational_queue": queue,
            "review_stats": review_stats,
            "plan": plan,
        }
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def review_queue_snapshot(limit: int = 100) -> dict:
    limit = max(1, min(100, int(limit)))
    con = connect()
    try:
        con.execute("BEGIN")
        total = int(
            con.execute(
                f"SELECT COUNT(*) AS n FROM listings WHERE status IN ({_placeholders(REVIEW_QUEUE_STATUSES)})",
                REVIEW_QUEUE_STATUSES,
            ).fetchone()["n"]
        )
        rows = [
            dict(row)
            for row in con.execute(
                f"""
                SELECT listing_id, platform, title, page_title, category, price,
                       currency, status, captured_at, updated_at
                FROM listings
                WHERE status IN ({_placeholders(REVIEW_QUEUE_STATUSES)})
                ORDER BY CASE status
                    WHEN 'needs_attention' THEN 0
                    WHEN 'flagged' THEN 1
                    WHEN 'under_review' THEN 2
                    WHEN 'ready_for_review' THEN 3
                    ELSE 4 END,
                    captured_at
                LIMIT ?
                """,
                (*REVIEW_QUEUE_STATUSES, limit),
            )
        ]
        con.commit()
        return {"rows": rows, "total": total, "limit": limit}
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def review_queue_rows() -> list[dict]:
    """Compatibility helper for callers that only need the bounded visible rows."""
    return review_queue_snapshot()["rows"]


def records_page(
    *,
    status: str | None = None,
    platform: str | None = None,
    query: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """Return a searchable lifecycle index without exposing private evidence."""
    if status and status not in LISTING_STATUSES:
        raise ValueError("unknown listing status")
    if platform and platform not in {"carousell", "mudah"}:
        raise ValueError("unknown platform")
    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))
    query = " ".join((query or "").split())[:200]

    clauses: list[str] = []
    parameters: list[object] = []
    if status:
        clauses.append("status=?")
        parameters.append(status)
    if platform:
        clauses.append("platform=?")
        parameters.append(platform)
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        clauses.append(
            """(
                listing_id LIKE ? ESCAPE '\\' OR
                COALESCE(marketplace_listing_id,'') LIKE ? ESCAPE '\\' OR
                COALESCE(title,'') LIKE ? ESCAPE '\\' OR
                COALESCE(page_title,'') LIKE ? ESCAPE '\\'
            )"""
        )
        parameters.extend([pattern] * 4)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    con = connect()
    try:
        con.execute("BEGIN")
        total = int(
            con.execute(f"SELECT COUNT(*) AS n FROM listings {where}", parameters).fetchone()["n"]
        )
        page_count = max(1, math.ceil(total / page_size))
        page = min(page, page_count)
        rows = [
            dict(row)
            for row in con.execute(
                f"""
                SELECT listing_id,marketplace_listing_id,platform,title,page_title,
                       category,price,currency,status,captured_at,updated_at
                FROM listings
                {where}
                ORDER BY updated_at DESC,captured_at DESC,listing_id
                LIMIT ? OFFSET ?
                """,
                (*parameters, page_size, (page - 1) * page_size),
            )
        ]
        con.commit()
        return {
            "rows": rows,
            "total": total,
            "page": page,
            "page_count": page_count,
            "page_size": page_size,
            "filters": {"status": status or "", "platform": platform or "", "query": query},
        }
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
