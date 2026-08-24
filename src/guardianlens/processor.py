from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import threading
import uuid

from .config import settings, get_active_provider, provider_model, provider_prompt_version
from .db import connect, transaction, now_iso
from .states import transition
from .extraction_contract import Extractor, normalize_extracted_listing
from .errors import ProviderOperationError, classify_error, format_error_status, redact_secrets
from .pii import scrub_text, residual_pii
from .dedupe import metadata_signature, hamming_hex
from .images import download_image, validate_image_bytes, persist_product_image


REQUIRED_FIELDS = ("title", "category", "price", "currency")
PII_FREE_TEXT_FIELDS = (
    "title",
    "description",
    "condition",
    "location_state",
    "location_city",
    "language",
)


def default_extractor() -> Extractor:
    """Build the provider selected by the dashboard override or AI_PROVIDER."""
    provider = get_active_provider()
    if provider == "anthropic":
        from .anthropic_extractor import AnthropicExtractor
        return AnthropicExtractor()
    if provider == "openrouter":
        from .openrouter_extractor import OpenRouterExtractor
        return OpenRouterExtractor()
    if provider == "gemini":
        from .gemini_extractor import GeminiExtractor
        return GeminiExtractor()
    if provider == "openai":
        from .openai_extractor import OpenAIExtractor
        return OpenAIExtractor()
    raise RuntimeError(f"Unknown active AI provider: {provider!r}")


def add_warning(con, listing_id: str, kind: str, message: str) -> None:
    con.execute(
        "INSERT INTO warnings(warning_id,listing_id,warning_type,message,created_at) VALUES(?,?,?,?,?)",
        (str(uuid.uuid4()), listing_id, kind, message, now_iso()),
    )


def _extractor_identity(extractor: Extractor | None) -> tuple[str, str, str]:
    provider = redact_secrets(str(getattr(extractor, "provider", None) or get_active_provider()))[:100]
    try:
        model = redact_secrets(str(getattr(extractor, "model", None) or provider_model(provider)))[:300]
        prompt_version = redact_secrets(str(
            getattr(extractor, "prompt_version", None) or provider_prompt_version(provider)
        ))[:100]
    except (KeyError, TypeError):
        model = "unknown"
        prompt_version = "unknown"
    return provider, model, prompt_version


