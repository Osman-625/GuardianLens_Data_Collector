from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from dotenv import load_dotenv
import yaml

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

AI_PROVIDERS = ("openai", "anthropic", "openrouter", "gemini")
RUN_MODES = ("production", "soft_test")
SCREENSHOT_RETENTION_POLICIES = ("review_only", "retain_private", "delete_after_approval")


def _env_text(name: str, default: str) -> str:
    return (os.getenv(name) or default).strip()


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = (os.getenv(name) or str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = (os.getenv(name) or str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value

@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    host: str = _env_text("HOST", "127.0.0.1")
    port: int = _env_int("PORT", 8765, minimum=1, maximum=65535)
    mode: str = _env_text("GUARDIANLENS_MODE", "production").lower()
    ai_provider: str = _env_text("AI_PROVIDER", "openai").lower()
    openai_api_key: str = _env_text("OPENAI_API_KEY", "")
    openai_model: str = _env_text("OPENAI_MODEL", "gpt-5.6-luna")
    openai_prompt_version: str = _env_text("OPENAI_PROMPT_VERSION", "collector_v1")
    anthropic_api_key: str = _env_text("ANTHROPIC_API_KEY", "")
    anthropic_model: str = _env_text("ANTHROPIC_MODEL", "claude-opus-5")
    anthropic_prompt_version: str = _env_text("ANTHROPIC_PROMPT_VERSION", "collector_v1")
    openrouter_api_key: str = _env_text("OPENROUTER_API_KEY", "")
    openrouter_model: str = _env_text("OPENROUTER_MODEL", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free")
    openrouter_prompt_version: str = _env_text("OPENROUTER_PROMPT_VERSION", "collector_v1")
    gemini_api_key: str = _env_text("GEMINI_API_KEY", "")
    gemini_model: str = _env_text("GEMINI_MODEL", "gemini-3.6-flash")
    gemini_prompt_version: str = _env_text("GEMINI_PROMPT_VERSION", "collector_v1")
    screenshot_retention: str = _env_text("SCREENSHOT_RETENTION", "review_only").lower()
    max_product_images: int = _env_int("MAX_PRODUCT_IMAGES", 12, minimum=1, maximum=20)
    max_image_bytes: int = _env_int("MAX_IMAGE_BYTES", 12 * 1024 * 1024, minimum=1024, maximum=50 * 1024 * 1024)
    image_min_width: int = _env_int("IMAGE_MIN_WIDTH", 160, minimum=1, maximum=10000)
    image_min_height: int = _env_int("IMAGE_MIN_HEIGHT", 160, minimum=1, maximum=10000)
    max_image_pixels: int = _env_int("MAX_IMAGE_PIXELS", 40_000_000, minimum=10_000, maximum=100_000_000)
    request_timeout_seconds: int = _env_int("REQUEST_TIMEOUT_SECONDS", 45, minimum=1, maximum=300)
    ai_max_attempts: int = _env_int("AI_MAX_ATTEMPTS", 3, minimum=1, maximum=5)
    ai_retry_base_seconds: float = _env_float("AI_RETRY_BASE_SECONDS", 1.0, minimum=0.0, maximum=30.0)
    ai_retry_max_seconds: float = _env_float("AI_RETRY_MAX_SECONDS", 8.0, minimum=0.0, maximum=60.0)
    operator_timezone: str = _env_text("OPERATOR_TIMEZONE", "Asia/Kuala_Lumpur")

    def __post_init__(self) -> None:
        if self.ai_provider not in AI_PROVIDERS:
            raise ValueError(f"AI_PROVIDER must be one of: {', '.join(AI_PROVIDERS)}")
        if self.mode not in RUN_MODES:
            raise ValueError(f"GUARDIANLENS_MODE must be one of: {', '.join(RUN_MODES)}")
        if self.screenshot_retention not in SCREENSHOT_RETENTION_POLICIES:
            raise ValueError(
                "SCREENSHOT_RETENTION must be one of: " + ", ".join(SCREENSHOT_RETENTION_POLICIES)
            )
        if self.ai_retry_base_seconds > self.ai_retry_max_seconds:
            raise ValueError("AI_RETRY_BASE_SECONDS cannot exceed AI_RETRY_MAX_SECONDS")
        try:
            ZoneInfo(self.operator_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"OPERATOR_TIMEZONE is not available: {self.operator_timezone}") from exc

    @property
    def base_data_dir(self) -> Path:
        return self.root / "data"

    @property
    def data_dir(self) -> Path:
        # All soft-test runtime assets are physically separated from production.
        if self.mode == "soft_test":
            return self.base_data_dir / "soft_test" / "runtime"
        return self.base_data_dir

    @property
    def db_path(self) -> Path:
        if self.mode == "soft_test":
            return self.base_data_dir / "soft_test" / "collector_soft_test.sqlite3"
        return self.base_data_dir / "collector.sqlite3"

    @property
    def token_path(self) -> Path:
        # One localhost token is shared by production and soft-test server modes.
        return self.base_data_dir / "private" / "local_token.txt"

    def categories(self) -> list[dict]:
        with open(self.root / "config" / "categories.yaml", encoding="utf-8") as f:
            return yaml.safe_load(f)["categories"]

    def platform_targets(self) -> dict:
        with open(self.root / "config" / "platform_targets.yaml", encoding="utf-8") as f:
            return yaml.safe_load(f)

settings = Settings()

def _provider_override_path() -> Path:
    return settings.base_data_dir / "private" / "ai_provider_override.txt"

def get_active_provider() -> str:
    """The provider actually used by the next extraction: the UI-set override if present, else AI_PROVIDER from .env."""
    p = _provider_override_path()
    if p.exists():
        v = p.read_text(encoding="utf-8").strip().lower()
        if v in AI_PROVIDERS:
            return v
    return settings.ai_provider

def set_active_provider(provider: str) -> None:
    provider = provider.strip().lower()
    if provider not in AI_PROVIDERS:
        raise ValueError(f"unknown provider: {provider!r}")
    p = _provider_override_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    temporary = p.with_name(f".{p.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(provider, encoding="utf-8")
        os.replace(temporary, p)
    finally:
        temporary.unlink(missing_ok=True)

def provider_model(provider: str) -> str:
    return {"openai": settings.openai_model, "anthropic": settings.anthropic_model, "openrouter": settings.openrouter_model, "gemini": settings.gemini_model}[provider]

def provider_prompt_version(provider: str) -> str:
    return {
        "openai": settings.openai_prompt_version,
        "anthropic": settings.anthropic_prompt_version,
        "openrouter": settings.openrouter_prompt_version,
        "gemini": settings.gemini_prompt_version,
    }[provider]

def provider_configured(provider: str) -> bool:
    return bool({"openai": settings.openai_api_key, "anthropic": settings.anthropic_api_key, "openrouter": settings.openrouter_api_key, "gemini": settings.gemini_api_key}[provider])

def provider_client_options() -> dict[str, int | float]:
    """Disable SDK retries so GuardianLens owns one bounded, auditable retry policy."""
    return {"timeout": settings.request_timeout_seconds, "max_retries": 0}
