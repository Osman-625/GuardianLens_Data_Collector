from __future__ import annotations

from dataclasses import dataclass
import json
import re


@dataclass(frozen=True)
class ErrorClassification:
    kind: str
    retryable: bool
    halt_batch: bool
    user_message: str


class ProviderConfigurationError(RuntimeError):
    """A selected provider cannot run until local configuration is corrected."""


class ProviderOperationError(RuntimeError):
    """Safe wrapper carried from processor to batch/UI-facing status records."""

    def __init__(self, classification: ErrorClassification):
        self.classification = classification
        super().__init__(classification.user_message)


_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_ -]?key|authorization|bearer|secret|token)\b(\s*[:=]\s*|\s+)([^\s,;]+)"
)
_SECRET_PREFIX_RE = re.compile(
    r"(?i)\b(?:sk-ant-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,})\b"
)


def redact_secrets(text: str) -> str:
    """Redact common credential shapes before any diagnostic text is persisted."""
    text = _SECRET_ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
    return _SECRET_PREFIX_RE.sub("[REDACTED]", text)


def _result(kind: str, *, retryable: bool, halt_batch: bool, message: str) -> ErrorClassification:
    return ErrorClassification(kind, retryable, halt_batch, redact_secrets(message))


def classify_error(exc: BaseException) -> ErrorClassification:
    """Normalize provider/SDK failures without exposing raw exception bodies or keys."""
    if isinstance(exc, ProviderOperationError):
        return exc.classification

    name = type(exc).__name__
    text = redact_secrets(str(exc)).lower()
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)

    if isinstance(exc, ProviderConfigurationError) or any(
        hint in text
        for hint in (
            "api_key is missing",
            "api key is missing",
            "sdk is not installed",
            "unknown active ai provider",
            "unsupported model",
            "model does not exist",
            "model not found",
            "invalid provider configuration",
        )
    ):
        return _result(
            "config",
            retryable=False,
            halt_batch=True,
            message="The selected AI provider is not configured correctly. Check its key, SDK, and model settings.",
        )

    if name in {"AuthenticationError", "PermissionDeniedError"} or status_code in {401, 403} or any(
        hint in text for hint in ("invalid api key", "unauthorized", "permission denied", "api key not valid")
    ):
        return _result(
            "auth",
            retryable=False,
            halt_batch=True,
            message="The selected AI provider rejected its credentials or permissions.",
        )

    quota_hints = (
        "insufficient_quota",
        "insufficient quota",
        "quota exhausted",
        "quota exceeded",
        "billing limit",
        "credit balance",
        "out of credits",
        "resource_exhausted",
    )
    if any(hint in text for hint in quota_hints):
        return _result(
            "quota",
            retryable=False,
            halt_batch=True,
            message="The selected AI provider has insufficient quota or credit.",
        )

    if name == "RateLimitError" or status_code == 429 or "rate limit" in text or "too many requests" in text:
        return _result(
            "rate_limit",
            retryable=True,
            halt_batch=False,
            message="The AI provider is temporarily rate-limiting requests.",
        )

    if isinstance(exc, TimeoutError) or name in {
        "APITimeoutError",
        "TimeoutException",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
    }:
        return _result(
            "timeout",
            retryable=True,
            halt_batch=False,
            message="The AI provider request timed out.",
        )

    if isinstance(exc, ConnectionError) or name in {
        "APIConnectionError",
        "ConnectError",
        "NetworkError",
        "ReadError",
        "WriteError",
        "RemoteProtocolError",
    } or any(hint in text for hint in ("connection reset", "connection refused", "temporary failure in name resolution")):
        return _result(
            "network",
            retryable=True,
            halt_batch=False,
            message="The AI provider is temporarily unreachable.",
        )

    if isinstance(status_code, int) and status_code >= 500:
        return _result(
            "network",
            retryable=True,
            halt_batch=False,
            message="The AI provider is temporarily unavailable.",
        )

    if isinstance(exc, json.JSONDecodeError) or name in {
        "InvalidExtractionResponse",
        "ValidationError",
    } or (isinstance(exc, ValueError) and "ai returned" in text):
        return _result(
            "invalid_response",
            retryable=False,
            halt_batch=False,
            message="The AI provider returned an invalid structured listing response.",
        )

    if status_code in {400, 404, 422} and "model" in text:
        return _result(
            "config",
            retryable=False,
            halt_batch=True,
            message="The configured AI model is unavailable or unsupported for this provider.",
        )

    return _result(
        "other",
        retryable=False,
        halt_batch=False,
        message="AI extraction failed for this listing.",
    )


def format_error_status(
    classification: ErrorClassification,
    *,
    provider: str,
    attempt: int,
    max_attempts: int,
) -> str:
    retryable = "Yes" if classification.retryable else "No"
    safe_provider = redact_secrets(str(provider))[:100]
    return (
        f"Provider: {safe_provider}. Reason: {classification.user_message} "
        f"Retryable: {retryable}. Attempts: {attempt} / {max_attempts}."
    )
