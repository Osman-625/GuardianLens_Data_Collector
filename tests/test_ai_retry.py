from __future__ import annotations

import time
import json

import pytest

from conftest import data_url, image_bytes
from guardianlens.batch import BatchManager
from guardianlens.capture import create_capture
from guardianlens.config import settings
from guardianlens.db import connect
from guardianlens.errors import ProviderConfigurationError, ProviderOperationError
from guardianlens.models import CaptureRequest
from guardianlens.openai_extractor import MockExtractor
from guardianlens.processor import process_listing
from guardianlens.review import get_listing


def capture(suffix: str):
    result = create_capture(
        CaptureRequest(
            platform="carousell",
            source_url=f"https://www.carousell.com.my/p/retry-fixture-{suffix}/",
            page_title="Retry fixture",
            screenshot_data_url=data_url(image_bytes()),
            image_urls=["https://images.example.test/retry-fixture.jpg"],
            listing_evidence={"h1_text": "Retry fixture", "has_price_signal": True},
        )
    )
    con = connect()
    row = con.execute(
        "SELECT staging_text_path FROM listings WHERE listing_id=?", (result["listing_id"],)
    ).fetchone()
    con.close()
    (settings.root / row["staging_text_path"]).write_text(
        json.dumps({"image_urls": []}), encoding="utf-8"
    )
    return result


def wait_for_batch(manager: BatchManager):
    for _ in range(300):
        status = manager.status()
        if not status["thread_alive"]:
            return status["latest_run"]
        time.sleep(0.01)
    raise AssertionError("batch did not finish")


def test_transient_ai_failure_retries_bounded_and_commits_attempts_atomically(isolated):
    class FlakyExtractor(MockExtractor):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def extract(self, *args, **kwargs):
            self.calls += 1
            if self.calls < 3:
                raise TimeoutError("temporary timeout with api_key=sk-secretfixture123456")
            return super().extract(*args, **kwargs)

    record = capture("100001")
    extractor = FlakyExtractor()
    original_base = settings.ai_retry_base_seconds
    original_max = settings.ai_retry_max_seconds
    object.__setattr__(settings, "ai_retry_base_seconds", 0)
    object.__setattr__(settings, "ai_retry_max_seconds", 0)
    try:
        result = process_listing(record["listing_id"], extractor)
    finally:
        object.__setattr__(settings, "ai_retry_base_seconds", original_base)
        object.__setattr__(settings, "ai_retry_max_seconds", original_max)

    assert result["status"] == "needs_attention"
    assert extractor.calls == 3
    con = connect()
    attempts = con.execute(
        "SELECT status,error FROM ai_extractions WHERE listing_id=? ORDER BY created_at,rowid",
        (record["listing_id"],),
    ).fetchall()
    con.close()
    assert [row["status"] for row in attempts] == ["failed", "failed", "success"]
    assert all("secretfixture" not in (row["error"] or "") for row in attempts)


def test_invalid_response_is_nonretryable(isolated):
    class InvalidExtractor:
        provider = "fixture"
        model = "invalid"
        prompt_version = "v1"
        calls = 0

        def extract(self, *args, **kwargs):
            self.calls += 1
            return {"title": "x", "confidence": 0.9}

    record = capture("100002")
    extractor = InvalidExtractor()
    with pytest.raises(ProviderOperationError):
        process_listing(record["listing_id"], extractor)
    assert extractor.calls == 1
    assert get_listing(record["listing_id"])["status"] == "ai_failed"


def test_extractor_initialization_failure_is_recorded_on_listing(isolated):
    record = capture("100003")

    def broken_factory():
        raise ProviderConfigurationError("api_key=sk-secretfixture123456 SDK is not installed")

    with pytest.raises(ProviderOperationError):
        process_listing(record["listing_id"], extractor_factory=broken_factory)
    item = get_listing(record["listing_id"])
    assert item["status"] == "ai_failed"
    con = connect()
    attempt = con.execute(
        "SELECT error FROM ai_extractions WHERE listing_id=?", (record["listing_id"],)
    ).fetchone()
    con.close()
    assert attempt is not None
    assert "secretfixture" not in attempt["error"]
    assert "Retryable: No" in attempt["error"]


def test_permanent_provider_failure_halts_after_one_and_failed_is_total(isolated):
    first = capture("100004")
    second = capture("100005")

    def broken_factory():
        raise ProviderConfigurationError("invalid provider configuration")

    manager = BatchManager()
    manager.start(None, None, broken_factory)
    run = wait_for_batch(manager)
    assert run["status"] == "halted_config_error"
    assert run["attempted"] == 1
    assert run["ai_failures"] == 1
    assert run["failed"] == 1
    assert get_listing(first["listing_id"])["status"] == "ai_failed"
    assert get_listing(second["listing_id"])["status"] == "captured"

    manager.start(None, None, lambda: MockExtractor())
    second_run = wait_for_batch(manager)
    assert second_run["attempted"] == 1
    assert get_listing(first["listing_id"])["status"] == "ai_failed"
    assert get_listing(second["listing_id"])["status"] == "needs_attention"
