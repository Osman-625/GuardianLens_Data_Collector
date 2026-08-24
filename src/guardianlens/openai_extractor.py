from __future__ import annotations
import base64
import json
from pathlib import Path
from .config import settings, provider_client_options
from .errors import ProviderConfigurationError
from .extraction_contract import (
    EXTRACTION_SCHEMA,
    SYSTEM_RULES,
    normalize_extracted_listing,
    parse_extracted_listing,
)

class OpenAIExtractor:
    provider = "openai"
    def __init__(self):
        if not settings.openai_api_key:
            raise ProviderConfigurationError("OPENAI_API_KEY is missing")
        try:
            from openai import OpenAI
        except ImportError as e:
            raise ProviderConfigurationError("OpenAI SDK is not installed") from e
        self.client = OpenAI(api_key=settings.openai_api_key, **provider_client_options())
        self.model = settings.openai_model
        self.prompt_version = settings.openai_prompt_version

    def extract(self, screenshot_path: Path, platform: str, page_title: str | None, categories: list[str]) -> dict:
        mime = "image/jpeg" if screenshot_path.suffix.lower() in {".jpg", ".jpeg"} else f"image/{screenshot_path.suffix.lower().lstrip('.')}"
        image_b64 = base64.b64encode(screenshot_path.read_bytes()).decode("ascii")
        data_url = f"data:{mime};base64,{image_b64}"
        prompt = (
            f"Platform: {platform}\nPage title: {page_title or ''}\n"
            f"Allowed categories: {', '.join(categories)}\n"
            f"Required JSON keys and types: {json.dumps(EXTRACTION_SCHEMA)}"
        )
        response = self.client.responses.create(
            model=self.model,
            instructions=SYSTEM_RULES,
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": data_url, "detail": "high"},
                ],
            }],
        )
        return parse_extracted_listing(response.output_text)

class MockExtractor:
    provider = "mock"
    model = "fixture-extractor"
    prompt_version = "test_v1"
    def __init__(self, payload: dict | None = None):
        self.payload = payload or {
            "title": "Fixture Product",
            "description": "Fixture description",
            "category": "Audio",
            "condition": "Used",
            "price": 900,
            "currency": "MYR",
            "location_state": "Selangor",
            "location_city": None,
            "account_age_days": 3650,
            "seller_rating": 5.0,
            "review_count": 55,
            "active_listing_count": 8,
            "language": "en",
        }
    def extract(self, screenshot_path: Path, platform: str, page_title: str | None, categories: list[str]) -> dict:
        return normalize_extracted_listing(self.payload)
