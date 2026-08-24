from __future__ import annotations
from pydantic import BaseModel, Field, field_validator, ConfigDict

MAX_URL_LENGTH = 4096
MAX_PAGE_TITLE_LENGTH = 1000
MAX_SCREENSHOT_DATA_URL_LENGTH = 70_000_128
MAX_VISIBLE_TEXT_LENGTH = 250_000
MAX_TITLE_LENGTH = 1000
MAX_DESCRIPTION_LENGTH = 20_000
MAX_SHORT_TEXT_LENGTH = 300

class ListingPageEvidence(BaseModel):
    """Small, non-persisted signals proving the active page looks like a listing."""

    h1_text: str | None = Field(default=None, max_length=500)
    has_price_signal: bool = False
    has_product_structured_data: bool = False
    has_offer_signal: bool = False

class CaptureRequest(BaseModel):
    platform: str = Field(max_length=20)
    source_url: str = Field(max_length=MAX_URL_LENGTH)
    page_title: str | None = Field(default=None, max_length=MAX_PAGE_TITLE_LENGTH)
    # The configured image limit tops out at 50 MiB; this is its base64 envelope.
    screenshot_data_url: str = Field(max_length=MAX_SCREENSHOT_DATA_URL_LENGTH)
    image_urls: list[str] = Field(default_factory=list, max_length=20)
    listing_evidence: ListingPageEvidence = Field(default_factory=ListingPageEvidence)
    visible_text: str | None = Field(default=None, max_length=MAX_VISIBLE_TEXT_LENGTH)
    marketplace_listing_id: str | None = Field(default=None, max_length=128)

    @field_validator("platform")
    @classmethod
    def platform_allowed(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in {"carousell", "mudah"}:
            raise ValueError("platform must be carousell or mudah")
        return v

    @field_validator("image_urls")
    @classmethod
    def image_urls_bounded(cls, values: list[str]) -> list[str]:
        if any(len(value) > MAX_URL_LENGTH for value in values):
            raise ValueError(f"image URLs must not exceed {MAX_URL_LENGTH} characters")
        return values

class ReviewUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=MAX_TITLE_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)
    category: str | None = Field(default=None, max_length=MAX_SHORT_TEXT_LENGTH)
    condition: str | None = Field(default=None, max_length=MAX_SHORT_TEXT_LENGTH)
    price: float | None = Field(default=None, ge=0, le=1_000_000_000, allow_inf_nan=False)
    currency: str | None = Field(default="MYR", max_length=8)
    location_state: str | None = Field(default=None, max_length=MAX_SHORT_TEXT_LENGTH)
    location_city: str | None = Field(default=None, max_length=MAX_SHORT_TEXT_LENGTH)
    account_age_days: int | None = Field(default=None, ge=0, le=100_000)
    seller_rating: float | None = Field(default=None, ge=0, le=5, allow_inf_nan=False)
    review_count: int | None = Field(default=None, ge=0, le=100_000_000)
    active_listing_count: int | None = Field(default=None, ge=0, le=100_000_000)
    language: str | None = Field(default=None, max_length=100)

class BatchStartRequest(BaseModel):
    duration_minutes: int | None = Field(default=60, ge=1, le=720)
    item_limit: int | None = Field(default=50, ge=1, le=10000)

class ExtractedListing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=MAX_TITLE_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)
    category: str | None = Field(default=None, max_length=MAX_SHORT_TEXT_LENGTH)
    condition: str | None = Field(default=None, max_length=MAX_SHORT_TEXT_LENGTH)
    price: float | None = Field(default=None, ge=0, le=1_000_000_000, allow_inf_nan=False)
    currency: str | None = Field(default="MYR", max_length=8)
    location_state: str | None = Field(default=None, max_length=MAX_SHORT_TEXT_LENGTH)
    location_city: str | None = Field(default=None, max_length=MAX_SHORT_TEXT_LENGTH)
    account_age_days: int | None = Field(default=None, ge=0, le=100_000)
    seller_rating: float | None = Field(default=None, ge=0, le=5, allow_inf_nan=False)
    review_count: int | None = Field(default=None, ge=0, le=100_000_000)
    active_listing_count: int | None = Field(default=None, ge=0, le=100_000_000)
    language: str | None = Field(default=None, max_length=100)
