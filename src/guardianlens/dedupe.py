from __future__ import annotations
import hashlib
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

TRACKING_KEYS = {"utm_source","utm_medium","utm_campaign","utm_term","utm_content","fbclid","gclid"}

def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in TRACKING_KEYS]
    path = re.sub(r"/+", "/", parts.path).rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(sorted(query)), ""))

def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def url_hash(url: str) -> str:
    return sha256_text(canonicalize_url(url))

def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())

def metadata_signature(platform: str, title: str | None, description: str | None, price: float | None) -> str:
    price_part = "" if price is None else f"{price:.2f}"
    raw = "|".join([platform.lower(), normalize_text(title), normalize_text(description), price_part])
    return sha256_text(raw)

def hamming_hex(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")
