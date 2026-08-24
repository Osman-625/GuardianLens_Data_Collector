import pytest

from guardianlens.capture import create_capture
from guardianlens.config import settings
from guardianlens.db import connect, transaction
from guardianlens.images import persist_product_image
from guardianlens.models import CaptureRequest
from guardianlens.openai_extractor import MockExtractor
from guardianlens.processor import process_listing
from guardianlens.review import get_listing
from conftest import data_url, image_bytes


def capture_with_images(suffix: str, count: int = 2) -> dict:
    return create_capture(
        CaptureRequest(
            platform="carousell",
            source_url=f"https://www.carousell.com.my/p/image-atomic-{suffix}/",
            page_title="Image atomicity fixture",
            screenshot_data_url=data_url(image_bytes()),
            image_urls=[f"https://images.example.test/{suffix}-{index}.jpg" for index in range(count)],
            listing_evidence={"h1_text": "Image atomicity fixture", "has_price_signal": True},
        )
    )


def test_image_transaction_failure_preserves_previous_commit_and_removes_new_files(
    isolated, monkeypatch
):
    import guardianlens.processor as processor

    captured = capture_with_images("910001")
    listing_id = captured["listing_id"]
    with transaction() as con:
        previous = persist_product_image(
            con, listing_id, "carousell", image_bytes(value=40), 1
        )
    previous_path = isolated / previous["path"]
    assert previous_path.exists()

    values = iter([image_bytes(value=90), image_bytes(value=170)])
    monkeypatch.setattr(processor, "download_image", lambda url: next(values))
    original_persist = processor.persist_product_image
    calls = 0

    def fail_second_persist(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected image persistence failure")
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(processor, "persist_product_image", fail_second_persist)
    with pytest.raises(OSError, match="injected"):
        process_listing(listing_id, MockExtractor())

    con = connect()
    rows = con.execute(
        "SELECT storage_path FROM images WHERE listing_id=?", (listing_id,)
    ).fetchall()
    con.close()
    assert [row["storage_path"] for row in rows] == [previous["path"]]
    assert previous_path.exists()
    product_folder = settings.data_dir / "images" / "carousell" / listing_id
    assert [path for path in product_folder.iterdir() if path.is_file()] == [previous_path]
    assert get_listing(listing_id)["status"] == "processing_failed"


def test_exact_duplicate_first_candidate_falls_back_to_next_image_as_primary(
    isolated, monkeypatch
):
    import guardianlens.processor as processor

    duplicate_bytes = image_bytes(value=55)
    seed_capture = capture_with_images("920001", 1)
    with transaction() as con:
        persist_product_image(
            con, seed_capture["listing_id"], "carousell", duplicate_bytes, 1
        )

    captured = capture_with_images("920002", 2)
    unique_bytes = image_bytes(value=155)
    payloads = iter([duplicate_bytes, unique_bytes])
    monkeypatch.setattr(processor, "download_image", lambda url: next(payloads))
    result = process_listing(captured["listing_id"], MockExtractor())

    con = connect()
    rows = con.execute(
        "SELECT source_position,is_primary FROM images WHERE listing_id=? ORDER BY source_position",
        (captured["listing_id"],),
    ).fetchall()
    con.close()
    assert result["status"] == "needs_attention"
    assert [(row["source_position"], row["is_primary"]) for row in rows] == [(2, 1)]
