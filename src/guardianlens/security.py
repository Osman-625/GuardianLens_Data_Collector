from __future__ import annotations
import ipaddress
import os
import secrets
from urllib.parse import urlsplit
from fastapi import Form, Header, HTTPException, Request, status
from .config import settings

def ensure_local_token() -> str:
    path = settings.token_path
    path.parent.mkdir(parents=True, exist_ok=True)
    token = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    if len(token) < 32:
        token = secrets.token_urlsafe(32)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        temporary.write_text(token, encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    return token

def get_local_token() -> str:
    return ensure_local_token()

def token_is_valid(candidate: str | None) -> bool:
    return bool(candidate) and secrets.compare_digest(candidate, get_local_token())

def require_local_token(x_guardianlens_token: str | None = Header(default=None)) -> None:
    if not token_is_valid(x_guardianlens_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid local collector token")

def require_local_token_form(token: str | None = Form(default=None)) -> None:
    """Same check as require_local_token, for plain HTML <form> posts that can't set a custom
    header. Without this, any page open in the same browser could POST to these review-mutating
    routes (no CORS preflight is required for a simple form-encoded POST)."""
    if not token_is_valid(token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid or missing form token")

def _is_loopback_host(value: str | None) -> bool:
    if not value:
        return False
    value = value.strip().strip("[]").rstrip(".").lower()
    if value == "localhost":
        return True
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)

def enforce_local_request(request: Request) -> None:
    """Reject DNS-rebinding hosts and non-loopback clients at the app boundary.

    This remains effective if somebody bypasses ``run()`` and launches Uvicorn
    directly with a non-local bind. ``testclient`` is Starlette's in-process
    transport name and can never be a real network client address.
    """
    client_host = request.client.host if request.client else None
    in_process_test = client_host == "testclient" and request.url.hostname == "testserver"
    if not in_process_test and not _is_loopback_host(request.url.hostname):
        raise HTTPException(status_code=421, detail="GuardianLens accepts only localhost Host headers")
    if client_host != "testclient" and not _is_loopback_host(client_host):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="GuardianLens accepts only loopback clients")

def require_same_origin_asset(request: Request) -> None:
    """Prevent a different website from embedding private evidence."""
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="cross-site private asset request rejected")
    expected = (request.url.scheme.lower(), request.url.netloc.lower())
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if not value:
            continue
        parsed = urlsplit(value)
        if (parsed.scheme.lower(), parsed.netloc.lower()) != expected:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="cross-origin private asset request rejected")

def assert_safe_bind(host: str) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("GuardianLens refuses non-localhost binding by default. Set HOST=127.0.0.1.")
