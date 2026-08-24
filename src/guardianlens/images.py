from __future__ import annotations
import base64
import binascii
import hashlib
import ipaddress
import io
import os
import socket
import uuid
import warnings
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunsplit
import httpx
from PIL import Image, UnidentifiedImageError
from .config import settings
from .db import now_iso

SUPPORTED = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 5

def decode_data_url(data_url: str) -> bytes:
    if not data_url.startswith("data:image/") or "," not in data_url:
        raise ValueError("invalid screenshot data URL")
    header, encoded = data_url.split(",", 1)
    if not header.lower().endswith(";base64"):
        raise ValueError("invalid screenshot data URL")
    maximum_encoded = 4 * ((settings.max_image_bytes + 2) // 3)
    if len(encoded) > maximum_encoded:
        raise ValueError("image exceeds configured maximum size")
    padding = len(encoded) - len(encoded.rstrip("="))
    decoded_size = (len(encoded) // 4) * 3 - padding
    if decoded_size > settings.max_image_bytes:
        raise ValueError("image exceeds configured maximum size")
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid screenshot data URL") from exc

def validate_image_bytes(data: bytes) -> tuple[str, int, int, str, int]:
    if not data:
        raise ValueError("empty image")
    if len(data) > settings.max_image_bytes:
        raise ValueError("image exceeds configured maximum size")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as im:
                fmt = im.format
                width, height = im.size
                if fmt not in SUPPORTED:
                    raise ValueError(f"unsupported image format: {fmt}")
                if width < settings.image_min_width or height < settings.image_min_height:
                    raise ValueError(f"image too small: {width}x{height}")
                if width * height > settings.max_image_pixels:
                    raise ValueError(f"image exceeds configured pixel limit: {width}x{height}")
                im.verify()
            with Image.open(io.BytesIO(data)) as im:
                im.load()
                gray = im.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
                pixel_reader = getattr(gray, "get_flattened_data", gray.getdata)
                px = list(pixel_reader())
                bits = []
                for row in range(8):
                    offset = row * 9
                    bits.extend(px[offset + col] > px[offset + col + 1] for col in range(8))
                phash = f"{sum(int(bit) << (63 - index) for index, bit in enumerate(bits)):016x}"
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as e:
        raise ValueError("corrupt or unreadable image") from e
    sha = hashlib.sha256(data).hexdigest()
    return fmt, width, height, sha, int(phash, 16)


def sanitize_product_image(data: bytes) -> tuple[bytes, str, int, int, str, int]:
    """Decode and re-encode a product image, dropping EXIF and ancillary metadata."""
    fmt, _, _, _, _ = validate_image_bytes(data)
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            output = io.BytesIO()
            if fmt == "JPEG":
                cleaned = source.convert("RGB")
                cleaned.save(output, format="JPEG", quality=95, optimize=True)
            elif fmt == "PNG":
                has_alpha = "A" in source.getbands() or "transparency" in source.info
                cleaned = source.convert("RGBA" if has_alpha else "RGB")
                cleaned.save(output, format="PNG", optimize=True)
            else:
                has_alpha = "A" in source.getbands()
                cleaned = source.convert("RGBA" if has_alpha else "RGB")
                cleaned.save(output, format="WEBP", lossless=True, method=6)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("image could not be safely re-encoded") from exc
    stored = output.getvalue()
    stored_fmt, width, height, sha, phash = validate_image_bytes(stored)
    return stored, stored_fmt, width, height, sha, phash

def save_private_screenshot(platform: str, listing_id: str, data_url: str) -> str:
    data, fmt, _, _, _, _ = sanitize_product_image(decode_data_url(data_url))
    ext = SUPPORTED[fmt]
    folder = settings.data_dir / "private" / "screenshots" / platform / listing_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"listing{ext}"
    temporary = folder / f".{uuid.uuid4().hex}.part"
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return str(path.relative_to(settings.root))

def _validate_remote_url(url: str) -> frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("remote image URLs must use HTTPS")
    if parsed.username or parsed.password or not parsed.hostname:
        raise ValueError("image URL contains invalid authority information")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("image URL contains an invalid port") from exc
    if port not in {None, 443}:
        raise ValueError("remote image URLs must use the standard HTTPS port")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("image URL resolves to a local host")
    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        try:
            addresses = frozenset({
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
            })
        except OSError as exc:
            raise ValueError("image host could not be resolved") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("image URL resolves to a non-public address")
    return frozenset(addresses)


def _validate_connected_peer(response, url: str, approved_addresses: frozenset) -> None:
    """Reject DNS rebinding when httpx exposes the connected peer address.

    On transports without peer metadata, a second resolution must remain identical
    to the set approved immediately before the request.
    """
    extensions = getattr(response, "extensions", {}) or {}
    network_stream = extensions.get("network_stream")
    peer = None
    if network_stream is not None:
        for key in ("server_addr", "peername"):
            try:
                peer = network_stream.get_extra_info(key)
            except (AttributeError, OSError):
                peer = None
            if peer:
                break
    if peer:
        try:
            peer_address = ipaddress.ip_address(peer[0] if isinstance(peer, tuple) else peer)
        except ValueError as exc:
            raise ValueError("remote image peer address is invalid") from exc
        if not peer_address.is_global or peer_address not in approved_addresses:
            raise ValueError("remote image connection address changed or is non-public")
        return
    if approved_addresses and _validate_remote_url(url) != approved_addresses:
        raise ValueError("remote image host resolution changed during download")


def download_image(url: str) -> bytes:
    current = url
    headers = {"User-Agent": "GuardianLens-ManualCollector/2.0"}
    with httpx.Client(
        timeout=settings.request_timeout_seconds,
        follow_redirects=False,
        trust_env=False,
        headers=headers,
    ) as client:
        for redirect_count in range(MAX_REDIRECTS + 1):
            approved_addresses = _validate_remote_url(current)
            with client.stream("GET", current) as response:
                _validate_connected_peer(response, current, approved_addresses)
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location or redirect_count >= MAX_REDIRECTS:
                        raise ValueError("image redirect chain is invalid or too long")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if not content_type.lower().startswith("image/"):
                    raise ValueError("remote resource is not an image")
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > settings.max_image_bytes:
                            raise ValueError("image exceeds configured maximum size")
                    except ValueError as exc:
                        if "exceeds" in str(exc):
                            raise
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > settings.max_image_bytes:
                        raise ValueError("image exceeds configured maximum size")
                return bytes(chunks)
    raise ValueError("image redirect chain did not produce an image")


def _safe_source_reference(source_url: str | None) -> str | None:
    if not source_url:
        return None
    parsed = urlparse(source_url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

def persist_product_image(con, listing_id: str, platform: str, data: bytes, source_position: int, source_url: str | None = None, is_primary: bool = False) -> dict:
    stored, fmt, width, height, sha, ph_int = sanitize_product_image(data)
    phash = f"{ph_int:016x}"
    existing = con.execute("SELECT image_id, listing_id FROM images WHERE sha256=? LIMIT 1", (sha,)).fetchone()
    if existing:
        return {"duplicate": True, "duplicate_of": existing["listing_id"], "sha256": sha, "perceptual_hash": phash}
    ext = SUPPORTED[fmt]
    image_id = str(uuid.uuid4())
    folder = settings.data_dir / "images" / platform / listing_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"image_{source_position:03d}_{image_id[:12]}{ext}"
    tmp = settings.data_dir / "temporary" / f"{uuid.uuid4()}.part"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp.write_bytes(stored)
        os.replace(tmp, path)
        con.execute("""
            INSERT INTO images(image_id,listing_id,platform,storage_path,sha256,perceptual_hash,file_size_bytes,width,height,source_position,is_primary,source_url,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (image_id, listing_id, platform, str(path.relative_to(settings.root)), sha, phash, len(stored), width, height, source_position, int(is_primary), _safe_source_reference(source_url), now_iso()))
    except Exception:
        tmp.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        raise
    return {"duplicate": False, "image_id": image_id, "sha256": sha, "perceptual_hash": phash, "path": str(path.relative_to(settings.root))}
