from __future__ import annotations
import base64
import json
from pathlib import Path
from .config import settings, provider_client_options
from .errors import ProviderConfigurationError
from .extraction_contract import EXTRACTION_SCHEMA, SYSTEM_RULES, parse_extracted_listing

class AnthropicExtractor:
    provider = "anthropic"
    def __init__(self):
        if not settings.anthropic_api_key:
            raise ProviderConfigurationError("ANTHROPIC_API_KEY is missing")
        try:
            import anthropic
        except ImportError as e:
            raise ProviderConfigurationError("Anthropic SDK is not installed") from e
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key, **provider_client_options())
        self.model = settings.anthropic_model
        self.prompt_version = settings.anthropic_prompt_version

    def extract(self, screenshot_path: Path, platform: str, page_title: str | None, categories: list[str]) -> dict:
        media_type = "image/jpeg" if screenshot_path.suffix.lower() in {".jpg", ".jpeg"} else f"image/{screenshot_path.suffix.lower().lstrip('.')}"
        image_b64 = base64.standard_b64encode(screenshot_path.read_bytes()).decode("ascii")
        prompt = (
            f"Platform: {platform}\nPage title: {page_title or ''}\n"
            f"Allowed categories: {', '.join(categories)}\n"
            f"Required JSON keys and types: {json.dumps(EXTRACTION_SCHEMA)}\n"
            "Return only one JSON object and no markdown fences."
        )
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=SYSTEM_RULES,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        raw = "".join(block.text for block in response.content if block.type == "text")
        return parse_extracted_listing(raw)
