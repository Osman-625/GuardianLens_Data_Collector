from __future__ import annotations
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol
from .models import ExtractedListing

EXTRACTION_SCHEMA = {
    "title": "string|null",
    "description": "string|null",
    "category": "string|null",
    "condition": "string|null",
    "price": "number|null",
    "currency": "string|null",
    "location_state": "string|null",
    "location_city": "string|null",
    "account_age_days": "integer|null",
    "seller_rating": "number|null",
    "review_count": "integer|null",
    "active_listing_count": "integer|null",
    "language": "string|null",
}

SYSTEM_RULES = """You extract only visible marketplace listing fields from one screenshot.
Do not infer hidden seller identity. Do not return seller username, seller name, email, phone, bank details, contact handles, or exact street address.
If a field is not visible, return null. Return only one JSON object and no markdown.
No confidence fields are allowed. Categories should use the configured GuardianLens category names when an obvious mapping exists."""

class Extractor(Protocol):
    provider: str
    model: str
    prompt_version: str
    def extract(self, screenshot_path: Path, platform: str, page_title: str | None, categories: list[str]) -> dict: ...


class InvalidExtractionResponse(ValueError):
    """Provider output was not the normalized GuardianLens listing contract."""

_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?```\s*$", re.DOTALL)

def strip_code_fence(text: str) -> str:
    """Removes a ```json ... ``` (or bare ```) wrapper some providers add around the JSON body.

    str.strip("`") only trims characters that sit at the very edge of the string, so a trailing
    newline after the closing fence (very common in real provider output) leaves the fence intact
    and breaks json.loads. This matches the fence as a whole block instead.
    """
    text = text.strip()
    if not text.startswith("```"):
        return text
    m = _FENCE_RE.match(text)
    return m.group(1).strip() if m else text


def normalize_extracted_listing(value: Mapping[str, object]) -> dict:
    """Validate one provider mapping and materialize every contract key.

    Explicitly filling absent keys with ``None`` prevents Pydantic field defaults
    (notably currency) from turning an unobserved value into an apparent extraction.
    """
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise InvalidExtractionResponse("AI returned an invalid structured listing response")
    if any("confidence" in key.lower() for key in value):
        raise InvalidExtractionResponse("AI returned a forbidden confidence field")
    complete = {key: value.get(key) for key in EXTRACTION_SCHEMA}
    complete.update({key: item for key, item in value.items() if key not in EXTRACTION_SCHEMA})
    try:
        return ExtractedListing.model_validate(complete).model_dump()
    except Exception as exc:
        raise InvalidExtractionResponse("AI returned fields outside the listing contract") from exc


def parse_extracted_listing(text: str) -> dict:
    """Parse provider text without retaining raw malformed output in errors."""
    try:
        parsed = json.loads(strip_code_fence(text))
    except (json.JSONDecodeError, TypeError) as exc:
        raise InvalidExtractionResponse("AI returned invalid listing JSON") from exc
    if not isinstance(parsed, dict):
        raise InvalidExtractionResponse("AI returned a non-object listing response")
    return normalize_extracted_listing(parsed)
