from __future__ import annotations
import json
import re
import uuid
from typing import NoReturn
from urllib.parse import unquote, urlsplit
from .config import settings
from .db import transaction, now_iso
from .dedupe import canonicalize_url, url_hash
from .images import save_private_screenshot

ALLOWED_HOSTS = {
    "carousell": ("carousell.com.my", "www.carousell.com.my"),
    "mudah": ("mudah.my", "www.mudah.my"),
}
LISTING_PATHS = {
    "carousell": re.compile(r"^/p/(?P<slug>[^/]+)-(?P<listing_id>[1-9]\d*)/?$", re.IGNORECASE),
    "mudah": re.compile(r"^/(?P<slug>[^/]+)-(?P<listing_id>[1-9]\d*)\.htm$", re.IGNORECASE),
}
INVALID_LISTING_MESSAGE = "Capture rejected: This does not appear to be an individual listing page."
GENERIC_HEADINGS = {
    "browse", "carousell", "categories", "category", "home", "login", "mudah",
    "profile", "search", "search results", "sign in", "sign up",
}

def _reject_invalid_listing() -> NoReturn:
    raise ValueError(INVALID_LISTING_MESSAGE)

def _listing_id_from_url(platform: str, url: str) -> str:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except (TypeError, ValueError):
        _reject_invalid_listing()
    if (
        parsed.scheme.lower() != "https"
        or host not in ALLOWED_HOSTS[platform]
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
    ):
        _reject_invalid_listing()
    path = unquote(parsed.path)
    if "\\" in path or any(ord(ch) < 32 for ch in path):
        _reject_invalid_listing()
    match = LISTING_PATHS[platform].fullmatch(path)
    if not match or not match.group("slug").strip(" .-_"):
        _reject_invalid_listing()
    return match.group("listing_id")

def _has_meaningful_heading(value: str | None) -> bool:
    heading = " ".join((value or "").split()).strip()
    return (
        3 <= len(heading) <= 500
        and heading.casefold() not in GENERIC_HEADINGS
        and any(ch.isalnum() for ch in heading)
    )

def _has_https_image_candidate(urls: list[str]) -> bool:
    for url in urls:
        try:
            parsed = urlsplit(url)
            if (
                parsed.scheme.lower() == "https"
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
            ):
                return True
        except (TypeError, ValueError):
            continue
    return False

def validate_listing_capture(payload) -> str:
    marketplace_listing_id = _listing_id_from_url(payload.platform, payload.source_url)
    supplied_id = (payload.marketplace_listing_id or "").strip()
    if supplied_id and supplied_id != marketplace_listing_id:
        _reject_invalid_listing()
    evidence = payload.listing_evidence
    has_listing_signal = (
        evidence.has_price_signal
        or evidence.has_product_structured_data
        or evidence.has_offer_signal
    )
    if (
        not _has_meaningful_heading(evidence.h1_text)
        or not has_listing_signal
        or not _has_https_image_candidate(payload.image_urls)
    ):
        _reject_invalid_listing()
    return marketplace_listing_id


def _cleanup_capture_file(path, stop_at) -> None:
    if path is None:
        return
    path = path.resolve()
    stop_at = stop_at.resolve()
    if path != stop_at and stop_at not in path.parents:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return
    parent = path.parent
    while parent != stop_at and stop_at in parent.parents:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent

def create_capture(payload) -> dict:
    marketplace_listing_id = validate_listing_capture(payload)
    listing_id = str(uuid.uuid4())
    canonical_url = canonicalize_url(payload.source_url)
    uhash = url_hash(canonical_url)
    screenshot_file = None
    staging_path = None
    staging_temporary = None
    try:
        with transaction() as con:
            existing = con.execute("SELECT listing_id,status FROM listings WHERE source_url_hash=? LIMIT 1", (uhash,)).fetchone()
            if existing:
                return {"duplicate": True, "listing_id": existing["listing_id"], "status": existing["status"]}
            existing = con.execute("SELECT listing_id,status FROM listings WHERE platform=? AND marketplace_listing_id=? LIMIT 1", (payload.platform, marketplace_listing_id)).fetchone()
            if existing:
                return {"duplicate": True, "listing_id": existing["listing_id"], "status": existing["status"]}
            screenshot_path = save_private_screenshot(payload.platform, listing_id, payload.screenshot_data_url)
            screenshot_file = settings.root / screenshot_path
            staging_path = settings.data_dir / "private" / "staging" / f"{listing_id}.json"
            staging_path.parent.mkdir(parents=True, exist_ok=True)
            staging_temporary = staging_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
            # Do not persist raw page text. Seller names/usernames are difficult to reliably scrub with regex.
            # The private screenshot is the review evidence and OpenAI extraction input.
            staging_temporary.write_text(json.dumps({"image_urls": payload.image_urls[:settings.max_product_images]}, ensure_ascii=False), encoding="utf-8")
            staging_temporary.replace(staging_path)
            now = now_iso()
            con.execute("""
                INSERT INTO listings(listing_id,platform,marketplace_listing_id,source_url,source_url_hash,page_title,status,screenshot_path,staging_text_path,captured_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """, (listing_id,payload.platform,marketplace_listing_id,canonical_url,uhash,payload.page_title,"captured",screenshot_path,str(staging_path.relative_to(settings.root)),now,now,now))
            con.execute("INSERT INTO status_history(history_id,listing_id,from_status,to_status,timestamp,reason) VALUES(?,?,?,?,?,?)", (str(uuid.uuid4()),listing_id,None,"captured",now,"manual extension capture"))
    except Exception:
        _cleanup_capture_file(staging_temporary, settings.data_dir / "private" / "staging")
        _cleanup_capture_file(staging_path, settings.data_dir / "private" / "staging")
        _cleanup_capture_file(screenshot_file, settings.data_dir / "private" / "screenshots")
        raise
    return {"duplicate": False, "listing_id": listing_id, "status": "captured"}
