from __future__ import annotations
import base64
import json
from pathlib import Path
from .config import settings, provider_client_options
from .errors import ProviderConfigurationError
from .extraction_contract import EXTRACTION_SCHEMA, SYSTEM_RULES, parse_extracted_listing

class GeminiExtractor:
    """Uses Google's OpenAI-compatible chat.completions endpoint so it can reuse the
    same openai SDK as the OpenRouter extractor, instead of adding a new dependency."""
    provider = "gemini"
    def __init__(self):
        if not settings.gemini_api_key:
            raise ProviderConfigurationError("GEMINI_API_KEY is missing")
        try:
            from openai import OpenAI
        except ImportError as e:
            raise ProviderConfigurationError("OpenAI SDK is not installed") from e
        self.client = OpenAI(
            api_key=settings.gemini_api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            **provider_client_options(),
        )
        self.model = settings.gemini_model
        self.prompt_version = settings.gemini_prompt_version

    def extract(self, screenshot_path: Path, platform: str, page_title: str | None, categories: list[str]) -> dict:
        mime = "image/jpeg" if screenshot_path.suffix.lower() in {".jpg", ".jpeg"} else f"image/{screenshot_path.suffix.lower().lstrip('.')}"
        image_b64 = base64.b64encode(screenshot_path.read_bytes()).decode("ascii")
        data_url = f"data:{mime};base64,{image_b64}"
        prompt = (
            f"Platform: {platform}\nPage title: {page_title or ''}\n"
            f"Allowed categories: {', '.join(categories)}\n"
            f"Required JSON keys and types: {json.dumps(EXTRACTION_SCHEMA)}\n"
            "Return only one JSON object and no markdown fences."
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_RULES},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]},
            ],
        )
        return parse_extracted_listing(response.choices[0].message.content or "")