def _insert_ai_attempts(con, listing_id: str, attempts: list[dict]) -> None:
    for item in attempts:
        con.execute(
            """INSERT INTO ai_extractions(
                   extraction_id,listing_id,provider,model,prompt_version,
                   structured_output,created_at,status,error
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()),
                listing_id,
                item["provider"],
                item["model"],
                item["prompt_version"],
                item.get("structured_output"),
                item["created_at"],
                item["status"],
                item.get("error"),
            ),
        )


def _wait_for_retry(delay: float, stop_event: threading.Event | None) -> bool:
    """Return True when a stop request interrupts the backoff."""
    if delay <= 0:
        return bool(stop_event and stop_event.is_set())
    if stop_event is not None:
        return stop_event.wait(delay)
    threading.Event().wait(delay)
    return False


def _unlink_product_path(path: Path) -> None:
    allowed = (settings.data_dir / "images").resolve()
    resolved = path.resolve()
    if allowed not in resolved.parents:
        return
    try:
        resolved.unlink(missing_ok=True)
        parent = resolved.parent
        if parent != allowed and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


def _load_listing_inputs(row) -> tuple[dict, list[str], list[str], Path]:
    if not row["screenshot_path"]:
        raise FileNotFoundError("private screenshot missing")
    screenshot = settings.root / row["screenshot_path"]
    if not screenshot.exists():
        raise FileNotFoundError("private screenshot missing")
    staging = {}
    if row["staging_text_path"]:
        staging_path = settings.root / row["staging_text_path"]
        if staging_path.exists():
            staging = json.loads(staging_path.read_text(encoding="utf-8"))
    image_urls_value = staging.get("image_urls") or []
    if not isinstance(image_urls_value, list) or not all(
        isinstance(url, str) for url in image_urls_value
    ):
        raise ValueError("private staging image URLs are malformed")
    categories = [item["name"] for item in settings.categories()]
    image_urls = list(dict.fromkeys(image_urls_value))[: settings.max_product_images]
    return staging, categories, image_urls, screenshot


def _extract_with_retries(
    listing_id: str,
    row,
    screenshot: Path,
    categories: list[str],
    extractor: Extractor | None,
    extractor_factory: Callable[[], Extractor] | None,
    stop_event: threading.Event | None,
) -> tuple[dict, Extractor, list[dict], set[str]]:
    attempts: list[dict] = []
    active_extractor = extractor
    for attempt in range(1, settings.ai_max_attempts + 1):
        try:
            if active_extractor is None:
                active_extractor = extractor_factory() if extractor_factory else default_extractor()
            extracted = normalize_extracted_listing(
                active_extractor.extract(
                    screenshot,
                    row["platform"],
                    row["page_title"],
                    categories,
                )
            )
            break
        except Exception as exc:
            classification = classify_error(exc)
            provider, model, prompt_version = _extractor_identity(active_extractor)
            message = format_error_status(
                classification,
                provider=provider,
                attempt=attempt,
                max_attempts=settings.ai_max_attempts,
            )
            attempts.append(
                {
                    "provider": provider,
                    "model": model,
                    "prompt_version": prompt_version,
                    "structured_output": None,
                    "created_at": now_iso(),
                    "status": "failed",
                    "error": message[:2000],
                }
            )
            if classification.retryable and attempt < settings.ai_max_attempts:
                delay = min(
                    settings.ai_retry_max_seconds,
                    settings.ai_retry_base_seconds * (2 ** (attempt - 1)),
                )
                if not _wait_for_retry(delay, stop_event):
                    continue
            with transaction() as con:
                _insert_ai_attempts(con, listing_id, attempts)
                transition(listing_id, "ai_failed", message[:500], con=con)
            raise ProviderOperationError(classification) from exc
    else:  # pragma: no cover - loop always returns or raises
        raise RuntimeError("AI extraction ended without a result")

    scrubbed_kinds: set[str] = set()
    for field in PII_FREE_TEXT_FIELDS:
        value = extracted.get(field)
        if value is None:
            continue
        scrubbed = scrub_text(str(value))
        extracted[field] = scrubbed.text or None
        scrubbed_kinds.update(scrubbed.kinds)
    return extracted, active_extractor, attempts, scrubbed_kinds


def process_listing(
    listing_id: str,
    extractor: Extractor | None = None,
    run_id: str | None = None,
    *,
    extractor_factory: Callable[[], Extractor] | None = None,
    stop_event: threading.Event | None = None,
) -> dict:
    if extractor is not None and extractor_factory is not None:
        raise ValueError("provide extractor or extractor_factory, not both")
    c = connect()
    row = c.execute("SELECT * FROM listings WHERE listing_id=?", (listing_id,)).fetchone()
    c.close()
    if not row:
        raise KeyError(listing_id)
    if row["status"] not in {"captured", "ai_failed", "image_failed", "processing_failed"}:
        raise ValueError(f"listing {listing_id} is not processable from status {row['status']}")
    if row["status"] != "captured":
        transition(listing_id, "captured", "explicit retry requested")
    with transaction() as con:
        con.execute(
            "UPDATE listings SET processing_run_id=?,updated_at=? WHERE listing_id=?",
            (run_id, now_iso(), listing_id),
        )
        transition(listing_id, "processing", "batch processing started", con=con)

    try:
        _, categories, image_urls, screenshot = _load_listing_inputs(row)
    except Exception as exc:
        transition(
            listing_id,
            "processing_failed",
            "private screenshot, staging, or category configuration is invalid",
        )
        raise RuntimeError("listing preparation failed") from exc

    extracted, active_extractor, attempts, scrubbed_kinds = _extract_with_retries(
        listing_id,
        row,
        screenshot,
        categories,
        extractor,
        extractor_factory,
        stop_event,
    )
    clean_json = json.dumps(extracted, ensure_ascii=False)
    provider, model, prompt_version = _extractor_identity(active_extractor)
    attempts.append(
        {
            "provider": provider,
            "model": model,
            "prompt_version": prompt_version,
            "structured_output": clean_json,
            "created_at": now_iso(),
            "status": "success",
            "error": None,
        }
    )
    with transaction() as con:
        _insert_ai_attempts(con, listing_id, attempts)
        con.execute(
            """UPDATE listings
               SET ai_extracted=1,ai_provider=?,ai_model=?,ai_prompt_version=?,
                   processing_run_id=?,updated_at=?
               WHERE listing_id=?""",
            (provider, model, prompt_version, run_id, now_iso(), listing_id),
        )
        transition(listing_id, "ai_extracted", "AI extraction complete", con=con)

    downloaded: list[tuple[str, bytes]] = []
    skipped_urls: list[str] = []
    for url in image_urls:
        try:
            data = download_image(url)
            validate_image_bytes(data)
            downloaded.append((url, data))
        except Exception:
            skipped_urls.append(url)
    if image_urls and not downloaded:
        transition(
            listing_id,
            "image_failed",
            f"all {len(image_urls)} staged image URLs were unusable",
        )
        raise ValueError("all staged image URLs were unusable")

    transition(listing_id, "validating", "validation started")
    needs_attention = False
    new_paths: list[Path] = []
    old_paths: list[Path] = []
    try:
        with transaction() as con:
            con.execute("DELETE FROM warnings WHERE listing_id=?", (listing_id,))
            signature = metadata_signature(
                row["platform"],
                extracted.get("title"),
                extracted.get("description"),
                extracted.get("price"),
            )
            duplicate = con.execute(
                """SELECT listing_id FROM listings
                   WHERE metadata_signature=? AND listing_id<>? AND status='approved'
                   LIMIT 1""",
                (signature, listing_id),
            ).fetchone()
            if duplicate:
                add_warning(
                    con,
                    listing_id,
                    "duplicate_metadata",
                    f"Exact approved metadata duplicate of {duplicate['listing_id']}",
                )
                con.execute(
                    "UPDATE listings SET metadata_signature=?,updated_at=? WHERE listing_id=?",
                    (signature, now_iso(), listing_id),
                )
                transition(
                    listing_id,
                    "duplicate_blocked",
                    "exact approved metadata duplicate",
                    con=con,
                )
                return {"status": "duplicate_blocked", "listing_id": listing_id}

            con.execute(
                """UPDATE listings
                   SET title=?,description=?,category=?,condition=?,price=?,currency=?,
                       location_state=?,location_city=?,account_age_days=?,seller_rating=?,
                       review_count=?,active_listing_count=?,language=?,metadata_signature=?,
                       processed_at=?,updated_at=?
                   WHERE listing_id=?""",
                (
                    extracted.get("title"),
                    extracted.get("description"),
                    extracted.get("category"),
                    extracted.get("condition"),
                    extracted.get("price"),
                    extracted.get("currency"),
                    extracted.get("location_state"),
                    extracted.get("location_city"),
                    extracted.get("account_age_days"),
                    extracted.get("seller_rating"),
                    extracted.get("review_count"),
                    extracted.get("active_listing_count"),
                    extracted.get("language"),
                    signature,
                    now_iso(),
                    now_iso(),
                    listing_id,
                ),
            )

            if scrubbed_kinds:
                add_warning(
                    con,
                    listing_id,
                    "pii",
                    "PII was automatically scrubbed: " + ", ".join(sorted(scrubbed_kinds)),
                )
                needs_attention = True
            if extracted.get("category") and extracted["category"] not in categories:
                add_warning(
                    con,
                    listing_id,
                    "category",
                    f"Category is not in configured list: {extracted['category']}",
                )
                needs_attention = True
            for key in REQUIRED_FIELDS:
                if extracted.get(key) in {None, ""}:
                    add_warning(con, listing_id, "missing_field", f"Missing required field: {key}")
                    needs_attention = True
            residual = residual_pii(
                "\n".join(str(extracted.get(key) or "") for key in PII_FREE_TEXT_FIELDS)
            )
            if residual:
                add_warning(
                    con,
                    listing_id,
                    "pii",
                    "Residual PII patterns detected: " + ", ".join(residual),
                )
                needs_attention = True
            if not downloaded:
                add_warning(con, listing_id, "image", "No product image candidates were captured")
                needs_attention = True
            if skipped_urls:
                add_warning(
                    con,
                    listing_id,
                    "image_skipped",
                    f"{len(skipped_urls)} of {len(image_urls)} staged image URLs were unusable and skipped",
                )

            old_paths = [
                settings.root / item["storage_path"]
                for item in con.execute(
                    "SELECT storage_path FROM images WHERE listing_id=?", (listing_id,)
                )
            ]
            con.execute("DELETE FROM images WHERE listing_id=?", (listing_id,))

            primary_assigned = False
            for position, (url, data) in enumerate(downloaded, start=1):
                result = persist_product_image(
                    con,
                    listing_id,
                    row["platform"],
                    data,
                    position,
                    url,
                    is_primary=not primary_assigned,
                )
                if result.get("duplicate"):
                    add_warning(
                        con,
                        listing_id,
                        "duplicate_image",
                        f"Exact image duplicate already used by listing {result['duplicate_of']}",
                    )
                    needs_attention = True
                    continue
                primary_assigned = True
                new_paths.append(settings.root / result["path"])
                perceptual_hash = result["perceptual_hash"]
                candidates = con.execute(
                    """SELECT listing_id,perceptual_hash FROM images
                       WHERE listing_id<>? AND perceptual_hash IS NOT NULL
                       ORDER BY listing_id,image_id""",
                    (listing_id,),
                ).fetchall()
                near = next(
                    (
                        item
                        for item in candidates
                        if hamming_hex(perceptual_hash, item["perceptual_hash"]) <= 5
                    ),
                    None,
                )
                if near:
                    add_warning(
                        con,
                        listing_id,
                        "near_duplicate_image",
                        f"Visually similar image to listing {near['listing_id']}",
                    )
                    needs_attention = True

            target = "needs_attention" if needs_attention else "ready_for_review"
            transition(listing_id, target, "validation complete", con=con)
    except Exception:
        for path in new_paths:
            _unlink_product_path(path)
        current = connect()
        try:
            status_row = current.execute(
                "SELECT status FROM listings WHERE listing_id=?", (listing_id,)
            ).fetchone()
        finally:
            current.close()
        if status_row and status_row["status"] == "validating":
            transition(
                listing_id,
                "processing_failed",
                "image persistence or validation failed",
            )
        raise

    for path in old_paths:
        _unlink_product_path(path)
    return {"status": target, "listing_id": listing_id}
