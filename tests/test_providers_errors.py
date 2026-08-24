from __future__ import annotations

from contextlib import contextmanager
import json
import sys
from types import SimpleNamespace

import pytest

from guardianlens.config import Settings, provider_client_options, settings
from guardianlens.errors import (
    ProviderConfigurationError,
    classify_error,
    redact_secrets,
)
from guardianlens.extraction_contract import EXTRACTION_SCHEMA


@contextmanager
def setting(name, value):
    original = getattr(settings, name)
    object.__setattr__(settings, name, value)
    try:
        yield
    finally:
        object.__setattr__(settings, name, original)


def test_settings_reject_invalid_provider_mode_and_retention():
    with pytest.raises(ValueError, match="AI_PROVIDER"):
        Settings(ai_provider="unknown")
    with pytest.raises(ValueError, match="GUARDIANLENS_MODE"):
        Settings(mode="test")
    with pytest.raises(ValueError, match="SCREENSHOT_RETENTION"):
        Settings(screenshot_retention="forever")


def test_client_retry_policy_is_owned_by_collector():
    assert provider_client_options() == {
        "timeout": settings.request_timeout_seconds,
        "max_retries": 0,
    }


@pytest.mark.parametrize(
    ("exc", "kind", "retryable", "halt"),
    [
        (ProviderConfigurationError("SDK is not installed"), "config", False, True),
        (type("AuthenticationError", (Exception,), {})("bad key"), "auth", False, True),
        (type("RateLimitError", (Exception,), {})("too many requests"), "rate_limit", True, False),
        (type("RateLimitError", (Exception,), {})("insufficient_quota"), "quota", False, True),
        (TimeoutError("slow"), "timeout", True, False),
        (type("APIConnectionError", (Exception,), {})("offline"), "network", True, False),
        (ConnectionError("connection reset"), "network", True, False),
        (RuntimeError("RESOURCE_EXHAUSTED: quota exceeded"), "quota", False, True),
        (json.JSONDecodeError("bad", "{", 0), "invalid_response", False, False),
    ],
)
def test_provider_error_classification(exc, kind, retryable, halt):
    result = classify_error(exc)
    assert (result.kind, result.retryable, result.halt_batch) == (kind, retryable, halt)


def test_error_messages_redact_common_credentials():
    text = "api_key=" + "sk-" + "secretvalue123456789 bearer " + "AIza" + "012345678901234567890123456789"
    redacted = redact_secrets(text)
    assert "secretvalue" not in redacted
    assert "AIza" not in redacted
    assert redacted.count("[REDACTED]") >= 2


def test_all_provider_adapters_share_normalized_schema_and_sdk_options(tmp_path, monkeypatch):
    raw = json.dumps({"title": "Fixture"})
    constructor_calls = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            constructor_calls.append(kwargs)
            self.responses = SimpleNamespace(create=lambda **unused: SimpleNamespace(output_text=raw))
            message = SimpleNamespace(content=raw)
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=lambda **unused: SimpleNamespace(choices=[SimpleNamespace(message=message)]))
            )

    class FakeAnthropic:
        def __init__(self, **kwargs):
            constructor_calls.append(kwargs)
            block = SimpleNamespace(type="text", text=raw)
            self.messages = SimpleNamespace(create=lambda **unused: SimpleNamespace(content=[block]))

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic))
    screenshot = tmp_path / "listing.jpg"
    screenshot.write_bytes(b"fixture")

    from guardianlens.openai_extractor import OpenAIExtractor
    from guardianlens.anthropic_extractor import AnthropicExtractor
    from guardianlens.openrouter_extractor import OpenRouterExtractor
    from guardianlens.gemini_extractor import GeminiExtractor

    with (
        setting("openai_api_key", "fixture"),
        setting("anthropic_api_key", "fixture"),
        setting("openrouter_api_key", "fixture"),
        setting("gemini_api_key", "fixture"),
    ):
        outputs = [
            OpenAIExtractor().extract(screenshot, "carousell", None, []),
            AnthropicExtractor().extract(screenshot, "carousell", None, []),
            OpenRouterExtractor().extract(screenshot, "carousell", None, []),
            GeminiExtractor().extract(screenshot, "carousell", None, []),
        ]

    assert all(set(output) == set(EXTRACTION_SCHEMA) for output in outputs)
    assert all(output["currency"] is None for output in outputs)
    assert all(call["max_retries"] == 0 for call in constructor_calls)
    assert all(call["timeout"] == settings.request_timeout_seconds for call in constructor_calls)


@pytest.mark.parametrize("provider", ["openai", "anthropic", "openrouter", "gemini"])
def test_active_provider_controls_default_factory(provider, monkeypatch):
    import guardianlens.processor as processor
    module = __import__(f"guardianlens.{provider}_extractor", fromlist=["*"])
    class_name = {
        "openai": "OpenAIExtractor",
        "anthropic": "AnthropicExtractor",
        "openrouter": "OpenRouterExtractor",
        "gemini": "GeminiExtractor",
    }[provider]
    sentinel = object()
    monkeypatch.setattr(processor, "get_active_provider", lambda: provider)
    monkeypatch.setattr(module, class_name, lambda: sentinel)
    assert processor.default_extractor() is sentinel


def test_missing_unused_provider_sdk_does_not_affect_selected_provider(monkeypatch):
    import guardianlens.processor as processor
    import guardianlens.openai_extractor as openai_module

    sentinel = object()
    monkeypatch.setattr(processor, "get_active_provider", lambda: "openai")
    monkeypatch.setattr(openai_module, "OpenAIExtractor", lambda: sentinel)
    monkeypatch.delitem(sys.modules, "anthropic", raising=False)
    assert processor.default_extractor() is sentinel
