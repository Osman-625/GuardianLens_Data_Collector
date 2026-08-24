"""Run a two-record synthetic pipeline entirely inside an OS temporary directory."""
from __future__ import annotations

import base64
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image

from guardianlens.capture import create_capture
from guardianlens.config import settings
from guardianlens.db import connect, init_db
from guardianlens.models import CaptureRequest
from guardianlens.openai_extractor import MockExtractor
import guardianlens.processor as processor
from guardianlens.review import approve, begin_review


def image_bytes(value: int) -> bytes:
    image = Image.new("RGB", (360, 280), (value, value, value))
    output = io.BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


def data_url(data: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


def run_mock_integration() -> list[dict]:
    original_root, original_mode = settings.root, settings.mode
    original_downloader = processor.download_image
    try:
        with tempfile.TemporaryDirectory(prefix="guardianlens-mock-integration-") as temporary:
            root = Path(temporary) / "project"
            (root / "src/guardianlens").mkdir(parents=True)
            shutil.copy(ROOT / "src/guardianlens/schema.sql", root / "src/guardianlens/schema.sql")
            shutil.copytree(ROOT / "config", root / "config")
            object.__setattr__(settings, "root", root)
            object.__setattr__(settings, "mode", "soft_test")
            init_db()
            processor.download_image = lambda url: image_bytes(110 if "car" in url else 140)
            items = [
                CaptureRequest(
                    platform="carousell",
                    source_url="https://www.carousell.com.my/p/guardianlens-soft-100001/",
                    page_title="Soft Carousell",
                    screenshot_data_url=data_url(image_bytes(180)),
                    image_urls=["https://fixture.example/car.jpg"],
                    listing_evidence={"h1_text": "Soft Carousell product", "has_price_signal": True},
                ),
                CaptureRequest(
                    platform="mudah",
                    source_url="https://www.mudah.my/guardianlens-soft-200001.htm",
                    page_title="Soft Mudah",
                    screenshot_data_url=data_url(image_bytes(190)),
                    image_urls=["https://fixture.example/mudah.jpg"],
                    listing_evidence={"h1_text": "Soft Mudah product", "has_price_signal": True},
                ),
            ]
            for index, request in enumerate(items):
                captured = create_capture(request)
                payload = MockExtractor().payload.copy()
                payload["title"] = f"Soft Test Product {index + 1}"
                payload["price"] = 900 + index
                processor.process_listing(captured["listing_id"], MockExtractor(payload))
                begin_review(captured["listing_id"])
                approve(captured["listing_id"])
            connection = connect()
            try:
                rows = [dict(row) for row in connection.execute(
                    "SELECT platform,status,COUNT(*) n FROM listings GROUP BY platform,status ORDER BY platform"
                )]
            finally:
                connection.close()
            assert sum(row["n"] for row in rows) == 2
            assert all(row["status"] == "approved" for row in rows)
            return rows
    finally:
        processor.download_image = original_downloader
        object.__setattr__(settings, "root", original_root)
        object.__setattr__(settings, "mode", original_mode)


def main() -> int:
    rows = run_mock_integration()
    print("MOCK INTEGRATION CHECK: PASS (temporary isolated runtime)")
    for row in rows:
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
