from __future__ import annotations
import math
from .config import settings
from .db import connect

REVIEW_PENDING = ("captured","processing","ai_extracted","validating","ready_for_review","needs_attention","under_review","skipped","flagged")


def _category_targets(categories: list[dict], total_target: int) -> dict[str, int]:
    """Largest-remainder allocation keeps category targets equal to total_target."""
    percentage_total = sum(float(item["target_pct"]) for item in categories)
    if percentage_total <= 0:
        raise ValueError("category target percentages must have a positive total")
    raw = [total_target * float(item["target_pct"]) / percentage_total for item in categories]
    allocated = [math.floor(value) for value in raw]
    remainder = total_target - sum(allocated)
    order = sorted(range(len(categories)), key=lambda index: (raw[index] - allocated[index], -index), reverse=True)
    if remainder > 0:
        for offset in range(remainder):
            allocated[order[offset % len(order)]] += 1
    elif remainder < 0:
        removal_order = sorted(order, key=lambda index: (raw[index] - allocated[index], index))
        for offset in range(-remainder):
            index = removal_order[offset % len(removal_order)]
            if allocated[index] < 1:
                raise ValueError("category target allocation produced a negative target")
            allocated[index] -= 1
    return {item["name"]: allocated[index] for index, item in enumerate(categories)}


def planner_snapshot(con=None) -> dict:
    owns = con is None
    c = con or connect()
    snapshot_started = False
    try:
        if not c.in_transaction:
            c.execute("BEGIN")
            snapshot_started = True
        cfg = settings.platform_targets()
        total_target = int(cfg["total_target"])
        if total_target < 1:
            raise ValueError("planner total_target must be positive")
        if not cfg.get("platforms"):
            raise ValueError("planner requires at least one configured platform")
        platform_targets = [int(item["target"]) for item in cfg["platforms"].values()]
        if any(target < 0 for target in platform_targets):
            raise ValueError("configured platform targets cannot be negative")
        configured_platform_total = sum(platform_targets)
        if configured_platform_total != total_target:
            raise ValueError("configured platform targets must sum to total_target")
        platform_ratios = [float(item["ratio"]) for item in cfg["platforms"].values()]
        if any(ratio < 0 for ratio in platform_ratios) or abs(sum(platform_ratios) - 1.0) > 0.00001:
            raise ValueError("configured platform ratios must be non-negative and sum to 1")

        status_counts = {row["status"]: row["n"] for row in c.execute("SELECT status,COUNT(*) n FROM listings GROUP BY status")}
        approved_total = c.execute("SELECT COUNT(*) n FROM listings WHERE status='approved'").fetchone()["n"]
        platform_rows = {r["platform"]: r["n"] for r in c.execute("SELECT platform, COUNT(*) n FROM listings WHERE status='approved' GROUP BY platform")}
        pending_platform = {r["platform"]: r["n"] for r in c.execute("SELECT platform, COUNT(*) n FROM listings WHERE status IN (%s) GROUP BY platform" % ",".join("?" * len(REVIEW_PENDING)), REVIEW_PENDING)}
        platforms = {}
        for name, p in cfg["platforms"].items():
            target = int(p["target"])
            approved = int(platform_rows.get(name, 0))
            pending = int(pending_platform.get(name, 0))
            platforms[name] = {"target": target, "approved": approved, "pending": pending, "remaining": max(0, target-approved-pending), "ratio": p["ratio"]}

        cats = settings.categories()
        if not cats:
            raise ValueError("planner requires at least one configured category")
        category_names = [item["name"] for item in cats]
        if len(set(category_names)) != len(category_names):
            raise ValueError("configured category names must be unique")
        if any(float(item["target_pct"]) < 0 for item in cats):
            raise ValueError("configured category target percentages cannot be negative")
        category_pct_total = sum(float(item["target_pct"]) for item in cats)
        if abs(category_pct_total - 100.0) > 0.00001:
            raise ValueError("configured category target percentages must sum to 100")
        category_targets = _category_targets(cats, total_target)
        approved_cats = {r["category"]: r["n"] for r in c.execute("SELECT category, COUNT(*) n FROM listings WHERE status='approved' GROUP BY category")}
        pending_cats = {r["category"]: r["n"] for r in c.execute("SELECT category, COUNT(*) n FROM listings WHERE status IN (%s) AND category IS NOT NULL AND TRIM(category)<>'' GROUP BY category" % ",".join("?" * len(REVIEW_PENDING)), REVIEW_PENDING)}
        unassigned_category_pending = c.execute(
            "SELECT COUNT(*) n FROM listings WHERE status IN (%s) AND (category IS NULL OR TRIM(category)='')" % ",".join("?" * len(REVIEW_PENDING)),
            REVIEW_PENDING,
        ).fetchone()["n"]
        categories = []
        for item in cats:
            target = category_targets[item["name"]]
            approved = int(approved_cats.get(item["name"], 0))
            pending = int(pending_cats.get(item["name"], 0))
            remaining = max(0, target-approved-pending)
            categories.append({**item, "target": target, "approved": approved, "pending": pending, "remaining": remaining})
        categories.sort(key=lambda x: (x["remaining"], x["target"]), reverse=True)

        platform_budget = {name: values["remaining"] for name, values in platforms.items() if values["remaining"] > 0}
        recommendations = []
        for category in (item for item in categories if item["remaining"] > 0):
            category_budget = category["remaining"]
            while platform_budget and category_budget > 0 and len(recommendations) < 3:
                platform = max(platform_budget, key=lambda name: (platform_budget[name], name))
                actionable = min(category_budget, platform_budget[platform])
                recommendations.append({
                    "platform": platform,
                    "category": category["name"],
                    "remaining": actionable,
                    "platform_remaining": platform_budget[platform],
                    "category_remaining": category_budget,
                })
                category_budget -= actionable
                platform_budget[platform] -= actionable
                if platform_budget[platform] <= 0:
                    del platform_budget[platform]
            if len(recommendations) >= 3:
                break

        configured_categories = {item["name"] for item in cats}
        return {
            "total_target": total_target,
            "approved_total": approved_total,
            "pending_total": sum(int(status_counts.get(status, 0)) for status in REVIEW_PENDING),
            "unassigned_category_pending": int(unassigned_category_pending),
            "unconfigured_category_approved": sum(int(count) for name, count in approved_cats.items() if name not in configured_categories),
            "status_counts": status_counts,
            "platforms": platforms,
            "categories": categories,
            "recommendations": recommendations,
        }
    finally:
        if snapshot_started:
            c.rollback()
        if owns:
            c.close()
