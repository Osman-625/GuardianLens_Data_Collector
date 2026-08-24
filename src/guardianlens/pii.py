from __future__ import annotations
import re
from dataclasses import dataclass

EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
OBFUSCATED_EMAIL = re.compile(
    r"\b[A-Z0-9._%+-]{2,}\s*(?:\[at\]|\(at\)|\bat\b)\s*[A-Z0-9.-]+\s*"
    r"(?:\[dot\]|\(dot\)|\bdot\b)\s*[A-Z]{2,}\b",
    re.I,
)
PHONE = re.compile(r"(?<!\d)(?:\+?60|0)[\s.-]?(?:1\d|[3-9]\d)[\s.-]?\d{3,4}[\s.-]?\d{3,4}(?!\d)")
GENERIC_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
BANK = re.compile(r"\b(?:bank\s*(?:account|acc)|acct|account\s*no\.?)[\s:#-]*[A-Z0-9 -]{6,24}\b", re.I)
CONTACT_HANDLE = re.compile(r"\b(?:whatsapp|telegram|wechat|contact\s*me|dm\s*me)\b[^\n]{0,80}", re.I)
CONTACT_LINK = re.compile(r"\b(?:wa\.me|t\.me|api\.whatsapp\.com)/[^\s]+", re.I)
SOCIAL_HANDLE = re.compile(r"(?<![\w@])@[A-Z0-9_][A-Z0-9_.-]{2,}\b", re.I)
ADDRESS_HINT = re.compile(
    r"\b(?:(?:no\.?\s*)?\d{1,5}[A-Za-z]?[,\s]+(?:jalan|lorong|persiaran|street|road|avenue|lane)\b[^\n]{0,100})",
    re.I,
)

PATTERNS = [
    ("email", EMAIL),
    ("obfuscated_email", OBFUSCATED_EMAIL),
    ("phone", PHONE),
    ("bank", BANK),
    ("contact_handle", CONTACT_HANDLE),
    ("contact_link", CONTACT_LINK),
    ("social_handle", SOCIAL_HANDLE),
    ("address", ADDRESS_HINT),
]

@dataclass
class ScrubResult:
    text: str
    matches: list[str]
    kinds: list[str]

def scrub_text(text: str | None) -> ScrubResult:
    if not text:
        return ScrubResult(text or "", [], [])
    cleaned = text
    matches: list[str] = []
    kinds: list[str] = []
    for kind, pattern in PATTERNS:
        found = pattern.findall(cleaned)
        for item in found:
            if isinstance(item, tuple):
                item = " ".join(item)
            matches.append(str(item))
            kinds.append(kind)
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return ScrubResult(cleaned.strip(), matches, sorted(set(kinds)))

def residual_pii(text: str | None) -> list[str]:
    if not text:
        return []
    hits = []
    for name, pattern in [*PATTERNS, ("generic_phone", GENERIC_PHONE)]:
        if pattern.search(text):
            hits.append(name)
    return sorted(set(hits))
